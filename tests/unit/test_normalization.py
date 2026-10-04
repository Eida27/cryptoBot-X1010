from decimal import Decimal as D

import pytest


def test_decimal_boundary_preserves_strings_and_rejects_floats():
    from crypto_bot.exchange.normalization import decimal_value

    assert decimal_value("0.1234567890123456789012345678901234") == D("0.1234567890123456789012345678901234")
    for value in [1.1, "NaN", "Infinity"]:
        with pytest.raises(ValueError):
            decimal_value(value)


def test_candle_close_is_exclusive_and_incomplete_is_explicit():
    from crypto_bot.exchange.normalization import normalize_candle

    bar = [0, "100", "101", "99", "100", "1", 3599999, "100"]
    assert normalize_candle("SOLUSDT", bar, 3600000).close_ms == 3600000
    assert not normalize_candle("SOLUSDT", bar, 3599999).closed


def test_funding_interval_is_required_and_not_assumed():
    from crypto_bot.exchange.errors import MissingFundingData
    from crypto_bot.exchange.normalization import normalize_funding

    value = normalize_funding({"lastFundingRate": "0.001", "nextFundingTime": 10000,
                               "fundingIntervalHours": "4", "history": ["-0.002"]}, 0)
    assert value.interval_hours == D("4")
    assert value.seven_day_max_abs_rate == D("0.002")
    with pytest.raises(MissingFundingData):
        normalize_funding({"lastFundingRate": "0.001"}, 0)


def test_missing_funding_is_not_zero():
    from crypto_bot.exchange.errors import MissingFundingData
    from crypto_bot.exchange.normalization import normalize_entry_context

    with pytest.raises(MissingFundingData):
        normalize_entry_context({"funding": None})


def test_filter_normalization_ignores_additive_fields_but_checks_assets():
    from crypto_bot.exchange.normalization import normalize_rules

    payload = {"symbol": "SOLUSDT", "status": "TRADING", "contractType": "PERPETUAL",
               "quoteAsset": "USDT", "marginAsset": "USDT", "newField": "ignored",
               "filters": [{"filterType": "PRICE_FILTER", "tickSize": "0.01"},
                           {"filterType": "LOT_SIZE", "stepSize": "0.01", "minQty": "0.01", "maxQty": "100"},
                           {"filterType": "MIN_NOTIONAL", "notional": "5"}]}
    rules = normalize_rules(payload, 12, {"notionalCap": "10000", "maintMarginRatio": "0.004"})
    assert rules.min_notional == D("5")
    assert rules.observed_ms == 12
    payload["marginAsset"] = "USDC"
    with pytest.raises(ValueError, match="USDT"):
        normalize_rules(payload, 12)
