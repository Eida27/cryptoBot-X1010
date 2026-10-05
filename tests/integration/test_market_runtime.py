import asyncio
from decimal import Decimal as D

import pytest

from crypto_bot.domain.models import FundingEvent, MarkEvent
from crypto_bot.execution.worker import BotWorker
from crypto_bot.research.paper import PaperBroker
from tests.fixtures.exchange import FakeExchange
from tests.fixtures.factories import context, signal
from tests.integration.test_entry_execution import engine_for
from tests.integration.test_protection import protect


async def test_material_adverse_funding_update_reduces_owned_exposure(repo, clock):
    exchange = FakeExchange(clock)
    engine = engine_for(repo, clock, exchange)
    protect(engine)
    await engine.process_signal(signal(), context())
    worker = BotWorker(engine)
    await worker.refresh()
    worker.last_refresh = clock.now_ms()
    worker.observe_mark(
        MarkEvent("SOLUSDT", clock.now_ms(), D("100"), D("0.01"), clock.now_ms() + 28800000)
    )
    await worker.tick(clock.now_ms())
    await worker.stop()
    assert any(c[0] == "reduce" for c in exchange.calls)
    assert repo.state()["state"] == "PAUSED"


async def test_market_task_failure_disqualifies_paper_observation(repo, clock):
    from crypto_bot.app import supervise_market

    exchange = FakeExchange(clock)
    worker = BotWorker(engine_for(repo, clock, exchange))

    class Adapter:
        settings = worker.engine.settings
        clock = worker.clock

        async def read(self, *args, **kwargs):
            raise ConnectionError("warmup unavailable")

    task = asyncio.create_task(supervise_market(worker, Adapter()))
    await asyncio.sleep(0.02)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert repo.current_trial().qualification_status == "INTERRUPTED"
    assert not repo.state()["reconciled"]


def test_funding_for_other_symbol_is_not_booked(repo, clock):
    from crypto_bot.domain.enums import PositionSide
    from crypto_bot.domain.models import Position

    repo.save_position(Position("SOLUSDT", PositionSide.LONG, D(".1"), D("100"), D("1"), 1))
    PaperBroker(repo, clock).book_funding(
        FundingEvent("ETHUSDT", clock.now_ms(), D(".01"), D("100"), "eth")
    )
    assert not repo.income_events()


@pytest.mark.parametrize("gap", ["stale_mark", "silent_book", "silent_symbol"])
async def test_required_source_gap_permanently_interrupts_paper_even_with_other_streams(
    repo, clock, gap
):
    from dataclasses import replace

    from crypto_bot.domain.models import BookEvent, MarkEvent
    from crypto_bot.execution.worker import BotWorker
    from tests.fixtures.exchange import FakeExchange
    from tests.fixtures.factories import context
    from tests.integration.test_entry_execution import engine_for

    worker = BotWorker(engine_for(repo, clock, FakeExchange(clock)))
    worker.begin_market_observation({s: 3600000 for s in worker.engine.settings.symbols})
    first = clock.now_ms()
    for index in range(7):
        for symbol in worker.engine.settings.symbols:
            if gap == "silent_symbol" and symbol == "SOLUSDT" and index:
                continue
            stamp = first if gap == "stale_mark" and symbol == "SOLUSDT" else clock.now_ms()
            worker.observe_mark(MarkEvent(symbol, stamp, D("100")))
            if not (gap == "silent_book" and symbol == "SOLUSDT" and index):
                worker.observe_book(
                    BookEvent(replace(context(clock.now_ms()).market, symbol=symbol))
                )
        await worker.tick(clock.now_ms())
        if worker.refresh_task:
            await worker.refresh_task
        clock.advance(1000)
    assert repo.current_trial().qualification_status == "INTERRUPTED"
    assert not repo.state()["reconciled"]
    repo.resume(repo.current_trial().run_id)
    assert repo.current_trial().qualification_status == "INTERRUPTED"


async def test_startup_readiness_waits_for_all_required_sources(repo, clock):
    from crypto_bot.execution.worker import BotWorker
    from tests.fixtures.exchange import FakeExchange
    from tests.integration.test_entry_execution import engine_for

    worker = BotWorker(engine_for(repo, clock, FakeExchange(clock)))
    worker.begin_market_observation({s: 3600000 for s in worker.engine.settings.symbols})
    await worker.tick(clock.now_ms())
    assert repo.current_trial().qualification_status == "WARMING_UP"
    assert not repo.state()["reconciled"]
    clock.advance(11000)
    await worker.tick(clock.now_ms())
    assert repo.current_trial().qualification_status == "INTERRUPTED"


def test_required_sources_accept_bounded_clock_skew_and_reject_larger_future_times(repo, clock):
    from dataclasses import replace

    from crypto_bot.domain.models import BookEvent

    worker = BotWorker(engine_for(repo, clock, FakeExchange(clock)))
    worker.begin_market_observation({s: 3600000 for s in worker.engine.settings.symbols})
    for symbol in worker.engine.settings.symbols:
        worker.observe_mark(MarkEvent(symbol, clock.now_ms() + 500, D("100")))
        worker.observe_book(BookEvent(replace(context(clock.now_ms()).market, symbol=symbol)))
    worker.check_market_observation(clock.now_ms())
    assert repo.current_trial().qualification_status == "OBSERVING"
    worker.observe_mark(MarkEvent("SOLUSDT", clock.now_ms() + 2000, D("100")))
    worker.check_market_observation(clock.now_ms())
    assert repo.current_trial().qualification_status == "INTERRUPTED"
