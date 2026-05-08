from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest

from crypto_swing_alerts.backtest import backtest_asset
from crypto_swing_alerts.config import Settings
from crypto_swing_alerts.models import AssetConfig, Candle, Signal
from crypto_swing_alerts.strategy import STRATEGIES


def _settings() -> Settings:
    return Settings(
        assets=(),
        strategy_name="test_strategy",
        telegram_bot_token=None,
        telegram_chat_id=None,
        run_once=True,
        loop_seconds=3600,
        alert_cooldown_hours=24,
        min_score_to_alert=7,
        max_stop_pct=0.06,
        min_stop_pct=0.002,
        max_distance_from_hourly_ema_pct=0.08,
        leverage=5.0,
        max_margin_loss_pct=0.20,
        maintenance_margin_pct=0.005,
        liquidation_buffer_pct=0.01,
        daily_lookback_days=180,
        hourly_lookback_hours=500,
        state_file=Path(".signal_state_test.json"),
    )


def _candles(count: int, interval_hours: int, start: float, spike_index: int) -> list[Candle]:
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    candles = []
    for index in range(count):
        close = start + index * 0.3
        if index == spike_index:
            close += 6
        if index > spike_index:
            close += 8
        candles.append(
            Candle(
                opened_at=base + timedelta(hours=index * interval_hours),
                open=close - 0.4,
                high=close + 0.7,
                low=close - 0.8,
                close=close,
                volume=2500 if index == spike_index else 1000,
            )
        )
    return candles


class BacktestTests(unittest.TestCase):
    def test_backtest_asset_returns_trade_results(self) -> None:
        asset = AssetConfig(symbol="TEST", provider="binance_spot", market="TESTUSDT")
        settings = _settings()

        def test_strategy(asset: AssetConfig, daily: list[Candle], hourly: list[Candle], settings: Settings) -> Signal:
            last = hourly[-1]
            should_alert = len(hourly) == 130
            return Signal(
                asset=asset.symbol,
                market=asset.market,
                provider=asset.provider,
                score=10 if should_alert else 0,
                should_alert=should_alert,
                entry=last.close,
                stop=last.close - 1,
                stop_pct=0.01,
                liquidation_buffer_pct=0.07,
                take_profit_1=last.close + 1.5,
                take_profit_2=last.close + 3,
                take_profit_3=last.close + 5,
                risk_reward_to_tp2=3.0,
                reasons=(),
                blockers=(),
                generated_at=last.opened_at,
            )

        STRATEGIES["test_strategy"] = test_strategy
        try:
            result = backtest_asset(
                asset,
                _candles(120, 24, 20, spike_index=100),
                _candles(220, 1, 40, spike_index=130),
                settings,
            )
        finally:
            del STRATEGIES["test_strategy"]

        self.assertEqual(result.strategy_name, "test_strategy")
        self.assertGreaterEqual(len(result.trades), 1)


if __name__ == "__main__":
    unittest.main()
