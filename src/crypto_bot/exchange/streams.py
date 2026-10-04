import asyncio
from collections.abc import AsyncIterator
from decimal import Decimal as D
from typing import Any

from binance_common.configuration import ConfigurationWebSocketStreams
from binance_sdk_derivatives_trading_usds_futures.derivatives_trading_usds_futures import (
    DerivativesTradingUsdsFutures,
)

from crypto_bot.config import ConfigurationError
from crypto_bot.domain.enums import OrderSide
from crypto_bot.domain.models import (
    BookEvent,
    BookLevel,
    CandleEvent,
    ExecutionEvent,
    FillEvent,
    MarketEvent,
    MarketFrame,
    MarkEvent,
    OrderUpdate,
    StreamGapEvent,
)
from crypto_bot.exchange.normalization import normalize_candle, normalize_order


async def raw_stream(
    adapter: Any, streams: tuple[str, ...], private: bool = False
) -> AsyncIterator[dict[str, Any]]:
    base = (
        "wss://demo-fstream.binance.com"
        if adapter.settings.mode.value == "DEMO"
        else "wss://fstream.binance.com"
    )
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1024)
    configuration = ConfigurationWebSocketStreams(stream_url=base, reconnect_delay=1000)
    client = DerivativesTradingUsdsFutures(config_ws_streams=configuration).websocket_streams

    def received(value: Any) -> None:
        if queue.full():
            queue.get_nowait()
            queue.put_nowait({"gap": "STREAM_BACKLOG"})
        else:
            queue.put_nowait(value if isinstance(value, dict) else {"gap": "STREAM_SCHEMA"})

    try:
        await client.create_connection()
        for stream in streams:
            category = "private" if private else "public" if "@depth" in stream else "market"
            await client.subscribe([stream], stream_url=category)
            client.on("message", received, stream)
        while True:
            try:
                yield await asyncio.wait_for(queue.get(), timeout=10)
            except TimeoutError:
                yield {"gap": "STREAM_HEARTBEAT_TIMEOUT"}
                break
    finally:
        await client.close_connection(close_session=True)


async def market_stream(adapter: Any, symbols: tuple[str, ...]) -> AsyncIterator[MarketEvent]:
    streams = tuple(
        f"{symbol.lower()}{suffix}"
        for symbol in symbols
        for suffix in ("@markPrice@1s", "@depth20@100ms", "@kline_1h")
    )
    while True:
        try:
            async for value in raw_stream(adapter, streams):
                at_ms = adapter.clock.now_ms()
                if "gap" in value:
                    yield StreamGapEvent(at_ms, value["gap"])
                    continue
                value = value.get("data", value)
                adapter.heartbeat_ms = min(at_ms, int(value.get("E", at_ms)))
                event = value.get("e")
                if event == "markPriceUpdate":
                    yield MarkEvent(
                        value["s"], int(value["E"]), D(value["p"]), D(value["r"]), int(value["T"])
                    )
                elif event == "kline":
                    k = value["k"]
                    candle = normalize_candle(
                        value["s"],
                        [k["t"], k["o"], k["h"], k["l"], k["c"], k["v"], k["T"], k["q"]],
                        int(value["E"]),
                    )
                    if k["x"] and candle.closed:
                        yield CandleEvent(candle)
                elif event == "depthUpdate":
                    bids = tuple(BookLevel(D(p), D(q)) for p, q in value["b"])
                    asks = tuple(BookLevel(D(p), D(q)) for p, q in value["a"])
                    if bids and asks:
                        yield BookEvent(
                            MarketFrame(
                                value["s"],
                                at_ms,
                                bids[0].price,
                                asks[0].price,
                                (bids[0].price + asks[0].price) / 2,
                                bids,
                                asks,
                                D("0"),
                                {
                                    "quote": min(at_ms, int(value["E"])),
                                    "heartbeat": adapter.heartbeat_ms,
                                },
                            )
                        )
        except asyncio.CancelledError:
            raise
        except Exception:
            yield StreamGapEvent(adapter.clock.now_ms(), "MARKET_STREAM_DISCONNECTED")
        await adapter.clock.sleep(2)


async def execution_stream(adapter: Any) -> AsyncIterator[ExecutionEvent]:
    if not adapter.private:
        raise ConfigurationError("Public PAPER adapter cannot start private streams")
    while True:
        key = await adapter.transport.request("POST", "/fapi/v1/listenKey", False, {})
        listen_key = key["listenKey"]

        async def renew() -> None:
            while True:
                await adapter.clock.sleep(30 * 60)
                await adapter.transport.request(
                    "PUT", "/fapi/v1/listenKey", False, {"listenKey": listen_key}
                )

        renewal = asyncio.create_task(renew())
        try:
            async for value in raw_stream(adapter, (listen_key,), private=True):
                value = value.get("data", value)
                if "gap" in value or renewal.done():
                    raise ConnectionError("Private stream requires snapshot reconciliation")
                if value.get("e") == "ORDER_TRADE_UPDATE":
                    order = value["o"]
                    yield OrderUpdate(
                        normalize_order(
                            {
                                "clientOrderId": order["c"],
                                "orderId": order["i"],
                                "symbol": order["s"],
                                "status": order["X"],
                                "executedQty": order["z"],
                                "avgPrice": order["ap"],
                            },
                            int(value["E"]),
                        )
                    )
                    if order["x"] == "TRADE":
                        yield FillEvent(
                            adapter.settings.mode.value,
                            adapter.account_id,
                            order["s"],
                            str(order["t"]),
                            str(order["i"]),
                            D(order["L"]),
                            D(order["l"]),
                            D(order.get("n") or "0"),
                            order.get("N") or "USDT",
                            int(order["T"]),
                            OrderSide(order["S"]),
                        )
                elif value.get("e") in {"ACCOUNT_UPDATE", "listenKeyExpired"}:
                    # Funding and transfers require venue transaction identities from REST.
                    raise ConnectionError("Account event requires durable income reconciliation")
        finally:
            renewal.cancel()
            await asyncio.gather(renewal, return_exceptions=True)
            await adapter.transport.request(
                "DELETE", "/fapi/v1/listenKey", False, {"listenKey": listen_key}
            )
