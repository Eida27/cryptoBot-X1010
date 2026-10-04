import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from binance_common.configuration import ConfigurationRestAPI
from binance_common.utils import send_request
from binance_sdk_derivatives_trading_usds_futures.derivatives_trading_usds_futures import DerivativesTradingUsdsFutures

from crypto_bot.config import ConfigurationError, Settings, validate_mode
from crypto_bot.domain.clock import Clock, SystemClock
from crypto_bot.domain.models import ExchangeSnapshot, SymbolRules
from crypto_bot.exchange.normalization import normalize_rules, normalize_snapshot

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
    "query_order": ("/fapi/v1/order", True),
    "query_algo_order": ("/fapi/v1/algoOrder", True),
}


class SDKTransport:
    def __init__(self, settings: Settings, private: bool) -> None:
        self.base_url = settings.rest_url
        self.private = private
        self.configuration = ConfigurationRestAPI(base_path=self.base_url, retries=0, timeout=3000,
            api_key=settings.api_key.get_secret_value() or None if private else None,
            api_secret=settings.api_secret.get_secret_value() or None if private else None)
        self.client = DerivativesTradingUsdsFutures(config_rest_api=self.configuration)
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="binance")
        self.pending: set[asyncio.Future[Any]] = set()

    async def request(self, http_method: str, endpoint: str, signed: bool,
                      params: dict[str, Any]) -> Any:
        if signed and not self.private:
            raise ConfigurationError("Public client cannot access private endpoints")
        api = self.client.rest_api
        loop = asyncio.get_running_loop()
        future = loop.run_in_executor(self.executor, lambda: send_request(
            api._session, self.configuration, http_method, endpoint, payload=params,
            is_signed=signed, signer=api._signer).data())
        self.pending.add(future)
        future.add_done_callback(self.pending.discard)
        return await asyncio.shield(future)

    async def read(self, method: str, **params: Any) -> Any:
        endpoint, signed = READ_ENDPOINTS[method]
        return await self.request("GET", endpoint, signed, params)

    def close(self) -> None:
        self.executor.shutdown(wait=True)


class BinanceAdapter:
    def __init__(self, settings: Settings, private: bool = False, transport: Any = None,
                 clock: Clock | None = None) -> None:
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

    async def read(self, method: str, **params: Any) -> Any:
        if method in READ_ENDPOINTS and READ_ENDPOINTS[method][1] and not self.private:
            raise ConfigurationError("Public adapter cannot make private reads")
        for attempt in range(3):
            try:
                return await self.transport.read(method, **params)
            except Exception as exc:
                if exc.__class__.__name__ in {"UnauthorizedError", "ForbiddenError", "BadRequestError", "RateLimitBanError"} or attempt == 2:
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
        return normalize_rules(value, observed_ms, bracket)

    async def fetch_snapshot(self) -> ExchangeSnapshot:
        if not self.private:
            raise ConfigurationError("Virtual broker owns paper account state")
        payload: dict[str, Any] = {"environment": self.settings.mode.value,
                                   "account_id": self.account_id}
        for name, method in (("account", "account"), ("positions", "positions"),
                             ("orders", "open_orders"), ("algo_orders", "open_algo_orders"),
                             ("income", "income")):
            payload[name] = await self.read(method)
        payload["fills"] = []
        for symbol in self.settings.symbols:
            payload["fills"].extend(await self.read("fills", symbol=symbol))
        payload["observed_ms"] = self.clock.now_ms()
        return normalize_snapshot(payload)

    def market_events(self, symbols: tuple[str, ...]) -> Any:
        from crypto_bot.exchange.streams import market_stream
        return market_stream(self, symbols)

    def execution_events(self) -> Any:
        from crypto_bot.exchange.streams import execution_stream
        return execution_stream(self)
