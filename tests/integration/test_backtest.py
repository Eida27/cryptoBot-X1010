from decimal import Decimal as D

import pytest

from crypto_bot.config import Settings
from crypto_bot.domain.models import FundingEvent
from tests.unit.test_datasets import minute


def test_backtest_replays_same_hash_to_identical_ledgers(tmp_path):
    from crypto_bot.research.datasets import DatasetRequest, write_dataset
    from crypto_bot.research.simulation import run_backtest

    bars = [minute(i) for i in range(3)]
    manifest = write_dataset(
        DatasetRequest(("SOLUSDT",), 0, 180000, 0),
        tmp_path,
        {"SOLUSDT": bars},
        {"SOLUSDT": bars},
        {"SOLUSDT": [FundingEvent("SOLUSDT", 60000, D("0.001"), D("100"), "f1")]},
        {},
    )
    a = run_backtest(manifest, Settings(mode="BACKTEST"))
    b = run_backtest(manifest, Settings(mode="BACKTEST"))
    assert a == b
    from crypto_bot.research.gates import code_hash

    assert a.hashes["code"] == code_hash()
    assert a.trades == ()
    assert "INSUFFICIENT_WARMUP" in a.failed_assumptions


@pytest.mark.parametrize("jitter_ms", [0, 16])
def test_hourly_candidates_rank_by_quote_volume_before_reserving(tmp_path, monkeypatch, jitter_ms):
    import json
    from dataclasses import replace
    from pathlib import Path

    from crypto_bot.domain.enums import PositionSide
    from crypto_bot.domain.models import Signal
    from crypto_bot.research import simulation
    from crypto_bot.research.datasets import DatasetRequest, write_dataset

    symbols = ("BTCUSDT", "SOLUSDT")
    trades = {
        s: [
            replace(
                minute(i), symbol=s, quote_volume=D("5000000") if s == "SOLUSDT" else D("3000000")
            )
            for i in range(120)
        ]
        for s in symbols
    }
    funding = {
        s: [
            FundingEvent(s, at, D("0.0001"), D("100"), f"{s}:{at}")
            for at in (-16 * 3600000, -8 * 3600000 + jitter_ms, 0)
        ]
        for s in symbols
    }
    raw = next(
        v
        for v in json.loads(Path("tests/fixtures/binance_exchange_info.json").read_text())[
            "symbols"
        ]
        if v["symbol"] == "SOLUSDT"
    )
    filters = {
        s: {
            **raw,
            "symbol": s,
            "onboardDate": 0,
            "maintenanceBracket": {
                "notionalCap": "100000",
                "maintMarginRatio": "0.004",
                "cum": "0",
            },
        }
        for s in symbols
    }
    manifest = write_dataset(
        DatasetRequest(symbols, 0, 120 * 60000, 0), tmp_path, trades, trades, funding, filters
    )
    monkeypatch.setattr(
        simulation,
        "evaluate_signal",
        lambda bars, state, version: Signal(
            f"{bars[-1].symbol}:{bars[-1].close_ms}",
            version,
            bars[-1].symbol,
            bars[-1].close_ms,
            PositionSide.LONG,
            D("100"),
            D("1"),
        ),
    )
    prepared = []
    reserves = []
    original_size = simulation.size_entry

    def capture_size(value):
        from crypto_bot.risk.sizing import adverse_funding_reserve

        reserves.append(adverse_funding_reserve(value.context.funding))
        return original_size(value)

    monkeypatch.setattr(simulation, "size_entry", capture_size)
    original = simulation.SimulationBroker.prepare_entry

    def capture(self, intent, *args):
        prepared.append(intent.symbol)
        return original(self, intent, *args)

    monkeypatch.setattr(simulation.SimulationBroker, "prepare_entry", capture)
    simulation.run_backtest(manifest, Settings(mode="BACKTEST"))
    assert prepared[0] == "SOLUSDT"
    assert reserves and all(reserve == D("0.0007") for reserve in reserves)
