import json
import sys
from dataclasses import replace
from pathlib import Path

from crypto_bot.domain.models import ApprovedSize, RiskInput, RiskPolicy


def test_documented_download_cli_freezes_supplied_brackets_and_reaches_sizing(
    tmp_path, monkeypatch, clock, repo
):
    from crypto_bot.cli import main
    from crypto_bot.exchange.normalization import normalize_rules
    from crypto_bot.research import datasets
    from crypto_bot.risk.sizing import size_entry
    from tests.fixtures.factories import context, signal

    raw = next(
        v
        for v in json.loads(Path("tests/fixtures/binance_exchange_info.json").read_text())[
            "symbols"
        ]
        if v["symbol"] == "SOLUSDT"
    )
    raw = {**raw, "onboardDate": 0}
    metadata = tmp_path / "brackets.json"
    metadata.write_text(
        json.dumps(
            {
                "environment": "DEMO",
                "observed_ms": clock.now_ms(),
                "brackets": {
                    "SOLUSDT": {"notionalCap": "100000", "maintMarginRatio": "0.004", "cum": "0"}
                },
                "fees": {"SOLUSDT": "0.0006"},
            }
        )
    )

    class Public:
        def __init__(self, *args, **kwargs):
            self.clock = clock

        async def read(self, method, **params):
            if method == "check_server_time":
                return {"serverTime": clock.now_ms()}
            if method == "exchange_information":
                return {"symbols": [raw]}
            if method in {"klines", "mark_klines"}:
                return [
                    [i * 60000, "100", "101", "99", "100", "1", (i + 1) * 60000 - 1, "5000000"]
                    for i in range(3)
                ]
            if method == "funding_history":
                return (
                    [{"fundingTime": 60000, "fundingRate": "0.0001", "markPrice": "100"}]
                    if params["startTime"] == 0
                    else []
                )
            raise AssertionError("Unexpected private or mutation request")

    monkeypatch.setattr(datasets, "BinanceAdapter", Public)
    out = tmp_path / "dataset"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "cbot",
            "data",
            "download",
            "--symbols",
            "SOLUSDT",
            "--start",
            "1970-01-01T00:00:00Z",
            "--end",
            "1970-01-01T00:03:00Z",
            "--warmup",
            "0",
            "--brackets",
            str(metadata),
            "--out",
            str(out),
        ],
    )
    main()
    manifest = datasets.load_manifest(out)
    assert datasets.validate_dataset(manifest).valid
    frozen = manifest.filters["SOLUSDT"]
    assert frozen["maintenanceProvenance"]["checksum"] == datasets.checksum(metadata)
    rules = normalize_rules(frozen, clock.now_ms(), frozen["maintenanceBracket"])
    ctx = replace(context(), rules=rules)
    assert isinstance(
        size_entry(RiskInput(repo.current_trial(), signal(), ctx, RiskPolicy())), ApprovedSize
    )
