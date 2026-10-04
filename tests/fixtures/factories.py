from dataclasses import replace
from decimal import Decimal as D
from typing import Any

from crypto_bot.domain.enums import PositionSide
from crypto_bot.domain.models import (
    AccountSnapshot, BookLevel, Candle, EntryContext, FundingContext, MarketFrame,
    Signal, SymbolRules, VerifiedSettings,
)


def candle(index: int = 0, price: str = "100", **changes: Any) -> Candle:
    value = D(price)
    return replace(Candle("SOLUSDT", index * 3600000, (index + 1) * 3600000,
                          value, value + D("1"), value - D("1"), value), **changes)


def signal(at_ms: int = 3600000, side: PositionSide = PositionSide.LONG) -> Signal:
    return Signal(f"strategy:SOLUSDT:{at_ms}", "strategy", "SOLUSDT", at_ms, side, D("100"), D("1"))


def context(at_ms: int = 3600001, **changes: Any) -> EntryContext:
    rules = SymbolRules("SOLUSDT", "TRADING", "PERPETUAL", "USDT", "USDT",
                         D("0.01"), D("0.01"), D("0.01"), D("1000000"), D("5"),
                         D("100000"), at_ms, D("0.004"))
    frame = MarketFrame("SOLUSDT", at_ms, D("99.99"), D("100"), D("100"),
                        (BookLevel(D("99.99"), D("1000")),),
                        (BookLevel(D("100"), D("1000")),), D("200000000"),
                        {"quote": at_ms, "heartbeat": at_ms})
    return replace(EntryContext(AccountSnapshot(at_ms, D("20"), D("0"), D("20")),
                                frame, rules, FundingContext(at_ms, at_ms + 28800000,
                                D("8"), D("0.0001"), D("0.0001")), VerifiedSettings()), **changes)
