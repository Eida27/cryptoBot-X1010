import json
from dataclasses import dataclass, replace
from decimal import Decimal as D
from typing import Any

from crypto_bot.config import Settings
from crypto_bot.domain.clock import Clock
from crypto_bot.domain.enums import OrderState, PositionPhase, PositionSide
from crypto_bot.domain.models import (
    ApprovedSize, EntryContext, OrderIntent, OrderObservation, Position, RiskInput,
    RiskPolicy, Signal, SubmitAck, SubmitRejected,
)
from crypto_bot.risk.eligibility import check_eligibility
from crypto_bot.risk.sizing import size_entry
from crypto_bot.storage.repository import Repository


@dataclass(frozen=True)
class EntryOutcome:
    status: str
    reason: str | None = None
    intent_id: str | None = None


class ExecutionCoordinator:
    def __init__(self, repo: Repository, exchange: Any, clock: Clock,
                 settings: Settings | None = None) -> None:
        self.repo, self.exchange, self.clock = repo, exchange, clock
        self.settings = settings or Settings()
        self.protection: Any = None

    async def process_signal(self, signal: Signal, context: EntryContext) -> EntryOutcome:
        trial = self.repo.current_trial()
        state = self.repo.state()
        if trial is None or trial.halt_reason or state.get("state") != "RUNNING" or not state.get("reconciled"):
            return EntryOutcome("skipped", "ENTRIES_PAUSED")
        if self.repo.has_active_slot():
            return EntryOutcome("skipped", "CAPACITY_OCCUPIED")
        reasons = check_eligibility(signal, context, self.clock.now_ms(), self.settings)
        if reasons:
            return EntryOutcome("skipped", reasons[0])
        policy = RiskPolicy(self.settings.leverage, self.settings.entry_slippage_reserve,
                             self.settings.exit_slippage_reserve, self.settings.fee_floor)
        size = size_entry(RiskInput(trial, signal, context, policy))
        if not isinstance(size, ApprovedSize):
            return EntryOutcome("skipped", size.reason)
        intent = self.repo.reserve_entry(signal, size)
        if intent is None:
            return EntryOutcome("skipped", "DUPLICATE_OR_CAPACITY")
        # Commit before transport; a crash can never turn a submitted request into PREPARED.
        self.repo.set_intent_state(intent.logical_id, OrderState.SUBMITTED)
        try:
            result = await self.exchange.submit_entry(intent)
        except Exception:
            result = None
        if isinstance(result, SubmitRejected):
            self.repo.set_intent_state(intent.logical_id, OrderState.REJECTED)
            self.repo.release_slot()
            return EntryOutcome("skipped", result.reason, intent.logical_id)
        if isinstance(result, SubmitAck):
            self.apply_observation(intent, result.observation)
        else:
            self.repo.set_intent_state(intent.logical_id, OrderState.UNKNOWN)
            self.repo.mark_reconciled(self.clock.now_ms(), False)
        await self.observe_exposure(intent)
        if self.protection and self.repo.position():
            await self.protection.ensure_protection(intent.logical_id)
        state = self.repo.intent_state(intent.logical_id)
        return EntryOutcome("filled" if state is OrderState.FILLED else "unresolved" if state is OrderState.UNKNOWN
                            else "partially_filled" if self.repo.position() else "pending", intent_id=intent.logical_id)

    def apply_observation(self, intent: OrderIntent, observation: OrderObservation) -> None:
        self.repo.record_order(observation)
        current = self.repo.order(intent.client_id)
        assert current is not None
        if current.cumulative_quantity > 0:
            row = self.repo.db.connection.execute("SELECT evidence FROM order_intents WHERE logical_id=?", (intent.logical_id,)).fetchone()
            evidence = json.loads(row[0])["evidence"]
            old = self.repo.position()
            side = PositionSide.LONG if intent.side.value == "BUY" else PositionSide.SHORT
            first = current.first_fill_ms if current.first_fill_ms is not None else self.clock.now_ms()
            first = min(first, old.first_fill_ms) if old else first
            position = Position(intent.symbol, side, current.cumulative_quantity, current.average_price,
                D(evidence.get("atr", "1")), first, old.stop if old else None, old.target if old else None,
                old.liquidation_price if old else None, old.phase if old else PositionPhase.PROTECTING,
                intent.logical_id)
            self.repo.save_position(position)
        elif current.state.terminal and self.repo.position() is None:
            self.repo.release_slot()

    async def observe_exposure(self, intent: OrderIntent) -> None:
        snapshot = await self.exchange.fetch_snapshot()
        observations = snapshot.ordinary_orders + snapshot.algo_orders
        for value in observations:
            if value.client_id == intent.client_id:
                self.apply_observation(intent, value)
        order = self.repo.order(intent.client_id)
        for fill in snapshot.fills:
            if order and fill.order_id in {order.venue_id, intent.client_id}:
                self.repo.record_execution(fill)
        position = self.repo.position()
        if position:
            real = next((p for p in snapshot.positions if p.symbol == position.symbol and p.side is position.side), None)
            if real:
                self.repo.save_position(replace(position, liquidation_price=real.liquidation_price))

    async def reconcile_entries(self) -> None:
        for intent in self.repo.intents():
            if intent.role != "ENTRY" or self.repo.intent_state(intent.logical_id).terminal:
                continue
            observed = await self.exchange.find_order(intent)
            if observed is not None:
                self.apply_observation(intent, observed)
            await self.observe_exposure(intent)
            if self.protection and self.repo.position():
                await self.protection.ensure_protection(intent.logical_id)
            position = self.repo.position()
            if self.repo.has_unresolved_intent(intent.signal_id) and (
                self.clock.now_ms() - (position.first_fill_ms if position else self.repo.current_trial().start_ms) > 30000
            ):
                self.repo.latch_halt(intent.run_id, "UNRESOLVED_ENTRY")
