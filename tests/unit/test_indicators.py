from dataclasses import replace
from decimal import Decimal as D

import pytest

from crypto_bot.domain.models import IndicatorState
from tests.fixtures.factories import candle


def test_ema_seed_and_first_recurrence_are_literal():
    from crypto_bot.market.indicators import update_indicators

    state = IndicatorState()
    for i in range(200):
        state = update_indicators(state, candle(i, str(i + 1), low=D(i + 1)))
    assert state.ema == D("100.5")
    state = update_indicators(state, candle(200, "201"))
    assert state.ema == D("101.5")


def test_atr_seed_uses_fourteen_ranges_with_a_previous_close():
    from crypto_bot.market.indicators import update_indicators

    state = IndicatorState()
    for i in range(14):
        state = update_indicators(state, candle(i))
    assert state.atr is None
    state = update_indicators(state, candle(14))
    assert state.atr == D("2")
    state = update_indicators(state, candle(15, "105"))
    assert state.atr == D("2.285714285714285714285714285714286")


@pytest.mark.parametrize(
    "changes",
    [
        {"closed": False},
        {"open_ms": 7200000, "close_ms": 10800000},
        {"open_ms": 0, "close_ms": 3600000},
    ],
)
def test_partial_duplicate_and_gap_cannot_advance_state(changes):
    from crypto_bot.market.indicators import update_indicators

    state = update_indicators(IndicatorState(), candle(0))
    with pytest.raises(ValueError):
        update_indicators(state, replace(candle(1), **changes))


def test_checkpoint_restart_matches_uninterrupted_replay(repo):
    from crypto_bot.market.indicators import update_indicators

    state = IndicatorState()
    for i in range(600):
        state = update_indicators(state, candle(i))
    repo.checkpoint_indicator(state, "dataset")
    recovered = repo.indicator("SOLUSDT")
    for i in range(600, 1200):
        state = update_indicators(state, candle(i, "102"))
        recovered = update_indicators(recovered, candle(i, "102"))
    assert recovered == state
