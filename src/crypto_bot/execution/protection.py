import json
from dataclasses import dataclass, replace
from decimal import Decimal as D

from crypto_bot.domain.enums import ExitReason, OrderSide, OrderState, PositionPhase, PositionSide
from crypto_bot.domain.models import OrderIntent, SubmitAck
from crypto_bot.execution.coordinator import ExecutionCoordinator
from crypto_bot.risk.sizing import liquidation_buffer_ok, protective_levels
from crypto_bot.storage.repository import make_intent


@dataclass(frozen=True)
class ProtectionResult:
    confirmed: bool
    reason: str | None = None


class ProtectionManager:
    def __init__(self, coordinator: ExecutionCoordinator) -> None:
        self.engine = coordinator
        self.repo, self.exchange, self.clock = coordinator.repo, coordinator.exchange, coordinator.clock
        coordinator.protection = self

    async def install(self, intent: OrderIntent) -> bool:
        existing = next((value for value in self.repo.intents() if value.logical_id == intent.logical_id), None)
        if existing is not None:
            observation = await self.exchange.find_order(existing)
            if observation:
                self.repo.record_order(observation)
                return observation.state is OrderState.ACKNOWLEDGED
            return False
        self.repo.add_intent(intent)
        self.repo.set_intent_state(intent.logical_id, OrderState.SUBMITTED)
        try:
            result = await self.exchange.submit_protection(intent)
        except Exception:
            result = None
        if isinstance(result, SubmitAck):
            self.repo.record_order(result.observation)
            return result.observation.state is OrderState.ACKNOWLEDGED
        self.repo.set_intent_state(intent.logical_id, OrderState.UNKNOWN)
        return False

    async def ensure_protection(self, intent_id: str) -> ProtectionResult:
        position = self.repo.position()
        if position is None:
            return ProtectionResult(True)
        entry = next(value for value in self.repo.intents() if value.logical_id == intent_id)
        if position.phase is PositionPhase.OPEN:
            active = [value for value in self.repo.intents() if value.signal_id == entry.signal_id and value.role in {"STOP", "TARGET"}]
            for protective in active:
                observation = await self.exchange.find_order(protective)
                if observation is not None:
                    self.repo.record_order(observation)
                if observation is not None and observation.state is OrderState.FILLED:
                    snapshot = await self.exchange.fetch_snapshot()
                    if not snapshot.positions:
                        complete = await self.engine.cleanup_flat()
                        return ProtectionResult(complete, None if complete else "SIBLING_UNRESOLVED")
                if observation is None or observation.state is not OrderState.ACKNOWLEDGED:
                    await self.engine.request_exit(ExitReason.PROTECTION_FAILURE)
                    return ProtectionResult(False, "PROTECTION_NOT_ACTIVE")
            return ProtectionResult(len(active) == 2)
        if self.clock.now_ms() - position.first_fill_ms >= 5000:
            await self.engine.request_exit(ExitReason.PROTECTION_FAILURE)
            return ProtectionResult(False, "PROTECTION_DEADLINE")
        approved = json.loads(self.repo.db.connection.execute("SELECT evidence FROM order_intents WHERE logical_id=?", (intent_id,)).fetchone()[0])
        evidence = approved.get("evidence", {})
        tick = D(evidence.get("rules", {}).get("tick_size", "0.01"))
        try:
            stop, target = protective_levels(position.side, position.average_entry, position.atr, tick)
        except ValueError:
            await self.engine.request_exit(ExitReason.PROTECTION_FAILURE)
            return ProtectionResult(False, "INVALID_PROTECTION")
        opposite = OrderSide.SELL if position.side is PositionSide.LONG else OrderSide.BUY
        terminal = self.repo.intent_state(entry.logical_id).terminal
        role, generation = ("STOP", 1) if terminal else ("PROVISIONAL_STOP", 0)
        stop_intent = make_intent(entry.run_id, entry.signal_id, role, generation, entry.symbol,
                                  opposite, trigger_price=stop)
        if not await self.install(stop_intent):
            await self.engine.request_exit(ExitReason.PROTECTION_FAILURE)
            return ProtectionResult(False, "STOP_UNCONFIRMED")
        position = self.repo.position()
        if position is None:
            return ProtectionResult(False, "ALREADY_EXITED")
        self.repo.save_position(replace(position, stop=stop))
        if not terminal:
            return ProtectionResult(False, "IOC_TERMINAL_PENDING")
        if position.liquidation_price is None or not liquidation_buffer_ok(position.side, position.average_entry, stop, position.liquidation_price):
            await self.engine.request_exit(ExitReason.PROTECTION_FAILURE)
            return ProtectionResult(False, "LIQUIDATION_BUFFER")
        planned = position.quantity * (abs(position.average_entry - stop) + position.average_entry * D(evidence.get("cost_rate", "0.0034")))
        if planned > D(approved.get("budget", "0")):
            await self.engine.request_exit(ExitReason.PROTECTION_FAILURE)
            return ProtectionResult(False, "POST_FILL_BUDGET")
        target_intent = make_intent(entry.run_id, entry.signal_id, "TARGET", 1, entry.symbol,
                                    opposite, trigger_price=target)
        if not await self.install(target_intent) or self.clock.now_ms() - position.first_fill_ms >= 5000:
            await self.engine.request_exit(ExitReason.PROTECTION_FAILURE)
            return ProtectionResult(False, "TARGET_OR_DEADLINE")
        for provisional in self.repo.intents():
            if provisional.signal_id == entry.signal_id and provisional.role == "PROVISIONAL_STOP" and not self.repo.intent_state(provisional.logical_id).terminal:
                result = await self.exchange.cancel_order(provisional)
                if isinstance(result, SubmitAck):
                    self.repo.record_order(result.observation)
                confirmed = await self.exchange.find_order(provisional)
                if confirmed is None or not confirmed.state.terminal:
                    await self.engine.request_exit(ExitReason.PROTECTION_FAILURE)
                    return ProtectionResult(False, "PROVISIONAL_CANCEL_UNCONFIRMED")
        self.repo.save_position(replace(position, stop=stop, target=target, phase=PositionPhase.OPEN))
        return ProtectionResult(True)

    async def check_deadline(self) -> None:
        position = self.repo.position()
        if position:
            if self.clock.now_ms() >= position.first_fill_ms + 48 * 3600000:
                await self.engine.request_exit(ExitReason.TIME_LIMIT)
            else:
                await self.ensure_protection(position.intent_id)
