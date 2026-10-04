from dataclasses import replace
from decimal import Decimal as D

from crypto_bot.domain.enums import OrderSide, OrderState, PositionSide
from crypto_bot.domain.models import (
    AccountSnapshot,
    ExchangeSnapshot,
    FillEvent,
    OrderObservation,
    Position,
    SubmitAck,
    SubmitRejected,
    SubmitUnknown,
)
from tests.fixtures.factories import context


class FakeExchange:
    def __init__(self, clock):
        self.clock = clock
        self.orders = {}
        self.position = None
        self.wallet = D("20")
        self.calls = []
        self.fills = []
        self.income = []
        self.entry_mode = "filled"
        self.partial_quantity = D("0.04")
        self.hidden = False
        self.reject_protection = False
        self.reject_cancel = False
        self.reject_exit = False
        self.exit_hook = None

    @property
    def entry_submission_count(self):
        return sum(call[0] == "entry" for call in self.calls)

    def accept_then_timeout(self, partial=False):
        self.entry_mode = "partial_unknown" if partial else "unknown"
        self.hidden = True

    async def fetch_snapshot(self):
        ordinary = tuple(o for o in self.orders.values() if o.namespace == "ordinary")
        if self.hidden and self.entry_mode != "partial_unknown":
            ordinary = ()
        algo = tuple(o for o in self.orders.values() if o.namespace == "algo")
        return ExchangeSnapshot(
            self.clock.now_ms(),
            AccountSnapshot(self.clock.now_ms(), self.wallet, D("0"), self.wallet),
            () if self.position is None else (self.position,),
            ordinary,
            algo,
            tuple(self.fills),
            tuple(self.income),
        )

    async def fetch_rules(self, symbol):
        return context(self.clock.now_ms()).rules

    async def submit_entry(self, intent):
        self.calls.append(("entry", intent))
        if self.entry_mode == "rejected":
            return SubmitRejected("FILTER_REJECTED")
        qty = self.partial_quantity if "partial" in self.entry_mode else intent.quantity
        state = OrderState.PARTIALLY_FILLED if "partial" in self.entry_mode else OrderState.FILLED
        if self.entry_mode == "zero":
            qty, state = D("0"), OrderState.EXPIRED
        observed = OrderObservation(
            intent.client_id,
            str(len(self.orders) + 1),
            "ordinary",
            intent.symbol,
            state,
            qty,
            D("100"),
            self.clock.now_ms() if qty else None,
            self.clock.now_ms(),
        )
        self.orders[intent.client_id] = observed
        if qty:
            side = PositionSide.LONG if intent.side is OrderSide.BUY else PositionSide.SHORT
            self.position = Position(
                intent.symbol,
                side,
                qty,
                D("100"),
                D("1"),
                self.clock.now_ms(),
                liquidation_price=D("50") if side is PositionSide.LONG else D("150"),
            )
            fee = qty * D("100") * D("0.0006")
            self.wallet -= fee
            self.fills.append(
                FillEvent(
                    "PAPER",
                    "virtual",
                    intent.symbol,
                    str(len(self.fills) + 1),
                    observed.venue_id,
                    D("100"),
                    qty,
                    fee,
                    "USDT",
                    self.clock.now_ms(),
                    intent.side,
                )
            )
        return SubmitUnknown() if "unknown" in self.entry_mode else SubmitAck(observed)

    async def find_order(self, intent):
        return None if self.hidden else self.orders.get(intent.client_id)

    async def submit_protection(self, intent):
        self.calls.append(("protection", intent))
        if self.reject_protection:
            return SubmitRejected("PROTECTION_REJECTED")
        value = OrderObservation(
            intent.client_id,
            str(len(self.orders) + 1),
            "algo",
            intent.symbol,
            OrderState.ACKNOWLEDGED,
            observed_ms=self.clock.now_ms(),
        )
        self.orders[intent.client_id] = value
        return SubmitAck(value)

    async def cancel_order(self, intent):
        self.calls.append(("cancel", intent))
        if self.reject_cancel:
            return SubmitRejected("CANCEL_REJECTED")
        old = self.orders.get(intent.client_id)
        if old is None:
            return SubmitUnknown()
        updated = replace(old, state=old.state if old.state.terminal else OrderState.CANCELED)
        self.orders[intent.client_id] = updated
        return SubmitAck(updated)

    async def reduce_position(self, symbol, side, quantity, client_id):
        self.calls.append(("reduce", symbol, side, quantity, client_id))
        if self.exit_hook:
            self.exit_hook()
        if self.reject_exit:
            return SubmitUnknown("EXIT_UNCONFIRMED")
        if self.position is None:
            return SubmitRejected("ALREADY_FLAT")
        assert symbol == self.position.symbol and side is self.position.side
        qty = min(quantity, self.position.quantity)
        self.position = (
            replace(self.position, quantity=self.position.quantity - qty)
            if qty < self.position.quantity
            else None
        )
        value = OrderObservation(
            client_id,
            str(len(self.orders) + 1),
            "ordinary",
            symbol,
            OrderState.FILLED,
            qty,
            D("100"),
            self.clock.now_ms(),
            self.clock.now_ms(),
        )
        self.orders[client_id] = value
        return SubmitAck(value)
