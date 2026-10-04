from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from crypto_bot.domain.enums import Mode, OrderSide, OrderState, PositionPhase, PositionSide

D = Decimal


@dataclass(frozen=True)
class Candle:
    symbol: str
    open_ms: int
    close_ms: int
    open: D
    high: D
    low: D
    close: D
    volume: D = D("1")
    quote_volume: D = D("100")
    closed: bool = True


@dataclass(frozen=True)
class AccountSnapshot:
    observed_ms: int
    wallet_balance: D
    unrealized_pnl: D
    available_balance: D


@dataclass(frozen=True)
class SymbolRules:
    symbol: str
    status: str
    contract_type: str
    quote_asset: str
    settle_asset: str
    tick_size: D
    step_size: D
    min_qty: D
    max_qty: D
    min_notional: D
    bracket_limit: D
    observed_ms: int
    maintenance_rate: D | None = None
    maintenance_deduction: D = D("0")
    min_price: D = D("0")
    max_price: D = D("1E30")
    multiplier_down: D = D("0.95")
    multiplier_up: D = D("1.05")


@dataclass(frozen=True)
class BookLevel:
    price: D
    quantity: D


@dataclass(frozen=True)
class MarketFrame:
    symbol: str
    observed_ms: int
    bid: D
    ask: D
    mark: D
    bids: tuple[BookLevel, ...]
    asks: tuple[BookLevel, ...]
    quote_volume_24h: D
    freshness: dict[str, int]


@dataclass(frozen=True)
class FundingContext:
    observed_ms: int
    next_event_ms: int
    interval_hours: D
    current_rate: D
    seven_day_max_abs_rate: D


@dataclass(frozen=True)
class VerifiedSettings:
    one_way: bool = True
    single_asset: bool = True
    isolated: bool = True
    auto_margin: bool = False
    bnb_fees: bool = False
    leverage: D = D("2")


@dataclass(frozen=True)
class EntryContext:
    account: AccountSnapshot
    market: MarketFrame
    rules: SymbolRules
    funding: FundingContext
    settings: VerifiedSettings
    entry_fee_rate: D = D("0.0006")
    exit_fee_rate: D = D("0.0006")


@dataclass(frozen=True)
class Signal:
    identity: str
    strategy_hash: str
    symbol: str
    close_ms: int
    side: PositionSide
    close_price: D
    atr: D
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Trial:
    run_id: str
    mode: Mode
    baseline: D
    floor: D
    php_per_usdt: D
    hashes: dict[str, str]
    halt_reason: str | None = None
    start_ms: int = 0
    qualification_status: str = "OBSERVING"


@dataclass(frozen=True)
class RiskPolicy:
    leverage: D = D("2")
    entry_slippage_reserve: D = D("0.0005")
    exit_slippage_reserve: D = D("0.0010")
    fee_floor: D = D("0.0006")


@dataclass(frozen=True)
class RiskInput:
    trial: Trial
    signal: Signal
    context: EntryContext
    policy: RiskPolicy = RiskPolicy()


@dataclass(frozen=True)
class ApprovedSize:
    quantity: D
    notional: D
    planned_loss: D
    budget: D
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RejectedSize:
    reason: str
    explanation: str
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OrderIntent:
    logical_id: str
    client_id: str
    role: str
    generation: int
    symbol: str
    side: OrderSide
    quantity: D | None
    limit_price: D | None
    trigger_price: D | None
    request_hash: str
    run_id: str = ""
    signal_id: str = ""


@dataclass(frozen=True)
class FillEvent:
    environment: str
    account: str
    symbol: str
    trade_id: str
    order_id: str
    price: D
    quantity: D
    commission: D
    commission_asset: str
    at_ms: int
    side: OrderSide = OrderSide.BUY


@dataclass(frozen=True)
class IncomeEvent:
    environment: str
    account: str
    symbol: str
    transaction_id: str
    income_type: str
    amount: D
    asset: str
    at_ms: int


@dataclass(frozen=True)
class OrderObservation:
    client_id: str
    venue_id: str
    namespace: str
    symbol: str
    state: OrderState
    cumulative_quantity: D = D("0")
    average_price: D = D("0")
    first_fill_ms: int | None = None
    observed_ms: int = 0


@dataclass(frozen=True)
class OrderUpdate:
    observation: OrderObservation


ExecutionEvent = FillEvent | IncomeEvent | OrderUpdate


@dataclass(frozen=True)
class Position:
    symbol: str
    side: PositionSide
    quantity: D
    average_entry: D
    atr: D
    first_fill_ms: int
    stop: D | None = None
    target: D | None = None
    liquidation_price: D | None = None
    phase: PositionPhase = PositionPhase.PROTECTING
    intent_id: str = ""


@dataclass(frozen=True)
class ExchangeSnapshot:
    observed_ms: int
    account: AccountSnapshot
    positions: tuple[Position, ...] = ()
    ordinary_orders: tuple[OrderObservation, ...] = ()
    algo_orders: tuple[OrderObservation, ...] = ()
    fills: tuple[FillEvent, ...] = ()
    income: tuple[IncomeEvent, ...] = ()


@dataclass(frozen=True)
class SubmitAck:
    observation: OrderObservation


@dataclass(frozen=True)
class SubmitUnknown:
    reason: str = "UNKNOWN_OUTCOME"


@dataclass(frozen=True)
class SubmitRejected:
    reason: str


SubmitResult = SubmitAck | SubmitUnknown | SubmitRejected


@dataclass(frozen=True)
class ControlCommand:
    request_id: str
    action: str
    operator: str
    at_ms: int


@dataclass(frozen=True)
class CandleEvent:
    candle: Candle


@dataclass(frozen=True)
class BookEvent:
    frame: MarketFrame


@dataclass(frozen=True)
class MarkEvent:
    symbol: str
    at_ms: int
    price: D


@dataclass(frozen=True)
class FundingEvent:
    symbol: str
    at_ms: int
    rate: D
    mark: D
    transaction_id: str


@dataclass(frozen=True)
class StreamGapEvent:
    at_ms: int
    reason: str


MarketEvent = CandleEvent | BookEvent | MarkEvent | FundingEvent | StreamGapEvent


@dataclass(frozen=True)
class IndicatorState:
    symbol: str = ""
    seed_epoch: int = 0
    last_close_ms: int = 0
    count: int = 0
    close_sum: D = D("0")
    tr_sum: D = D("0")
    tr_count: int = 0
    previous_close: D | None = None
    ema: D | None = None
    atr: D | None = None
