import asyncio
from decimal import Decimal as D

from crypto_bot.config import STRATEGY_HASH
from crypto_bot.domain.enums import ExitReason, PositionPhase, PositionSide
from crypto_bot.domain.models import Candle, IndicatorState, MarkEvent
from crypto_bot.execution.reconciliation import Reconciler
from crypto_bot.execution.worker import BotWorker
from crypto_bot.market.indicators import update_indicators
from crypto_bot.research.paper import PaperBroker
from crypto_bot.research.reports import HostingCost, build_report, repository_run
from crypto_bot.strategy.breakout import evaluate_signal
from tests.fixtures.exchange import FakeExchange
from tests.fixtures.factories import candle, context, signal
from tests.integration.test_entry_execution import engine_for
from tests.integration.test_protection import protect


async def test_candle_to_paper_funding_exit_report_and_dashboard(repo, clock):
    from crypto_bot.web.views import build_dashboard_view

    state = IndicatorState()
    history = []
    for i in range(1000):
        c = candle(i, "99", high=D("99.3"), low=D("98.7"))
        history.append(c)
        state = update_indicators(state, c)
    c = candle(1000, "100", open=D("99"), high=D("100.1"), low=D("98.9"))
    state = update_indicators(state, c)
    found = evaluate_signal(history[-20:] + [c], state, STRATEGY_HASH)
    assert found is not None
    clock.at_ms = found.close_ms + 1
    broker = PaperBroker(repo, clock)
    ctx = context(clock.now_ms())
    broker.frames["SOLUSDT"] = ctx.market
    broker.marks["SOLUSDT"] = (clock.now_ms(), D("100"))
    engine = engine_for(repo, clock, broker)
    protect(engine)
    outcome = await engine.process_signal(found, ctx)
    assert outcome.status == "filled"
    assert repo.position().phase is PositionPhase.OPEN
    from crypto_bot.domain.models import FundingEvent

    funding = FundingEvent("SOLUSDT", clock.now_ms(), D("0.001"), D("100"), "settlement")
    broker.book_funding(funding)
    broker.book_funding(funding)
    assert len(repo.income_events()) == 1
    position = repo.position()
    result = await engine.request_exit(ExitReason.TARGET)
    assert result.confirmed_flat and result.cleanup_complete
    assert not repo.has_active_slot()
    recovered = await Reconciler(engine).recover(await broker.fetch_snapshot())
    assert recovered.entry_prerequisites_satisfied
    run = repository_run(repo, repo.current_trial().run_id, clock.now_ms())
    report = build_report(run, HostingCost())
    assert len(run.trades) == 1
    assert report.metrics["funding"] == -position.quantity * D("100") * D("0.001")
    assert report.metrics["net_trading_pnl"] == broker.wallet() - D("20")
    assert build_dashboard_view(repo)["position"] is None


async def test_first_fill_is_protected_even_when_snapshot_read_fails(repo, clock):
    exchange = FakeExchange(clock)
    engine = engine_for(repo, clock, exchange)
    protect(engine)

    async def unavailable():
        raise ConnectionError("temporarily unavailable")

    exchange.fetch_snapshot = unavailable
    await engine.process_signal(signal(), context())
    assert any(call[0] == "protection" for call in exchange.calls)
    assert repo.position() is not None
    assert repo.has_active_slot()


async def test_crossed_unknown_exits_have_one_transport_submission(repo, clock):
    exchange = FakeExchange(clock)
    engine = engine_for(repo, clock, exchange)
    protect(engine)
    await engine.process_signal(signal(), context())
    original = exchange.reduce_position
    exchange.reject_exit = True

    async def delayed(*args):
        await asyncio.sleep(0.01)
        return await original(*args)

    exchange.reduce_position = delayed
    await asyncio.gather(
        engine.request_exit(ExitReason.OPERATOR), engine.request_exit(ExitReason.TRIAL_LOSS)
    )
    assert sum(c[0] == "reduce" for c in exchange.calls) == 1
    assert repo.current_trial().halt_reason == "TRIAL_LOSS"


async def test_mark_equity_is_evaluated_on_each_tick_between_rest_reads(repo, clock):
    exchange = FakeExchange(clock)
    engine = engine_for(repo, clock, exchange)
    protect(engine)
    await engine.process_signal(signal(), context())
    worker = BotWorker(engine)
    await worker.refresh()
    worker.last_refresh = clock.now_ms()
    worker.observe_mark(MarkEvent("SOLUSDT", clock.now_ms(), D("70")))
    await worker.tick(clock.now_ms())
    assert repo.current_trial().halt_reason == "TRIAL_LOSS"
    await worker.stop()


async def test_unknown_entry_age_uses_request_not_trial_start(repo, clock):
    exchange = FakeExchange(clock)
    engine = engine_for(repo, clock, exchange)
    exchange.accept_then_timeout()
    await engine.process_signal(signal(), context())
    await engine.reconcile_entries()
    assert repo.current_trial().halt_reason is None


def test_unrelated_symbol_cannot_revalue_simulated_position():
    from crypto_bot.domain.models import Position
    from crypto_bot.research.simulation import SimulationBroker

    broker = SimulationBroker(D("20"))
    broker.position = Position(
        "SOLUSDT", PositionSide.LONG, D("0.1"), D("100"), D("1"), 0, D("98"), D("104")
    )
    c = Candle("BTCUSDT", 0, 60000, D("60000"), D("60001"), D("59999"), D("60000"))
    broker.advance(c, c)
    assert broker.equity[-1][1] == D("20")
