from dataclasses import replace
from decimal import Decimal as D

from crypto_bot.domain.enums import OrderSide, PositionSide
from crypto_bot.domain.models import Candle, FundingEvent, Position, SubmitRejected
from crypto_bot.storage.repository import make_intent


def minute(at_ms, low="99", high="101", opened="100"):
    return Candle("SOLUSDT", at_ms, at_ms + 60000, D(opened), D(high), D(low), D(opened))


def test_funding_direction():
    from crypto_bot.research.simulation import funding_cashflow

    assert funding_cashflow(PositionSide.LONG, D("2"), D("100"), D("0.001")) == D("-0.2")
    assert funding_cashflow(PositionSide.SHORT, D("2"), D("100"), D("0.001")) == D("0.2")


def test_entry_waits_for_next_minute_strictly_after_close_and_expires():
    from crypto_bot.research.simulation import SimulationBroker

    broker = SimulationBroker(D("20"))
    intent = make_intent(
        "r", "signal", "ENTRY", 0, "SOLUSDT", OrderSide.BUY, D("0.08"), D("100.05")
    )
    broker.prepare_entry(intent, signal_close_ms=36000000, atr=D("1"))
    broker.advance(minute(36000000), minute(36000000))
    assert broker.position is None
    broker.advance(minute(36060000), minute(36060000))
    assert broker.position.first_fill_ms == 36060000
    late = SimulationBroker(D("20"))
    late.prepare_entry(intent, 36000000, D("1"))
    late.advance(minute(36120000), minute(36120000))
    assert late.position is None


def test_ioc_outside_limit_is_rejected_instead_of_clipped():
    from crypto_bot.research.simulation import SimulationBroker

    broker = SimulationBroker(D("20"))
    broker.current_price = D("101")
    intent = make_intent("r", "s", "ENTRY", 0, "SOLUSDT", OrderSide.BUY, D("0.08"), D("100.05"))
    assert isinstance(broker.submit(intent), SubmitRejected)
    assert broker.position is None


def test_gap_stop_and_ambiguous_touch_choose_adverse_exit():
    from crypto_bot.research.simulation import SimulationBroker

    broker = SimulationBroker(D("20"))
    broker.position = Position(
        "SOLUSDT", PositionSide.LONG, D("0.08"), D("100"), D("1"), 0, D("98"), D("104"), D("50")
    )
    broker.advance(
        minute(60000, low="96", high="105", opened="97"),
        minute(60000, low="96", high="105", opened="97"),
    )
    assert broker.trades[0].exit_price < D("97")
    assert broker.trades[0].reason == "STOP"


def test_intraminute_floor_and_liquidation_are_recorded_as_failures():
    from crypto_bot.research.simulation import SimulationBroker

    broker = SimulationBroker(D("20"))
    broker.position = Position(
        "SOLUSDT", PositionSide.LONG, D("1"), D("100"), D("1"), 0, D("98"), D("104"), D("50")
    )
    broker.advance(minute(60000, low="97", high="101"), minute(60000, low="97", high="101"))
    assert broker.halt_reason == "TRIAL_LOSS"
    liquidated = SimulationBroker(D("20"))
    liquidated.position = replace(broker.trades[0].position, liquidation_price=D("99"))
    liquidated.advance(minute(60000, low="97", high="101"), minute(60000, low="97", high="101"))
    assert "LIQUIDATION" in liquidated.failed_assumptions


def test_funding_boundary_charges_existing_position_before_exit():
    from crypto_bot.research.simulation import SimulationBroker

    broker = SimulationBroker(D("20"))
    broker.position = Position(
        "SOLUSDT", PositionSide.LONG, D("1"), D("100"), D("1"), 0, D("98"), D("104"), D("50")
    )
    event = FundingEvent("SOLUSDT", 60000, D("0.001"), D("100"), "f1")
    broker.apply(event)
    broker.apply(event)
    assert broker.wallet == D("19.9")


def test_entry_minute_protection_is_evaluated_without_waiting_one_bar():
    from crypto_bot.research.simulation import SimulationBroker

    broker = SimulationBroker(D("20"))
    intent = make_intent("r", "s", "ENTRY", 0, "SOLUSDT", OrderSide.BUY, D("0.08"), D("100.05"))
    broker.prepare_entry(intent, 3600000, D("1"))
    broker.advance(minute(3660000, low="97"), minute(3660000, low="97"))
    assert broker.position is None
    assert broker.trades[0].reason == "STOP"


def test_simulated_fill_uses_verified_maintenance_bracket_for_liquidation():
    from crypto_bot.research.simulation import SimulationBroker
    from tests.fixtures.factories import context

    broker = SimulationBroker(D("20"))
    broker.rules = context().rules
    broker.current_price = D("100")
    intent = make_intent("r", "s", "ENTRY", 0, "SOLUSDT", OrderSide.BUY, D("0.08"), D("100.05"))
    broker.submit(intent)
    position = broker.position
    notional = position.average_entry * position.quantity
    expected = (notional / 2 + broker.entry_fee) / (position.quantity * (1 - D("0.004")))
    assert position.liquidation_price == expected


def test_replay_flattens_when_actual_fill_exceeds_frozen_budget():
    from crypto_bot.domain.models import ApprovedSize
    from crypto_bot.research.simulation import SimulationBroker
    from tests.fixtures.factories import context

    broker = SimulationBroker(D("20"))
    broker.rules = context().rules
    intent = make_intent("r", "s", "ENTRY", 0, "SOLUSDT", OrderSide.BUY, D("0.08"), D("100.05"))
    approved = ApprovedSize(D("0.08"), D("8"), D("0.18"), D("0.15"), {"cost_rate": D("0.0034")})
    broker.prepare_entry(intent, 3600000, D("1"), approved)
    broker.advance(minute(3660000), minute(3660000))
    assert broker.position is None
    assert broker.trades[0].reason == "POST_FILL_BUDGET"
    assert "POST_FILL_BUDGET" in broker.failed_assumptions
