from decimal import Decimal as D

from crypto_bot.config import Settings
from crypto_bot.domain.models import FundingEvent
from tests.unit.test_datasets import minute


def test_backtest_replays_same_hash_to_identical_ledgers(tmp_path):
    from crypto_bot.research.datasets import DatasetRequest, write_dataset
    from crypto_bot.research.simulation import run_backtest
    bars = [minute(i) for i in range(3)]
    manifest = write_dataset(DatasetRequest(("SOLUSDT",), 0, 180000, 0), tmp_path,
                             {"SOLUSDT": bars}, {"SOLUSDT": bars},
                             {"SOLUSDT": [FundingEvent("SOLUSDT", 60000, D("0.001"), D("100"), "f1")]}, {})
    a = run_backtest(manifest, Settings(mode="BACKTEST"))
    b = run_backtest(manifest, Settings(mode="BACKTEST"))
    assert a == b
    assert a.trades == ()
    assert "INSUFFICIENT_WARMUP" in a.failed_assumptions
