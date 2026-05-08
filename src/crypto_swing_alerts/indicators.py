from __future__ import annotations

from statistics import median

from .models import Candle


def sma(values: list[float], period: int) -> list[float | None]:
    result: list[float | None] = []
    rolling = 0.0
    for index, value in enumerate(values):
        rolling += value
        if index >= period:
            rolling -= values[index - period]
        result.append(rolling / period if index >= period - 1 else None)
    return result


def ema(values: list[float], period: int) -> list[float | None]:
    if not values:
        return []
    result: list[float | None] = [None] * len(values)
    if len(values) < period:
        return result
    seed = sum(values[:period]) / period
    result[period - 1] = seed
    multiplier = 2 / (period + 1)
    previous = seed
    for index in range(period, len(values)):
        previous = (values[index] - previous) * multiplier + previous
        result[index] = previous
    return result


def rsi(values: list[float], period: int = 14) -> list[float | None]:
    result: list[float | None] = [None] * len(values)
    if len(values) <= period:
        return result

    gains = []
    losses = []
    for index in range(1, period + 1):
        change = values[index] - values[index - 1]
        gains.append(max(change, 0.0))
        losses.append(abs(min(change, 0.0)))

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    result[period] = 100.0 if avg_loss == 0 else 100 - (100 / (1 + avg_gain / avg_loss))

    for index in range(period + 1, len(values)):
        change = values[index] - values[index - 1]
        gain = max(change, 0.0)
        loss = abs(min(change, 0.0))
        avg_gain = ((avg_gain * (period - 1)) + gain) / period
        avg_loss = ((avg_loss * (period - 1)) + loss) / period
        result[index] = 100.0 if avg_loss == 0 else 100 - (100 / (1 + avg_gain / avg_loss))
    return result


def atr(candles: list[Candle], period: int = 14) -> list[float | None]:
    result: list[float | None] = [None] * len(candles)
    if len(candles) <= period:
        return result

    true_ranges = [candles[0].high - candles[0].low]
    for index in range(1, len(candles)):
        previous_close = candles[index - 1].close
        candle = candles[index]
        true_ranges.append(
            max(
                candle.high - candle.low,
                abs(candle.high - previous_close),
                abs(candle.low - previous_close),
            )
        )

    first_atr = sum(true_ranges[1 : period + 1]) / period
    result[period] = first_atr
    previous = first_atr
    for index in range(period + 1, len(candles)):
        previous = ((previous * (period - 1)) + true_ranges[index]) / period
        result[index] = previous
    return result


def previous_high(candles: list[Candle], period: int) -> float:
    window = candles[-period - 1 : -1]
    return max(candle.high for candle in window)


def previous_low(candles: list[Candle], period: int) -> float:
    window = candles[-period - 1 : -1]
    return min(candle.low for candle in window)


def median_atr_pct(candles: list[Candle], period: int = 100) -> float | None:
    atr_values = atr(candles, 14)
    pairs = [
        atr_value / candle.close
        for candle, atr_value in zip(candles[-period:], atr_values[-period:])
        if atr_value is not None and candle.close > 0
    ]
    return median(pairs) if pairs else None
