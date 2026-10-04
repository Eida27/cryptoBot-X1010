from dataclasses import replace
from decimal import Decimal as D

from crypto_bot.domain.enums import OrderState
from tests.fixtures.exchange import FakeExchange
from tests.fixtures.factories import context, signal


def engine_for(repo, clock, exchange):
    from crypto_bot.execution.coordinator import ExecutionCoordinator
    repo.mark_reconciled(clock.now_ms(), True)
    assert repo.resume(repo.current_trial().run_id)
    return ExecutionCoordinator(repo, exchange, clock)


async def test_accepted_timeout_does_not_duplicate_entry(repo, clock):
    exchange = FakeExchange(clock)
    engine = engine_for(repo, clock, exchange)
    exchange.accept_then_timeout()
    await engine.process_signal(signal(), context())
    await engine.process_signal(signal(), context())
    await engine.reconcile_entries()
    assert exchange.entry_submission_count == 1
    assert repo.has_unresolved_intent(signal().identity)
    assert repo.has_active_slot()


async def test_zero_fill_consumes_signal_but_releases_capacity(repo, clock):
    exchange = FakeExchange(clock)
    exchange.entry_mode = "zero"
    engine = engine_for(repo, clock, exchange)
    await engine.process_signal(signal(), context())
    await engine.process_signal(signal(), context())
    assert exchange.entry_submission_count == 1
    assert not repo.has_active_slot()
    assert repo.position() is None


async def test_partial_fill_is_persisted_while_terminal_ack_is_unknown(repo, clock):
    exchange = FakeExchange(clock)
    exchange.accept_then_timeout(partial=True)
    engine = engine_for(repo, clock, exchange)
    await engine.process_signal(signal(), context())
    assert repo.position().quantity == D("0.04")
    assert repo.has_unresolved_intent(signal().identity)
    assert repo.has_active_slot()


async def test_out_of_order_cumulative_quantity_cannot_erase_fills(repo, clock):
    exchange = FakeExchange(clock)
    engine = engine_for(repo, clock, exchange)
    await engine.process_signal(signal(), context())
    intent = repo.intents()[0]
    observed = repo.order(intent.client_id)
    repo.record_order(replace(observed, cumulative_quantity=D("0.01"), state=OrderState.PARTIALLY_FILLED))
    assert repo.order(intent.client_id).cumulative_quantity == D("0.08")
    assert repo.order(intent.client_id).state is OrderState.FILLED


async def test_prepared_intent_after_crash_never_auto_resubmits(repo, clock):
    from crypto_bot.domain.models import ApprovedSize
    exchange = FakeExchange(clock)
    engine = engine_for(repo, clock, exchange)
    repo.reserve_entry(signal(), ApprovedSize(D("0.08"), D("8"), D("0.1872"), D("0.2")))
    await engine.reconcile_entries()
    assert exchange.entry_submission_count == 0
    assert repo.has_active_slot()


async def test_risk_rejection_has_no_order_intent(repo, clock):
    exchange = FakeExchange(clock)
    engine = engine_for(repo, clock, exchange)
    ctx = context(rules=replace(context().rules, min_notional=D("10")))
    result = await engine.process_signal(signal(), ctx)
    assert result.reason == "BELOW_MIN_NOTIONAL"
    assert not repo.intents()
    assert exchange.entry_submission_count == 0
