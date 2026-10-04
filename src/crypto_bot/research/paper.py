import json
from dataclasses import replace
from decimal import Decimal as D
from typing import Any

from crypto_bot.domain.clock import Clock
from crypto_bot.domain.enums import Mode, OrderSide, OrderState, PositionPhase, PositionSide
from crypto_bot.domain.models import (
    AccountSnapshot,
    BookEvent,
    ExchangeSnapshot,
    FillEvent,
    FundingEvent,
    IncomeEvent,
    MarketFrame,
    OrderIntent,
    OrderObservation,
    Position,
    SubmitAck,
    SubmitRejected,
    Trial,
)
from crypto_bot.research.simulation import funding_cashflow
from crypto_bot.storage.repository import Repository, decode_order, encode


def mark_interrupted(repo: Repository, run_id: str, reason: str, at_ms: int) -> None:
    current = repo.current_trial()
    if current and current.run_id == run_id and current.qualification_status == "INTERRUPTED":
        return
    with repo.db.transaction() as conn:
        conn.execute(
            "UPDATE runs SET qualification_status='INTERRUPTED' WHERE run_id=? AND mode='PAPER'",
            (run_id,),
        )
        conn.execute(
            "UPDATE run_state SET reconciled=0,state=CASE WHEN halt_reason IS NULL THEN 'PAUSED' ELSE state END WHERE run_id=?",
            (run_id,),
        )
        conn.execute(
            "INSERT INTO audit_events(run_id,at_ms,kind,payload) VALUES(?,?,'INTERRUPTED',?)",
            (run_id, at_ms, encode({"reason": reason})),
        )
    position = repo.position()
    if position:
        repo.save_position(replace(position, phase=PositionPhase.RECONCILING))


def new_paper_session(repo: Repository, baseline: D, hashes: dict[str, str], at_ms: int) -> Trial:
    current = repo.current_trial()
    if current and current.mode is not Mode.PAPER:
        raise ValueError("Only PAPER sessions can be reset")
    with repo.db.transaction() as conn:
        if current:
            conn.execute(
                "UPDATE runs SET active=0,end_ms=? WHERE run_id=?", (at_ms, current.run_id)
            )
            conn.execute("DELETE FROM active_slot")
    return repo.create_run(Mode.PAPER, baseline, hashes, at_ms)


class PaperBroker:
    def __init__(self, repo: Repository, clock: Clock, market_adapter: Any = None) -> None:
        self.repo, self.clock, self.market_adapter = repo, clock, market_adapter
        self.frames: dict[str, MarketFrame] = {}
        self.marks: dict[str, tuple[int, D]] = {}

    def capture(self, event: BookEvent) -> None:
        self.frames[event.frame.symbol] = event.frame

    def wallet(self) -> D:
        trial = self.repo.current_trial()
        assert trial is not None
        fills = self.repo.db.connection.execute(
            "SELECT commission FROM fills WHERE run_id=?", (trial.run_id,)
        )
        income = self.repo.db.connection.execute(
            "SELECT amount FROM income_events WHERE run_id=?", (trial.run_id,)
        )
        return (
            trial.baseline
            - sum((D(row[0]) for row in fills), D("0"))
            + sum((D(row[0]) for row in income), D("0"))
        )

    async def fetch_snapshot(self) -> ExchangeSnapshot:
        wallet = self.wallet()
        position = self.repo.position()
        pnl, margin = D("0"), D("0")
        if position:
            mark = self.marks.get(position.symbol, (self.clock.now_ms(), position.average_entry))[1]
            pnl = (
                (mark - position.average_entry)
                * position.quantity
                * (1 if position.side is PositionSide.LONG else -1)
            )
            margin = position.quantity * position.average_entry / 2
        trial = self.repo.current_trial()
        assert trial is not None
        rows = self.repo.db.connection.execute(
            "SELECT o.payload FROM orders o JOIN order_intents i USING(client_id) WHERE i.run_id=?",
            (trial.run_id,),
        )
        orders = tuple(decode_order(row[0]) for row in rows)
        return ExchangeSnapshot(
            self.clock.now_ms(),
            AccountSnapshot(self.clock.now_ms(), wallet, pnl, max(D("0"), wallet - margin)),
            (position,) if position else (),
            tuple(o for o in orders if o.namespace == "ordinary"),
            tuple(o for o in orders if o.namespace == "algo"),
            self.repo.fills(),
            self.repo.income_events(),
        )

    async def fetch_rules(self, symbol: str) -> Any:
        if self.market_adapter is None:
            raise ValueError("Fresh public rules required")
        return await self.market_adapter.fetch_rules(symbol)

    async def submit_entry(self, intent: OrderIntent) -> SubmitAck | SubmitRejected:
        frame = self.frames.get(intent.symbol)
        if (
            frame is None
            or self.clock.now_ms() - frame.observed_ms > 3000
            or self.repo.position() is not None
        ):
            return SubmitRejected("STALE_BOOK_OR_CAPACITY")
        assert intent.quantity is not None and intent.limit_price is not None
        long = intent.side is OrderSide.BUY
        levels = frame.asks if long else frame.bids
        remaining, filled, value = intent.quantity, D("0"), D("0")
        venue_id = intent.client_id
        for index, level in enumerate(levels):
            price = level.price * (1 + D("0.0002") if long else 1 - D("0.0002"))
            if (long and price > intent.limit_price) or (not long and price < intent.limit_price):
                break
            quantity = min(remaining, level.quantity)
            if quantity <= 0:
                continue
            fee = quantity * price * D("0.0006")
            self.repo.record_execution(
                FillEvent(
                    "PAPER",
                    "virtual",
                    intent.symbol,
                    f"{venue_id}:{index}",
                    venue_id,
                    price,
                    quantity,
                    fee,
                    "USDT",
                    self.clock.now_ms(),
                    intent.side,
                )
            )
            filled += quantity
            value += quantity * price
            remaining -= quantity
            if remaining == 0:
                break
        state = OrderState.FILLED if remaining == 0 else OrderState.EXPIRED
        average = value / filled if filled else D("0")
        first = self.clock.now_ms() if filled else None
        observation = OrderObservation(
            intent.client_id,
            venue_id,
            "ordinary",
            intent.symbol,
            state,
            filled,
            average,
            first,
            self.clock.now_ms(),
        )
        self.repo.record_order(observation)
        if filled:
            evidence = json.loads(
                self.repo.db.connection.execute(
                    "SELECT evidence FROM order_intents WHERE logical_id=?", (intent.logical_id,)
                ).fetchone()[0]
            )["evidence"]
            liquidation = None
            if evidence.get("rules", {}).get("maintenance_rate") is not None:
                mmr = D(evidence["rules"]["maintenance_rate"])
                deduction = D(evidence["rules"]["maintenance_deduction"])
                leverage = self.market_adapter.settings.leverage if self.market_adapter else D("2")
                margin = filled * average / leverage - filled * average * D(evidence["entry_fee"])
                liquidation = (
                    (filled * average - margin - deduction) / (filled * (1 - mmr))
                    if long
                    else (filled * average + margin + deduction) / (filled * (1 + mmr))
                )
            self.repo.save_position(
                Position(
                    intent.symbol,
                    PositionSide.LONG if long else PositionSide.SHORT,
                    filled,
                    average,
                    D(evidence["atr"]),
                    self.clock.now_ms(),
                    liquidation_price=liquidation,
                    intent_id=intent.logical_id,
                )
            )
        return SubmitAck(observation)

    async def find_order(self, intent: OrderIntent) -> OrderObservation | None:
        return self.repo.order(intent.client_id)

    async def submit_protection(self, intent: OrderIntent) -> SubmitAck:
        value = OrderObservation(
            intent.client_id,
            intent.client_id,
            "algo",
            intent.symbol,
            OrderState.ACKNOWLEDGED,
            observed_ms=self.clock.now_ms(),
        )
        self.repo.record_order(value)
        return SubmitAck(value)

    async def cancel_order(self, intent: OrderIntent) -> SubmitAck | SubmitRejected:
        order = self.repo.order(intent.client_id)
        if order is None:
            return SubmitRejected("VIRTUAL_ORDER_UNKNOWN")
        order = replace(order, state=order.state if order.state.terminal else OrderState.CANCELED)
        self.repo.record_order(order)
        return SubmitAck(order)

    async def reduce_position(
        self, symbol: str, side: PositionSide, quantity: D, client_id: str
    ) -> SubmitAck | SubmitRejected:
        trial = self.repo.current_trial()
        if trial and trial.qualification_status == "INTERRUPTED":
            return SubmitRejected("INTERRUPTED_VIRTUAL_EXPOSURE_REQUIRES_REVIEW")
        position = self.repo.position()
        if position is None or position.symbol != symbol or position.side is not side:
            return SubmitRejected("VIRTUAL_POSITION_UNKNOWN")
        frame = self.frames.get(symbol)
        if frame is None or self.clock.now_ms() - frame.observed_ms > 3000:
            return SubmitRejected("STALE_EXIT_BOOK")
        levels = frame.bids if side is PositionSide.LONG else frame.asks
        remaining, value, filled = min(quantity, position.quantity), D("0"), D("0")
        for index, level in enumerate(levels):
            qty = min(remaining, level.quantity)
            if qty <= 0:
                continue
            price = level.price * (
                1 - D("0.0005") if side is PositionSide.LONG else 1 + D("0.0005")
            )
            self.repo.record_execution(
                FillEvent(
                    "PAPER",
                    "virtual",
                    symbol,
                    f"{client_id}:{index}",
                    client_id,
                    price,
                    qty,
                    qty * price * D("0.0006"),
                    "USDT",
                    self.clock.now_ms(),
                    OrderSide.SELL if side is PositionSide.LONG else OrderSide.BUY,
                )
            )
            gross = (
                (price - position.average_entry) * qty * (1 if side is PositionSide.LONG else -1)
            )
            self.repo.record_execution(
                IncomeEvent(
                    "PAPER",
                    "virtual",
                    symbol,
                    f"{client_id}:pnl:{index}",
                    "REALIZED_PNL",
                    gross,
                    "USDT",
                    self.clock.now_ms(),
                )
            )
            remaining -= qty
            value += qty * price
            filled += qty
            if remaining == 0:
                break
        if not filled:
            return SubmitRejected("NO_EXIT_LIQUIDITY")
        residual = position.quantity - filled
        self.repo.save_position(replace(position, quantity=residual) if residual else None)
        observation = OrderObservation(
            client_id,
            client_id,
            "ordinary",
            symbol,
            OrderState.FILLED,
            filled,
            value / filled,
            self.clock.now_ms(),
            self.clock.now_ms(),
        )
        self.repo.record_order(observation)
        return SubmitAck(observation)

    def book_funding(self, event: FundingEvent) -> None:
        position = self.repo.position()
        trial = self.repo.current_trial()
        if (
            position is not None
            and position.symbol == event.symbol
            and trial is not None
            and trial.qualification_status != "INTERRUPTED"
        ):
            amount = funding_cashflow(position.side, position.quantity, event.mark, event.rate)
            self.repo.record_execution(
                IncomeEvent(
                    "PAPER",
                    "virtual",
                    event.symbol,
                    event.transaction_id,
                    "FUNDING_FEE",
                    amount,
                    "USDT",
                    event.at_ms,
                )
            )
