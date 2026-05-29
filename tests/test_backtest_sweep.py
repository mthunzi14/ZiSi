import unittest
from tools.backtest.sweep import cell_metrics, rank_cells


class TestSweep(unittest.TestCase):
    def test_cell_metrics(self):
        pnls = [10.0, -2.0, 5.0, 8.0]  # 3 wins / 4
        m = cell_metrics(pnls)
        self.assertEqual(m["trades"], 4)
        self.assertAlmostEqual(m["win_rate"], 75.0, places=1)
        self.assertAlmostEqual(m["total_pnl"], 21.0, places=4)
        self.assertGreater(m["expectancy"], 0)

    def test_cell_metrics_empty(self):
        m = cell_metrics([])
        self.assertEqual(m["trades"], 0)
        self.assertEqual(m["total_pnl"], 0)

    def test_rank_flags_volume_drop(self):
        cells = [
            {"params": {"a": 1}, "metrics": cell_metrics([5.0, 5.0, 5.0])},   # 3 trades
            {"params": {"a": 2}, "metrics": cell_metrics([9.0])},             # 1 trade
        ]
        ranked = rank_cells(cells, baseline_trades=3, objective="total_pnl")
        # The 1-trade cell must carry a volume-reduction flag
        flagged = [c for c in ranked if c.get("below_baseline_volume")]
        self.assertTrue(any(c["params"] == {"a": 2} for c in flagged))
