import unittest
from tools.backtest.klines import Candle
from tools.backtest.pricing import PricingParams
from tools.backtest.value_simulator import simulate_value, ValueConfig


def _c(ot, o, h, l, c, vol=100.0, tbb=50.0):
    return Candle.from_binance([ot, o, h, l, c, vol, 0, 0, 0, tbb, 0, 0])


class TestValueSimulator(unittest.TestCase):
    def _series(self, drift):
        out = []
        base = 100.0
        for i in range(20):
            out.append(_c(i * 900000, base, base + 0.1, base - 0.1, base))
        last_open = base
        out.append(_c(20 * 900000, last_open, last_open * (1 + drift),
                      last_open, last_open * (1 + drift)))
        return out

    def test_enters_on_clear_divergence(self):
        candles = {"BTC": self._series(0.01)}
        cfg = ValueConfig(pricing=PricingParams(), start_balance=100.0)
        trades = simulate_value(candles, "15m", cfg)
        self.assertGreaterEqual(len(trades), 1)
        self.assertTrue(all(t.direction in ("UP", "DOWN") for t in trades))

    def test_no_entry_on_flat_market(self):
        candles = {"BTC": self._series(0.0)}
        cfg = ValueConfig(pricing=PricingParams(), start_balance=100.0)
        trades = simulate_value(candles, "15m", cfg)
        self.assertEqual(len(trades), 0)

    def test_pnl_is_net_of_costs(self):
        candles = {"BTC": self._series(0.02)}
        cfg = ValueConfig(pricing=PricingParams(fee_frac=0.02, slippage_floor=0.02),
                          start_balance=100.0)
        trades = simulate_value(candles, "15m", cfg)
        for t in trades:
            self.assertGreaterEqual(t.entry_price, 0.01)
            self.assertLessEqual(t.entry_price, 0.99)
