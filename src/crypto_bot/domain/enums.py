from enum import StrEnum


class Mode(StrEnum):
    BACKTEST = "BACKTEST"
    PAPER = "PAPER"
    DEMO = "DEMO"
    LIVE = "LIVE"


class PositionSide(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"


class OrderSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class RunState(StrEnum):
    STARTING = "STARTING"
    PAUSED = "PAUSED"
    RUNNING = "RUNNING"
    DEGRADED = "DEGRADED"
    HALTING = "HALTING"
    HALTED = "HALTED"


class PositionPhase(StrEnum):
    FLAT = "FLAT"
    ENTRY_PENDING = "ENTRY_PENDING"
    PROTECTING = "PROTECTING"
    OPEN = "OPEN"
    EXIT_PENDING = "EXIT_PENDING"
    RECONCILING = "RECONCILING"


class OrderState(StrEnum):
    PREPARED = "PREPARED"
    SUBMITTED = "SUBMITTED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    EXPIRED = "EXPIRED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"

    @property
    def terminal(self) -> bool:
        return self in {self.FILLED, self.CANCELED, self.EXPIRED, self.REJECTED}


class ExitReason(StrEnum):
    STOP = "STOP"
    TARGET = "TARGET"
    TIME_LIMIT = "TIME_LIMIT"
    TRIAL_LOSS = "TRIAL_LOSS"
    OPERATOR = "OPERATOR"
    PROTECTION_FAILURE = "PROTECTION_FAILURE"
    FUNDING_RISK = "FUNDING_RISK"
    RECOVERY_FAULT = "RECOVERY_FAULT"
