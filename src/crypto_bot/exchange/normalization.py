from collections.abc import Mapping, Sequence
from decimal import Decimal as D
from typing import Any

from crypto_bot.domain.enums import OrderSide, OrderState, PositionSide
from crypto_bot.domain.models import (
    AccountSnapshot,
    BookLevel,
    Candle,
    EntryContext,
    ExchangeSnapshot,
    FillEvent,
    FundingContext,
    IncomeEvent,
    MarketFrame,
    OrderObservation,
    Position,
    SymbolRules,
    VerifiedSettings,
)
from crypto_bot.exchange.errors import MissingFundingData, SchemaError


def decimal_value(value: Any) -> D:
    if isinstance(value, (float, bool)) or not isinstance(value, (str, int, D)):
        raise SchemaError("Financial boundaries require decimal strings")
    result = D(value)
    if not result.is_finite():
        raise SchemaError("Nonfinite financial value")
    return result


def normalize_candle(symbol: str, row: Sequence[Any], exchange_ms: int) -> Candle:
    close_ms = int(row[6]) + 1
    return Candle(
        symbol,
        int(row[0]),
        close_ms,
        decimal_value(row[1]),
        decimal_value(row[2]),
        decimal_value(row[3]),
        decimal_value(row[4]),
        decimal_value(row[5]),
        decimal_value(row[7]),
        close_ms <= exchange_ms,
    )


def normalize_rules(
    payload: Mapping[str, Any], observed_ms: int, bracket: Mapping[str, Any] | None = None
) -> SymbolRules:
    if payload["quoteAsset"] != "USDT" or payload["marginAsset"] != "USDT":
        raise SchemaError("Only USDT quote/settlement supported")
    filters = {f["filterType"]: f for f in payload["filters"]}
    price, lot, minimum = filters["PRICE_FILTER"], filters["LOT_SIZE"], filters["MIN_NOTIONAL"]
    bands = filters.get("PERCENT_PRICE", {})
    return SymbolRules(
        str(payload["symbol"]),
        str(payload["status"]),
        str(payload["contractType"]),
        "USDT",
        "USDT",
        decimal_value(price["tickSize"]),
        decimal_value(lot["stepSize"]),
        decimal_value(lot["minQty"]),
        decimal_value(lot["maxQty"]),
        decimal_value(minimum["notional"]),
        decimal_value(bracket["notionalCap"]) if bracket else D("0"),
        observed_ms,
        decimal_value(bracket["maintMarginRatio"]) if bracket else None,
        decimal_value(bracket.get("cum", "0")) if bracket else D("0"),
        decimal_value(price.get("minPrice", "0")),
        decimal_value(price.get("maxPrice", "1E30")),
        decimal_value(bands.get("multiplierDown", "0.95")),
        decimal_value(bands.get("multiplierUp", "1.05")),
    )


def normalize_funding(payload: Mapping[str, Any] | None, observed_ms: int) -> FundingContext:
    required = {"lastFundingRate", "nextFundingTime", "fundingIntervalHours", "history"}
    if payload is None or not required.issubset(payload) or not payload["history"]:
        raise MissingFundingData("Rate, verified interval and seven-day history required")
    interval = decimal_value(payload["fundingIntervalHours"])
    if interval <= 0:
        raise MissingFundingData("Funding interval must be positive")
    return FundingContext(
        observed_ms,
        int(payload["nextFundingTime"]),
        interval,
        decimal_value(payload["lastFundingRate"]),
        max(abs(decimal_value(rate)) for rate in payload["history"]),
    )


def normalize_account(payload: Mapping[str, Any], observed_ms: int) -> AccountSnapshot:
    return AccountSnapshot(
        observed_ms,
        decimal_value(payload["totalWalletBalance"]),
        decimal_value(payload["totalUnrealizedProfit"]),
        decimal_value(payload["availableBalance"]),
    )


def normalize_order(
    value: Mapping[str, Any], at_ms: int, namespace: str = "ordinary"
) -> OrderObservation:
    status = str(value.get("status", value.get("algoStatus", "UNKNOWN")))
    status = {"NEW": "ACKNOWLEDGED", "FINISHED": "FILLED", "TRIGGERED": "UNKNOWN"}.get(
        status, status
    )
    if status not in OrderState._value2member_map_:
        status = "UNKNOWN"
    return OrderObservation(
        str(value.get("clientOrderId", value.get("clientAlgoId", ""))),
        str(value.get("orderId", value.get("algoId", ""))),
        namespace,
        str(value["symbol"]),
        OrderState(status),
        decimal_value(value.get("executedQty", value.get("actualQty", "0"))),
        decimal_value(value.get("avgPrice", value.get("actualPrice", "0"))),
        int(value["firstFillTime"]) if value.get("firstFillTime") else None,
        at_ms,
        str(value["actualOrderId"])
        if value.get("actualOrderId") not in (None, "", 0, "0")
        else None,
    )


def normalize_snapshot(payloads: Mapping[str, Any]) -> ExchangeSnapshot:
    at_ms = int(payloads["observed_ms"])
    environment, account = str(payloads["environment"]), str(payloads["account_id"])
    positions = []
    for value in payloads.get("positions", []):
        qty = decimal_value(value["positionAmt"])
        if qty:
            positions.append(
                Position(
                    str(value["symbol"]),
                    PositionSide.LONG if qty > 0 else PositionSide.SHORT,
                    abs(qty),
                    decimal_value(value["entryPrice"]),
                    D("0"),
                    0,
                    liquidation_price=decimal_value(value["liquidationPrice"]),
                )
            )
    fills = tuple(
        FillEvent(
            environment,
            account,
            str(v["symbol"]),
            str(v["id"]),
            str(v["orderId"]),
            decimal_value(v["price"]),
            decimal_value(v["qty"]),
            decimal_value(v["commission"]),
            str(v["commissionAsset"]),
            int(v["time"]),
            OrderSide(v["side"]),
        )
        for v in payloads.get("fills", [])
    )
    income = tuple(
        IncomeEvent(
            environment,
            account,
            str(v.get("symbol", "")),
            str(v["tranId"]),
            str(v["incomeType"]),
            decimal_value(v["income"]),
            str(v["asset"]),
            int(v["time"]),
        )
        for v in payloads.get("income", [])
    )
    return ExchangeSnapshot(
        at_ms,
        normalize_account(payloads["account"], int(payloads.get("account_observed_ms", at_ms))),
        tuple(positions),
        tuple(normalize_order(v, at_ms) for v in payloads.get("orders", [])),
        tuple(normalize_order(v, at_ms, "algo") for v in payloads.get("algo_orders", [])),
        fills,
        income,
    )


def normalize_entry_context(payload: Mapping[str, Any]) -> EntryContext:
    at_ms = int(payload.get("observed_ms", 0))
    funding = normalize_funding(payload.get("funding"), at_ms)
    market = payload["market"]
    bids = tuple(BookLevel(decimal_value(v[0]), decimal_value(v[1])) for v in market["bids"])
    asks = tuple(BookLevel(decimal_value(v[0]), decimal_value(v[1])) for v in market["asks"])
    if not bids or not asks:
        raise SchemaError("Empty executable book")
    settings = payload["settings"]

    def truth(value: Any) -> bool:
        if value not in (True, False, "true", "false"):
            raise SchemaError("Unknown boolean setting")
        return value is True or value == "true"

    verified = VerifiedSettings(
        not truth(settings["dualSidePosition"]),
        not truth(settings["multiAssetsMargin"]),
        settings["marginType"] == "isolated",
        truth(settings["isAutoAddMargin"]),
        truth(settings["feeBurn"]),
        decimal_value(settings["leverage"]),
    )
    frame = MarketFrame(
        str(market["symbol"]),
        int(market["observed_ms"]),
        bids[0].price,
        asks[0].price,
        decimal_value(market["markPrice"]),
        bids,
        asks,
        decimal_value(market["quoteVolume"]),
        {str(k): int(v) for k, v in market["freshness"].items()},
    )
    return EntryContext(
        normalize_account(payload["account"], int(payload["account_observed_ms"])),
        frame,
        normalize_rules(
            payload["rules"], int(payload["rules_observed_ms"]), payload.get("bracket")
        ),
        funding,
        verified,
        decimal_value(payload["entry_fee_rate"]),
        decimal_value(payload["exit_fee_rate"]),
    )
