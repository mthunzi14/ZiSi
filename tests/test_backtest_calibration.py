import unittest
from tools.backtest.calibration import CalibrationReport, evaluate


class TestCalibration(unittest.TestCase):
    def test_pass_when_within_tolerance(self):
        rep = evaluate(mean_entry_error=0.05, wl_agreement=0.90, xrp_reproduced=True)
        self.assertIsInstance(rep, CalibrationReport)
        self.assertTrue(rep.passed)

    def test_fail_on_entry_error(self):
        rep = evaluate(mean_entry_error=0.12, wl_agreement=0.90, xrp_reproduced=True)
        self.assertFalse(rep.passed)
        self.assertIn("entry-price error", rep.reason)

    def test_fail_on_wl_agreement(self):
        rep = evaluate(mean_entry_error=0.04, wl_agreement=0.70, xrp_reproduced=True)
        self.assertFalse(rep.passed)
        self.assertIn("W/L agreement", rep.reason)

    def test_fail_on_missing_xrp_vector(self):
        rep = evaluate(mean_entry_error=0.04, wl_agreement=0.95, xrp_reproduced=False)
        self.assertFalse(rep.passed)
        self.assertIn("XRP", rep.reason)
