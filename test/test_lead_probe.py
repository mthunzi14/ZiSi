import unittest
from core.engine.lead_probe import reprice_lag_seconds


class TestLeadProbe(unittest.TestCase):
    def test_lag_is_reprice_minus_move(self):
        self.assertAlmostEqual(reprice_lag_seconds(binance_move_ts=10.0,
                                                   poly_reprice_ts=12.5), 2.5, places=4)

    def test_negative_means_we_are_behind(self):
        self.assertLess(reprice_lag_seconds(binance_move_ts=10.0, poly_reprice_ts=9.7), 0)

    def test_none_when_missing(self):
        self.assertIsNone(reprice_lag_seconds(binance_move_ts=None, poly_reprice_ts=12.5))
        self.assertIsNone(reprice_lag_seconds(binance_move_ts=10.0, poly_reprice_ts=None))
