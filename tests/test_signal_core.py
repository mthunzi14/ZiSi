import unittest
from core.engine.signal_core import decide_signal, DEFAULT_SIGNAL_PARAMS


class TestDecideSignal(unittest.TestCase):
    def test_up_momentum(self):
        # rsi>60 and mom>=0.02 -> UP; score_base = 0.50 + (70-60)/40*0.35 = 0.5875
        r = decide_signal(70.0, 0.03, 0.5, "5m")
        self.assertEqual(r["direction"], "UP")
        self.assertAlmostEqual(r["score"], 0.5875, places=4)
        self.assertFalse(r["is_reversal"])
        self.assertFalse(r["blocked"])

    def test_up_blocked_by_ofi_divergence(self):
        r = decide_signal(70.0, 0.03, -0.5, "5m")
        self.assertIsNone(r["direction"])
        self.assertTrue(r["blocked"])

    def test_down_momentum(self):
        r = decide_signal(30.0, -0.03, -0.5, "5m")
        self.assertEqual(r["direction"], "DOWN")
        self.assertAlmostEqual(r["score"], 0.5875, places=4)
        self.assertFalse(r["blocked"])

    def test_reversal_oversold(self):
        r = decide_signal(15.0, 0.0, 0.0, "5m")
        self.assertEqual(r["direction"], "UP")
        self.assertAlmostEqual(r["score"], 0.70, places=4)
        self.assertTrue(r["is_reversal"])

    def test_reversal_overbought(self):
        r = decide_signal(85.0, 0.0, 0.0, "15m")
        self.assertEqual(r["direction"], "DOWN")
        self.assertTrue(r["is_reversal"])

    def test_neutral(self):
        r = decide_signal(50.0, 0.0, 0.0, "5m")
        self.assertIsNone(r["direction"])
        self.assertFalse(r["blocked"])
        self.assertFalse(r["is_reversal"])

    def test_none_rsi(self):
        r = decide_signal(None, 0.0, 0.0, "5m")
        self.assertIsNone(r["direction"])

    def test_params_are_overridable(self):
        p = dict(DEFAULT_SIGNAL_PARAMS, rsi_up=45.0)
        r = decide_signal(50.0, 0.03, 0.0, "5m", params=p)
        self.assertEqual(r["direction"], "UP")

    def test_regime_adaptive_params(self):
        # In VOLATILE_CHAOS, rsi_up is tightened to 65.0 (instead of 60.0). So RSI=62.0, mom=0.03, ofi=0.5 should be NEUTRAL/None.
        r = decide_signal(62.0, 0.03, 0.5, "5m", regime="VOLATILE_CHAOS")
        self.assertIsNone(r["direction"])

        # In COMPRESSION, rsi_up_soft is loosened to 52.0 and ofi_confirm_up is 0.35. So RSI=53.0, mom=0.015, ofi=0.4 should trigger UP.
        r = decide_signal(53.0, 0.015, 0.4, "5m", regime="COMPRESSION")
        self.assertEqual(r["direction"], "UP")
