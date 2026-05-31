from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .models import AssetConfig, Provider

HYPERLIQUID_DEFAULT_SYMBOLS = {"BTC", "ETH", "HYPE", "NEAR", "PENGU", "SOL", "XMR", "XRP", "ZEC"}


@dataclass(frozen=True)
class Settings:
    assets: tuple[AssetConfig, ...]
    strategy_name: str
    telegram_bot_token: str | None
    telegram_chat_id: str | None
    run_once: bool
    loop_seconds: int
    alert_cooldown_hours: int
    min_score_to_alert: int
    max_stop_pct: float
    min_stop_pct: float
    max_distance_from_hourly_ema_pct: float
    momentum_continuation_max_pullback_pct: float
    momentum_continuation_min_24h_gain_pct: float
    leverage: float
    max_margin_loss_pct: float
    maintenance_margin_pct: float
    liquidation_buffer_pct: float
    daily_lookback_days: int
    hourly_lookback_hours: int
    state_file: Path


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return default if value is None else int(value)


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    return default if value is None else float(value)


def _asset_config(symbol: str) -> AssetConfig:
    normalized = symbol.strip().upper()
    provider = os.getenv(
        f"{normalized}_PROVIDER",
        "hyperliquid_perp" if normalized in HYPERLIQUID_DEFAULT_SYMBOLS else "binance_spot",
    )
    market = os.getenv(f"{normalized}_MARKET", normalized if provider == "hyperliquid_perp" else f"{normalized}USDT")
    if provider not in {"binance_spot", "hyperliquid_perp"}:
        raise ValueError(f"{normalized}_PROVIDER must be binance_spot or hyperliquid_perp")
    return AssetConfig(symbol=normalized, provider=provider, market=market)  # type: ignore[arg-type]


def load_settings() -> Settings:
    watchlist = os.getenv("WATCHLIST", "ZEC,HYPE,BTC,SOL,NEAR")
    assets = tuple(_asset_config(symbol) for symbol in watchlist.split(",") if symbol.strip())
    if not assets:
        raise ValueError("WATCHLIST must contain at least one asset")

    return Settings(
        assets=assets,
        strategy_name=os.getenv("STRATEGY", "swing_breakout"),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN") or None,
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID") or None,
        run_once=_env_bool("RUN_ONCE", True),
        loop_seconds=_env_int("LOOP_SECONDS", 3600),
        alert_cooldown_hours=_env_int("ALERT_COOLDOWN_HOURS", 24),
        min_score_to_alert=_env_int("MIN_SCORE_TO_ALERT", 8),
        max_stop_pct=_env_float("MAX_STOP_PCT", 0.025),
        min_stop_pct=_env_float("MIN_STOP_PCT", 0.008),
        max_distance_from_hourly_ema_pct=_env_float("MAX_DISTANCE_FROM_HOURLY_EMA_PCT", 0.035),
        momentum_continuation_max_pullback_pct=_env_float("MOMENTUM_CONTINUATION_MAX_PULLBACK_PCT", 0.015),
        momentum_continuation_min_24h_gain_pct=_env_float("MOMENTUM_CONTINUATION_MIN_24H_GAIN_PCT", 0.04),
        leverage=_env_float("LEVERAGE", 5.0),
        max_margin_loss_pct=_env_float("MAX_MARGIN_LOSS_PCT", 0.12),
        maintenance_margin_pct=_env_float("MAINTENANCE_MARGIN_PCT", 0.005),
        liquidation_buffer_pct=_env_float("LIQUIDATION_BUFFER_PCT", 0.01),
        daily_lookback_days=_env_int("DAILY_LOOKBACK_DAYS", 180),
        hourly_lookback_hours=_env_int("HOURLY_LOOKBACK_HOURS", 500),
        state_file=Path(os.getenv("STATE_FILE", ".signal_state.json")),
    )
