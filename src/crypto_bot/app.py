import asyncio
import logging
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import replace
from decimal import Decimal as D
from typing import Any

from crypto_bot.config import STRATEGY_HASH, ConfigurationError, Settings
from crypto_bot.domain.clock import SystemClock
from crypto_bot.domain.enums import ExitReason, Mode, PositionSide
from crypto_bot.domain.models import BookEvent, CandleEvent, IndicatorState, MarkEvent, StreamGapEvent
from crypto_bot.exchange.binance_adapter import BinanceAdapter
from crypto_bot.exchange.normalization import normalize_candle
from crypto_bot.execution.coordinator import ExecutionCoordinator
from crypto_bot.execution.protection import ProtectionManager
from crypto_bot.execution.worker import BotWorker
from crypto_bot.market.indicators import update_indicators
from crypto_bot.market.service import MarketService
from crypto_bot.research.paper import PaperBroker, mark_interrupted
from crypto_bot.storage.database import Database, ProcessLock
from crypto_bot.storage.repository import Repository
from crypto_bot.strategy.breakout import evaluate_signal


async def run_market(worker: BotWorker, adapter: BinanceAdapter) -> None:
    repo = worker.repo
    broker = worker.exchange
    service = MarketService(adapter, broker)
    history: dict[str, deque[Any]] = {s: deque(maxlen=21) for s in adapter.settings.symbols}
    states = {s: repo.indicator(s) or IndicatorState() for s in adapter.settings.symbols}
    exchange_time = await adapter.read("check_server_time")
    verified_ms = int(exchange_time["serverTime"])
    if abs(adapter.clock.now_ms() - verified_ms) > 1000:
        raise ConfigurationError("Clock differs from exchange by more than one second")
    for symbol in adapter.settings.symbols:
        state = states[symbol]
        end_ms = verified_ms // 3600000 * 3600000
        start = max(state.last_close_ms - 21 * 3600000, state.seed_epoch) if state.count else end_ms - 1000 * 3600000
        while start < end_ms:
            rows = await adapter.read("klines", symbol=symbol, interval="1h", startTime=start,
                                      endTime=end_ms - 1, limit=1000)
            if not rows:
                raise ValueError("Missing warm-up history")
            for row in rows:
                candle = normalize_candle(symbol, row, verified_ms)
                history[symbol].append(candle)
                if candle.close_ms > state.last_close_ms:
                    state = update_indicators(state, candle)
            start = int(rows[-1][6]) + 1
        states[symbol] = state
        repo.checkpoint_indicator(state)
    pending: dict[int, list[Any]] = {}
    async for event in adapter.market_events(adapter.settings.symbols):
        trial = repo.current_trial()
        assert trial is not None
        if isinstance(event, StreamGapEvent):
            repo.mark_reconciled(adapter.clock.now_ms(), False)
            if trial.mode is Mode.PAPER:
                mark_interrupted(repo, trial.run_id, event.reason, event.at_ms)
            continue
        if isinstance(event, BookEvent) and isinstance(broker, PaperBroker):
            broker.capture(event)
        elif isinstance(event, MarkEvent) and isinstance(broker, PaperBroker):
            broker.marks[event.symbol] = (event.at_ms, event.price)
            position = repo.position()
            if position and trial.qualification_status != "INTERRUPTED" and position.symbol == event.symbol:
                long = position.side is PositionSide.LONG
                reason = None
                if position.stop and ((long and event.price <= position.stop) or (not long and event.price >= position.stop)):
                    reason = ExitReason.STOP
                elif position.target and ((long and event.price >= position.target) or (not long and event.price <= position.target)):
                    reason = ExitReason.TARGET
                if reason:
                    await worker.engine.request_exit(reason)
        elif isinstance(event, CandleEvent):
            candle = event.candle
            if candle.close_ms <= states[candle.symbol].last_close_ms:
                continue
            try:
                states[candle.symbol] = update_indicators(states[candle.symbol], candle)
            except ValueError:
                if trial.mode is Mode.PAPER:
                    mark_interrupted(repo, trial.run_id, "CANDLE_GAP", adapter.clock.now_ms())
                continue
            history[candle.symbol].append(candle)
            repo.checkpoint_indicator(states[candle.symbol])
            found = evaluate_signal(list(history[candle.symbol]), states[candle.symbol], STRATEGY_HASH)
            candidates = pending.setdefault(candle.close_ms, [])
            if found:
                try:
                    ctx = await service.build_context(found)
                    candidates.append((found, ctx))
                except Exception:
                    worker.last_reason = "ENTRY_CONTEXT_UNAVAILABLE"
            if all(state.last_close_ms >= candle.close_ms for state in states.values()):
                await worker.process_candidates(candidates)
                pending.pop(candle.close_ms, None)


def build_service(settings: Settings) -> Any:
    from crypto_bot.web.routes import create_app
    if settings.mode is Mode.BACKTEST:
        raise ConfigurationError("BACKTEST runs as a separate CLI job")
    clock = SystemClock()
    repo = Repository(Database(settings.database))
    trial = repo.current_trial()
    if trial is None:
        if settings.mode is Mode.LIVE:
            raise ConfigurationError("Explicit trial arming required before LIVE service")
        trial = repo.create_run(settings.mode, settings.initial_capital_usdt,
                                {"config": settings.config_hash, "strategy": STRATEGY_HASH}, clock.now_ms())
    if trial.hashes.get("config") != settings.config_hash:
        repo.latch_halt(trial.run_id, "CONFIGURATION_CHANGED")
    from crypto_bot.research.gates import code_hash
    if settings.mode is Mode.LIVE and (trial.hashes.get("code") != code_hash() or trial.hashes.get("strategy") != STRATEGY_HASH or not trial.hashes.get("evidence")):
        repo.latch_halt(trial.run_id, "EVIDENCE_CHANGED")
    adapter = BinanceAdapter(settings, private=settings.mode in {Mode.DEMO, Mode.LIVE}, clock=clock)
    adapter.live_armed = settings.mode is Mode.LIVE and trial.hashes.get("armed") == "true" and trial.hashes.get("code") == code_hash() and trial.hashes.get("config") == settings.config_hash
    exchange = PaperBroker(repo, clock, adapter) if settings.mode is Mode.PAPER else adapter
    engine = ExecutionCoordinator(repo, exchange, clock, settings)
    ProtectionManager(engine)
    worker = BotWorker(engine)
    app = create_app(settings, repo)
    app.state.worker = worker
    @asynccontextmanager
    async def lifespan(application: Any) -> Any:
        with ProcessLock(settings.database):
            from crypto_bot.ops.logging import configure_logging
            from crypto_bot.ops.backup import check_disk, create_backup
            from pathlib import Path
            configure_logging(settings.database.parent / "logs")
            async def maintenance() -> None:
                last_backup = 0
                while True:
                    try:
                        check_disk(settings.database.parent)
                        if clock.now_ms() - last_backup >= 86400000:
                            await asyncio.to_thread(create_backup, settings.database, Path("backups"))
                            last_backup = clock.now_ms()
                        repo.db.connection.execute("DELETE FROM equity_snapshots WHERE at_ms<?", (clock.now_ms() - 90 * 86400000,))
                    except Exception:
                        repo.latch_halt(trial.run_id, "STORAGE_HEALTH_FAILED")
                        logging.getLogger("crypto_bot").error("Storage health failed; entries blocked")
                    await clock.sleep(60)
            tasks = [asyncio.create_task(worker.run()), asyncio.create_task(run_market(worker, adapter))]
            tasks.append(asyncio.create_task(maintenance()))
            async def private_events() -> None:
                while True:
                    try:
                        async for event in adapter.execution_events():
                            repo.record_execution(event)
                            await worker.refresh()
                    except Exception:
                        repo.mark_reconciled(clock.now_ms(), False)
                        await worker.refresh()
                        await clock.sleep(2)
            if settings.mode in {Mode.DEMO, Mode.LIVE}:
                tasks.append(asyncio.create_task(private_events()))
            try:
                yield
            finally:
                await worker.stop()
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                if hasattr(adapter.transport, "close"):
                    adapter.transport.close()
                repo.db.close()
    app.router.lifespan_context = lifespan
    return app
