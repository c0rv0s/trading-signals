import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from urllib.error import HTTPError

from crypto_swing_alerts.data import fetch_candles
from crypto_swing_alerts.models import AssetConfig, Candle


class DataTests(unittest.TestCase):
    def test_binance_451_falls_back_to_hyperliquid_for_known_symbol(self) -> None:
        asset = AssetConfig(symbol="BTC", provider="binance_spot", market="BTCUSDT")
        candles = [
            Candle(
                opened_at=datetime(2026, 5, 21, tzinfo=timezone.utc),
                open=100.0,
                high=101.0,
                low=99.0,
                close=100.5,
                volume=10.0,
            )
        ]
        error = HTTPError("https://api.binance.com/api/v3/klines", 451, "", {}, None)

        with self.assertLogs("crypto_swing_alerts.data", level="WARNING") as logs:
            with (
                patch("crypto_swing_alerts.data._fetch_binance_klines", side_effect=error),
                patch("crypto_swing_alerts.data._fetch_hyperliquid_candles", return_value=candles) as hyperliquid,
            ):
                result = fetch_candles(asset, "1h", 100)

        self.assertEqual(result, candles)
        self.assertIn("Binance returned HTTP 451", logs.output[0])
        hyperliquid.assert_called_once_with("BTC", "1h", 100)

    def test_binance_non_451_error_is_not_swallowed(self) -> None:
        asset = AssetConfig(symbol="BTC", provider="binance_spot", market="BTCUSDT")
        error = HTTPError("https://api.binance.com/api/v3/klines", 500, "", {}, None)

        with patch("crypto_swing_alerts.data._fetch_binance_klines", side_effect=error):
            with self.assertRaises(HTTPError):
                fetch_candles(asset, "1h", 100)


if __name__ == "__main__":
    unittest.main()
