from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from .config import Settings, load_settings
from .data import fetch_candles
from .models import AssetConfig, Signal
from .state import SignalState
from .strategy import analyze_asset
from .telegram import format_signal, send_telegram_message


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOGGER = logging.getLogger("crypto_swing_alerts")


def _state_key(signal: Signal) -> str:
    return f"{signal.asset}:{signal.provider}:{signal.market}"


def _send_or_print(signal: Signal, settings: Settings, state: SignalState) -> None:
    message = format_signal(signal)
    LOGGER.info("\n%s", message)

    if not signal.should_alert:
        return

    key = _state_key(signal)
    if not state.can_alert(key):
        LOGGER.info("Skipping duplicate %s alert due to cooldown.", signal.asset)
        return

    if settings.telegram_bot_token and settings.telegram_chat_id:
        send_telegram_message(settings.telegram_bot_token, settings.telegram_chat_id, message)
        LOGGER.info("Telegram alert sent for %s.", signal.asset)
    else:
        LOGGER.info("Telegram credentials are not configured; alert printed only.")

    state.mark_alerted(key)


def analyze_once_asset(asset: AssetConfig, settings: Settings) -> Signal:
    daily = fetch_candles(asset, "1d", settings.daily_lookback_days)
    hourly = fetch_candles(asset, "1h", settings.hourly_lookback_hours)
    return analyze_asset(asset, daily, hourly, settings)


def run_once(settings: Settings, state: SignalState) -> None:
    LOGGER.info("Starting scan at %s", datetime.now(tz=timezone.utc).isoformat())
    for asset in settings.assets:
        try:
            signal = analyze_once_asset(asset, settings)
        except Exception:
            LOGGER.exception("Failed to analyze %s (%s/%s).", asset.symbol, asset.provider, asset.market)
            continue
        _send_or_print(signal, settings, state)


def main() -> None:
    settings = load_settings()
    state = SignalState(settings.state_file, settings.alert_cooldown_hours)

    if settings.run_once:
        run_once(settings, state)
        return

    LOGGER.info("Running hourly loop every %s seconds.", settings.loop_seconds)
    while True:
        started = time.monotonic()
        run_once(settings, state)
        elapsed = time.monotonic() - started
        time.sleep(max(30, settings.loop_seconds - elapsed))


if __name__ == "__main__":
    main()
