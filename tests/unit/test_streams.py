import asyncio
from types import SimpleNamespace

from crypto_bot.config import Settings
from crypto_bot.domain.clock import FakeClock
from crypto_bot.domain.models import OrderUpdate


async def test_depth_and_regular_streams_use_current_categories(monkeypatch):
    from crypto_bot.exchange import streams

    calls = []

    class Client:
        async def create_connection(self):
            pass

        async def subscribe(self, values, stream_url):
            calls.append((values[0], stream_url))

        def on(self, event, callback, stream):
            if event == "message":
                callback({"data": {"e": "markPriceUpdate"}})

        async def close_connection(self, close_session):
            pass

    monkeypatch.setattr(
        streams,
        "DerivativesTradingUsdsFutures",
        lambda **kwargs: SimpleNamespace(websocket_streams=Client()),
    )
    adapter = SimpleNamespace(settings=Settings(), clock=FakeClock(1))
    iterator = streams.raw_stream(adapter, ("solusdt@depth20@100ms", "solusdt@markPrice@1s"))
    await anext(iterator)
    await iterator.aclose()
    assert calls == [("solusdt@depth20@100ms", "public"), ("solusdt@markPrice@1s", "market")]


async def test_combined_private_events_are_unwrapped_before_normalization(monkeypatch):
    from crypto_bot.exchange import streams

    async def raw(*args, **kwargs):
        yield {
            "stream": "private-key",
            "data": {
                "e": "ORDER_TRADE_UPDATE",
                "E": 10,
                "o": {
                    "c": "owned",
                    "i": 1,
                    "s": "SOLUSDT",
                    "X": "NEW",
                    "z": "0",
                    "ap": "0",
                    "x": "NEW",
                },
            },
        }

    class Transport:
        async def request(self, method, *args):
            return {"listenKey": "private-key"}

    monkeypatch.setattr(streams, "raw_stream", raw)
    adapter = SimpleNamespace(private=True, transport=Transport(), clock=FakeClock(1))
    iterator = streams.execution_stream(adapter)
    result = await asyncio.wait_for(anext(iterator), 0.1)
    await iterator.aclose()
    assert isinstance(result, OrderUpdate)
    assert result.observation.client_id == "owned"
