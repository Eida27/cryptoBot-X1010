import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal as D
from typing import Any, cast

from binance_common.configuration import ConfigurationRestAPI
from binance_common.utils import send_request
from binance_sdk_derivatives_trading_usds_futures.derivatives_trading_usds_futures import (
    DerivativesTradingUsdsFutures,
)

from crypto_bot.config import ConfigurationError, Settings, validate_mode
from crypto_bot.domain.clock import Clock, SystemClock
from crypto_bot.domain.enums import PositionSide
from crypto_bot.domain.models import (
    ExchangeSnapshot,
    OrderIntent,
    OrderObservation,
    SubmitAck,
    SubmitRejected,
    SubmitResult,
    SubmitUnknown,
    SymbolRules,
)
from crypto_bot.exchange.normalization import normalize_order, normalize_rules, normalize_snapshot

# Generic SDK request methods preserve decimal strings before generated model coercion.
READ_ENDPOINTS = {
    "check_server_time": ("/fapi/v1/time", False),
    "exchange_information": ("/fapi/v1/exchangeInfo", False),
    "order_book": ("/fapi/v1/depth", False),
    "mark_price": ("/fapi/v1/premiumIndex", False),
    "ticker24hr": ("/fapi/v1/ticker/24hr", False),
    "klines": ("/fapi/v1/klines", False),
    "mark_klines": ("/fapi/v1/markPriceKlines", False),
    "funding_history": ("/fapi/v1/fundingRate", False),
    "funding_info": ("/fapi/v1/fundingInfo", False),
    "account": ("/fapi/v3/account", True),
    "positions": ("/fapi/v3/positionRisk", True),
    "open_orders": ("/fapi/v1/openOrders", True),
    "open_algo_orders": ("/fapi/v1/openAlgoOrders", True),
    "fills": ("/fapi/v1/userTrades", True),
    "income": ("/fapi/v1/income", True),
    "brackets": ("/fapi/v1/leverageBracket", True),
    "commission": ("/fapi/v1/commissionRate", True),
    "position_mode": ("/fapi/v1/positionSide/dual", True),
    "asset_mode": ("/fapi/v1/multiAssetsMargin", True),
    "fee_burn": ("/fapi/v1/feeBurn", True),
    "symbol_config": ("/fapi/v1/symbolConfig", True),
    "query_order": ("/fapi/v1/order", True),
    "query_algo_order": ("/fapi/v1/algoOrder", True),
}


class _ResponseCapture:
    """Retain this request's wire response while the SDK handles signing/errors."""

    def __init__(self, session: Any) -> None:
        self.session = session
        self.response: Any = None

    def request(self, **kwargs: Any) -> Any:
        self.response = self.session.request(**kwargs)
        return self.response

    def mount(self, *args: Any) -> None:
        self.session.mount(*args)


class SDKTransport:
    def __init__(self, settings: Settings, private: bool) -> None:
        self.base_url = settings.rest_url
        self.private = private
        self.configuration = ConfigurationRestAPI(
            base_path=self.base_url,
            retries=0,
            timeout=3000,
            api_key=settings.api_key.get_secret_value() or None if private else None,
            api_secret=settings.api_secret.get_secret_value() or None if private else None,
        )
        self.client = DerivativesTradingUsdsFutures(config_rest_api=self.configuration)
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="binance")
        self.pending: set[asyncio.Future[Any]] = set()

    async def request(
        self, http_method: str, endpoint: str, signed: bool, params: dict[str, Any]
    ) -> Any:
        if signed and not self.private:
            raise ConfigurationError("Public client cannot access private endpoints")
        api = self.client.rest_api
        loop = asyncio.get_running_loop()

        def send_exact() -> Any:
            capture = _ResponseCapture(api._session)
            send_request(
                cast(Any, capture),
                self.configuration,
                http_method,
                endpoint,
                payload=params,
                is_signed=signed,
                signer=cast(Any, api._signer),
            ).data()
            # Brackets contain JSON numbers. Decode the original wire text so the
            # SDK's float coercion cannot lose precision before normalization.
            return json.loads(capture.response.text, parse_float=D)

        future: asyncio.Future[Any] = loop.run_in_executor(
            self.executor,
            send_exact,
        )
        self.pending.add(future)
        future.add_done_callback(self.pending.discard)
        return await asyncio.shield(future)

    async def read(self, method: str, **params: Any) -> Any:
        endpoint, signed = READ_ENDPOINTS[method]
        return await self.request("GET", endpoint, signed, params)

    def close(self) -> None:
        self.executor.shutdown(wait=True)


class BinanceAdapter:
    def __init__(
        self,
        settings: Settings,
        private: bool = False,
        transport: Any = None,
        clock: Clock | None = None,
    ) -> None:
        validate_mode(settings)
        if private and settings.mode.value not in {"DEMO", "LIVE"}:
            raise ConfigurationError("PAPER/BACKTEST cannot create a private client")
        if transport is not None and transport.base_url != settings.rest_url:
            raise ConfigurationError("Transport host does not match configured mode")
        self.settings, self.private = settings, private
        self.clock = clock or SystemClock()
        self.transport = transport or SDKTransport(settings, private)
        self.heartbeat_ms = 0
        self.account_id = "configured-wallet"
        self.live_armed = False
        self.history_start_ms: int | None = None

    def public_brackets(self) -> dict[str, Any]:
        path = self.settings.bracket_metadata
        if path is None:
            return {}
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        if (
            data.get("environment") not in {"DEMO", "LIVE"}
            or not 0 <= self.clock.now_ms() - int(data.get("observed_ms", 0)) <= 21600000
        ):
            raise ConfigurationError("Maintenance metadata is missing or older than six hours")
        return data

    async def read(self, method: str, **params: Any) -> Any:
        if method in READ_ENDPOINTS and READ_ENDPOINTS[method][1] and not self.private:
            raise ConfigurationError("Public adapter cannot make private reads")
        for attempt in range(3):
            try:
                return await self.transport.read(method, **params)
            except Exception as exc:
                if (
                    exc.__class__.__name__
                    in {
                        "UnauthorizedError",
                        "ForbiddenError",
                        "BadRequestError",
                        "RateLimitBanError",
                    }
                    or attempt == 2
                ):
                    raise
                delay = max(0.25 * 2**attempt, float(getattr(exc, "retry_after", 0) or 0))
                await self.clock.sleep(delay)
        raise RuntimeError("Read attempts exhausted")

    async def fetch_rules(self, symbol: str) -> SymbolRules:
        payload = await self.read("exchange_information")
        observed_ms = self.clock.now_ms()
        value = next(v for v in payload["symbols"] if v["symbol"] == symbol)
        bracket = None
        if self.private:
            brackets = await self.read("brackets", symbol=symbol)
            bracket = brackets[0]["brackets"][0]
        elif self.settings.bracket_metadata:
            metadata = self.public_brackets()
            bracket = metadata["brackets"].get(symbol)
            observed_ms = min(observed_ms, int(metadata["observed_ms"]))
        return normalize_rules(value, observed_ms, bracket)

    async def fetch_snapshot(self) -> ExchangeSnapshot:
        if not self.private:
            raise ConfigurationError("Virtual broker owns paper account state")
        payload: dict[str, Any] = {
            "environment": self.settings.mode.value,
            "account_id": self.account_id,
        }
        for name, method in (
            ("account", "account"),
            ("positions", "positions"),
            ("orders", "open_orders"),
            ("algo_orders", "open_algo_orders"),
            ("income", "income"),
        ):
            params = (
                {"startTime": self.history_start_ms, "limit": 1000}
                if method == "income" and self.history_start_ms is not None
                else {}
            )
            payload[name] = await self.read(method, **params)
            if method == "income" and len(payload[name]) >= 1000:
                raise ConfigurationError(
                    "Income page saturated; reconciliation requires complete pagination"
                )
            if name == "account":
                payload["account_observed_ms"] = self.clock.now_ms()
        payload["fills"] = []
        for symbol in self.settings.symbols:
            params = (
                {"startTime": self.history_start_ms, "limit": 1000}
                if self.history_start_ms is not None
                else {}
            )
            fills = await self.read("fills", symbol=symbol, **params)
            if len(fills) >= 1000:
                raise ConfigurationError(
                    "Fill page saturated; reconciliation requires complete pagination"
                )
            payload["fills"].extend(fills)
        payload["observed_ms"] = self.clock.now_ms()
        return normalize_snapshot(payload)

    def market_events(self, symbols: tuple[str, ...]) -> Any:
        from crypto_bot.exchange.streams import market_stream

        return market_stream(self, symbols)

    def execution_events(self) -> Any:
        from crypto_bot.exchange.streams import execution_stream

        return execution_stream(self)

    async def mutate(self, method: str, endpoint: str, params: dict[str, Any]) -> SubmitResult:
        if not self.private:
            raise ConfigurationError("Public adapter cannot mutate orders")
        if self.settings.mode.value == "LIVE" and (
            not self.settings.live_trading_enabled or not self.live_armed
        ):
            return SubmitRejected("LIVE_DISABLED")
        try:
            raw = await self.transport.request(method, endpoint, True, params)
            if method == "DELETE" and "symbol" not in raw:
                return SubmitUnknown("CANCELLATION_REQUIRES_STATUS_CONFIRMATION")
            namespace = "algo" if "algo" in endpoint.lower() else "ordinary"
            return SubmitAck(normalize_order(raw, self.clock.now_ms(), namespace))
        except Exception as exc:
            if exc.__class__.__name__ == "BadRequestError" and getattr(
                exc, "status_code", 0
            ) not in {-1006, -1007}:
                return SubmitRejected(f"VENUE_REJECTED_{getattr(exc, 'status_code', 'UNKNOWN')}")
            return SubmitUnknown()

    async def submit_entry(self, intent: OrderIntent) -> SubmitResult:
        return await self.mutate(
            "POST",
            "/fapi/v1/order",
            {
                "symbol": intent.symbol,
                "side": intent.side.value,
                "type": "LIMIT",
                "timeInForce": "IOC",
                "positionSide": "BOTH",
                "quantity": str(intent.quantity),
                "price": str(intent.limit_price),
                "newClientOrderId": intent.client_id,
                "newOrderRespType": "RESULT",
            },
        )

    async def find_order(self, intent: OrderIntent) -> OrderObservation | None:
        algo = intent.role in {"STOP", "TARGET", "PROVISIONAL_STOP"}
        try:
            raw = await self.read(
                "query_algo_order" if algo else "query_order",
                **(
                    {"clientAlgoId": intent.client_id}
                    if algo
                    else {"symbol": intent.symbol, "origClientOrderId": intent.client_id}
                ),
            )
            return normalize_order(raw, self.clock.now_ms(), "algo" if algo else "ordinary")
        except Exception as exc:
            if getattr(exc, "status_code", 0) in {-2013, -2011, 404}:
                return None
            raise

    async def cancel_order(self, intent: OrderIntent) -> SubmitResult:
        algo = intent.role in {"STOP", "TARGET", "PROVISIONAL_STOP"}
        return await self.mutate(
            "DELETE",
            "/fapi/v1/algoOrder" if algo else "/fapi/v1/order",
            {"clientAlgoId": intent.client_id}
            if algo
            else {"symbol": intent.symbol, "origClientOrderId": intent.client_id},
        )

    async def submit_protection(self, intent: OrderIntent) -> SubmitResult:
        return await self.mutate(
            "POST",
            "/fapi/v1/algoOrder",
            {
                "algoType": "CONDITIONAL",
                "symbol": intent.symbol,
                "side": intent.side.value,
                "positionSide": "BOTH",
                "type": "TAKE_PROFIT_MARKET" if intent.role == "TARGET" else "STOP_MARKET",
                "triggerPrice": str(intent.trigger_price),
                "workingType": "MARK_PRICE",
                "closePosition": "true",
                "clientAlgoId": intent.client_id,
            },
        )

    async def reduce_position(
        self, symbol: str, side: PositionSide, quantity: D, client_id: str
    ) -> SubmitResult:
        return await self.mutate(
            "POST",
            "/fapi/v1/order",
            {
                "symbol": symbol,
                "side": "SELL" if side is PositionSide.LONG else "BUY",
                "positionSide": "BOTH",
                "type": "MARKET",
                "reduceOnly": "true",
                "quantity": str(quantity),
                "newClientOrderId": client_id,
                "newOrderRespType": "RESULT",
            },
        )
