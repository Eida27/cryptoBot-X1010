from dataclasses import replace
from decimal import Decimal as D

from crypto_bot.domain.models import Candle, IndicatorState
from crypto_bot.market.validation import validate_candle


def update_indicators(state: IndicatorState, candle: Candle) -> IndicatorState:
    validate_candle(candle)
    if not candle.closed or candle.close_ms - candle.open_ms != 3600000:
        raise ValueError("Indicators require completed one-hour candles")
    if state.count and (candle.symbol != state.symbol or candle.open_ms != state.last_close_ms):
        raise ValueError("Duplicate, reordered or missing indicator candle")
    count = state.count + 1
    close_sum = state.close_sum + candle.close if count <= 200 else state.close_sum
    ema = close_sum / D("200") if count == 200 else state.ema
    if count > 200:
        assert ema is not None
        ema = (candle.close * D("2") + ema * D("199")) / D("201")
    atr, tr_sum, tr_count = state.atr, state.tr_sum, state.tr_count
    if state.previous_close is not None:
        tr = max(
            candle.high - candle.low,
            abs(candle.high - state.previous_close),
            abs(candle.low - state.previous_close),
        )
        tr_count += 1
        if tr_count <= 14:
            tr_sum += tr
            if tr_count == 14:
                atr = tr_sum / D("14")
        else:
            assert atr is not None
            atr = (atr * D("13") + tr) / D("14")
    return replace(
        state,
        symbol=candle.symbol,
        seed_epoch=state.seed_epoch if state.count else candle.open_ms,
        last_close_ms=candle.close_ms,
        count=count,
        close_sum=close_sum,
        tr_sum=tr_sum,
        tr_count=tr_count,
        previous_close=candle.close,
        ema=ema,
        atr=atr,
    )
