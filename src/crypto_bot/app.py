import asyncio
import logging
from collections import deque
from contextlib import asynccontextmanager
from decimal import Decimal as D
from typing import Any

from crypto_bot.config import STRATEGY_HASH, ConfigurationError, Settings
from crypto_bot.domain.clock import SystemClock
from crypto_bot.domain.enums import ExitReason, Mode, PositionSide
from crypto_bot.domain.models import (
    BookEvent,
    CandleEvent,
    FundingEvent,
    IndicatorState,
    MarkEvent,
    StreamGapEvent,
)
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
    funding_due: dict[str, int] = {}
    exchange_time = await adapter.read("check_server_time")
    verified_ms = int(exchange_time["serverTime"])
    if abs(adapter.clock.now_ms() - verified_ms) > 1000:
        raise ConfigurationError("Clock differs from exchange by more than one second")
    for symbol in adapter.settings.symbols:
        premium = await adapter.read("mark_price", symbol=symbol)
        funding_due[symbol] = int(premium["nextFundingTime"])
        state = states[symbol]
        end_ms = verified_ms // 3600000 * 3600000
        start = (
            max(state.last_close_ms - 21 * 3600000, state.seed_epoch)
            if state.count
            else end_ms - 1000 * 3600000
        )
        while start < end_ms:
            rows = await adapter.read(
                "klines",
                symbol=symbol,
                interval="1h",
                startTime=start,
                endTime=end_ms - 1,
                limit=1000,
            )
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
    worker.begin_market_observation(
        {symbol: state.last_close_ms for symbol, state in states.items()}
    )
    pending: dict[int, list[Any]] = {}
    async for event in adapter.market_events(adapter.settings.symbols):
        trial = repo.current_trial()
        assert trial is not None
        worker.market_heartbeat_ms = adapter.heartbeat_ms
        if isinstance(event, StreamGapEvent):
            repo.mark_reconciled(adapter.clock.now_ms(), False)
            if trial.mode is Mode.PAPER:
                mark_interrupted(repo, trial.run_id, event.reason, event.at_ms)
            continue
        if isinstance(event, BookEvent):
            worker.observe_book(event)
            if isinstance(broker, PaperBroker):
                broker.capture(event)
        elif isinstance(event, MarkEvent):
            worker.observe_mark(event)
            if not isinstance(broker, PaperBroker):
                continue
            broker.marks[event.symbol] = (event.at_ms, event.price)
            position = repo.position()
            if (
                position
                and trial.qualification_status != "INTERRUPTED"
                and position.symbol == event.symbol
            ):
                due = funding_due[event.symbol]
                if event.at_ms >= due:
                    settlements = await adapter.read(
                        "funding_history",
                        symbol=event.symbol,
                        startTime=due,
                        endTime=event.at_ms,
                        limit=100,
                    )
                    actual = [v for v in settlements if int(v["fundingTime"]) >= due]
                    if not actual:
                        mark_interrupted(
                            repo, trial.run_id, "FUNDING_SETTLEMENT_UNAVAILABLE", event.at_ms
                        )
                        continue
                    for value in actual:
                        when = int(value["fundingTime"])
                        if when >= position.first_fill_ms:
                            broker.book_funding(
                                FundingEvent(
                                    event.symbol,
                                    when,
                                    D(value["fundingRate"]),
                                    D(value["markPrice"]),
                                    f"{event.symbol}:{when}",
                                )
                            )
                    if event.next_funding_ms is None or event.next_funding_ms <= event.at_ms:
                        mark_interrupted(repo, trial.run_id, "FUNDING_UPDATE_MISSED", event.at_ms)
                        continue
                    funding_due[event.symbol] = event.next_funding_ms
                long = position.side is PositionSide.LONG
                reason = None
                if position.stop and (
                    (long and event.price <= position.stop)
                    or (not long and event.price >= position.stop)
                ):
                    reason = ExitReason.STOP
                elif position.target and (
                    (long and event.price >= position.target)
                    or (not long and event.price <= position.target)
                ):
                    reason = ExitReason.TARGET
                if reason:
                    await worker.engine.request_exit(reason)
            elif position is None and event.next_funding_ms:
                funding_due[event.symbol] = event.next_funding_ms
        elif isinstance(event, CandleEvent):
            candle = event.candle
            worker.candle_closes[candle.symbol] = max(
                candle.close_ms, worker.candle_closes.get(candle.symbol, 0)
            )
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
            found = evaluate_signal(
                list(history[candle.symbol]), states[candle.symbol], STRATEGY_HASH
            )
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


async def supervise_market(worker: BotWorker, adapter: BinanceAdapter) -> None:
    while worker.running:
        try:
            await run_market(worker, adapter)
            raise ConnectionError("Market stream ended")
        except asyncio.CancelledError:
            raise
        except Exception:
            worker.last_reason = "MARKET_OBSERVATION_FAILED"
            trial = worker.repo.current_trial()
            if trial and trial.mode is Mode.PAPER:
                mark_interrupted(
                    worker.repo, trial.run_id, "MARKET_OBSERVATION_FAILED", worker.clock.now_ms()
                )
            else:
                worker.repo.mark_reconciled(worker.clock.now_ms(), False)
            logging.getLogger("crypto_bot").error("Market observation failed; entries blocked")
        await worker.clock.sleep(2)


def build_service(settings: Settings) -> Any:
    if settings.mode is Mode.BACKTEST:
        raise ConfigurationError("BACKTEST runs as a separate CLI job")
    process_lock = ProcessLock(settings.database)
    process_lock.__enter__()
    try:
        app = _build_service(settings, process_lock)
        app.state.process_lock = process_lock
        return app
    except BaseException:
        process_lock.__exit__(None, None, None)
        raise


def _build_service(settings: Settings, process_lock: ProcessLock) -> Any:
    from crypto_bot.web.routes import create_app

    if settings.mode is Mode.BACKTEST:
        raise ConfigurationError("BACKTEST runs as a separate CLI job")
    clock = SystemClock()
    repo = Repository(Database(settings.database))
    trial = repo.current_trial()
    if trial is None:
        if settings.mode is Mode.LIVE:
            repo.db.close()
            raise ConfigurationError("Explicit trial arming required before LIVE service")
        trial = repo.create_run(
            settings.mode,
            settings.initial_capital_usdt,
            {"config": settings.config_hash, "strategy": STRATEGY_HASH},
            clock.now_ms(),
        )
    if trial.hashes.get("config") != settings.config_hash:
        repo.latch_halt(trial.run_id, "CONFIGURATION_CHANGED")
    from crypto_bot.research.gates import code_hash

    if settings.mode is Mode.LIVE and (
        trial.hashes.get("code") != code_hash()
        or trial.hashes.get("strategy") != STRATEGY_HASH
        or not trial.hashes.get("evidence")
    ):
        repo.latch_halt(trial.run_id, "EVIDENCE_CHANGED")
    adapter = BinanceAdapter(settings, private=settings.mode in {Mode.DEMO, Mode.LIVE}, clock=clock)
    adapter.history_start_ms = trial.start_ms
    adapter.live_armed = (
        settings.mode is Mode.LIVE
        and trial.hashes.get("armed") == "true"
        and trial.hashes.get("code") == code_hash()
        and trial.hashes.get("config") == settings.config_hash
    )
    exchange = PaperBroker(repo, clock, adapter) if settings.mode is Mode.PAPER else adapter
    engine = ExecutionCoordinator(repo, exchange, clock, settings)
    ProtectionManager(engine)
    worker = BotWorker(engine)
    app = create_app(settings, repo)
    app.state.worker = worker

    @asynccontextmanager
    async def lifespan(application: Any) -> Any:
        with process_lock:
            from pathlib import Path

            from crypto_bot.ops.backup import check_disk, create_backup
            from crypto_bot.ops.logging import configure_logging

            configure_logging(settings.database.parent / "logs")

            async def maintenance() -> None:
                last_backup = 0
                while True:
                    try:
                        check_disk(settings.database.parent)
                        if clock.now_ms() - last_backup >= 86400000:
                            await asyncio.to_thread(
                                create_backup, settings.database, Path("backups")
                            )
                            last_backup = clock.now_ms()
                        repo.db.connection.execute(
                            "DELETE FROM equity_snapshots WHERE at_ms<?",
                            (clock.now_ms() - 90 * 86400000,),
                        )
                    except Exception:
                        repo.latch_halt(trial.run_id, "STORAGE_HEALTH_FAILED")
                        logging.getLogger("crypto_bot").error(
                            "Storage health failed; entries blocked"
                        )
                    await clock.sleep(60)

            tasks = [
                asyncio.create_task(worker.run()),
                asyncio.create_task(supervise_market(worker, adapter)),
            ]
            tasks.append(asyncio.create_task(maintenance()))

            async def private_events() -> None:
                while True:
                    try:
                        async for event in adapter.execution_events():
                            await worker.observe_execution(event)
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
