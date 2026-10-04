from dataclasses import replace
from decimal import Decimal as D

from crypto_bot.domain.enums import ExitReason
from tests.fixtures.exchange import FakeExchange
from tests.fixtures.factories import context, signal
from tests.integration.test_entry_execution import engine_for
from tests.integration.test_protection import protect


async def setup(repo, clock):
    exchange = FakeExchange(clock)
    engine = engine_for(repo, clock, exchange)
    protect(engine)
    await engine.process_signal(signal(), context())
    return engine, exchange


async def test_loss_halt_commits_before_exit_and_survives_failed_exit(repo, clock):
    engine, exchange = await setup(repo, clock)
    exchange.reject_exit = True
    exchange.exit_hook = lambda: assert_loss_halted(repo)
    result = await engine.request_exit(ExitReason.TRIAL_LOSS)
    assert not result.confirmed_flat
    assert repo.position().quantity == D("0.08")
    assert not any(call[0] == "cancel" and call[1].role == "STOP" for call in exchange.calls)


def assert_loss_halted(repo):
    assert repo.current_trial().halt_reason == "TRIAL_LOSS"


async def test_operator_close_pauses_and_residual_cancel_failure_keeps_capacity(repo, clock):
    engine, exchange = await setup(repo, clock)
    exchange.reject_cancel = True
    result = await engine.request_exit(ExitReason.OPERATOR)
    assert result.confirmed_flat
    assert not result.cleanup_complete
    assert repo.has_active_slot()
    assert repo.state()["state"] == "PAUSED"


async def test_time_exit_uses_original_first_fill_after_restart(repo, clock):
    engine, exchange = await setup(repo, clock)
    first = repo.position().first_fill_ms
    from crypto_bot.execution.coordinator import ExecutionCoordinator
    restarted = ExecutionCoordinator(repo, exchange, clock)
    protect(restarted)
    clock.at_ms = first + 48 * 3600000
    await restarted.protection.check_deadline()
    assert repo.position() is None
    assert not repo.has_active_slot()
    assert repo.state()["state"] == "RUNNING"


async def test_closing_dust_is_not_rejected_by_opening_minima(repo, clock):
    engine, exchange = await setup(repo, clock)
    exchange.position = replace(exchange.position, quantity=D("0.001"))
    repo.save_position(replace(repo.position(), quantity=D("0.001")))
    result = await engine.request_exit(ExitReason.OPERATOR)
    assert result.confirmed_flat
    assert exchange.position is None
