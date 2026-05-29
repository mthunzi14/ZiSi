import unittest
from tools.backtest.sweep import cell_metrics, rank_cells, build_grid


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

    def test_build_grid_length(self):
        grid = build_grid()
        # 3 rsi_up * 3 rsi_dn * 3 target_threshold = 27 cells
        self.assertEqual(len(grid), 27)

    def test_build_grid_keys(self):
        grid = build_grid()
        required_keys = {"rsi_up", "rsi_dn", "target_threshold"}
        for cell in grid:
            self.assertEqual(set(cell.keys()), required_keys)

    def test_build_grid_values(self):
        grid = build_grid()
        rsi_ups = {c["rsi_up"] for c in grid}
        rsi_dns = {c["rsi_dn"] for c in grid}
        thresholds = {c["target_threshold"] for c in grid}
        self.assertEqual(rsi_ups, {58, 60, 62})
        self.assertEqual(rsi_dns, {38, 40, 42})
        self.assertEqual(thresholds, {0.85, 0.88, 0.90})

    def test_build_grid_no_duplicates(self):
        grid = build_grid()
        seen = set()
        for cell in grid:
            key = (cell["rsi_up"], cell["rsi_dn"], cell["target_threshold"])
            self.assertNotIn(key, seen, f"Duplicate cell: {key}")
            seen.add(key)

    def test_rank_flags_volume_drop(self):
        cells = [
            {"params": {"a": 1}, "metrics": cell_metrics([5.0, 5.0, 5.0])},   # 3 trades
            {"params": {"a": 2}, "metrics": cell_metrics([9.0])},             # 1 trade
        ]
        ranked = rank_cells(cells, baseline_trades=3, objective="total_pnl")
        # The 1-trade cell must carry a volume-reduction flag
        flagged = [c for c in ranked if c.get("below_baseline_volume")]
        self.assertTrue(any(c["params"] == {"a": 2} for c in flagged))
