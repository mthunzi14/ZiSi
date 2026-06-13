import unittest
from backtest.pricing import PricingParams, apply_entry_slippage, net_pnl


class TestCosts(unittest.TestCase):
    def test_slippage_worsens_entry(self):
        p = PricingParams(slippage_floor=0.01, slippage_atr_coef=0.5)
        filled = apply_entry_slippage(quoted=0.50, atr_frac=0.02, params=p)
        self.assertAlmostEqual(filled, 0.51, places=4)

    def test_slippage_scales_with_atr(self):
        p = PricingParams(slippage_floor=0.01, slippage_atr_coef=2.0)
        filled = apply_entry_slippage(quoted=0.50, atr_frac=0.05, params=p)
        self.assertAlmostEqual(filled, 0.60, places=4)

    def test_slippage_clamped_below_one(self):
        p = PricingParams(slippage_floor=0.01, slippage_atr_coef=20.0)
        self.assertLessEqual(apply_entry_slippage(quoted=0.95, atr_frac=0.5, params=p), 0.99)

    def test_net_pnl_subtracts_fee(self):
        p = PricingParams(fee_frac=0.02)
        self.assertAlmostEqual(net_pnl(gross=10.0, size=5.0, params=p), 9.90, places=4)

    def test_net_pnl_zero_fee(self):
        p = PricingParams(fee_frac=0.0)
        self.assertAlmostEqual(net_pnl(gross=-3.0, size=5.0, params=p), -3.0, places=4)
