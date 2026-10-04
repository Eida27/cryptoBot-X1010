from dataclasses import replace
from decimal import Decimal as D

import pytest

from tests.fixtures.factories import context, signal


@pytest.mark.parametrize(
    "age,key,code",
    [
        (3001, "quote", "STALE_QUOTE"),
        (10001, "heartbeat", "STALE_HEARTBEAT"),
        (15001, "account", "STALE_ACCOUNT"),
        (21600001, "filters", "STALE_FILTERS"),
    ],
)
def test_stale_sources_block_entry(age, key, code):
    from crypto_bot.risk.eligibility import check_eligibility

    now = 3600001
    ctx = context(now)
    if key == "account":
        ctx = replace(ctx, account=replace(ctx.account, observed_ms=now - age))
    elif key == "filters":
        ctx = replace(ctx, rules=replace(ctx.rules, observed_ms=now - age))
    else:
        ctx = replace(
            ctx, market=replace(ctx.market, freshness={**ctx.market.freshness, key: now - age})
        )
    assert code in check_eligibility(signal(), ctx, now)


def test_expired_signal_and_missed_funding_update_are_vetoes():
    from crypto_bot.risk.eligibility import check_eligibility

    ctx = context(3690001)
    ctx = replace(ctx, funding=replace(ctx.funding, next_event_ms=3600000))
    reasons = check_eligibility(signal(), ctx, 3690001)
    assert "SIGNAL_EXPIRED" in reasons
    assert "FUNDING_UPDATE_MISSED" in reasons


def test_liquidity_and_account_settings_are_required():
    from crypto_bot.risk.eligibility import check_eligibility

    ctx = context()
    assert check_eligibility(signal(), ctx, 3600001) == ()
    bad = replace(
        ctx,
        market=replace(ctx.market, ask=D("102"), quote_volume_24h=D("10")),
        settings=replace(ctx.settings, auto_margin=True),
    )
    reasons = check_eligibility(signal(), bad, 3600001)
    assert {"LOW_LIQUIDITY", "WIDE_SPREAD", "UNSAFE_ACCOUNT_SETTINGS"}.issubset(reasons)
