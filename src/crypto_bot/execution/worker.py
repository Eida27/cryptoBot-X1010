import asyncio
from dataclasses import dataclass, replace
from decimal import Decimal as D
from typing import Any

from crypto_bot.domain.enums import ExitReason, Mode
from crypto_bot.domain.models import (
    AccountSnapshot,
    BookEvent,
    ControlCommand,
    EntryContext,
    ExecutionEvent,
    FillEvent,
    MarkEvent,
    OrderUpdate,
    Signal,
)
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


def rank_candidates(
    candidates: list[tuple[Signal, EntryContext]],
) -> list[tuple[Signal, EntryContext]]:
    return sorted(
        candidates,
        key=lambda candidate: (-candidate[1].market.quote_volume_24h, candidate[0].symbol),
    )


class BotWorker:
    def __init__(self, engine: ExecutionCoordinator) -> None:
        self.engine, self.repo, self.exchange, self.clock = (
            engine,
            engine.repo,
            engine.exchange,
            engine.clock,
        )
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
        self.market_heartbeat_ms = 0
        self.loop_lag_seconds = 0.0
        self.loop_lags: list[float] = []
        self.marks: dict[str, MarkEvent] = {}
        self.refresh_lock = asyncio.Lock()
        self.market_started_ms: int | None = None
        self.market_ready = False
        self.books: dict[str, int] = {}
        self.candle_closes: dict[str, int] = {}

    def observe_mark(self, event: MarkEvent) -> None:
        self.marks[event.symbol] = event
        self.market_heartbeat_ms = min(self.clock.now_ms(), event.at_ms)

    def observe_book(self, event: BookEvent) -> None:
        self.books[event.frame.symbol] = min(
            event.frame.observed_ms, event.frame.freshness.get("quote", 0)
        )

    def begin_market_observation(self, candle_closes: dict[str, int]) -> None:
        self.market_started_ms = self.clock.now_ms()
        self.market_ready = False
        self.books.clear()
        self.marks.clear()
        self.candle_closes = dict(candle_closes)
        trial = self.repo.current_trial()
        if trial and trial.mode is Mode.PAPER:
            self.repo.db.connection.execute(
                "UPDATE runs SET qualification_status='WARMING_UP' WHERE run_id=? AND qualification_status!='INTERRUPTED'",
                (trial.run_id,),
            )
        self.repo.mark_reconciled(self.clock.now_ms(), False)

    def check_market_observation(self, now_ms: int) -> None:
        if self.market_started_ms is None:
            return
        boundary = now_ms // 3600000 * 3600000
        fresh = all(
            symbol in self.marks
            and self.marks[symbol].at_ms <= now_ms + 1000
            and now_ms - min(now_ms, self.marks[symbol].at_ms) <= 3000
            and symbol in self.books
            and 0 <= now_ms - self.books[symbol] <= 3000
            and self.candle_closes.get(symbol, -1)
            >= boundary - (3600000 if now_ms - boundary <= 10000 else 0)
            for symbol in self.engine.settings.symbols
        )
        trial = self.repo.current_trial()
        assert trial is not None
        if trial.qualification_status == "INTERRUPTED":
            self.repo.mark_reconciled(now_ms, False)
        elif fresh:
            if not self.market_ready and trial.mode is Mode.PAPER:
                self.repo.db.connection.execute(
                    "UPDATE runs SET qualification_status='OBSERVING' WHERE run_id=?",
                    (trial.run_id,),
                )
                self.repo.db.connection.execute(
                    "INSERT INTO audit_events(run_id,at_ms,kind,payload) VALUES(?,?,'PAPER_OBSERVATION_STARTED','{}')",
                    (trial.run_id, now_ms),
                )
            self.market_ready = True
        else:
            self.repo.mark_reconciled(now_ms, False)
            if self.market_ready or now_ms - self.market_started_ms > 10000:
                self.last_reason = "REQUIRED_MARKET_SOURCE_STALE"
                if trial.mode is Mode.PAPER:
                    mark_interrupted(self.repo, trial.run_id, self.last_reason, now_ms)

    async def refresh(self) -> None:
        async with self.refresh_lock:
            await self._refresh()

    async def observe_execution(self, event: ExecutionEvent) -> None:
        self.repo.record_execution(event)
        slot = self.repo.db.connection.execute("SELECT intent_id FROM active_slot").fetchone()
        entry = next(
            (
                i
                for i in self.repo.intents()
                if slot and i.logical_id == slot[0] and i.role == "ENTRY"
            ),
            None,
        )
        if entry is not None:
            order = self.repo.order(entry.client_id)
            attributed = (
                isinstance(event, OrderUpdate) and event.observation.client_id == entry.client_id
            ) or (
                isinstance(event, FillEvent)
                and order is not None
                and event.order_id in {entry.client_id, order.venue_id}
                and event.symbol == entry.symbol
            )
            if attributed and order is not None:
                self.engine.apply_observation(entry, order)
                if self.engine.protection and self.repo.position():
                    await self.engine.protection.protect_observed_stop(entry.logical_id)
        await self.refresh()

    async def _refresh(self) -> None:
        try:
            snapshot = await self.exchange.fetch_snapshot()
            self.account = snapshot.account
            await self.reconciler.recover(snapshot)
            self.check_market_observation(self.clock.now_ms())
        except Exception:
            self.last_reason = "ACCOUNT_REFRESH_FAILED"
            self.repo.mark_reconciled(self.clock.now_ms(), False)

    async def tick(self, now_ms: int) -> None:
        trial = self.repo.current_trial()
        if trial is None:
            return
        if self.last_tick is not None and (
            now_ms < self.last_tick or now_ms - self.last_tick > 10000
        ):
            if trial.mode is Mode.PAPER:
                mark_interrupted(self.repo, trial.run_id, "RISK_LOOP_GAP", now_ms)
            self.repo.mark_reconciled(now_ms, False)
        self.last_tick = now_ms
        self.loop_lags.append(self.loop_lag_seconds)
        if len(self.loop_lags) >= 60:
            from crypto_bot.storage.repository import encode

            self.repo.db.connection.execute(
                "INSERT INTO audit_events(run_id,at_ms,kind,payload) VALUES(?,?,'LOOP_TIMING',?)",
                (trial.run_id, now_ms, encode({"lags": self.loop_lags})),
            )
            self.loop_lags = []
        self.repo.db.connection.execute(
            "UPDATE run_state SET last_heartbeat=? WHERE run_id=?", (now_ms, trial.run_id)
        )
        cadence = 5000 if self.repo.position() else 30000
        if (self.last_refresh is None or now_ms - self.last_refresh >= cadence) and (
            self.refresh_task is None or self.refresh_task.done()
        ):
            self.last_refresh = now_ms
            self.refresh_task = asyncio.create_task(self.refresh())
            await asyncio.sleep(0)
        position = self.repo.position()
        if self.account is not None:
            account = self.account
            mark = self.marks.get(position.symbol) if position else None
            if position and mark and 0 <= now_ms - mark.at_ms <= 3000:
                account = replace(
                    account,
                    unrealized_pnl=(mark.price - position.average_entry)
                    * position.quantity
                    * (1 if position.side.value == "LONG" else -1),
                )
            reference = (
                mark.price if position and mark else position.average_entry if position else D("0")
            )
            allowance = (
                position.quantity
                * reference
                * (self.engine.settings.fee_floor + self.engine.settings.exit_slippage_reserve)
                if position
                else D("0")
            )
            decision = check_trial(trial, account, allowance)
            funding_unsafe = False
            if position and mark and mark.funding_rate is not None:
                import json
                from decimal import ROUND_CEILING

                row = self.repo.db.connection.execute(
                    "SELECT evidence FROM order_intents WHERE logical_id=?", (position.intent_id,)
                ).fetchone()
                approved = json.loads(row[0]) if row else {}
                evidence = approved.get("evidence", {})
                funding = evidence.get("funding", {})
                interval = D(funding.get("interval_hours", "0"))
                remaining = D(max(0, position.first_fill_ms + 48 * 3600000 - now_ms)) / 3600000
                reserve = (
                    max(
                        abs(mark.funding_rate),
                        D(funding.get("seven_day_max_abs_rate", "0")),
                        D("0.0001"),
                    )
                    * ((remaining / interval).to_integral_value(rounding=ROUND_CEILING) + 1)
                    if interval > 0
                    else D("Infinity")
                )
                nonfunding_cost = D(evidence.get("cost_rate", "0")) - D(
                    evidence.get("funding_reserve", "0")
                )
                planned = position.quantity * (
                    abs(position.average_entry - (position.stop or position.average_entry))
                    + position.average_entry * (nonfunding_cost + reserve)
                )
                funding_unsafe = (
                    planned > D(approved.get("budget", "0"))
                    or closing_equity(
                        account, allowance + reserve * position.quantity * position.average_entry
                    )
                    <= trial.floor
                )
            if decision.halt:
                self.repo.latch_halt(trial.run_id, "TRIAL_LOSS")
                if self.safety_task is None or self.safety_task.done():
                    self.safety_task = asyncio.create_task(
                        self.engine.request_exit(ExitReason.TRIAL_LOSS)
                    )
                    await asyncio.sleep(0)
            elif funding_unsafe and (self.safety_task is None or self.safety_task.done()):
                self.safety_task = asyncio.create_task(
                    self.engine.request_exit(ExitReason.FUNDING_RISK)
                )
                await asyncio.sleep(0)
            elif (
                position
                and self.engine.protection
                and (self.safety_task is None or self.safety_task.done())
            ):
                self.safety_task = asyncio.create_task(self.engine.protection.check_deadline())
                await asyncio.sleep(0)
            if now_ms - self.account.observed_ms > 15000:
                self.repo.mark_reconciled(now_ms, False)
                if trial.mode is Mode.PAPER and position:
                    mark_interrupted(self.repo, trial.run_id, "STALE_ACCOUNT", now_ms)
            if self.last_snapshot is None or now_ms - self.last_snapshot >= 60000:
                self.last_snapshot = now_ms
                self.repo.db.connection.execute(
                    "INSERT INTO equity_snapshots(run_id,at_ms,mark_equity,closing_equity,floor,freshness) VALUES(?,?,?,?,?,?)",
                    (
                        trial.run_id,
                        now_ms,
                        str(mark_equity(account)),
                        str(closing_equity(account, allowance)),
                        str(trial.floor),
                        str(self.account.observed_ms),
                    ),
                )
        if self.repo.pending_commands() and (self.command_task is None or self.command_task.done()):
            command = self.repo.pending_commands()[0]
            self.command_task = asyncio.create_task(self.consume_command(command))
            await asyncio.sleep(0)
        self.check_market_observation(now_ms)

    async def consume_command(self, command: ControlCommand) -> None:
        result = await self.handle_command(command)
        if not result.pending:
            self.repo.complete_command(
                command.request_id, result.accepted, {"reason": result.reason}
            )

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
            if trial.mode is Mode.LIVE:
                from crypto_bot.research.gates import code_hash

                if (
                    trial.hashes.get("code") != code_hash()
                    or trial.hashes.get("config") != self.engine.settings.config_hash
                ):
                    return CommandResult(False, "LIVE_EVIDENCE_CHANGED")
            return CommandResult(
                self.repo.resume(trial.run_id),
                "RECONCILIATION_OR_HALT"
                if not self.repo.state().get("reconciled") or trial.halt_reason == "TRIAL_LOSS"
                else None,
            )
        if command.action == "close-and-pause":
            result = await self.engine.request_exit(ExitReason.OPERATOR)
            return CommandResult(
                result.confirmed_flat and result.cleanup_complete,
                result.reason,
                pending=not result.confirmed_flat or not result.cleanup_complete,
            )
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
        if (
            trial
            and trial.mode is Mode.PAPER
            and self.repo.state().get("last_heartbeat") is not None
        ):
            mark_interrupted(self.repo, trial.run_id, "SERVICE_RESTART", self.clock.now_ms())
        await self.refresh()
        expected_tick = asyncio.get_running_loop().time()
        while self.running:
            self.loop_lag_seconds = max(0, asyncio.get_running_loop().time() - expected_tick)
            await self.tick(self.clock.now_ms())
            expected_tick += 1
            await self.clock.sleep(max(0, expected_tick - asyncio.get_running_loop().time()))

    async def stop(self) -> None:
        self.running = False
        trial = self.repo.current_trial()
        self.repo.pause()
        if trial and trial.mode is Mode.PAPER:
            mark_interrupted(self.repo, trial.run_id, "SERVICE_STOPPED", self.clock.now_ms())
        tasks = [task for task in (self.refresh_task, self.command_task, self.safety_task) if task]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
