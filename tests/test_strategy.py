from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest

from crypto_swing_alerts.config import Settings
from crypto_swing_alerts.models import AssetConfig, Candle
from crypto_swing_alerts.strategy import analyze_asset


def _settings() -> Settings:
    return Settings(
        assets=(),
        telegram_bot_token=None,
        telegram_chat_id=None,
        run_once=True,
        loop_seconds=3600,
        alert_cooldown_hours=24,
        min_score_to_alert=7,
        max_stop_pct=0.06,
        min_stop_pct=0.002,
        max_distance_from_hourly_ema_pct=0.05,
        daily_lookback_days=180,
        hourly_lookback_hours=500,
        state_file=Path(".signal_state_test.json"),
    )


def _trend_candles(count: int, interval_hours: int, start: float) -> list[Candle]:
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    candles = []
    for index in range(count):
        close = start + index * 0.4
        if index == count - 1:
            close += 4
        candles.append(
            Candle(
                opened_at=base + timedelta(hours=index * interval_hours),
                open=close - 0.6,
                high=close + 0.5,
                low=close - 1.0,
                close=close,
                volume=2000 if index == count - 1 else 1000,
            )
        )
    return candles


class StrategyTests(unittest.TestCase):
    def test_analyze_asset_can_score_aligned_breakout(self) -> None:
        asset = AssetConfig(symbol="TEST", provider="binance_spot", market="TESTUSDT")
        signal = analyze_asset(asset, _trend_candles(120, 24, 20), _trend_candles(180, 1, 40), _settings())
        self.assertGreaterEqual(signal.score, 7)
        self.assertGreater(signal.entry, signal.stop)
        self.assertGreater(signal.take_profit_2, signal.entry)


if __name__ == "__main__":
    unittest.main()
