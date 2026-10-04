from decimal import Decimal as D

import pytest

from crypto_bot.domain.enums import PositionSide
from crypto_bot.domain.models import IndicatorState
from tests.fixtures.factories import candle


def replay(bars):
    from crypto_bot.market.indicators import update_indicators
    from crypto_bot.strategy.breakout import evaluate_signal

    state = IndicatorState()
    signals = []
    for i, bar in enumerate(bars):
        state = update_indicators(state, bar)
        found = evaluate_signal(bars[max(0, i - 20):i + 1], state, "strategy")
        if found:
            signals.append(found)
    return signals


@pytest.mark.parametrize("price,side", [("102", PositionSide.LONG), ("98", PositionSide.SHORT)])
def test_breakout_uses_preceding_channel_and_both_trend_directions(price, side):
    bars = [candle(i) for i in range(999)] + [candle(999, price)]
    signals = replay(bars)
    assert len(signals) == 1
    assert signals[0].side is side
    assert signals[0].close_ms == 3600000000


def test_equality_and_999_candle_warmup_produce_no_signal():
    assert replay([candle(i) for i in range(998)] + [candle(998, "102")]) == []
    assert replay([candle(i) for i in range(999)] + [candle(999, "101")]) == []


def test_future_rows_cannot_change_past_signals():
    bars = [candle(i) for i in range(1199)] + [candle(1199, "102")]
    before = replay(bars)
    extended = replay(bars + [candle(i, "1000") for i in range(1200, 1220)])
    assert extended[:len(before)] == before


def test_signal_function_rejects_partial_and_gapped_history():
    from crypto_bot.strategy.breakout import evaluate_signal
    state = IndicatorState("SOLUSDT", 0, 3600000000, 1000, ema=D("100"), atr=D("2"))
    with pytest.raises(ValueError):
        evaluate_signal([candle(999, "102", closed=False)], state, "strategy")
