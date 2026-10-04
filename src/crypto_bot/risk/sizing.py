from decimal import ROUND_CEILING, ROUND_FLOOR
from decimal import Decimal as D

from crypto_bot.domain.enums import PositionSide
from crypto_bot.domain.models import ApprovedSize, FundingContext, RejectedSize, RiskInput
from crypto_bot.risk.equity import mark_equity


def round_step(value: D, step: D, upward: bool = False) -> D:
    if not step.is_finite() or step <= 0 or not value.is_finite():
        raise ValueError("Invalid rounding input")
    return (value / step).to_integral_value(
        rounding=ROUND_CEILING if upward else ROUND_FLOOR
    ) * step


def protective_levels(side: PositionSide, entry: D, atr: D, tick: D) -> tuple[D, D]:
    if atr <= 0 or not atr.is_finite():
        raise ValueError("Invalid ATR")
    long = side is PositionSide.LONG
    stop = round_step(entry - 2 * atr if long else entry + 2 * atr, tick, upward=long)
    distance = abs(entry - stop)
    if distance == 0 or stop <= 0:
        raise ValueError("Invalid rounded stop")
    target = round_step(entry + 2 * distance if long else entry - 2 * distance, tick, upward=long)
    if target <= 0:
        raise ValueError("Invalid target")
    return stop, target


def adverse_funding_reserve(funding: FundingContext) -> D:
    if funding.interval_hours <= 0 or not funding.interval_hours.is_finite():
        raise ValueError("Unverified funding interval")
    settlements = (D("48") / funding.interval_hours).to_integral_value(rounding=ROUND_CEILING) + 1
    return max(abs(funding.current_rate), funding.seven_day_max_abs_rate, D("0.0001")) * settlements


def liquidation_buffer_ok(side: PositionSide, entry: D, stop: D, liquidation: D) -> bool:
    if liquidation <= 0:
        return False
    if side is PositionSide.LONG:
        return liquidation < stop < entry and stop >= liquidation + D("0.20") * (
            entry - liquidation
        )
    return entry < stop < liquidation and stop <= liquidation - D("0.20") * (liquidation - entry)


def size_entry(value: RiskInput) -> ApprovedSize | RejectedSize:
    context, policy = value.context, value.policy
    rules = context.rules
    entry = context.market.ask if value.signal.side is PositionSide.LONG else context.market.bid

    def reject(reason: str) -> RejectedSize:
        return RejectedSize(reason, reason.replace("_", " ").capitalize())

    if value.trial.halt_reason:
        return reject("TRIAL_HALTED")
    if entry <= 0 or not entry.is_finite() or not D("1") <= policy.leverage <= D("2"):
        return reject("INVALID_ENTRY_INPUT")
    try:
        stop, target = protective_levels(
            value.signal.side, entry, value.signal.atr, rules.tick_size
        )
    except ValueError:
        return reject("INVALID_STOP")
    if rules.maintenance_rate is None or rules.bracket_limit <= 0:
        return reject("MISSING_BRACKETS")
    if not D("0") <= rules.maintenance_rate < D("1") or rules.maintenance_deduction < 0:
        return reject("INVALID_BRACKETS")
    equity = mark_equity(context.account)
    budget = min(
        D("0.01") * equity,
        D("0.01") * value.trial.baseline,
        max(equity - value.trial.floor, D("0")),
    )
    if budget <= 0 or context.account.available_balance <= 0:
        return reject("NO_REMAINING_BUDGET")
    entry_fee = max(context.entry_fee_rate, policy.fee_floor, D("0.0006"))
    exit_fee = max(context.exit_fee_rate, policy.fee_floor, D("0.0006"))
    funding = adverse_funding_reserve(context.funding)
    costs = (
        entry_fee
        + exit_fee
        + policy.entry_slippage_reserve
        + policy.exit_slippage_reserve
        + funding
    )
    distance = abs(entry - stop) / entry
    maximum = min(
        budget / (distance + costs),
        equity,
        context.account.available_balance / (1 / policy.leverage + costs),
        rules.bracket_limit,
    )
    qty = min(
        round_step(maximum / entry, rules.step_size), round_step(rules.max_qty, rules.step_size)
    )
    notional = qty * entry
    if qty <= 0 or qty < rules.min_qty:
        return reject("BELOW_MIN_QUANTITY")
    if notional < rules.min_notional:
        return reject("BELOW_MIN_NOTIONAL")
    planned = notional * (distance + costs)
    if (
        planned > budget
        or notional / policy.leverage + notional * costs > context.account.available_balance
    ):
        return reject("BUDGET_EXCEEDED")
    long = value.signal.side is PositionSide.LONG
    limit = round_step(
        entry * (1 + policy.entry_slippage_reserve if long else 1 - policy.entry_slippage_reserve),
        rules.tick_size,
        upward=not long,
    )
    if (long and limit < entry) or (not long and limit > entry):
        return reject("UNMARKETABLE_IOC")
    if (
        not rules.min_price <= limit <= rules.max_price
        or not context.market.mark * rules.multiplier_down
        <= limit
        <= context.market.mark * rules.multiplier_up
    ):
        return reject("PRICE_BAND")
    margin = notional / policy.leverage - notional * entry_fee
    if long:
        liquidation = (notional - margin - rules.maintenance_deduction) / (
            qty * (1 - rules.maintenance_rate)
        )
    else:
        liquidation = (notional + margin + rules.maintenance_deduction) / (
            qty * (1 + rules.maintenance_rate)
        )
    if not liquidation_buffer_ok(value.signal.side, entry, stop, liquidation):
        return reject("LIQUIDATION_BUFFER")
    return ApprovedSize(
        qty,
        notional,
        planned,
        budget,
        {
            "entry": entry,
            "stop": stop,
            "target": target,
            "atr": value.signal.atr,
            "limit_price": limit,
            "cost_rate": costs,
            "entry_fee": entry_fee,
            "exit_fee": exit_fee,
            "funding_reserve": funding,
            "liquidation_estimate": liquidation,
            "rules": rules,
            "funding": context.funding,
            "account": context.account,
            "market": context.market,
        },
    )
