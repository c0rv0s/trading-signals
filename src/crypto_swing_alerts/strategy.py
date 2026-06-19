from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from .config import Settings
from .indicators import atr, ema, median_atr_pct, previous_high, previous_low, rsi, sma
from .models import AssetConfig, Candle, Signal
from .risk import liquidation_buffer_pct

StrategyFn = Callable[[AssetConfig, list[Candle], list[Candle], Settings], Signal]


def _last(value: list[float | None]) -> float | None:
    return value[-1] if value else None


def _risk_reward(entry: float, stop: float, target: float) -> float:
    risk = entry - stop
    return 0.0 if risk <= 0 else (target - entry) / risk


def take_profit_r_levels_for_score(score: int) -> tuple[float, float, float]:
    if score >= 10:
        return (2.0, 5.0, 10.0)
    return (1.5, 3.0, 5.0)


def _rolling_vwap(candles: list[Candle], period: int) -> float | None:
    if len(candles) < period:
        return None
    window = candles[-period:]
    volume = sum(candle.volume for candle in window)
    if volume <= 0:
        return None
    notional = sum(((candle.high + candle.low + candle.close) / 3) * candle.volume for candle in window)
    return notional / volume


def _daily_uptrend(daily: list[Candle], reasons: list[str], blockers: list[str], strict: bool = False) -> int:
    daily_closes = [candle.close for candle in daily]
    last_daily = daily[-1]
    daily_ema20 = _last(ema(daily_closes, 20))
    daily_ema50 = _last(ema(daily_closes, 50))
    daily_rsi14 = _last(rsi(daily_closes, 14))
    score = 0
    if strict:
        if daily_ema20 is not None and daily_ema50 is not None and last_daily.close > daily_ema20 > daily_ema50:
            score += 3
            reasons.append("daily close is above stacked EMA20/EMA50")
        else:
            blockers.append("daily EMA stack is not bullish")
    else:
        if daily_ema50 is not None and last_daily.close > daily_ema50:
            score += 2
            reasons.append("daily close is above EMA50")
        else:
            blockers.append("daily trend is below EMA50")
        if daily_ema20 is not None and daily_ema50 is not None and daily_ema20 > daily_ema50:
            score += 1
            reasons.append("daily EMA20 is above EMA50")
    if daily_rsi14 is not None and 45 <= daily_rsi14 <= 78:
        score += 1
        reasons.append(f"daily RSI is trend-friendly at {daily_rsi14:.1f}")
    elif daily_rsi14 is not None and daily_rsi14 > 84:
        blockers.append(f"daily RSI is overheated at {daily_rsi14:.1f}")
    return score


def _empty_signal(asset: AssetConfig, daily: list[Candle], hourly: list[Candle], blocker: str) -> Signal:
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
        blockers=(blocker,),
        generated_at=datetime.now(tz=timezone.utc),
    )


def _finalize_long_signal(
    asset: AssetConfig,
    settings: Settings,
    score: int,
    entry: float,
    stop: float,
    reasons: list[str],
    blockers: list[str],
    target_2_r: float = 3.0,
) -> Signal:
    stop_pct = 0.0 if entry <= 0 else (entry - stop) / entry
    risk = max(entry - stop, 0.0)
    tp1_r, tp2_r, tp3_r = take_profit_r_levels_for_score(score)
    take_profit_1 = entry + tp1_r * risk
    take_profit_2 = entry + max(target_2_r, tp2_r) * risk
    take_profit_3 = entry + max(target_2_r, tp3_r) * risk
    buffer_pct = liquidation_buffer_pct(
        entry,
        stop,
        settings.leverage,
        settings.maintenance_margin_pct,
    )

    if stop <= 0 or stop >= entry:
        blockers.append("invalid stop calculation")
    if stop_pct > settings.max_stop_pct:
        blockers.append(f"stop is too wide for 10x rules ({stop_pct:.2%})")
    margin_loss_pct = stop_pct * settings.leverage
    if margin_loss_pct > settings.max_margin_loss_pct:
        blockers.append(
            f"stop risks too much margin at {settings.leverage:.1f}x "
            f"({margin_loss_pct:.2%} > {settings.max_margin_loss_pct:.2%})"
        )
    if 0 < stop_pct < settings.min_stop_pct:
        blockers.append(f"stop is too tight/noisy ({stop_pct:.2%})")
    if buffer_pct < settings.liquidation_buffer_pct:
        blockers.append(
            f"stop is too close to estimated {settings.leverage:.1f}x liquidation "
            f"({buffer_pct:.2%} buffer)"
        )

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
        liquidation_buffer_pct=buffer_pct,
        take_profit_1=take_profit_1,
        take_profit_2=take_profit_2,
        take_profit_3=take_profit_3,
        risk_reward_to_tp2=_risk_reward(entry, stop, take_profit_2),
        reasons=tuple(reasons),
        blockers=tuple(blockers),
        generated_at=datetime.now(tz=timezone.utc),
    )


def analyze_swing_breakout(asset: AssetConfig, daily: list[Candle], hourly: list[Candle], settings: Settings) -> Signal:
    reasons: list[str] = []
    blockers: list[str] = []

    if len(daily) < 80 or len(hourly) < 120:
        return _empty_signal(asset, daily, hourly, "insufficient candle history")

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
    close_24h_ago = hourly[-25].close

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
    elif (
        high_24h > 0
        and close_24h_ago > 0
        and hourly_ema21 is not None
        and hourly_ema55 is not None
        and last_hour.close > hourly_ema21 > hourly_ema55
        and last_hour.close >= high_24h * (1 - settings.momentum_continuation_max_pullback_pct)
        and last_hour.close >= close_24h_ago * (1 + settings.momentum_continuation_min_24h_gain_pct)
    ):
        score += 2
        reasons.append("hourly close is holding near the prior 24-hour high after a strong 24-hour move")
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

    if hourly_ema21 is not None and (entry - hourly_ema21) / entry > settings.max_distance_from_hourly_ema_pct:
        blockers.append("entry is too extended above hourly EMA21")

    return _finalize_long_signal(asset, settings, score, entry, stop, reasons, blockers)


def analyze_pullback_reclaim(asset: AssetConfig, daily: list[Candle], hourly: list[Candle], settings: Settings) -> Signal:
    if len(daily) < 80 or len(hourly) < 120:
        return _empty_signal(asset, daily, hourly, "insufficient candle history")

    reasons: list[str] = []
    blockers: list[str] = []
    daily_closes = [candle.close for candle in daily]
    hourly_closes = [candle.close for candle in hourly]
    hourly_volumes = [candle.volume for candle in hourly]
    last_daily = daily[-1]
    last_hour = hourly[-1]
    previous_hour = hourly[-2]

    daily_ema20 = _last(ema(daily_closes, 20))
    daily_ema50 = _last(ema(daily_closes, 50))
    daily_rsi14 = _last(rsi(daily_closes, 14))
    hourly_ema21_values = ema(hourly_closes, 21)
    hourly_ema55 = _last(ema(hourly_closes, 55))
    hourly_ema21 = _last(hourly_ema21_values)
    previous_hourly_ema21 = hourly_ema21_values[-2]
    hourly_atr14 = _last(atr(hourly, 14))
    hourly_volume_sma20 = _last(sma(hourly_volumes, 20))
    low_12h = previous_low(hourly, 12)
    low_24h = previous_low(hourly, 24)

    score = 0
    if daily_ema50 is not None and last_daily.close > daily_ema50:
        score += 2
        reasons.append("daily close is above EMA50")
    else:
        blockers.append("daily trend is below EMA50")
    if daily_ema20 is not None and daily_ema50 is not None and daily_ema20 > daily_ema50:
        score += 1
        reasons.append("daily EMA20 is above EMA50")
    if daily_rsi14 is not None and 45 <= daily_rsi14 <= 76:
        score += 1
        reasons.append(f"daily RSI supports trend continuation at {daily_rsi14:.1f}")
    elif daily_rsi14 is not None and daily_rsi14 > 82:
        blockers.append(f"daily RSI is overheated at {daily_rsi14:.1f}")

    if hourly_ema21 is not None and hourly_ema55 is not None and hourly_ema21 > hourly_ema55:
        score += 1
        reasons.append("hourly EMA21 is above EMA55")
    else:
        blockers.append("hourly EMA trend is not aligned")
    if previous_hourly_ema21 is not None and previous_hour.low <= previous_hourly_ema21 and last_hour.close > hourly_ema21:
        score += 2
        reasons.append("price pulled into EMA21 and reclaimed it")
    else:
        blockers.append("no EMA21 pullback reclaim")
    if last_hour.close > previous_hour.high:
        score += 1
        reasons.append("reclaim candle closed above previous hourly high")
    if hourly_volume_sma20 is not None and hourly_volume_sma20 > 0 and last_hour.volume >= hourly_volume_sma20:
        score += 1
        reasons.append("reclaim volume is above hourly volume SMA20")
    if low_12h > low_24h:
        score += 1
        reasons.append("recent hourly structure has a higher low")

    entry = last_hour.close
    stop = entry if hourly_atr14 is None else max(entry - 1.2 * hourly_atr14, min(last_hour.low, previous_hour.low) * 0.997)
    return _finalize_long_signal(asset, settings, score, entry, stop, reasons, blockers, target_2_r=2.5)


def analyze_momentum_continuation(asset: AssetConfig, daily: list[Candle], hourly: list[Candle], settings: Settings) -> Signal:
    if len(daily) < 80 or len(hourly) < 120:
        return _empty_signal(asset, daily, hourly, "insufficient candle history")

    reasons: list[str] = []
    blockers: list[str] = []
    daily_closes = [candle.close for candle in daily]
    hourly_closes = [candle.close for candle in hourly]
    hourly_volumes = [candle.volume for candle in hourly]
    last_daily = daily[-1]
    last_hour = hourly[-1]

    daily_ema20 = _last(ema(daily_closes, 20))
    daily_ema50 = _last(ema(daily_closes, 50))
    daily_rsi14 = _last(rsi(daily_closes, 14))
    hourly_ema21 = _last(ema(hourly_closes, 21))
    hourly_ema55 = _last(ema(hourly_closes, 55))
    hourly_rsi14 = _last(rsi(hourly_closes, 14))
    hourly_atr14 = _last(atr(hourly, 14))
    hourly_volume_sma20 = _last(sma(hourly_volumes, 20))
    high_12h = previous_high(hourly, 12)
    low_6h = previous_low(hourly, 6)

    score = 0
    if daily_ema20 is not None and daily_ema50 is not None and last_daily.close > daily_ema20 > daily_ema50:
        score += 3
        reasons.append("daily close is above stacked EMA20/EMA50")
    else:
        blockers.append("daily EMA stack is not bullish")
    if daily_rsi14 is not None and 48 <= daily_rsi14 <= 78:
        score += 1
        reasons.append(f"daily RSI is trend-friendly at {daily_rsi14:.1f}")
    elif daily_rsi14 is not None and daily_rsi14 > 84:
        blockers.append(f"daily RSI is overheated at {daily_rsi14:.1f}")
    if hourly_ema21 is not None and hourly_ema55 is not None and last_hour.close > hourly_ema21 > hourly_ema55:
        score += 1
        reasons.append("hourly price is above stacked EMA21/EMA55")
    else:
        blockers.append("hourly trend is not stacked bullish")
    if last_hour.close > high_12h:
        score += 2
        reasons.append("hourly close broke the prior 12-hour high")
    else:
        blockers.append("no 12-hour momentum breakout")
    if hourly_rsi14 is not None and 55 <= hourly_rsi14 <= 76:
        score += 1
        reasons.append(f"hourly RSI confirms momentum at {hourly_rsi14:.1f}")
    elif hourly_rsi14 is not None and hourly_rsi14 > 82:
        blockers.append(f"hourly RSI is stretched at {hourly_rsi14:.1f}")
    if hourly_volume_sma20 is not None and hourly_volume_sma20 > 0 and last_hour.volume >= 0.9 * hourly_volume_sma20:
        score += 1
        reasons.append("momentum volume is near or above hourly volume SMA20")

    entry = last_hour.close
    stop = entry if hourly_atr14 is None else max(entry - hourly_atr14, low_6h * 0.998)
    if hourly_ema21 is not None and (entry - hourly_ema21) / entry > settings.max_distance_from_hourly_ema_pct:
        blockers.append("entry is too extended above hourly EMA21")
    return _finalize_long_signal(asset, settings, score, entry, stop, reasons, blockers, target_2_r=2.0)


def analyze_council_long(asset: AssetConfig, daily: list[Candle], hourly: list[Candle], settings: Settings) -> Signal:
    member_names = ("swing_breakout", "pullback_reclaim", "momentum_continuation")
    member_settings = Settings(**{**settings.__dict__, "strategy_name": "swing_breakout"})
    signals = [STRATEGIES[name](asset, daily, hourly, member_settings) for name in member_names]
    approved = [signal for signal in signals if signal.should_alert]
    if len(approved) < 2:
        best = max(signals, key=lambda signal: signal.score)
        reasons = [f"{len(approved)} of {len(member_names)} council strategies approved"]
        reasons.extend(f"{name}: score {signal.score}" for name, signal in zip(member_names, signals))
        return Signal(
            asset=best.asset,
            market=best.market,
            provider=best.provider,
            score=best.score,
            should_alert=False,
            entry=best.entry,
            stop=best.stop,
            stop_pct=best.stop_pct,
            liquidation_buffer_pct=best.liquidation_buffer_pct,
            take_profit_1=best.take_profit_1,
            take_profit_2=best.take_profit_2,
            take_profit_3=best.take_profit_3,
            risk_reward_to_tp2=best.risk_reward_to_tp2,
            reasons=tuple(reasons),
            blockers=("fewer than 2 council strategies approved",),
            generated_at=best.generated_at,
        )

    selected = min(approved, key=lambda signal: signal.stop_pct)
    reasons = [f"{len(approved)} of {len(member_names)} council strategies approved"]
    for name, signal in zip(member_names, signals):
        if signal.should_alert:
            reasons.append(f"{name} approved with score {signal.score}")
    return Signal(
        asset=selected.asset,
        market=selected.market,
        provider=selected.provider,
        score=sum(signal.score for signal in approved),
        should_alert=True,
        entry=selected.entry,
        stop=selected.stop,
        stop_pct=selected.stop_pct,
        liquidation_buffer_pct=selected.liquidation_buffer_pct,
        take_profit_1=selected.take_profit_1,
        take_profit_2=selected.take_profit_2,
        take_profit_3=selected.take_profit_3,
        risk_reward_to_tp2=selected.risk_reward_to_tp2,
        reasons=tuple(reasons),
        blockers=(),
        generated_at=selected.generated_at,
    )


def analyze_volatility_contraction_breakout(
    asset: AssetConfig, daily: list[Candle], hourly: list[Candle], settings: Settings
) -> Signal:
    if len(daily) < 80 or len(hourly) < 140:
        return _empty_signal(asset, daily, hourly, "insufficient candle history")
    reasons: list[str] = []
    blockers: list[str] = []
    score = _daily_uptrend(daily, reasons, blockers)
    hourly_closes = [candle.close for candle in hourly]
    hourly_volumes = [candle.volume for candle in hourly]
    last_hour = hourly[-1]
    hourly_ema21 = _last(ema(hourly_closes, 21))
    hourly_ema55 = _last(ema(hourly_closes, 55))
    hourly_atr14 = _last(atr(hourly, 14))
    atr_median = median_atr_pct(hourly, 100)
    volume_sma20 = _last(sma(hourly_volumes, 20))
    range_high = previous_high(hourly, 36)
    range_low = previous_low(hourly, 36)
    range_pct = (range_high - range_low) / last_hour.close if last_hour.close > 0 else 0.0

    if hourly_ema21 is not None and hourly_ema55 is not None and hourly_ema21 > hourly_ema55:
        score += 1
        reasons.append("hourly EMA21 is above EMA55")
    else:
        blockers.append("hourly trend is not constructive")
    if hourly_atr14 is not None and atr_median is not None and hourly_atr14 / last_hour.close <= 0.85 * atr_median:
        score += 2
        reasons.append("hourly ATR is materially compressed")
    else:
        blockers.append("volatility is not compressed enough")
    if 0 < range_pct <= 0.075:
        score += 1
        reasons.append("36-hour range is tight enough for expansion")
    if last_hour.close > range_high:
        score += 2
        reasons.append("price broke above the 36-hour range")
    else:
        blockers.append("no range breakout")
    if volume_sma20 is not None and volume_sma20 > 0 and last_hour.volume >= 1.15 * volume_sma20:
        score += 1
        reasons.append("breakout volume is above normal")

    stop = last_hour.close if hourly_atr14 is None else max(last_hour.close - 1.15 * hourly_atr14, range_low * 0.998)
    return _finalize_long_signal(asset, settings, score, last_hour.close, stop, reasons, blockers, target_2_r=3.0)


def analyze_liquidity_sweep_reversal(asset: AssetConfig, daily: list[Candle], hourly: list[Candle], settings: Settings) -> Signal:
    if len(daily) < 80 or len(hourly) < 120:
        return _empty_signal(asset, daily, hourly, "insufficient candle history")
    reasons: list[str] = []
    blockers: list[str] = []
    score = _daily_uptrend(daily, reasons, blockers)
    hourly_closes = [candle.close for candle in hourly]
    last_hour = hourly[-1]
    previous_hour = hourly[-2]
    hourly_ema55 = _last(ema(hourly_closes, 55))
    hourly_atr14 = _last(atr(hourly, 14))
    swept_low = previous_low(hourly[:-1], 24)
    body_high = max(last_hour.open, last_hour.close)
    body_low = min(last_hour.open, last_hour.close)
    wick_pct = (body_low - last_hour.low) / last_hour.close if last_hour.close > 0 else 0.0

    if hourly_ema55 is not None and last_hour.close > hourly_ema55:
        score += 1
        reasons.append("reversal closed above hourly EMA55")
    else:
        blockers.append("reversal did not reclaim EMA55")
    if last_hour.low < swept_low and last_hour.close > swept_low:
        score += 3
        reasons.append("price swept the prior 24-hour low and reclaimed it")
    else:
        blockers.append("no 24-hour low sweep reclaim")
    if last_hour.close > previous_hour.high:
        score += 1
        reasons.append("reversal closed above previous hourly high")
    if wick_pct >= 0.006:
        score += 1
        reasons.append("lower wick shows rejection")
    if last_hour.close > body_high - (body_high - body_low) * 0.35:
        score += 1
        reasons.append("candle closed in the upper body area")

    stop = max(last_hour.low * 0.997, last_hour.close - 1.25 * hourly_atr14) if hourly_atr14 is not None else last_hour.low
    return _finalize_long_signal(asset, settings, score, last_hour.close, stop, reasons, blockers, target_2_r=3.0)


def analyze_range_reclaim(asset: AssetConfig, daily: list[Candle], hourly: list[Candle], settings: Settings) -> Signal:
    if len(daily) < 80 or len(hourly) < 150:
        return _empty_signal(asset, daily, hourly, "insufficient candle history")
    reasons: list[str] = []
    blockers: list[str] = []
    score = _daily_uptrend(daily, reasons, blockers)
    hourly_closes = [candle.close for candle in hourly]
    last_hour = hourly[-1]
    previous_hour = hourly[-2]
    range_high = previous_high(hourly[:-1], 72)
    range_low = previous_low(hourly[:-1], 72)
    midpoint = (range_high + range_low) / 2
    hourly_ema21 = _last(ema(hourly_closes, 21))
    hourly_atr14 = _last(atr(hourly, 14))

    if previous_hour.close < midpoint and last_hour.close > midpoint:
        score += 2
        reasons.append("price reclaimed the 72-hour range midpoint")
    else:
        blockers.append("no range midpoint reclaim")
    if last_hour.close > hourly_ema21 if hourly_ema21 is not None else False:
        score += 1
        reasons.append("price reclaimed hourly EMA21")
    if last_hour.close < range_high:
        score += 1
        reasons.append("entry remains inside range with room to high")
    else:
        blockers.append("entry is already above range high")
    if (range_high - last_hour.close) > 2 * (last_hour.close - min(last_hour.low, range_low)):
        score += 1
        reasons.append("range high offers at least 2R of room")

    stop = max(range_low * 0.998, last_hour.close - 1.2 * hourly_atr14) if hourly_atr14 is not None else range_low
    return _finalize_long_signal(asset, settings, score, last_hour.close, stop, reasons, blockers, target_2_r=2.0)


def analyze_vwap_reclaim(asset: AssetConfig, daily: list[Candle], hourly: list[Candle], settings: Settings) -> Signal:
    if len(daily) < 80 or len(hourly) < 120:
        return _empty_signal(asset, daily, hourly, "insufficient candle history")
    reasons: list[str] = []
    blockers: list[str] = []
    score = _daily_uptrend(daily, reasons, blockers)
    hourly_closes = [candle.close for candle in hourly]
    last_hour = hourly[-1]
    previous_hour = hourly[-2]
    vwap_72 = _rolling_vwap(hourly, 72)
    previous_vwap_72 = _rolling_vwap(hourly[:-1], 72)
    hourly_ema55 = _last(ema(hourly_closes, 55))
    hourly_atr14 = _last(atr(hourly, 14))

    if previous_vwap_72 is not None and vwap_72 is not None and previous_hour.close < previous_vwap_72 < last_hour.close:
        score += 3
        reasons.append("price reclaimed 72-hour VWAP")
    else:
        blockers.append("no 72-hour VWAP reclaim")
    if hourly_ema55 is not None and last_hour.close > hourly_ema55:
        score += 1
        reasons.append("price is above hourly EMA55")
    if previous_hour.low < previous_hour.close and last_hour.close > previous_hour.high:
        score += 1
        reasons.append("reclaim also broke previous hourly high")

    reference = vwap_72 or last_hour.low
    stop = max(reference * 0.997, last_hour.close - 1.15 * hourly_atr14) if hourly_atr14 is not None else reference * 0.997
    return _finalize_long_signal(asset, settings, score, last_hour.close, stop, reasons, blockers, target_2_r=2.5)


def analyze_structure_retest(asset: AssetConfig, daily: list[Candle], hourly: list[Candle], settings: Settings) -> Signal:
    if len(daily) < 80 or len(hourly) < 150:
        return _empty_signal(asset, daily, hourly, "insufficient candle history")
    reasons: list[str] = []
    blockers: list[str] = []
    score = _daily_uptrend(daily, reasons, blockers, strict=True)
    hourly_closes = [candle.close for candle in hourly]
    last_hour = hourly[-1]
    previous_breakout_high = previous_high(hourly[:-6], 48)
    recent_high = previous_high(hourly[:-1], 6)
    recent_low = previous_low(hourly[:-1], 6)
    hourly_ema21 = _last(ema(hourly_closes, 21))
    hourly_atr14 = _last(atr(hourly, 14))

    if recent_high > previous_breakout_high:
        score += 2
        reasons.append("market structure broke above prior 48-hour high")
    else:
        blockers.append("no prior structure break")
    if recent_low <= previous_breakout_high <= last_hour.close:
        score += 2
        reasons.append("price retested and held the prior breakout level")
    else:
        blockers.append("no breakout-level retest hold")
    if hourly_ema21 is not None and last_hour.close > hourly_ema21:
        score += 1
        reasons.append("retest closed above EMA21")
    if last_hour.close > hourly[-2].high:
        score += 1
        reasons.append("retest confirmation closed above previous high")

    stop_anchor = min(recent_low, previous_breakout_high)
    stop = max(stop_anchor * 0.997, last_hour.close - 1.2 * hourly_atr14) if hourly_atr14 is not None else stop_anchor
    return _finalize_long_signal(asset, settings, score, last_hour.close, stop, reasons, blockers, target_2_r=3.0)


STRATEGIES: dict[str, StrategyFn] = {
    "council_long": analyze_council_long,
    "liquidity_sweep_reversal": analyze_liquidity_sweep_reversal,
    "momentum_continuation": analyze_momentum_continuation,
    "pullback_reclaim": analyze_pullback_reclaim,
    "range_reclaim": analyze_range_reclaim,
    "structure_retest": analyze_structure_retest,
    "swing_breakout": analyze_swing_breakout,
    "volatility_contraction_breakout": analyze_volatility_contraction_breakout,
    "vwap_reclaim": analyze_vwap_reclaim,
}


def get_strategy(name: str) -> StrategyFn:
    try:
        return STRATEGIES[name]
    except KeyError as error:
        available = ", ".join(sorted(STRATEGIES))
        raise ValueError(f"Unknown strategy '{name}'. Available strategies: {available}") from error


def effective_strategy_name(asset: AssetConfig, settings: Settings) -> str:
    return asset.strategy_name or settings.strategy_name


def analyze_asset(asset: AssetConfig, daily: list[Candle], hourly: list[Candle], settings: Settings) -> Signal:
    return get_strategy(effective_strategy_name(asset, settings))(asset, daily, hourly, settings)
