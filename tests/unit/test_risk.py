from dataclasses import replace
from decimal import Decimal as D

import pytest
from hypothesis import given, strategies as st

from crypto_bot.domain.enums import Mode, PositionSide
from crypto_bot.domain.models import ApprovedSize, RiskInput, Trial
from tests.fixtures.factories import context, signal


def risk_case(**changes):
    ctx = context()
    ctx = replace(ctx, rules=replace(ctx.rules, **changes))
    return RiskInput(Trial("r", Mode.PAPER, D("20"), D("18"), D("50"), {}), signal(), ctx)


def test_rounding_never_increases_risk():
    from crypto_bot.risk.sizing import size_entry

    result = size_entry(risk_case())
    assert result.quantity == D("0.08")
    assert result.notional == D("8")
    assert result.planned_loss == D("0.1872")
    assert result.budget == D("0.20")


def test_minimum_notional_does_not_force_larger_order():
    from crypto_bot.risk.sizing import size_entry

    assert size_entry(risk_case(min_notional=D("10"))).reason == "BELOW_MIN_NOTIONAL"


def test_equity_and_collateral_caps_apply_independently():
    from crypto_bot.risk.sizing import size_entry

    case = risk_case()
    small_atr = replace(case.signal, atr=D("0.005"))
    large = size_entry(replace(case, signal=small_atr))
    assert large.notional <= D("20")
    small_available = replace(case.context.account, available_balance=D("1"))
    blocked = size_entry(replace(case, context=replace(case.context, account=small_available)))
    assert blocked.reason == "BELOW_MIN_NOTIONAL"


def test_zero_atr_and_unknown_maintenance_inputs_block():
    from crypto_bot.risk.sizing import size_entry

    case = risk_case()
    assert (
        size_entry(replace(case, signal=replace(case.signal, atr=D("0")))).reason == "INVALID_STOP"
    )
    assert size_entry(risk_case(maintenance_rate=None)).reason == "MISSING_BRACKETS"


@pytest.mark.parametrize(
    "side,entry,stop,liquidation,expected",
    [
        (PositionSide.LONG, "100", "98", "95", True),
        (PositionSide.LONG, "100", "95", "95", False),
        (PositionSide.SHORT, "100", "102", "105", True),
        (PositionSide.SHORT, "100", "105", "105", False),
    ],
)
def test_liquidation_cushion_in_both_directions(side, entry, stop, liquidation, expected):
    from crypto_bot.risk.sizing import liquidation_buffer_ok

    assert liquidation_buffer_ok(side, D(entry), D(stop), D(liquidation)) is expected


@given(
    st.decimals(min_value="0.01", max_value="100", places=2, allow_nan=False, allow_infinity=False)
)
def test_accepted_quantity_always_respects_budget_and_filters(atr):
    from crypto_bot.risk.sizing import size_entry

    case = risk_case()
    result = size_entry(replace(case, signal=replace(case.signal, atr=atr)))
    if isinstance(result, ApprovedSize):
        assert result.quantity % D("0.01") == 0
        assert result.planned_loss <= result.budget
        assert D("5") <= result.notional <= D("20")


def test_tick_rounding_moves_stop_toward_entry_and_target_away():
    from crypto_bot.risk.sizing import protective_levels

    assert protective_levels(PositionSide.LONG, D("100.005"), D("1"), D("0.01")) == (
        D("98.01"),
        D("104.00"),
    )
    assert protective_levels(PositionSide.SHORT, D("100.005"), D("1"), D("0.01")) == (
        D("102.00"),
        D("96.01"),
    )


def test_near_floor_and_higher_funding_never_manufacture_affordability():
    from crypto_bot.risk.sizing import size_entry, adverse_funding_reserve

    case = risk_case()
    assert adverse_funding_reserve(replace(case.context.funding, interval_hours=D("4"))) == D(
        "0.0013"
    )
    account = replace(case.context.account, wallet_balance=D("18.01"), available_balance=D("18.01"))
    assert not isinstance(
        size_entry(replace(case, context=replace(case.context, account=account))), ApprovedSize
    )
    funding = replace(case.context.funding, current_rate=D("0.01"))
    assert not isinstance(
        size_entry(replace(case, context=replace(case.context, funding=funding))), ApprovedSize
    )
