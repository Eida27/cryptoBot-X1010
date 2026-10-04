from crypto_bot.domain.models import Candle


def validate_candle(candle: Candle) -> None:
    prices = (candle.open, candle.high, candle.low, candle.close)
    if any(not p.is_finite() or p <= 0 for p in prices):
        raise ValueError(f"{candle.symbol}:{candle.open_ms}: invalid price")
    if candle.low > min(candle.open, candle.close) or candle.high < max(candle.open, candle.close) or candle.low > candle.high:
        raise ValueError(f"{candle.symbol}:{candle.open_ms}: impossible OHLC")
    if any(not v.is_finite() or v < 0 for v in (candle.volume, candle.quote_volume)):
        raise ValueError(f"{candle.symbol}:{candle.open_ms}: invalid volume")
    if candle.close_ms <= candle.open_ms:
        raise ValueError(f"{candle.symbol}:{candle.open_ms}: invalid interval")
