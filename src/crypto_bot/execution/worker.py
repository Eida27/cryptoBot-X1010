import asyncio
from dataclasses import dataclass
from decimal import Decimal as D
from typing import Any

from crypto_bot.domain.enums import ExitReason, Mode
from crypto_bot.domain.models import AccountSnapshot, ControlCommand, EntryContext, Signal
from crypto_bot.execution.coordinator import ExecutionCoordinator
from crypto_bot.execution.reconciliation import Reconciler
from crypto_bot.research.paper import mark_interrupted
from crypto_bot.risk.equity import closing_equity, mark_equity
from crypto_bot.risk.trial import check_trial


@dataclass(frozen=True)
class CommandResult:
    accepted: bool
    reason: str | None = None
    pending: bool = False


def rank_candidates(candidates: list[tuple[Signal, EntryContext]]) -> list[tuple[Signal, EntryContext]]:
    return sorted(candidates, key=lambda candidate: (-candidate[1].market.quote_volume_24h, candidate[0].symbol))


class BotWorker:
    def __init__(self, engine: ExecutionCoordinator) -> None:
        self.engine, self.repo, self.exchange, self.clock = engine, engine.repo, engine.exchange, engine.clock
        self.reconciler = Reconciler(engine)
        self.account: AccountSnapshot | None = None
        self.last_refresh: int | None = None
        self.last_tick: int | None = None
        self.last_snapshot: int | None = None
        self.refresh_task: asyncio.Task[Any] | None = None
        self.command_task: asyncio.Task[Any] | None = None
        self.safety_task: asyncio.Task[Any] | None = None
        self.running = True
        self.last_reason: str | None = None

    async def refresh(self) -> None:
        try:
            snapshot = await self.exchange.fetch_snapshot()
            self.account = snapshot.account
            await self.reconciler.recover(snapshot)
        except Exception:
            self.last_reason = "ACCOUNT_REFRESH_FAILED"
            self.repo.mark_reconciled(self.clock.now_ms(), False)

    async def tick(self, now_ms: int) -> None:
        trial = self.repo.current_trial()
        if trial is None:
            return
        if self.last_tick is not None and (now_ms < self.last_tick or now_ms - self.last_tick > 10000):
            if trial.mode is Mode.PAPER:
                mark_interrupted(self.repo, trial.run_id, "RISK_LOOP_GAP", now_ms)
            self.repo.mark_reconciled(now_ms, False)
        self.last_tick = now_ms
        self.repo.db.connection.execute("UPDATE run_state SET last_heartbeat=? WHERE run_id=?", (now_ms, trial.run_id))
        cadence = 5000 if self.repo.position() else 30000
        if (self.last_refresh is None or now_ms - self.last_refresh >= cadence) and (self.refresh_task is None or self.refresh_task.done()):
            self.last_refresh = now_ms
            self.refresh_task = asyncio.create_task(self.refresh())
            await asyncio.sleep(0)
        position = self.repo.position()
        if self.account is not None:
            allowance = position.quantity * position.average_entry * D("0.0016") if position else D("0")
            decision = check_trial(trial, self.account, allowance)
            if decision.halt:
                self.repo.latch_halt(trial.run_id, "TRIAL_LOSS")
                if self.safety_task is None or self.safety_task.done():
                    self.safety_task = asyncio.create_task(self.engine.request_exit(ExitReason.TRIAL_LOSS))
                    await asyncio.sleep(0)
            elif position and self.engine.protection and (self.safety_task is None or self.safety_task.done()):
                self.safety_task = asyncio.create_task(self.engine.protection.check_deadline())
                await asyncio.sleep(0)
            if now_ms - self.account.observed_ms > 15000:
                self.repo.mark_reconciled(now_ms, False)
                if trial.mode is Mode.PAPER and position:
                    mark_interrupted(self.repo, trial.run_id, "STALE_ACCOUNT", now_ms)
            if self.last_snapshot is None or now_ms - self.last_snapshot >= 60000:
                self.last_snapshot = now_ms
                self.repo.db.connection.execute("INSERT INTO equity_snapshots(run_id,at_ms,mark_equity,closing_equity,floor,freshness) VALUES(?,?,?,?,?,?)",
                    (trial.run_id, now_ms, str(mark_equity(self.account)), str(closing_equity(self.account, allowance)),
                     str(trial.floor), str(self.account.observed_ms)))
        if self.repo.pending_commands() and (self.command_task is None or self.command_task.done()):
            command = self.repo.pending_commands()[0]
            self.command_task = asyncio.create_task(self.consume_command(command))
            await asyncio.sleep(0)

    async def consume_command(self, command: ControlCommand) -> None:
        result = await self.handle_command(command)
        if not result.pending:
            self.repo.complete_command(command.request_id, result.accepted, {"reason": result.reason})

    async def handle_command(self, command: ControlCommand) -> CommandResult:
        if command.action == "pause":
            self.repo.pause()
            return CommandResult(True)
        if command.action == "resume":
            trial = self.repo.current_trial()
            if trial is None:
                return CommandResult(False, "NO_TRIAL")
            if trial.qualification_status == "INTERRUPTED":
                return CommandResult(False, "NEW_PAPER_SESSION_REQUIRED")
            if trial.mode is Mode.LIVE and trial.hashes.get("armed") != "true":
                return CommandResult(False, "LIVE_NOT_ARMED")
            return CommandResult(self.repo.resume(trial.run_id), "RECONCILIATION_OR_HALT" if not self.repo.state().get("reconciled") or trial.halt_reason == "TRIAL_LOSS" else None)
        if command.action == "close-and-pause":
            result = await self.engine.request_exit(ExitReason.OPERATOR)
            return CommandResult(result.confirmed_flat and result.cleanup_complete, result.reason,
                                  pending=not result.confirmed_flat or not result.cleanup_complete)
        if command.action == "acknowledge-fault":
            return CommandResult(True, "REVIEW_RECORDED_ENTRIES_UNCHANGED")
        return CommandResult(False, "UNKNOWN_COMMAND")

    async def process_candidates(self, candidates: list[tuple[Signal, EntryContext]]) -> None:
        for signal, context in rank_candidates(candidates):
            result = await self.engine.process_signal(signal, context)
            self.last_reason = result.reason
            if result.intent_id is not None:
                break

    async def run(self) -> None:
        self.repo.pause()
        trial = self.repo.current_trial()
        if trial and trial.mode is Mode.PAPER and self.repo.state().get("last_heartbeat") is not None:
            mark_interrupted(self.repo, trial.run_id, "SERVICE_RESTART", self.clock.now_ms())
        await self.refresh()
        while self.running:
            await self.tick(self.clock.now_ms())
            await self.clock.sleep(1)

    async def stop(self) -> None:
        self.running = False
        trial = self.repo.current_trial()
        self.repo.pause()
        if trial and trial.mode is Mode.PAPER:
            mark_interrupted(self.repo, trial.run_id, "SERVICE_STOPPED", self.clock.now_ms())
        tasks = [task for task in (self.refresh_task, self.command_task, self.safety_task) if task]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
