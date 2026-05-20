import unittest
from unittest.mock import patch

from crypto_swing_alerts.config import load_settings


class ConfigTests(unittest.TestCase):
    def test_known_watchlist_symbols_default_to_hyperliquid(self) -> None:
        with patch.dict("os.environ", {"WATCHLIST": "BTC,ETH,SOL,XMR,PENGU,XRP,ZEC,HYPE"}, clear=True):
            settings = load_settings()

        self.assertTrue(all(asset.provider == "hyperliquid_perp" for asset in settings.assets))
        self.assertEqual(tuple(asset.market for asset in settings.assets), ("BTC", "ETH", "SOL", "XMR", "PENGU", "XRP", "ZEC", "HYPE"))

    def test_provider_override_is_preserved(self) -> None:
        with patch.dict("os.environ", {"WATCHLIST": "BTC", "BTC_PROVIDER": "binance_spot"}, clear=True):
            settings = load_settings()

        self.assertEqual(settings.assets[0].provider, "binance_spot")
        self.assertEqual(settings.assets[0].market, "BTCUSDT")


if __name__ == "__main__":
    unittest.main()
