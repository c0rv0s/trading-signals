from __future__ import annotations

from datetime import datetime, timezone

from .config import Settings
from .indicators import atr, ema, median_atr_pct, previous_high, previous_low, rsi, sma
from .models import AssetConfig, Candle, Signal


def _last(value: list[float | None]) -> float | None:
    return value[-1] if value else None


def _risk_reward(entry: float, stop: float, target: float) -> float:
    risk = entry - stop
    return 0.0 if risk <= 0 else (target - entry) / risk


def analyze_asset(asset: AssetConfig, daily: list[Candle], hourly: list[Candle], settings: Settings) -> Signal:
    reasons: list[str] = []
    blockers: list[str] = []

    if len(daily) < 80 or len(hourly) < 120:
        latest = hourly[-1].close if hourly else daily[-1].close if daily else 0.0
        return Signal(
            asset=asset.symbol,
            market=asset.market,
            provider=asset.provider,
            score=0,
            should_alert=False,
            entry=latest,
            stop=latest,
            stop_pct=0.0,
            liquidation_buffer_pct=0.0,
            take_profit_1=latest,
            take_profit_2=latest,
            take_profit_3=latest,
            risk_reward_to_tp2=0.0,
            reasons=(),
            blockers=("insufficient candle history",),
            generated_at=datetime.now(tz=timezone.utc),
        )

    daily_closes = [candle.close for candle in daily]
    hourly_closes = [candle.close for candle in hourly]
    hourly_volumes = [candle.volume for candle in hourly]
    last_daily = daily[-1]
    last_hour = hourly[-1]

    daily_ema20 = _last(ema(daily_closes, 20))
    daily_ema50 = _last(ema(daily_closes, 50))
    daily_rsi14 = _last(rsi(daily_closes, 14))
    high_20d = previous_high(daily, 20)

    hourly_ema21 = _last(ema(hourly_closes, 21))
    hourly_ema55 = _last(ema(hourly_closes, 55))
    hourly_atr14 = _last(atr(hourly, 14))
    hourly_volume_sma20 = _last(sma(hourly_volumes, 20))
    hourly_atr_pct_median = median_atr_pct(hourly, 100)
    high_24h = previous_high(hourly, 24)
    low_12h = previous_low(hourly, 12)
    low_24h = previous_low(hourly, 24)

    score = 0
    if daily_ema50 is not None and last_daily.close > daily_ema50:
        score += 2
        reasons.append("daily close is above EMA50")
    else:
        blockers.append("daily close is below EMA50")

    if daily_ema20 is not None and daily_ema50 is not None and daily_ema20 > daily_ema50:
        score += 1
        reasons.append("daily EMA20 is above EMA50")

    if daily_rsi14 is not None and 50 <= daily_rsi14 <= 72:
        score += 1
        reasons.append(f"daily RSI is constructive at {daily_rsi14:.1f}")
    elif daily_rsi14 is not None and daily_rsi14 > 78:
        blockers.append(f"daily RSI is stretched at {daily_rsi14:.1f}")

    if high_20d > 0 and last_daily.close >= high_20d * 0.88:
        score += 1
        reasons.append("daily close is within 12% of the prior 20-day high")

    if hourly_ema21 is not None and hourly_ema55 is not None and last_hour.close > hourly_ema21 > hourly_ema55:
        score += 1
        reasons.append("hourly trend is above EMA21/EMA55")
    else:
        blockers.append("hourly trend is not aligned above EMA21/EMA55")

    hourly_breakout = last_hour.close > high_24h
    if hourly_breakout:
        score += 2
        reasons.append("hourly close broke the prior 24-hour high")
    else:
        blockers.append("no hourly close above the prior 24-hour high")

    if hourly_volume_sma20 is not None and hourly_volume_sma20 > 0 and last_hour.volume >= 1.25 * hourly_volume_sma20:
        score += 1
        reasons.append("breakout volume is at least 1.25x hourly volume SMA20")

    if hourly_atr14 is not None and hourly_atr_pct_median is not None:
        current_atr_pct = hourly_atr14 / last_hour.close
        if current_atr_pct <= hourly_atr_pct_median:
            score += 1
            reasons.append("hourly ATR is compressed versus its 100-hour median")

    if low_12h > low_24h:
        score += 1
        reasons.append("recent hourly structure has a higher low")

    entry = last_hour.close
    if hourly_atr14 is None:
        stop = entry
    else:
        atr_stop = entry - (1.4 * hourly_atr14)
        structure_stop = min(last_hour.low, low_12h) * 0.997
        stop = max(atr_stop, structure_stop)

    stop_pct = 0.0 if entry <= 0 else (entry - stop) / entry
    risk = max(entry - stop, 0.0)
    take_profit_1 = entry + 1.5 * risk
    take_profit_2 = entry + 3.0 * risk
    take_profit_3 = entry + 5.0 * risk
    liquidation_buffer_pct = max(0.0, 0.085 - stop_pct)

    if stop <= 0 or stop >= entry:
        blockers.append("invalid stop calculation")
    if stop_pct > settings.max_stop_pct:
        blockers.append(f"stop is too wide for 10x rules ({stop_pct:.2%})")
    if 0 < stop_pct < settings.min_stop_pct:
        blockers.append(f"stop is too tight/noisy ({stop_pct:.2%})")
    if hourly_ema21 is not None and (entry - hourly_ema21) / entry > settings.max_distance_from_hourly_ema_pct:
        blockers.append("entry is too extended above hourly EMA21")
    if liquidation_buffer_pct <= 0:
        blockers.append("stop leaves no estimated buffer before 10x liquidation")

    should_alert = score >= settings.min_score_to_alert and not blockers

    return Signal(
        asset=asset.symbol,
        market=asset.market,
        provider=asset.provider,
        score=score,
        should_alert=should_alert,
        entry=entry,
        stop=stop,
        stop_pct=stop_pct,
        liquidation_buffer_pct=liquidation_buffer_pct,
        take_profit_1=take_profit_1,
        take_profit_2=take_profit_2,
        take_profit_3=take_profit_3,
        risk_reward_to_tp2=_risk_reward(entry, stop, take_profit_2),
        reasons=tuple(reasons),
        blockers=tuple(blockers),
        generated_at=datetime.now(tz=timezone.utc),
    )
