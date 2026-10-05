from dataclasses import replace
from decimal import Decimal as D

from crypto_bot.domain.enums import OrderState, PositionSide
from crypto_bot.domain.models import IncomeEvent, Position
from tests.fixtures.exchange import FakeExchange
from tests.fixtures.factories import context, signal
from tests.integration.test_entry_execution import engine_for
from tests.integration.test_protection import protect


async def recover(repo, clock, exchange, engine=None):
    from crypto_bot.execution.reconciliation import Reconciler

    engine = engine or engine_for(repo, clock, exchange)
    return await Reconciler(engine).recover(await exchange.fetch_snapshot())


async def test_lost_ack_recovers_venue_truth_without_resubmission(repo, clock):
    exchange = FakeExchange(clock)
    engine = engine_for(repo, clock, exchange)
    exchange.accept_then_timeout()
    await engine.process_signal(signal(), context())
    exchange.hidden = False
    protect(engine)
    result = await recover(repo, clock, exchange, engine)
    assert result.entry_prerequisites_satisfied
    assert repo.position().quantity == D("0.08")
    assert exchange.entry_submission_count == 1


async def test_terminal_private_update_recovers_owned_fill_absent_from_open_orders(repo, clock):
    from crypto_bot.domain.models import OrderUpdate

    exchange = FakeExchange(clock)
    engine = engine_for(repo, clock, exchange)
    exchange.accept_then_timeout()
    await engine.process_signal(signal(), context())
    entry = next(i for i in repo.intents() if i.role == "ENTRY")
    actual_first = exchange.fills[0].at_ms
    clock.advance(2000)
    repo.record_execution(
        OrderUpdate(replace(exchange.orders[entry.client_id], first_fill_ms=None))
    )
    exchange.hidden = False
    protect(engine)
    snapshot = replace(await exchange.fetch_snapshot(), ordinary_orders=())
    from crypto_bot.execution.reconciliation import Reconciler

    result = await Reconciler(engine).recover(snapshot)
    assert "UNKNOWN_POSITION" not in result.accounting_mismatches
    assert result.entry_prerequisites_satisfied
    assert repo.position().quantity == exchange.position.quantity
    assert repo.position().first_fill_ms == actual_first
    assert any(call[0] == "protection" for call in exchange.calls)
    assert exchange.entry_submission_count == 1


async def test_private_fill_gets_provisional_stop_before_unavailable_rest(repo, clock):
    from crypto_bot.domain.models import OrderUpdate
    from crypto_bot.execution.worker import BotWorker

    exchange = FakeExchange(clock)
    engine = engine_for(repo, clock, exchange)
    exchange.accept_then_timeout()
    await engine.process_signal(signal(), context())
    entry = next(i for i in repo.intents() if i.role == "ENTRY")
    exchange.hidden = False
    protect(engine)

    async def unavailable():
        raise ConnectionError("REST temporarily unavailable")

    exchange.fetch_snapshot = unavailable
    worker = BotWorker(engine)
    await worker.observe_execution(OrderUpdate(exchange.orders[entry.client_id]))
    assert repo.position().quantity == D("0.08")
    assert any(c[0] == "protection" and c[1].role == "PROVISIONAL_STOP" for c in exchange.calls)
    assert not repo.state()["reconciled"]


async def test_restart_preserves_original_floor_and_loss_halt(repo, clock):
    repo.latch_halt(repo.current_trial().run_id, "TRIAL_LOSS")
    exchange = FakeExchange(clock)
    from crypto_bot.execution.coordinator import ExecutionCoordinator

    engine = ExecutionCoordinator(repo, exchange, clock)
    await recover(repo, clock, exchange, engine)
    assert repo.current_trial().floor == D("18")
    assert not repo.resume(repo.current_trial().run_id)


async def test_duplicate_funding_is_booked_once_and_reconciled_in_wallet(repo, clock):
    exchange = FakeExchange(clock)
    event = IncomeEvent(
        "PAPER", "virtual", "SOLUSDT", "fund1", "FUNDING_FEE", D("-0.01"), "USDT", clock.now_ms()
    )
    exchange.income = [event, event]
    exchange.wallet -= D("0.01")
    result = await recover(repo, clock, exchange)
    assert result.entry_prerequisites_satisfied
    assert repo.count_income_events() == 1
    assert repo.current_trial().baseline == D("20")


async def test_external_deposit_cannot_mask_losses_or_change_baseline(repo, clock):
    exchange = FakeExchange(clock)
    exchange.wallet = D("200")
    exchange.income = [
        IncomeEvent("PAPER", "virtual", "", "deposit", "TRANSFER", D("180"), "USDT", clock.now_ms())
    ]
    result = await recover(repo, clock, exchange)
    assert not result.entry_prerequisites_satisfied
    assert repo.current_trial().baseline == D("20")
    assert repo.current_trial().floor == D("18")


async def test_unknown_position_is_never_adopted_or_liquidated(repo, clock):
    exchange = FakeExchange(clock)
    exchange.position = Position("ETHUSDT", PositionSide.LONG, D("1"), D("2000"), D("20"), 0)
    result = await recover(repo, clock, exchange)
    assert "UNKNOWN_POSITION" in result.accounting_mismatches
    assert repo.position() is None
    assert not any(call[0] == "reduce" for call in exchange.calls)


async def test_unsupported_commission_asset_blocks_entries(repo, clock):
    exchange = FakeExchange(clock)
    engine = engine_for(repo, clock, exchange)
    await engine.process_signal(signal(), context())
    exchange.fills[0] = replace(exchange.fills[0], commission_asset="BNB")
    result = await recover(repo, clock, exchange, engine)
    assert "UNSUPPORTED_FEE_ASSET" in result.accounting_mismatches


async def test_filled_target_cleans_stale_open_position_on_restart(repo, clock):
    exchange = FakeExchange(clock)
    engine = engine_for(repo, clock, exchange)
    protect(engine)
    await engine.process_signal(signal(), context())
    target = next(i for i in repo.intents() if i.role == "TARGET")
    exchange.orders[target.client_id] = replace(
        exchange.orders[target.client_id], state=OrderState.FILLED
    )
    exchange.position = None
    result = await recover(repo, clock, exchange, engine)
    assert repo.position() is None
    assert not repo.has_active_slot()
    assert not result.unresolved_orders


async def test_sqlite_failure_prevents_entries_and_preserves_protection(repo, clock):
    from crypto_bot.execution.reconciliation import Reconciler

    exchange = FakeExchange(clock)
    engine = engine_for(repo, clock, exchange)
    protect(engine)
    await engine.process_signal(signal(), context())
    reconciler = Reconciler(engine, emergency_log=repo.db.path.parent / "emergency.log")
    await reconciler.recover(await exchange.fetch_snapshot())
    repo.db.connection.execute("PRAGMA query_only=ON")
    result = await reconciler.recover(await exchange.fetch_snapshot())
    repo.db.connection.execute("PRAGMA query_only=OFF")
    assert not result.entry_prerequisites_satisfied
    assert "STORAGE_UNAVAILABLE" in result.accounting_mismatches
    assert (repo.db.path.parent / "emergency.log").exists()
