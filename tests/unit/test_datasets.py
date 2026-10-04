from dataclasses import replace
from decimal import Decimal as D

import pytest

from crypto_bot.domain.models import Candle, FundingEvent


def minute(index):
    return Candle(
        "SOLUSDT",
        index * 60000,
        (index + 1) * 60000,
        D("100"),
        D("101"),
        D("99"),
        D("100"),
        D("1"),
        D("100"),
    )


@pytest.mark.parametrize(
    "bars,reason",
    [
        ([minute(0), minute(2)], "GAP"),
        ([minute(0), replace(minute(0), close=D("100.1"))], "DUPLICATE"),
        ([replace(minute(0), high=D("90"))], "OHLC"),
        ([replace(minute(0), volume=D("-1"))], "volume"),
    ],
)
def test_invalid_history_reports_symbol_time_and_reason(bars, reason):
    from crypto_bot.research.datasets import validate_rows

    with pytest.raises(ValueError) as failure:
        validate_rows(bars, "SOLUSDT", 60000)
    assert "SOLUSDT" in str(failure.value)
    assert reason in str(failure.value)


def test_complete_manifest_requires_actual_funding_and_matching_marks(tmp_path):
    from crypto_bot.research.datasets import DatasetRequest, write_dataset

    request = DatasetRequest(("SOLUSDT",), 0, 180000, warmup=0)
    bars = [minute(i) for i in range(3)]
    with pytest.raises(ValueError, match="FUNDING"):
        write_dataset(request, tmp_path, {"SOLUSDT": bars}, {"SOLUSDT": bars}, {}, {})
    assert not (tmp_path / "manifest.json").exists()


def test_normalized_hash_is_reproducible_and_split_is_frozen(tmp_path):
    from crypto_bot.research.datasets import DatasetRequest, validate_dataset, write_dataset

    request = DatasetRequest(("SOLUSDT",), 0, 180000, warmup=0)
    bars = [minute(i) for i in range(3)]
    funding = {"SOLUSDT": [FundingEvent("SOLUSDT", 60000, D("0.001"), D("100"), "f1")]}
    a = write_dataset(request, tmp_path / "a", {"SOLUSDT": bars}, {"SOLUSDT": bars}, funding, {})
    b = write_dataset(request, tmp_path / "b", {"SOLUSDT": bars}, {"SOLUSDT": bars}, funding, {})
    assert a.content_hash == b.content_hash
    assert a.split_boundaries == (0, 60000, 120000, 180000)
    assert validate_dataset(a).valid
    a.files[0].path.write_bytes(b"tampered")
    assert not validate_dataset(a).valid


def test_prelisting_history_cannot_be_marked_complete(tmp_path):
    from crypto_bot.research.datasets import DatasetRequest, write_dataset

    request = DatasetRequest(("SOLUSDT",), 0, 60000, warmup=0)
    with pytest.raises(ValueError, match="LISTING"):
        write_dataset(
            request,
            tmp_path,
            {"SOLUSDT": [minute(0)]},
            {"SOLUSDT": [minute(0)]},
            {},
            {"SOLUSDT": {"onboardDate": 60000}},
        )


def test_manifest_metadata_cannot_change_without_invalidating_hash(tmp_path):
    from crypto_bot.research.datasets import DatasetRequest, validate_dataset, write_dataset

    request = DatasetRequest(("SOLUSDT",), 0, 180000, warmup=0)
    bars = [minute(i) for i in range(3)]
    funding = {"SOLUSDT": [FundingEvent("SOLUSDT", 60000, D("0.001"), D("100"), "f1")]}
    manifest = write_dataset(request, tmp_path, {"SOLUSDT": bars}, {"SOLUSDT": bars}, funding, {})
    assert not validate_dataset(replace(manifest, split_boundaries=(0, 60000, 60000, 180000))).valid
    assert not validate_dataset(replace(manifest, filters={"SOLUSDT": {"min_notional": "1"}})).valid


def test_funding_gap_cannot_be_called_complete(tmp_path):
    from crypto_bot.research.datasets import DatasetRequest, write_dataset

    request = DatasetRequest(("SOLUSDT",), 0, 24 * 3600000, warmup=0)
    bars = (minute(i) for i in range(24 * 60))
    marks = (minute(i) for i in range(24 * 60))
    events = [
        FundingEvent("SOLUSDT", at, D("0.001"), D("100"), str(at))
        for at in (0, 8 * 3600000, 20 * 3600000)
    ]
    with pytest.raises(ValueError, match="FUNDING"):
        write_dataset(
            request, tmp_path, {"SOLUSDT": bars}, {"SOLUSDT": marks}, {"SOLUSDT": events}, {}
        )
