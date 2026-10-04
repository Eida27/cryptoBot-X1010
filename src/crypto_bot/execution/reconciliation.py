import sqlite3
from dataclasses import dataclass, replace
from decimal import Decimal as D
from pathlib import Path

from crypto_bot.domain.enums import OrderState, PositionPhase
from crypto_bot.domain.models import ExchangeSnapshot, Position
from crypto_bot.execution.coordinator import ExecutionCoordinator
from crypto_bot.storage.repository import encode


@dataclass(frozen=True)
class RecoveryResult:
    owned_exposure: Position | None
    unresolved_intents: tuple[str, ...]
    unresolved_orders: tuple[str, ...]
    accounting_mismatches: tuple[str, ...]
    entry_prerequisites_satisfied: bool


class Reconciler:
    def __init__(self, engine: ExecutionCoordinator, emergency_log: Path | None = None) -> None:
        self.engine, self.repo, self.exchange = engine, engine.repo, engine.exchange
        self.last_verified_owned: Position | None = None
        self.emergency_log = emergency_log or self.repo.db.path.with_suffix(".emergency.log")

    async def recover(self, snapshot: ExchangeSnapshot) -> RecoveryResult:
        try:
            return await self._recover(snapshot)
        except sqlite3.Error:
            # No entry path survives a lost journal. Only previously verified owned exposure
            # can be reduced, and a stop is never canceled in this emergency path.
            position = self.last_verified_owned
            self.emergency_log.parent.mkdir(parents=True, exist_ok=True)
            with self.emergency_log.open("a", encoding="utf-8") as log:
                log.write(encode({"at_ms": snapshot.observed_ms, "reason": "STORAGE_UNAVAILABLE",
                                  "owned": position}) + "\n")
            if position:
                real = next((p for p in snapshot.positions if p.symbol == position.symbol and p.side is position.side), None)
                if real and real.quantity <= position.quantity:
                    await self.exchange.reduce_position(real.symbol, real.side, real.quantity,
                        "cb-emergency-" + str(snapshot.observed_ms))
            return RecoveryResult(position, (), (), ("STORAGE_UNAVAILABLE",), False)

    async def _recover(self, snapshot: ExchangeSnapshot) -> RecoveryResult:
        trial = self.repo.current_trial()
        if trial is None:
            return RecoveryResult(None, (), (), ("NO_TRIAL",), False)
        mismatches: list[str] = []
        known = {intent.client_id: intent for intent in self.repo.intents() if intent.run_id == trial.run_id}
        slot = self.repo.db.connection.execute("SELECT intent_id FROM active_slot").fetchone()
        active_identity = slot[0] if slot else None
        observed = {o.client_id: o for o in snapshot.ordinary_orders + snapshot.algo_orders}
        for client_id, order in observed.items():
            intent = known.get(client_id)
            if intent is None:
                if not order.state.terminal:
                    mismatches.append("UNKNOWN_ORDER")
                continue
            old = self.repo.order(client_id)
            if old and old.state.terminal and not order.state.terminal:
                mismatches.append("ORDER_STATE_CONFLICT")
            if intent.role == "ENTRY" and intent.logical_id == active_identity and snapshot.positions:
                self.engine.apply_observation(intent, order)
            else:
                self.repo.record_order(order)
        for intent in known.values():
            if intent.client_id in observed:
                continue
            state = self.repo.intent_state(intent.logical_id)
            if state.terminal:
                continue
            order = await self.exchange.find_order(intent)
            if order is not None:
                if intent.role == "ENTRY" and intent.logical_id == active_identity and snapshot.positions:
                    self.engine.apply_observation(intent, order)
                else:
                    self.repo.record_order(order)
        venue_ids = {order.venue_id for client in known if (order := self.repo.order(client))}
        for fill in snapshot.fills:
            if fill.order_id not in venue_ids and fill.order_id not in known:
                mismatches.append("UNATTRIBUTED_FILL")
                continue
            self.repo.record_execution(fill)
            if fill.commission_asset != "USDT":
                mismatches.append("UNSUPPORTED_FEE_ASSET")
        for income in snapshot.income:
            self.repo.record_execution(income)
            if income.asset != "USDT":
                mismatches.append("UNSUPPORTED_INCOME_ASSET")
            if income.income_type not in {"FUNDING_FEE", "COMMISSION", "REALIZED_PNL"}:
                mismatches.append("EXTERNAL_ACCOUNT_ACTIVITY")
        owned = self.repo.position()
        for real in snapshot.positions:
            if owned is None or real.symbol != owned.symbol or real.side is not owned.side:
                mismatches.append("UNKNOWN_POSITION")
            elif real.quantity != owned.quantity:
                if not any(i.role == "EXIT" for i in known.values()):
                    mismatches.append("QUANTITY_MISMATCH")
                else:
                    self.repo.save_position(replace(owned, quantity=real.quantity,
                                                   liquidation_price=real.liquidation_price))
            else:
                self.repo.save_position(replace(owned, liquidation_price=real.liquidation_price))
                self.last_verified_owned = self.repo.position()
        if owned is not None and not snapshot.positions:
            await self.engine.cleanup_flat()
        elif owned is not None and self.engine.protection and not mismatches:
            await self.engine.protection.ensure_protection(owned.intent_id)
        # Booked wallet already contains commissions/funding. Reconcile, do not subtract
        # these events again when computing mark equity or the shutdown floor.
        fills = [f for f in self.repo.fills() if f.commission_asset == "USDT"]
        income = [i for i in self.repo.income_events() if i.asset == "USDT"]
        expected_wallet = trial.baseline - sum((f.commission for f in fills), D("0"))
        expected_wallet += sum((i.amount for i in income if i.income_type in {"FUNDING_FEE", "REALIZED_PNL"}), D("0"))
        if abs(expected_wallet - snapshot.account.wallet_balance) > D("0.01"):
            mismatches.append("WALLET_LEDGER_MISMATCH")
        unresolved_intents, unresolved_orders = [], []
        position = self.repo.position()
        for intent in known.values():
            state = self.repo.intent_state(intent.logical_id)
            if state in {OrderState.PREPARED, OrderState.SUBMITTED, OrderState.UNKNOWN} or (
                intent.role == "ENTRY" and not state.terminal):
                unresolved_intents.append(intent.logical_id)
            if intent.role in {"STOP", "TARGET", "PROVISIONAL_STOP"} and not state.terminal:
                if position is None or state is not OrderState.ACKNOWLEDGED:
                    unresolved_orders.append(intent.logical_id)
        if position and position.phase is not PositionPhase.OPEN:
            mismatches.append("PROTECTION_PENDING")
        if mismatches:
            self.repo.latch_halt(trial.run_id, "ACCOUNT_RECONCILIATION")
        healthy = not mismatches and not unresolved_intents and not unresolved_orders
        self.repo.mark_reconciled(snapshot.observed_ms, healthy)
        return RecoveryResult(position, tuple(unresolved_intents), tuple(unresolved_orders),
                              tuple(dict.fromkeys(mismatches)), healthy)
