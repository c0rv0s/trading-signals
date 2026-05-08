from datetime import datetime, timedelta, timezone
import unittest

from crypto_swing_alerts.indicators import atr, ema, rsi, sma
from crypto_swing_alerts.models import Candle


def _candles(count: int) -> list[Candle]:
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [
        Candle(
            opened_at=base + timedelta(hours=index),
            open=100 + index,
            high=102 + index,
            low=99 + index,
            close=101 + index,
            volume=1000 + index,
        )
        for index in range(count)
    ]


class IndicatorTests(unittest.TestCase):
    def test_sma_warms_up_then_tracks_average(self) -> None:
        self.assertEqual(sma([1, 2, 3, 4], 3), [None, None, 2.0, 3.0])

    def test_ema_seeds_from_simple_average(self) -> None:
        values = [1, 2, 3, 4, 5]
        result = ema(values, 3)
        self.assertIsNone(result[0])
        self.assertEqual(result[2], 2.0)
        self.assertIsNotNone(result[-1])

    def test_rsi_reaches_100_when_no_losses(self) -> None:
        result = rsi(list(range(1, 30)), 14)
        self.assertEqual(result[-1], 100.0)

    def test_atr_returns_values_after_warmup(self) -> None:
        result = atr(_candles(30), 14)
        self.assertIsNone(result[13])
        self.assertIsNotNone(result[14])


if __name__ == "__main__":
    unittest.main()
