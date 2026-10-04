import asyncio
from decimal import Decimal as D

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
