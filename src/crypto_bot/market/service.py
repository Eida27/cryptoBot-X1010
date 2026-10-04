from decimal import Decimal as D
from typing import Any

from crypto_bot.domain.models import (
    BookLevel,
    EntryContext,
    FundingContext,
    MarketFrame,
    Signal,
    VerifiedSettings,
)
from crypto_bot.exchange.errors import MissingFundingData


class MarketService:
    def __init__(self, adapter: Any, account_source: Any) -> None:
        self.adapter, self.account_source = adapter, account_source

    async def build_context(self, signal: Signal) -> EntryContext:
        adapter = self.adapter
        book = await adapter.read("order_book", symbol=signal.symbol, limit=20)
        book_ms = adapter.clock.now_ms()
        mark = await adapter.read("mark_price", symbol=signal.symbol)
        ticker = await adapter.read("ticker24hr", symbol=signal.symbol)
        rules = await adapter.fetch_rules(signal.symbol)
        history = await adapter.read(
            "funding_history",
            symbol=signal.symbol,
            startTime=adapter.clock.now_ms() - 7 * 86400000,
            limit=1000,
        )
        info = await adapter.read("funding_info")
        funding_info = next((v for v in info if v["symbol"] == signal.symbol), None)
        if len(history) < 3:
            raise MissingFundingData("Incomplete funding history")
        intervals = {
            int(b["fundingTime"]) - int(a["fundingTime"]) for a, b in zip(history, history[1:])
        }
        if funding_info:
            interval = D(str(funding_info["fundingIntervalHours"]))
        elif len(intervals) == 1:
            interval = D(intervals.pop()) / D("3600000")
        else:
            raise MissingFundingData("Unverified funding interval")
        observed_ms = adapter.clock.now_ms()
        if history[0]["fundingTime"] > observed_ms - 7 * 86400000 + int(interval * 3600000):
            raise MissingFundingData("Seven-day funding history incomplete")
        funding = FundingContext(
            observed_ms,
            int(mark["nextFundingTime"]),
            interval,
            D(mark["lastFundingRate"]),
            max(abs(D(v["fundingRate"])) for v in history),
        )
        snapshot = await self.account_source.fetch_snapshot()
        bids = tuple(BookLevel(D(v[0]), D(v[1])) for v in book["bids"])
        asks = tuple(BookLevel(D(v[0]), D(v[1])) for v in book["asks"])
        frame = MarketFrame(
            signal.symbol,
            book_ms,
            bids[0].price,
            asks[0].price,
            D(mark["markPrice"]),
            bids,
            asks,
            D(ticker["quoteVolume"]),
            {"quote": book_ms, "heartbeat": adapter.heartbeat_ms},
        )
        verified = VerifiedSettings(leverage=adapter.settings.leverage)
        fee = D("0.0006")
        if adapter.private:
            position_mode = await adapter.read("position_mode")
            asset_mode = await adapter.read("asset_mode")
            burn = await adapter.read("fee_burn")
            account = await adapter.read("account")
            position = next(v for v in account["positions"] if v["symbol"] == signal.symbol)
            positions = await adapter.read("positions", symbol=signal.symbol)
            raw = positions[0]
            verified = VerifiedSettings(
                not position_mode["dualSidePosition"],
                not asset_mode["multiAssetsMargin"],
                bool(position.get("isolated")),
                str(raw.get("isAutoAddMargin", "true")).lower() != "false",
                bool(burn["feeBurn"]),
                D(str(raw["leverage"])),
            )
            rates = await adapter.read("commission", symbol=signal.symbol)
            fee = max(fee, D(rates["takerCommissionRate"]))
        return EntryContext(snapshot.account, frame, rules, funding, verified, fee, fee)
