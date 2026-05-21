from datetime import datetime, timedelta, timezone
from dataclasses import replace
from pathlib import Path
import unittest

from crypto_swing_alerts.config import Settings
from crypto_swing_alerts.models import AssetConfig, Candle
from crypto_swing_alerts.strategy import (
    STRATEGIES,
    analyze_asset,
    get_strategy,
    take_profit_r_levels_for_score,
)


def _settings() -> Settings:
    return Settings(
        assets=(),
        strategy_name="swing_breakout",
        telegram_bot_token=None,
        telegram_chat_id=None,
        run_once=True,
        loop_seconds=3600,
        alert_cooldown_hours=24,
        min_score_to_alert=7,
        max_stop_pct=0.06,
        min_stop_pct=0.002,
        max_distance_from_hourly_ema_pct=0.05,
        momentum_continuation_max_pullback_pct=0.015,
        momentum_continuation_min_24h_gain_pct=0.04,
        leverage=5.0,
        max_margin_loss_pct=0.20,
        maintenance_margin_pct=0.005,
        liquidation_buffer_pct=0.01,
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

    def test_swing_breakout_allows_strong_momentum_continuation_near_high(self) -> None:
        settings = Settings(**{**_settings().__dict__, "min_score_to_alert": 8})
        asset = AssetConfig(symbol="TEST", provider="binance_spot", market="TESTUSDT")
        daily = []
        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        for index in range(120):
            close = 50 + index * 0.25 + (2 if index % 2 else -2)
            daily.append(
                Candle(
                    opened_at=base + timedelta(days=index),
                    open=close - 0.5,
                    high=close + 0.5,
                    low=close - 1.0,
                    close=close,
                    volume=1000.0,
                )
            )
        hourly = _trend_candles(180, 1, 40)
        hourly[-25] = replace(hourly[-25], close=100.0)
        hourly[-2] = replace(hourly[-2], high=112.0)
        hourly[-1] = replace(
            hourly[-1],
            open=108.0,
            high=112.5,
            low=109.0,
            close=111.0,
            volume=3000.0,
        )

        signal = analyze_asset(asset, daily, hourly, settings)

        self.assertTrue(signal.should_alert)
        self.assertIn(
            "hourly close is holding near the prior 24-hour high after a strong 24-hour move",
            signal.reasons,
        )
        self.assertNotIn("no hourly close above the prior 24-hour high", signal.blockers)

    def test_get_strategy_rejects_unknown_name(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unknown strategy"):
            get_strategy("missing")

    def test_expected_strategies_are_registered(self) -> None:
        self.assertIn("swing_breakout", STRATEGIES)
        self.assertIn("pullback_reclaim", STRATEGIES)
        self.assertIn("momentum_continuation", STRATEGIES)
        self.assertIn("council_long", STRATEGIES)
        self.assertIn("volatility_contraction_breakout", STRATEGIES)
        self.assertIn("liquidity_sweep_reversal", STRATEGIES)
        self.assertIn("range_reclaim", STRATEGIES)
        self.assertIn("vwap_reclaim", STRATEGIES)
        self.assertIn("structure_retest", STRATEGIES)

    def test_take_profit_ladder_scales_with_score(self) -> None:
        self.assertEqual(take_profit_r_levels_for_score(9), (1.5, 3.0, 5.0))
        self.assertEqual(take_profit_r_levels_for_score(10), (2.0, 5.0, 10.0))
        self.assertEqual(take_profit_r_levels_for_score(11), (2.0, 5.0, 10.0))


if __name__ == "__main__":
    unittest.main()
