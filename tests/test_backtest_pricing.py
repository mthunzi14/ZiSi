import unittest
from tools.backtest.pricing import PricingParams, contract_price, entry_price, price_path_exit


class TestPricing(unittest.TestCase):
    def test_atm_open_is_half(self):
        # At t=0 with S_t == S_0, d2 = 0 -> N(0) = 0.5
        self.assertAlmostEqual(contract_price(s_t=100.0, s_0=100.0, sigma_frac=0.01,
                                              t_min=0.0, total_min=5.0), 0.5, places=4)

    def test_monotonic_in_move(self):
        lo = contract_price(100.5, 100.0, 0.01, 2.5, 5.0)
        hi = contract_price(101.5, 100.0, 0.01, 2.5, 5.0)
        self.assertGreater(hi, lo)

    def test_clamped(self):
        p = contract_price(200.0, 100.0, 0.001, 4.99, 5.0)
        self.assertLessEqual(p, 0.99)
        self.assertGreaterEqual(p, 0.01)

    def test_reversal_entry_is_deep_discount(self):
        pp = PricingParams()
        # Very oversold RSI should yield a cheap UP entry well below 0.50
        e = entry_price(direction="UP", is_reversal=True, rsi=8.0, sigma_frac=0.02, params=pp)
        self.assertLess(e, 0.20)
        self.assertGreaterEqual(e, 0.01)

    def test_target_hit_exits_high(self):
        # A strongly favorable path should hit TARGET and exit >= 0.88
        spot_path = [100.0 + 0.2 * i for i in range(0, 11)]  # rising
        price, reason = price_path_exit(direction="UP", s_0=100.0, entry=0.50,
                                        spot_path=spot_path, minutes=[0.5 * i for i in range(11)],
                                        sigma_frac=0.01, total_min=5.0, params=PricingParams())
        self.assertEqual(reason, "TARGET_HIT")
        self.assertGreaterEqual(price, 0.88)

    def test_expired_exits_at_mid(self):
        # A flat path never hits target -> MARKET_EXPIRED at ~expired_mid (0.50)
        spot_path = [100.0 for _ in range(11)]
        price, reason = price_path_exit(direction="UP", s_0=100.0, entry=0.50,
                                        spot_path=spot_path, minutes=[0.5 * i for i in range(11)],
                                        sigma_frac=0.01, total_min=5.0, params=PricingParams())
        self.assertEqual(reason, "MARKET_EXPIRED")
        self.assertAlmostEqual(price, 0.50, places=2)
