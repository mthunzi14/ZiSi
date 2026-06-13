import unittest
from backtest.klines import Candle
from backtest.pricing import PricingParams
from backtest.value_simulator import simulate_value, ValueConfig


def _c(ot, o, h, l, c, vol=100.0, tbb=50.0):
    return Candle.from_binance([ot, o, h, l, c, vol, 0, 0, 0, tbb, 0, 0])


class TestValueSimulator(unittest.TestCase):
    def _one_window_uptrend(self):
        """30 flat prior 1m candles (ATR history) + one 15-candle window in a strong uptrend.
        No lookahead: the window path is real per-minute closes, resolution = window close vs open."""
        out = []
        base = 100.0
        for i in range(30):  # prior flat history (skipped by the ATR-history guard)
            out.append(_c(i * 60_000, base, base + 0.05, base - 0.05, base))
        win_start = ((out[-1].open_time // 900_000) + 1) * 900_000
        prev = base
        for j in range(15):
            close = base + 0.5 * (j + 1)  # strong, steady uptrend
            out.append(_c(win_start + j * 60_000, prev, close + 0.05, prev - 0.05, close))
            prev = close
        return out

    def test_lag_zero_no_edge(self):
        # Efficient market (quote == fair) -> zero edge -> no trades.
        candles = {"BTC": self._one_window_uptrend()}
        cfg = ValueConfig(pricing=PricingParams(slippage_floor=0.0), lag_min=0)
        self.assertEqual(len(simulate_value(candles, cfg)), 0)

    def test_lag_creates_edge(self):
        # A lagging market -> divergence -> an UP entry that wins (window resolves up).
        candles = {"BTC": self._one_window_uptrend()}
        cfg = ValueConfig(pricing=PricingParams(slippage_floor=0.0), lag_min=3)
        trades = simulate_value(candles, cfg)
        self.assertGreaterEqual(len(trades), 1)
        self.assertEqual(trades[0].direction, "UP")
        self.assertGreater(trades[0].realized_pnl, 0)

    def test_entry_price_bounded(self):
        candles = {"BTC": self._one_window_uptrend()}
        cfg = ValueConfig(pricing=PricingParams(fee_frac=0.02, slippage_floor=0.02), lag_min=2)
        for t in simulate_value(candles, cfg):
            self.assertGreaterEqual(t.entry_price, 0.01)
            self.assertLessEqual(t.entry_price, 0.99)
