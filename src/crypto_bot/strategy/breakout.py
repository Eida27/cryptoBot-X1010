from collections.abc import Sequence

from crypto_bot.domain.enums import PositionSide
from crypto_bot.domain.models import Candle, IndicatorState, Signal
from crypto_bot.market.validation import validate_candle
from crypto_bot.storage.repository import digest


def evaluate_signal(
    history: Sequence[Candle], state: IndicatorState, strategy_hash: str
) -> Signal | None:
    for index, candle in enumerate(history):
        validate_candle(candle)
        if not candle.closed or candle.close_ms > state.last_close_ms:
            raise ValueError("Partial or future candle in signal history")
        if index and (
            candle.symbol != history[index - 1].symbol
            or candle.open_ms != history[index - 1].close_ms
        ):
            raise ValueError("Nonconsecutive signal history")
    if state.count < 1000 or len(history) < 21 or state.ema is None or state.atr is None:
        return None
    candle = history[-1]
    if candle.close_ms != state.last_close_ms or candle.symbol != state.symbol:
        raise ValueError("Indicator checkpoint and decision candle disagree")
    high = max(c.high for c in history[-21:-1])
    low = min(c.low for c in history[-21:-1])
    side = None
    if candle.close > state.ema and candle.close > high:
        side = PositionSide.LONG
    elif candle.close < state.ema and candle.close < low:
        side = PositionSide.SHORT
    if side is None:
        return None
    identity = digest([strategy_hash, candle.symbol, candle.close_ms])
    return Signal(
        identity,
        strategy_hash,
        candle.symbol,
        candle.close_ms,
        side,
        candle.close,
        state.atr,
        {
            "ema": state.ema,
            "channel_high": high,
            "channel_low": low,
            "seed_epoch": state.seed_epoch,
            "count": state.count,
        },
    )
