import pytest

from crypto_bot.config import ConfigurationError, Settings


def test_paper_cannot_create_private_client():
    from crypto_bot.exchange.binance_adapter import BinanceAdapter

    with pytest.raises(ConfigurationError, match="private"):
        BinanceAdapter(Settings(), private=True)


def test_demo_client_uses_demo_host_and_disables_sdk_retries():
    from crypto_bot.exchange.binance_adapter import BinanceAdapter

    adapter = BinanceAdapter(Settings(mode="DEMO"), private=True)
    assert adapter.transport.base_url == "https://demo-fapi.binance.com"
    assert adapter.transport.configuration.retries == 0
    assert adapter.transport.configuration.api_key is None


async def test_read_backoff_does_not_refresh_source_timestamp(clock):
    from crypto_bot.exchange.binance_adapter import BinanceAdapter

    class Transport:
        base_url = "https://fapi.binance.com"
        calls = 0

        async def read(self, method, **params):
            self.calls += 1
            if self.calls < 3:
                raise ConnectionError("temporary")
            return {"serverTime": 3600000}

    transport = Transport()
    adapter = BinanceAdapter(Settings(), transport=transport, clock=clock)
    assert await adapter.read("check_server_time") == {"serverTime": 3600000}
    assert transport.calls == 3
    assert clock.now_ms() > 3600001


def test_injected_transport_cannot_cross_demo_boundary():
    from crypto_bot.exchange.binance_adapter import BinanceAdapter

    class WrongTransport:
        base_url = "https://fapi.binance.com"

    with pytest.raises(ConfigurationError):
        BinanceAdapter(Settings(mode="DEMO"), private=True, transport=WrongTransport())


async def test_pinned_sdk_raw_transport_keeps_decimal_strings(monkeypatch):
    import requests

    from crypto_bot.exchange.binance_adapter import BinanceAdapter

    response = requests.Response()
    response.status_code = 200
    response._content = b'{"markPrice":"100.1234567890123456789012345678901"}'
    response.headers["Content-Type"] = "application/json"
    observed = []

    def request(session, **kwargs):
        observed.append(kwargs)
        return response

    monkeypatch.setattr(requests.Session, "request", request)
    adapter = BinanceAdapter(Settings())
    value = await adapter.read("mark_price", symbol="SOLUSDT")
    assert value["markPrice"] == "100.1234567890123456789012345678901"
    assert observed[0]["url"] == "https://fapi.binance.com/fapi/v1/premiumIndex"
    adapter.transport.close()


async def test_private_sdk_brackets_preserve_exact_json_numbers(monkeypatch):
    import json
    from decimal import Decimal as D
    from pathlib import Path

    import requests

    from crypto_bot.exchange.binance_adapter import BinanceAdapter

    information = Path("tests/fixtures/binance_exchange_info.json").read_bytes()
    bracket = (
        b'[{"symbol":"SOLUSDT","brackets":[{"bracket":1,"initialLeverage":100,'
        b'"notionalCap":50000,"notionalFloor":0,'
        b'"maintMarginRatio":0.005000000000000000000000000000001,"cum":0.0}]}]'
    )

    def request(session, **kwargs):
        assert kwargs["method"] == "GET"
        response = requests.Response()
        response.status_code = 200
        response.headers["Content-Type"] = "application/json"
        response._content = information if kwargs["url"].endswith("/exchangeInfo") else bracket
        return response

    monkeypatch.setattr(requests.Session, "request", request)
    adapter = BinanceAdapter(
        Settings(mode="DEMO", api_key="fixture-key", api_secret="fixture-secret"), private=True
    )
    try:
        rules = await adapter.fetch_rules("SOLUSDT")
        assert rules.maintenance_rate == D("0.005000000000000000000000000000001")
        assert rules.maintenance_deduction == D("0.0")
        assert rules.bracket_limit == D("50000")
        raw = await adapter.read("brackets", symbol="SOLUSDT")
        assert raw[0]["brackets"][0]["initialLeverage"] == 100
        # The preserved metadata must remain serializable as an exact frozen export.
        from crypto_bot.storage.repository import encode

        assert json.loads(encode(raw))[0]["brackets"][0]["maintMarginRatio"] == (
            "0.005000000000000000000000000000001"
        )
    finally:
        adapter.transport.close()


async def test_paper_uses_expiring_verified_bracket_export_without_private_calls(tmp_path, clock):
    import json
    from pathlib import Path

    from crypto_bot.exchange.binance_adapter import BinanceAdapter

    metadata = tmp_path / "brackets.json"
    metadata.write_text(
        json.dumps(
            {
                "observed_ms": clock.now_ms(),
                "environment": "DEMO",
                "fees": {"SOLUSDT": "0.0006"},
                "brackets": {
                    "SOLUSDT": {"notionalCap": "100000", "maintMarginRatio": "0.004", "cum": "0"}
                },
            }
        )
    )

    class Transport:
        base_url = "https://fapi.binance.com"

        async def read(self, method, **params):
            assert method == "exchange_information"
            return json.loads(Path("tests/fixtures/binance_exchange_info.json").read_text())

    adapter = BinanceAdapter(
        Settings(bracket_metadata=metadata), transport=Transport(), clock=clock
    )
    assert (await adapter.fetch_rules("SOLUSDT")).maintenance_rate == __import__("decimal").Decimal(
        "0.004"
    )
    clock.advance(21600001)
    with pytest.raises(ConfigurationError, match="six hours"):
        await adapter.fetch_rules("SOLUSDT")


@pytest.mark.parametrize("jitter_ms", [0, 16])
async def test_flat_v3_account_verifies_settings_from_symbol_config(clock, jitter_ms):
    from decimal import Decimal as D

    from crypto_bot.domain.models import ExchangeSnapshot
    from crypto_bot.market.service import MarketService
    from tests.fixtures.factories import context, signal

    clock.at_ms = 8 * 86400000
    ctx = context(clock.now_ms())

    class Adapter:
        private = True
        settings = Settings(mode="DEMO")
        heartbeat_ms = clock.now_ms()

        async def fetch_rules(self, symbol):
            return ctx.rules

        async def fetch_snapshot(self):
            return ExchangeSnapshot(clock.now_ms(), ctx.account)

        async def read(self, method, **params):
            return {
                "order_book": {"bids": [["99.99", "1"]], "asks": [["100", "1"]]},
                "mark_price": {
                    "markPrice": "100",
                    "lastFundingRate": "0.0001",
                    "nextFundingTime": clock.now_ms() + 28800000,
                },
                "ticker24hr": {"quoteVolume": "200000000"},
                "funding_history": [
                    {
                        "fundingTime": clock.now_ms() - i * 28800000 + (jitter_ms if i % 2 else 0),
                        "fundingRate": "0.0001",
                    }
                    for i in reversed(range(21))
                ],
                "funding_info": [],
                "position_mode": {"dualSidePosition": False},
                "asset_mode": {"multiAssetsMargin": False},
                "fee_burn": {"feeBurn": False},
                "account": {"positions": []},
                "positions": [],
                "symbol_config": [
                    {
                        "symbol": "SOLUSDT",
                        "marginType": "ISOLATED",
                        "isAutoAddMargin": "false",
                        "leverage": 2,
                    }
                ],
                "commission": {"takerCommissionRate": "0.0006"},
            }[method]

    adapter = Adapter()
    adapter.clock = clock
    result = await MarketService(adapter, adapter).build_context(signal(clock.now_ms() - 1))
    assert result.settings.isolated and result.settings.leverage == D("2")
    assert result.funding.interval_hours == D("8")
