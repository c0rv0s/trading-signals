import unittest

from crypto_swing_alerts.risk import liquidation_buffer_pct, liquidation_price_long, margin_return_pct


class RiskTests(unittest.TestCase):
    def test_liquidation_price_uses_leverage_and_maintenance_margin(self) -> None:
        self.assertAlmostEqual(liquidation_price_long(100.0, 10.0, 0.005), 90.5)
        self.assertAlmostEqual(liquidation_price_long(100.0, 5.0, 0.005), 80.5)

    def test_liquidation_buffer_pct_measures_stop_distance_above_liquidation(self) -> None:
        self.assertAlmostEqual(liquidation_buffer_pct(100.0, 95.0, 10.0, 0.005), 0.045)

    def test_margin_return_pct_scales_price_return_by_leverage(self) -> None:
        self.assertAlmostEqual(margin_return_pct(100.0, 105.0, 5.0), 0.25)
        self.assertAlmostEqual(margin_return_pct(100.0, 98.0, 5.0), -0.10)


if __name__ == "__main__":
    unittest.main()
