from crypto_bot.config import Settings
from crypto_bot.domain.enums import PositionSide
from crypto_bot.domain.models import EntryContext, Signal


def check_eligibility(
    signal: Signal, context: EntryContext, now_ms: int, settings: Settings | None = None
) -> tuple[str, ...]:
    policy = settings or Settings()
    market, rules, account, funding = (
        context.market,
        context.rules,
        context.account,
        context.funding,
    )
    reasons = []
    if not signal.close_ms < now_ms <= signal.close_ms + 90000:
        reasons.append("SIGNAL_EXPIRED")
    for label, timestamp, maximum in (
        ("QUOTE", market.freshness.get("quote", 0), 3000),
        ("HEARTBEAT", market.freshness.get("heartbeat", 0), 10000),
        ("ACCOUNT", account.observed_ms, 15000),
        ("FILTERS", rules.observed_ms, 21600000),
    ):
        if timestamp > now_ms or now_ms - timestamp > maximum:
            reasons.append(f"STALE_{label}")
    if signal.symbol != market.symbol or signal.symbol != rules.symbol:
        reasons.append("SYMBOL_MISMATCH")
    if (
        rules.status != "TRADING"
        or rules.contract_type != "PERPETUAL"
        or rules.settle_asset != "USDT"
        or rules.quote_asset != "USDT"
    ):
        reasons.append("UNSUPPORTED_CONTRACT")
    if market.quote_volume_24h < policy.min_quote_volume:
        reasons.append("LOW_LIQUIDITY")
    if market.bid <= 0 or market.ask < market.bid or market.mark <= 0 or signal.close_price <= 0:
        reasons.append("INVALID_QUOTE")
    else:
        mid = (market.bid + market.ask) / 2
        if (market.ask - market.bid) / mid * 10000 > policy.max_spread_bps:
            reasons.append("WIDE_SPREAD")
        if abs(market.mark - mid) / mid * 10000 > policy.max_mark_divergence_bps:
            reasons.append("MARK_DIVERGENCE")
        quote = market.ask if signal.side is PositionSide.LONG else market.bid
        if (
            abs(quote - signal.close_price) / signal.close_price * 10000
            > policy.max_signal_deviation_bps
        ):
            reasons.append("SIGNAL_PRICE_DEVIATION")
    if funding.next_event_ms <= now_ms:
        reasons.append("FUNDING_UPDATE_MISSED")
    verified = context.settings
    if (
        not verified.one_way
        or not verified.single_asset
        or not verified.isolated
        or verified.auto_margin
        or verified.bnb_fees
        or verified.leverage != policy.leverage
    ):
        reasons.append("UNSAFE_ACCOUNT_SETTINGS")
    return tuple(reasons)
