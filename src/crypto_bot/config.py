import hashlib
import json
import tomllib
from collections.abc import Mapping
from decimal import Decimal, DefaultContext, getcontext
from pathlib import Path
from typing import Any

from pydantic import Field, SecretStr, ValidationError
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from crypto_bot.domain.enums import Mode

DefaultContext.prec = 34
getcontext().prec = 34
PRODUCTION_REST = "https://fapi.binance.com"
DEMO_REST = "https://demo-fapi.binance.com"
STRATEGY_VERSION = "hourly-breakout-ema200-atr14-v1"
STRATEGY_HASH = hashlib.sha256(STRATEGY_VERSION.encode()).hexdigest()


class ConfigurationError(ValueError):
    pass


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="forbid", allow_inf_nan=False, frozen=True)
    mode: Mode = Mode.PAPER
    database: Path = Path("state/paper.sqlite3")
    host_profile: str = "local"
    initial_capital_usdt: Decimal = Decimal("16.00")
    allocation_reference: str = "Virtual estimate for PHP 1,000; not a current FX quote"
    live_trading_enabled: bool = False
    entries_enabled: bool = False
    leverage: Decimal = Decimal("2")
    symbols: tuple[str, ...] = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
    rest_url: str = PRODUCTION_REST
    bind_host: str = "127.0.0.1"
    port: int = 8000
    secure_cookies: bool = False
    min_quote_volume: Decimal = Decimal("100000000")
    max_spread_bps: Decimal = Decimal("10")
    max_mark_divergence_bps: Decimal = Decimal("20")
    max_signal_deviation_bps: Decimal = Decimal("20")
    entry_slippage_reserve: Decimal = Decimal("0.0005")
    exit_slippage_reserve: Decimal = Decimal("0.0010")
    fee_floor: Decimal = Decimal("0.0006")
    api_key: SecretStr = Field(default=SecretStr(""), repr=False, exclude=True)
    api_secret: SecretStr = Field(default=SecretStr(""), repr=False, exclude=True)
    password_hash: SecretStr = Field(default=SecretStr(""), repr=False, exclude=True)

    def __init__(self, **values: Any) -> None:
        mode = str(values.get("mode", "PAPER"))
        values.setdefault("database", Path("state") / f"{mode.lower()}.sqlite3")
        values.setdefault("rest_url", DEMO_REST if mode == "DEMO" else PRODUCTION_REST)
        try:
            super().__init__(**values)
        except ValidationError as exc:
            raise ConfigurationError("Invalid configuration types or nonfinite decimals") from exc

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (init_settings,)

    def safe_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    @property
    def config_hash(self) -> str:
        values = self.safe_dict()
        for field in (
            "mode",
            "database",
            "host_profile",
            "live_trading_enabled",
            "port",
            "bind_host",
            "secure_cookies",
        ):
            values.pop(field, None)
        return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def validate_mode(settings: Settings) -> None:
    errors: list[str] = []
    if settings.initial_capital_usdt <= 0:
        errors.append("Explicit positive allocation required")
    if not Decimal("1") <= settings.leverage <= Decimal("2"):
        errors.append("Leverage must be between 1x and 2x")
    if settings.entries_enabled:
        errors.append("Startup entries must be paused")
    if settings.database.resolve().name != f"{settings.mode.value.lower()}.sqlite3":
        errors.append("Use a distinct mode-named database")
    expected_url = DEMO_REST if settings.mode is Mode.DEMO else PRODUCTION_REST
    if settings.rest_url != expected_url:
        errors.append("Endpoint does not match the allowlisted environment")
    if settings.bind_host not in {"127.0.0.1", "0.0.0.0"}:
        errors.append("Unsupported bind address")
    if settings.bind_host == "0.0.0.0" and settings.host_profile != "container":
        errors.append("Wildcard bind allowed only inside loopback-published container")
    if settings.mode is Mode.LIVE and settings.host_profile not in {"vps", "container"}:
        errors.append("LIVE requires a continuously operated VPS")
    if set(settings.symbols) - {"BTCUSDT", "ETHUSDT", "SOLUSDT"}:
        errors.append("Unapproved research universe")
    if not settings.symbols or len(set(settings.symbols)) != len(settings.symbols):
        errors.append("Universe must be nonempty and unique")
    if min(settings.entry_slippage_reserve, settings.exit_slippage_reserve) < 0:
        errors.append("Cost allowances cannot be negative")
    if settings.fee_floor < Decimal("0.0006"):
        errors.append("Fee floor cannot be lowered")
    if errors:
        raise ConfigurationError("; ".join(errors))


def load_settings(path: Path, environ: Mapping[str, str]) -> Settings:
    with path.open("rb") as source:
        values = tomllib.load(source)
    mode = environ.get("CBOT_MODE", values.get("mode", "PAPER"))
    values["mode"] = mode
    for key in ("host_profile", "live_trading_enabled", "password_hash"):
        if f"CBOT_{key.upper()}" in environ:
            values[key] = environ[f"CBOT_{key.upper()}"]
    if mode in {"LIVE", "DEMO"}:
        for key in ("api_key", "api_secret"):
            values[key] = environ.get(f"CBOT_{mode}_{key.upper()}", "")
    settings = Settings(**values)
    validate_mode(settings)
    return settings
