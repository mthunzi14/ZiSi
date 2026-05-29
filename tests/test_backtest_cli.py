import unittest
from tools.historical_backtest import build_report


class TestCLI(unittest.TestCase):
    def test_blocked_sweep_when_calibration_fails(self):
        from tools.backtest.calibration import CalibrationReport
        bad = CalibrationReport(passed=False, reason="mean entry-price error 0.20 >= 0.07",
                                mean_entry_error=0.20, wl_agreement=0.9, xrp_reproduced=True)
        report = build_report(calibration=bad, sweep_cells=[{"params": {"a": 1},
                              "metrics": {"trades": 3, "total_pnl": 9.0}}], baseline_trades=3)
        self.assertFalse(report["calibration"]["passed"])
        self.assertEqual(report["sweep_results"], [])  # sweep blocked
        self.assertIn("blocked", report["note"].lower())

    def test_sweep_present_when_calibration_passes(self):
        from tools.backtest.calibration import CalibrationReport
        ok = CalibrationReport(passed=True, reason="calibration passed",
                               mean_entry_error=0.05, wl_agreement=0.9, xrp_reproduced=True)
        report = build_report(calibration=ok, sweep_cells=[{"params": {"a": 1},
                              "metrics": {"trades": 3, "total_pnl": 9.0}}], baseline_trades=3)
        self.assertTrue(report["calibration"]["passed"])
        self.assertEqual(len(report["sweep_results"]), 1)
