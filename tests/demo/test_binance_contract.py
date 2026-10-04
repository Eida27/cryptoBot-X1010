"""Explicitly authorized DEMO lifecycle only; never part of normal verification."""

import asyncio
import os
from decimal import Decimal as D
from pathlib import Path

import pytest

from crypto_bot.config import DEMO_REST, STRATEGY_HASH, load_settings
from crypto_bot.domain.clock import SystemClock
from crypto_bot.domain.enums import ExitReason, Mode, OrderState, PositionSide
from crypto_bot.domain.models import Signal
from crypto_bot.exchange.binance_adapter import BinanceAdapter
from crypto_bot.execution.coordinator import ExecutionCoordinator
from crypto_bot.execution.protection import ProtectionManager
from crypto_bot.execution.reconciliation import Reconciler
from crypto_bot.market.service import MarketService
from crypto_bot.risk.trial import validate_allocation
from crypto_bot.storage.database import Database
from crypto_bot.storage.repository import Repository, encode

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_BINANCE_DEMO_TESTS") != "1",
    reason="Requires separate explicit DEMO order authorization and local credentials",
)


async def test_real_demo_entry_protection_restart_and_reduce_only_cleanup(tmp_path):
    if not os.environ.get("CBOT_DEMO_API_KEY") or not os.environ.get("CBOT_DEMO_API_SECRET"):
        pytest.skip("Separate DEMO credentials not supplied")
    settings = load_settings(
        Path("config/demo.toml"),
        {k: v for k, v in os.environ.items() if k.startswith("CBOT_DEMO_")},
    )
    assert settings.mode is Mode.DEMO and settings.rest_url == DEMO_REST
    adapter = BinanceAdapter(settings, private=True)
    assert adapter.transport.base_url == DEMO_REST and adapter.transport.configuration.retries == 0
    clock = SystemClock()
    repo = Repository(Database(tmp_path / "demo.sqlite3"))
    trial = repo.create_run(
        Mode.DEMO, settings.initial_capital_usdt, {"config": settings.config_hash}, clock.now_ms()
    )
    adapter.history_start_ms = trial.start_ms
    engine = ExecutionCoordinator(repo, adapter, clock, settings)
    manager = ProtectionManager(engine)
    try:
        snapshot = await adapter.fetch_snapshot()
        validate_allocation(settings.initial_capital_usdt, snapshot.account.wallet_balance)
        assert not snapshot.positions and not snapshot.ordinary_orders and not snapshot.algo_orders
        # Read public streams twice to exercise separate connections; no synthetic stream proof.
        for _ in range(2):
            events = adapter.market_events(("SOLUSDT",))
            async with asyncio.timeout(30):
                await anext(events)
            await events.aclose()
        premium = await adapter.read("mark_price", symbol="SOLUSDT")
        signal = Signal(
            "demo-contract:" + str(clock.now_ms()),
            STRATEGY_HASH,
            "SOLUSDT",
            clock.now_ms() - 1,
            PositionSide.LONG,
            D(premium["markPrice"]),
            D(premium["markPrice"]) * D("0.005"),
        )
        ctx = await MarketService(adapter, adapter).build_context(signal)
        repo.mark_reconciled(clock.now_ms(), True)
        assert repo.resume(trial.run_id)
        result = await engine.process_signal(signal, ctx)
        if result.status == "skipped":
            pytest.skip("Bounded declared allocation not currently feasible: " + str(result.reason))
        assert result.intent_id
        position = repo.position()
        assert position is not None, "A zero fill cannot establish conditional lifecycle evidence"
        assert (await manager.ensure_protection(result.intent_id)).confirmed
        protective = [i for i in repo.intents() if i.role in {"STOP", "TARGET"}]
        assert len(protective) == 2
        for intent in protective:
            order = await adapter.find_order(intent)
            assert order and order.namespace == "algo" and order.state is OrderState.ACKNOWLEDGED
        restarted = ExecutionCoordinator(repo, adapter, clock, settings)
        ProtectionManager(restarted)
        recovered = await Reconciler(restarted).recover(await adapter.fetch_snapshot())
        assert not recovered.accounting_mismatches
        exit_result = await restarted.request_exit(ExitReason.OPERATOR)
        for _ in range(10):
            if exit_result.confirmed_flat and exit_result.cleanup_complete:
                break
            await asyncio.sleep(1)
            exit_result = await restarted.request_exit(ExitReason.OPERATOR)
        assert exit_result.confirmed_flat and exit_result.cleanup_complete
        snapshot = await adapter.fetch_snapshot()
        assert not snapshot.positions and not snapshot.ordinary_orders and not snapshot.algo_orders
        artifact = Path("reports/demo/contract.json")
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text(
            encode(
                {
                    "run_id": trial.run_id,
                    "ordinary_lifecycle": True,
                    "conditional_lifecycle": True,
                    "close_semantics": True,
                    "sibling_cleanup": True,
                    "reconnect": True,
                    "mutation_retry_suppression": True,
                    "partial_zero_fill": False,
                    "limitations": "Partial/zero fill outcome not forced; G4 still requires observed coverage",
                }
            ),
            encoding="utf-8",
        )
    finally:
        if repo.position() or repo.has_active_slot():
            await engine.request_exit(ExitReason.OPERATOR)
        adapter.transport.close()
        repo.db.close()
