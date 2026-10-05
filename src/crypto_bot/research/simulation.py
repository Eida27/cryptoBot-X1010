from dataclasses import dataclass
from decimal import Decimal as D
from typing import Any

from crypto_bot.config import STRATEGY_HASH, Settings
from crypto_bot.domain.enums import Mode, OrderSide, OrderState, PositionSide
from crypto_bot.domain.models import (
    AccountSnapshot,
    ApprovedSize,
    BookLevel,
    Candle,
    CandleEvent,
    EntryContext,
    ExecutionEvent,
    FillEvent,
    FundingContext,
    FundingEvent,
    IncomeEvent,
    IndicatorState,
    MarketEvent,
    MarketFrame,
    OrderIntent,
    OrderObservation,
    Position,
    RiskInput,
    RiskPolicy,
    Signal,
    SubmitAck,
    SubmitRejected,
    SymbolRules,
    Trial,
    VerifiedSettings,
)
from crypto_bot.exchange.normalization import normalize_rules
from crypto_bot.market.indicators import update_indicators
from crypto_bot.market.validation import funding_schedule_intervals
from crypto_bot.research.datasets import (
    DatasetManifest,
    iter_candles,
    iter_funding,
    validate_dataset,
)
from crypto_bot.risk.eligibility import check_eligibility
from crypto_bot.risk.sizing import liquidation_buffer_ok, protective_levels, size_entry
from crypto_bot.storage.repository import digest, make_intent
from crypto_bot.strategy.breakout import evaluate_signal


def funding_cashflow(side: PositionSide, quantity: D, mark: D, rate: D) -> D:
    return quantity * mark * rate * (-1 if side is PositionSide.LONG else 1)


@dataclass(frozen=True)
class ClosedTrade:
    position: Position
    exit_ms: int
    exit_price: D
    gross_pnl: D
    fees: D
    funding: D
    net_pnl: D
    reason: str


@dataclass(frozen=True)
class RunResult:
    run_id: str
    mode: Mode
    baseline: D
    start_ms: int
    end_ms: int
    hashes: dict[str, str]
    trades: tuple[ClosedTrade, ...]
    equity: tuple[tuple[int, D], ...]
    fills: tuple[FillEvent, ...]
    funding: tuple[IncomeEvent, ...]
    vetoes: tuple[tuple[int, str, str], ...]
    interruptions: tuple[str, ...]
    failed_assumptions: tuple[str, ...]
    stress: bool = False
    split_boundaries: tuple[int, int, int, int] | None = None


class SimulationBroker:
    def __init__(self, baseline: D, stress: bool = False) -> None:
        self.baseline = self.wallet = baseline
        self.floor = baseline * D("0.9")
        self.position: Position | None = None
        self.pending: tuple[OrderIntent, int, D] | None = None
        self.current_price = D("0")
        self.now_ms = 0
        multiplier = 2 if stress else 1
        self.spread = D("0.0005") * multiplier
        self.entry_slippage = D("0.0002") * multiplier
        self.exit_slippage = D("0.0005") * multiplier
        self.fee_rate = D("0.0006") * multiplier
        self.fills: list[FillEvent] = []
        self.income: list[IncomeEvent] = []
        self.trades: list[ClosedTrade] = []
        self.equity: list[tuple[int, D]] = []
        self.failed_assumptions: list[str] = []
        self.halt_reason: str | None = None
        self.entry_fee = self.position_funding = D("0")
        self.funding_ids: set[str] = set()
        self.next_atr = D("1")
        self.rules: SymbolRules | None = None
        self.last_marks: dict[str, D] = {}
        self.leverage = D("2")
        self.approved: ApprovedSize | None = None

    def prepare_entry(
        self,
        intent: OrderIntent,
        signal_close_ms: int,
        atr: D,
        approved: ApprovedSize | None = None,
    ) -> None:
        if self.position is None and self.pending is None and self.halt_reason is None:
            self.pending = intent, signal_close_ms, atr
            self.approved = approved

    def submit(self, intent: OrderIntent) -> SubmitAck | SubmitRejected:
        if (
            self.position
            or self.halt_reason
            or intent.quantity is None
            or intent.limit_price is None
        ):
            return SubmitRejected("CAPACITY_OR_INVALID_INTENT")
        long = intent.side is OrderSide.BUY
        price = self.current_price * (
            1 + self.spread / 2 + self.entry_slippage
            if long
            else 1 - self.spread / 2 - self.entry_slippage
        )
        if (
            price <= 0
            or (long and price > intent.limit_price)
            or (not long and price < intent.limit_price)
        ):
            return SubmitRejected("IOC_LIMIT_UNFILLABLE")
        if self.rules and (
            intent.quantity % self.rules.step_size
            or intent.quantity < self.rules.min_qty
            or intent.quantity > self.rules.max_qty
            or intent.quantity * price < self.rules.min_notional
        ):
            return SubmitRejected("FILTER_REJECTED")
        side = PositionSide.LONG if long else PositionSide.SHORT
        tick = self.rules.tick_size if self.rules else D("0.01")
        stop, target = protective_levels(side, price, self.next_atr, tick)
        self.entry_fee = price * intent.quantity * self.fee_rate
        self.wallet -= self.entry_fee
        self.position_funding = D("0")
        liquidation = None
        if self.rules and self.rules.maintenance_rate is not None:
            notional = price * intent.quantity
            margin = notional / self.leverage - self.entry_fee
            liquidation = (
                (notional - margin - self.rules.maintenance_deduction)
                / (intent.quantity * (1 - self.rules.maintenance_rate))
                if long
                else (notional + margin + self.rules.maintenance_deduction)
                / (intent.quantity * (1 + self.rules.maintenance_rate))
            )
        self.position = Position(
            intent.symbol,
            side,
            intent.quantity,
            price,
            self.next_atr,
            self.now_ms,
            stop,
            target,
            liquidation,
            intent_id=intent.logical_id,
        )
        fill = FillEvent(
            "BACKTEST",
            "virtual",
            intent.symbol,
            str(len(self.fills)),
            intent.client_id,
            price,
            intent.quantity,
            self.entry_fee,
            "USDT",
            self.now_ms,
            intent.side,
        )
        self.fills.append(fill)
        failure = None
        if liquidation is not None and not liquidation_buffer_ok(side, price, stop, liquidation):
            failure = "LIQUIDATION_BUFFER"
        if self.approved:
            planned = intent.quantity * (
                abs(price - stop) + price * D(self.approved.evidence["cost_rate"])
            )
            if planned > self.approved.budget:
                failure = "POST_FILL_BUDGET"
        if failure:
            self.failed_assumptions.append(failure)
            self.close(price, failure)
        return SubmitAck(
            OrderObservation(
                intent.client_id,
                intent.client_id,
                "ordinary",
                intent.symbol,
                OrderState.FILLED,
                intent.quantity,
                price,
                self.now_ms,
                self.now_ms,
            )
        )

    def close(self, reference: D, reason: str) -> None:
        position = self.position
        if position is None:
            return
        long = position.side is PositionSide.LONG
        price = reference * (
            1 - self.spread / 2 - self.exit_slippage
            if long
            else 1 + self.spread / 2 + self.exit_slippage
        )
        gross = (price - position.average_entry) * position.quantity * (1 if long else -1)
        fee = price * position.quantity * self.fee_rate
        self.wallet += gross - fee
        fees = fee + self.entry_fee
        self.trades.append(
            ClosedTrade(
                position,
                self.now_ms,
                price,
                gross,
                fees,
                self.position_funding,
                gross - fees + self.position_funding,
                reason,
            )
        )
        self.fills.append(
            FillEvent(
                "BACKTEST",
                "virtual",
                position.symbol,
                str(len(self.fills)),
                f"exit-{len(self.trades)}",
                price,
                position.quantity,
                fee,
                "USDT",
                self.now_ms,
                OrderSide.SELL if long else OrderSide.BUY,
            )
        )
        self.position = None

    def apply(self, event: MarketEvent) -> tuple[ExecutionEvent, ...]:
        if isinstance(event, FundingEvent) and event.transaction_id not in self.funding_ids:
            self.funding_ids.add(event.transaction_id)
            if self.position is not None and self.position.symbol == event.symbol:
                amount = funding_cashflow(
                    self.position.side, self.position.quantity, event.mark, event.rate
                )
                self.wallet += amount
                self.position_funding += amount
                income = IncomeEvent(
                    "BACKTEST",
                    "virtual",
                    event.symbol,
                    event.transaction_id,
                    "FUNDING_FEE",
                    amount,
                    "USDT",
                    event.at_ms,
                )
                self.income.append(income)
                return (income,)
        elif isinstance(event, CandleEvent):
            self.advance(event.candle, event.candle)
        return ()

    def check_position(self, trade: Candle, mark: Candle) -> None:
        position = self.position
        if position is not None and trade.symbol == position.symbol:
            long = position.side is PositionSide.LONG
            adverse = mark.low if long else mark.high
            pnl = (adverse - position.average_entry) * position.quantity * (1 if long else -1)
            allowance = adverse * position.quantity * (self.fee_rate + D("0.0010"))
            liquidated = position.liquidation_price is not None and (
                (
                    long
                    and (
                        mark.open <= position.liquidation_price
                        or (
                            position.stop is not None
                            and position.liquidation_price >= position.stop
                            and mark.low <= position.liquidation_price
                        )
                    )
                )
                or (
                    not long
                    and (
                        mark.open >= position.liquidation_price
                        or (
                            position.stop is not None
                            and position.liquidation_price <= position.stop
                            and mark.high >= position.liquidation_price
                        )
                    )
                )
            )
            if liquidated:
                self.failed_assumptions.append("LIQUIDATION")
                self.halt_reason = "LIQUIDATION"
                self.close(adverse, "LIQUIDATION_UNMODELED_FEE")
            elif self.wallet + pnl - allowance <= self.floor:
                self.halt_reason = "TRIAL_LOSS"
                self.close(trade.low if long else trade.high, "TRIAL_LOSS")
            elif position.stop is not None and (
                (long and mark.low <= position.stop) or (not long and mark.high >= position.stop)
            ):
                self.close(
                    min(position.stop, trade.open) if long else max(position.stop, trade.open),
                    "STOP",
                )
            elif position.target is not None and (
                (long and mark.high >= position.target)
                or (not long and mark.low <= position.target)
            ):
                self.close(
                    min(position.target, trade.open) if long else max(position.target, trade.open),
                    "TARGET",
                )
            elif trade.open_ms - position.first_fill_ms >= 48 * 3600000:
                self.close(trade.open, "TIME_LIMIT")

    def advance(self, trade: Candle, mark: Candle) -> None:
        self.last_marks[mark.symbol] = mark.close
        self.now_ms, self.current_price = trade.open_ms, trade.open
        self.check_position(trade, mark)
        if self.pending and self.position is None:
            intent, close_ms, atr = self.pending
            if trade.symbol == intent.symbol and trade.open_ms > close_ms:
                self.pending = None
                if trade.open_ms <= close_ms + 90000:
                    self.next_atr = atr
                    self.submit(intent)
                    self.check_position(trade, mark)
        equity = self.wallet
        if self.position:
            equity += (
                (
                    self.last_marks.get(self.position.symbol, self.position.average_entry)
                    - self.position.average_entry
                )
                * self.position.quantity
                * (1 if self.position.side is PositionSide.LONG else -1)
            )
        self.equity.append((trade.close_ms, equity))


def run_backtest(manifest: DatasetManifest, settings: Settings, stress: bool = False) -> RunResult:
    quality = validate_dataset(manifest)
    if not quality.valid:
        raise ValueError("Invalid dataset: " + "; ".join(quality.issues))
    broker = SimulationBroker(settings.initial_capital_usdt, stress)
    observed_fee = max(
        (D(str(raw.get("takerFeeRate", "0.0006"))) for raw in manifest.filters.values()),
        default=D("0.0006"),
    )
    broker.fee_rate = max(settings.fee_floor, D("0.0006"), observed_fee) * (2 if stress else 1)
    broker.leverage = settings.leverage
    from crypto_bot.research.gates import code_hash

    hashes = {
        "data": manifest.content_hash,
        "config": settings.config_hash,
        "strategy": STRATEGY_HASH,
        "code": code_hash(),
    }
    run_id = digest([hashes, stress, manifest.split_boundaries])
    trial = Trial(
        run_id, Mode.BACKTEST, broker.baseline, broker.floor, D("1000") / broker.baseline, hashes
    )
    vetoes: list[tuple[int, str, str]] = []
    # Merge iterators across symbols; fixed present-day filters are explicitly an assumption.
    import heapq

    def pairs(symbol: str) -> Any:
        return zip(
            iter_candles(manifest, symbol, "trade"),
            iter_candles(manifest, symbol, "mark"),
            strict=True,
        )

    minutes = heapq.merge(
        *(pairs(s) for s in manifest.request.symbols),
        key=lambda pair: (pair[0].open_ms, pair[0].symbol),
    )
    states = {s: IndicatorState() for s in manifest.request.symbols}
    hourly: dict[str, list[Candle]] = {s: [] for s in manifest.request.symbols}
    buckets: dict[str, list[Candle]] = {s: [] for s in manifest.request.symbols}
    funding_by_symbol = {s: list(iter_funding(manifest, s)) for s in manifest.request.symbols}
    funding_index = {s: 0 for s in manifest.request.symbols}
    from itertools import groupby

    for _, batch in groupby(minutes, key=lambda pair: pair[0].close_ms):
        candidates: list[tuple[Signal, EntryContext, ApprovedSize]] = []
        for trade, mark in batch:
            symbol = trade.symbol
            events = funding_by_symbol[symbol]
            while (
                funding_index[symbol] < len(events)
                and events[funding_index[symbol]].at_ms <= trade.open_ms
            ):
                broker.apply(events[funding_index[symbol]])
                funding_index[symbol] += 1
            broker.advance(trade, mark)
            bucket = buckets[symbol]
            if bucket and trade.open_ms // 3600000 != bucket[0].open_ms // 3600000:
                bucket.clear()
            bucket.append(trade)
            if len(bucket) != 60 or trade.close_ms % 3600000:
                continue
            hour = Candle(
                symbol,
                bucket[0].open_ms,
                trade.close_ms,
                bucket[0].open,
                max(c.high for c in bucket),
                min(c.low for c in bucket),
                trade.close,
                sum((c.volume for c in bucket), D("0")),
                sum((c.quote_volume for c in bucket), D("0")),
            )
            bucket.clear()
            state = states[symbol] = update_indicators(states[symbol], hour)
            history = hourly[symbol]
            history.append(hour)
            if len(history) > 1000:
                history.pop(0)
            signal = evaluate_signal(history[-21:], state, STRATEGY_HASH)
            if (
                signal is None
                or hour.close_ms < manifest.request.start_ms
                or broker.position
                or broker.pending
                or broker.halt_reason
            ):
                continue
            raw_rules = manifest.filters.get(symbol)
            if not raw_rules:
                vetoes.append((signal.close_ms, symbol, "MISSING_FILTER_SNAPSHOT"))
                continue
            # Historical maintenance inputs must be supplied; never invented to enable fills.
            rules = normalize_rules(raw_rules, signal.close_ms, raw_rules.get("maintenanceBracket"))
            past = [
                event
                for event in events
                if signal.close_ms - 7 * 86400000 <= event.at_ms <= signal.close_ms
            ]
            if len(past) < 3:
                vetoes.append((signal.close_ms, symbol, "MISSING_FUNDING_HISTORY"))
                continue
            interval = D(funding_schedule_intervals(event.at_ms for event in past[-2:])[0]) / D(
                "3600000"
            )
            funding = FundingContext(
                signal.close_ms,
                past[-1].at_ms + int(interval * 3600000),
                interval,
                past[-1].rate,
                max(abs(event.rate) for event in past),
            )
            bid, ask = hour.close * (1 - broker.spread / 2), hour.close * (1 + broker.spread / 2)
            volume = sum((c.quote_volume for c in history[-24:]), D("0"))
            market = MarketFrame(
                symbol,
                signal.close_ms,
                bid,
                ask,
                mark.close,
                (BookLevel(bid, D("1E9")),),
                (BookLevel(ask, D("1E9")),),
                volume,
                {"quote": signal.close_ms, "heartbeat": signal.close_ms},
            )
            ctx = EntryContext(
                AccountSnapshot(signal.close_ms, broker.wallet, D("0"), broker.wallet),
                market,
                rules,
                funding,
                VerifiedSettings(leverage=settings.leverage),
                broker.fee_rate,
                broker.fee_rate,
            )
            reasons = check_eligibility(signal, ctx, signal.close_ms + 1, settings)
            policy = RiskPolicy(
                settings.leverage,
                settings.entry_slippage_reserve * (2 if stress else 1),
                settings.exit_slippage_reserve * (2 if stress else 1),
                broker.fee_rate,
            )
            size = size_entry(RiskInput(trial, signal, ctx, policy))
            if reasons or not isinstance(size, ApprovedSize):
                reason = (
                    reasons[0]
                    if reasons
                    else size.reason
                    if not isinstance(size, ApprovedSize)
                    else "UNKNOWN"
                )
                vetoes.append((signal.close_ms, symbol, reason))
                continue
            candidates.append((signal, ctx, size))
        for signal, ctx, size in sorted(
            candidates, key=lambda v: (-v[1].market.quote_volume_24h, v[0].symbol)
        ):
            broker.rules = ctx.rules
            intent = make_intent(
                run_id,
                signal.identity,
                "ENTRY",
                0,
                signal.symbol,
                OrderSide.BUY if signal.side is PositionSide.LONG else OrderSide.SELL,
                size.quantity,
                D(size.evidence["limit_price"]),
            )
            broker.prepare_entry(intent, signal.close_ms, signal.atr, size)
            break
    if any(state.count < 1000 for state in states.values()):
        broker.failed_assumptions.append("INSUFFICIENT_WARMUP")
    if broker.position or broker.pending:
        broker.failed_assumptions.append("UNRESOLVED_AT_DATA_END")
    if broker.halt_reason:
        broker.failed_assumptions.append(broker.halt_reason)
    return RunResult(
        run_id,
        Mode.BACKTEST,
        broker.baseline,
        manifest.request.start_ms,
        manifest.request.end_ms,
        hashes,
        tuple(broker.trades),
        tuple(broker.equity),
        tuple(broker.fills),
        tuple(broker.income),
        tuple(vetoes),
        (),
        tuple(broker.failed_assumptions),
        stress,
        manifest.split_boundaries,
    )
