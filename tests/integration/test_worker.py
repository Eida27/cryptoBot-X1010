from dataclasses import replace
from decimal import Decimal as D

from crypto_bot.domain.models import ControlCommand
from tests.fixtures.exchange import FakeExchange
from tests.fixtures.factories import context, signal
from tests.integration.test_entry_execution import engine_for
from tests.integration.test_protection import protect


async def test_pause_stops_entries_but_one_second_loss_checks_continue(repo, clock):
    from crypto_bot.execution.worker import BotWorker

    exchange = FakeExchange(clock)
    engine = engine_for(repo, clock, exchange)
    protect(engine)
    await engine.process_signal(signal(), context())
    worker = BotWorker(engine)
    await worker.handle_command(ControlCommand("pause", "pause", "operator", clock.now_ms()))
    assert repo.state()["state"] == "PAUSED"
    exchange.wallet = D("17.9")
    worker.account = (await exchange.fetch_snapshot()).account
    await worker.tick(clock.now_ms())
    assert repo.current_trial().halt_reason == "TRIAL_LOSS"
    assert any(call[0] == "reduce" for call in exchange.calls)


async def test_refresh_cadence_is_five_seconds_exposed_and_thirty_flat(repo, clock):
    from crypto_bot.execution.worker import BotWorker

    exchange = FakeExchange(clock)
    calls = []
    original = exchange.fetch_snapshot

    async def snapshot():
        calls.append(clock.now_ms())
        return await original()

    exchange.fetch_snapshot = snapshot
    worker = BotWorker(engine_for(repo, clock, exchange))
    await worker.tick(clock.now_ms())
    for _ in range(29):
        clock.advance(1000)
        await worker.tick(clock.now_ms())
    assert len(calls) == 1
    clock.advance(1000)
    await worker.tick(clock.now_ms())
    assert len(calls) == 2


async def test_loss_halt_cannot_be_cleared_by_queued_resume(repo, clock):
    from crypto_bot.execution.worker import BotWorker

    exchange = FakeExchange(clock)
    worker = BotWorker(engine_for(repo, clock, exchange))
    repo.latch_halt(repo.current_trial().run_id, "TRIAL_LOSS")
    repo.mark_reconciled(clock.now_ms(), True)
    result = await worker.handle_command(
        ControlCommand("resume", "resume", "operator", clock.now_ms())
    )
    assert not result.accepted
    assert repo.current_trial().halt_reason == "TRIAL_LOSS"


async def test_signal_candidates_rank_by_volume_then_name(repo, clock):
    from crypto_bot.execution.worker import rank_candidates

    a, b = context(), context()
    a = replace(a, market=replace(a.market, symbol="BTCUSDT", quote_volume_24h=D("100000000")))
    b = replace(b, market=replace(b.market, symbol="ETHUSDT", quote_volume_24h=D("200000000")))
    ranked = rank_candidates(
        [(replace(signal(), symbol="BTCUSDT"), a), (replace(signal(), symbol="ETHUSDT"), b)]
    )
    assert [candidate[0].symbol for candidate in ranked] == ["ETHUSDT", "BTCUSDT"]


def test_second_service_cannot_mutate_state_before_ownership_lock(tmp_path):
    import pytest

    from crypto_bot.app import build_service
    from crypto_bot.config import Settings

    settings = Settings(database=tmp_path / "paper.sqlite3")
    first = build_service(settings)
    second = None
    try:
        with pytest.raises(RuntimeError, match="owned"):
            second = build_service(Settings(database=settings.database, initial_capital_usdt="20"))
        assert first.state.worker.repo.current_trial().halt_reason is None
    finally:
        for app in (first, second):
            if app:
                app.state.worker.exchange.market_adapter.transport.close()
                app.state.worker.repo.db.close()
                if hasattr(app.state, "process_lock"):
                    app.state.process_lock.__exit__(None, None, None)
