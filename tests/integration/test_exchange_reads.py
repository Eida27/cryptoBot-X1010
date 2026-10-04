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
