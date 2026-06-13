import unittest
from backtest.klines import Candle
from backtest.pricing import PricingParams
from backtest.value_simulator import ValueConfig
from backtest.validate_fairvalue import summarize, lag_sensitivity, interpret


class _T:
    """Minimal SimTrade stand-in (only the fields summarize() reads)."""
    def __init__(self, pnl, entry):
        self.realized_pnl = pnl
        self.entry_price = entry


def _uptrend_window():
    out = []
    base = 100.0
    for i in range(30):
        out.append(Candle.from_binance([i * 60_000, base, base + 0.05, base - 0.05, base,
                                        100.0, 0, 0, 0, 50.0, 0, 0]))
    win_start = ((out[-1].open_time // 900_000) + 1) * 900_000
    prev = base
    for j in range(15):
        close = base + 0.5 * (j + 1)
        out.append(Candle.from_binance([win_start + j * 60_000, prev, close + 0.05,
                                        prev - 0.05, close, 100.0, 0, 0, 0, 50.0, 0, 0]))
        prev = close
    return out


class TestValidate(unittest.TestCase):
    def test_summarize(self):
        s = summarize([_T(2.0, 0.5), _T(-1.0, 0.6), _T(3.0, 0.4)])
        self.assertEqual(s["trades"], 3)
        self.assertAlmostEqual(s["win_rate"], 2 / 3, places=4)
        self.assertAlmostEqual(s["total_pnl"], 4.0, places=4)

    def test_summarize_empty(self):
        self.assertEqual(summarize([])["trades"], 0)

    def test_lag_sensitivity_one_row_per_lag(self):
        candles = {"BTC": _uptrend_window()}
        rows = lag_sensitivity(candles, [0, 2], ValueConfig(pricing=PricingParams(slippage_floor=0.0)))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["lag_min"], 0)
        self.assertEqual(rows[1]["lag_min"], 2)
        # lag 0 -> efficient -> no trades; lag 2 -> edge -> trades
        self.assertEqual(rows[0]["trades"], 0)
        self.assertGreaterEqual(rows[1]["trades"], 1)

    def test_interpret_flags_lag0_edge_as_caution(self):
        rows = [{"lag_min": 0, "trades": 5, "net_expectancy": 0.5}]
        self.assertIn("CAUTION", interpret(rows))

    def test_interpret_conditional_go(self):
        rows = [{"lag_min": 0, "trades": 5, "net_expectancy": -0.1},
                {"lag_min": 2, "trades": 5, "net_expectancy": 0.3}]
        self.assertIn("GO (conditional)", interpret(rows))

    def test_interpret_no_go(self):
        rows = [{"lag_min": 0, "trades": 5, "net_expectancy": -0.1},
                {"lag_min": 2, "trades": 5, "net_expectancy": -0.2}]
        self.assertIn("NO-GO", interpret(rows))
