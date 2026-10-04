from dataclasses import replace
from decimal import Decimal as D

from crypto_bot.domain.enums import OrderState, PositionPhase
from tests.fixtures.exchange import FakeExchange
from tests.fixtures.factories import context, signal
from tests.integration.test_entry_execution import engine_for


def protect(engine):
    from crypto_bot.execution.protection import ProtectionManager
    return ProtectionManager(engine)


async def test_first_partial_fill_is_protected_while_acknowledgement_is_ambiguous(repo, clock):
    exchange = FakeExchange(clock)
    engine = engine_for(repo, clock, exchange)
    protect(engine)
    exchange.accept_then_timeout(partial=True)
    await engine.process_signal(signal(), context())
    assert any(call[0] == "protection" and call[1].role == "PROVISIONAL_STOP" for call in exchange.calls)
    assert repo.position().phase is PositionPhase.PROTECTING
    assert repo.has_unresolved_intent(signal().identity)


async def test_terminal_average_entry_anchors_fixed_protection(repo, clock):
    exchange = FakeExchange(clock)
    engine = engine_for(repo, clock, exchange)
    manager = protect(engine)
    await engine.process_signal(signal(), context())
    position = repo.position()
    assert position.stop == D("98")
    assert position.target == D("104")
    assert position.phase is PositionPhase.OPEN
    before = len(exchange.calls)
    await manager.ensure_protection(position.intent_id)
    assert len(exchange.calls) == before


async def test_replacement_is_installed_before_provisional_retirement(repo, clock):
    exchange = FakeExchange(clock)
    exchange.entry_mode = "partial"
    engine = engine_for(repo, clock, exchange)
    manager = protect(engine)
    await engine.process_signal(signal(), context())
    intent = repo.intents()[0]
    exchange.orders[intent.client_id] = replace(exchange.orders[intent.client_id], state=OrderState.FILLED,
                                               cumulative_quantity=D("0.08"))
    exchange.position = replace(exchange.position, quantity=D("0.08"))
    await engine.reconcile_entries()
    roles = [call[1].role for call in exchange.calls if call[0] in {"protection", "cancel"}]
    assert roles.index("STOP") < len(roles) - 1
    assert roles[-1] == "PROVISIONAL_STOP"
    assert repo.position().phase is PositionPhase.OPEN


async def test_five_second_deadline_starts_fault_reduction(repo, clock):
    exchange = FakeExchange(clock)
    exchange.entry_mode = "partial"
    engine = engine_for(repo, clock, exchange)
    manager = protect(engine)
    await engine.process_signal(signal(), context())
    clock.advance(5000)
    await manager.ensure_protection(repo.intents()[0].logical_id)
    assert repo.current_trial().halt_reason == "PROTECTION_FAILURE"
    assert any(call[0] == "reduce" for call in exchange.calls)


async def test_rejected_stop_latches_fault_before_attempting_flatten(repo, clock):
    exchange = FakeExchange(clock)
    exchange.reject_protection = True
    engine = engine_for(repo, clock, exchange)
    protect(engine)
    exchange.exit_hook = lambda: assert_halted(repo)
    await engine.process_signal(signal(), context())
    assert repo.current_trial().halt_reason == "PROTECTION_FAILURE"
    assert repo.position() is None


def assert_halted(repo):
    assert repo.state()["state"] == "HALTED"


async def test_sdk_conditional_payload_uses_close_all_without_incompatible_parameters():
    from crypto_bot.config import Settings
    from crypto_bot.domain.enums import OrderSide
    from crypto_bot.exchange.binance_adapter import BinanceAdapter
    from crypto_bot.storage.repository import make_intent
    class Transport:
        base_url = "https://demo-fapi.binance.com"
        calls = []
        async def request(self, method, path, signed, params):
            self.calls.append((method, path, signed, params))
            return {"clientAlgoId": params["clientAlgoId"], "algoId": 1, "symbol": "SOLUSDT", "algoStatus": "NEW"}
    transport = Transport()
    adapter = BinanceAdapter(Settings(mode="DEMO"), private=True, transport=transport)
    await adapter.submit_protection(make_intent("r", "s", "STOP", 1, "SOLUSDT", OrderSide.SELL,
                                               trigger_price=D("98.01")))
    params = transport.calls[0][3]
    assert transport.calls[0][:3] == ("POST", "/fapi/v1/algoOrder", True)
    assert params["closePosition"] == "true" and params["workingType"] == "MARK_PRICE"
    assert params["triggerPrice"] == "98.01"
    assert "quantity" not in params and "reduceOnly" not in params


async def test_completed_target_and_crossed_exits_clean_siblings_without_fault_halt(repo, clock):
    exchange = FakeExchange(clock)
    engine = engine_for(repo, clock, exchange)
    manager = protect(engine)
    await engine.process_signal(signal(), context())
    position = repo.position()
    target = next(i for i in repo.intents() if i.role == "TARGET")
    exchange.position = None
    exchange.orders[target.client_id] = replace(exchange.orders[target.client_id], state=OrderState.FILLED)
    result = await manager.ensure_protection(position.intent_id)
    assert result.confirmed
    assert repo.position() is None and not repo.has_active_slot()
    assert repo.state()["state"] == "RUNNING"
    assert not any(call[0] == "reduce" for call in exchange.calls)
