import unittest
from core.engine.signal_core import decide_signal

class TestVolatilityFilters(unittest.TestCase):
    def test_low_volatility_veto(self):
        # Base indicators that would normally trigger an UP signal
        # RSI = 65, Mom = 0.03, OFI = 0.50
        # Timeframe = 5m
        
        # Scenario 1: Volatility is normal/high (ATR % = 50, BBW % = 50)
        res_normal = decide_signal(
            rsi=65.0,
            mom=0.03,
            ofi=0.50,
            timeframe="5m",
            atr_percentile=50.0,
            bbw_percentile=50.0,
            regime="TRENDING"
        )
        self.assertEqual(res_normal["direction"], "UP")
        self.assertFalse(res_normal["blocked"])
        
        # Scenario 2: ATR Percentile is low (ATR % = 15, BBW % = 50)
        res_low_atr = decide_signal(
            rsi=65.0,
            mom=0.03,
            ofi=0.50,
            timeframe="5m",
            atr_percentile=15.0,
            bbw_percentile=50.0,
            regime="TRENDING"
        )
        self.assertTrue(res_low_atr["blocked"])
        
        # Scenario 3: BBW Percentile is low (ATR % = 50, BBW % = 15) - should NOT block under optimal ATR-only filter
        res_low_bbw = decide_signal(
            rsi=65.0,
            mom=0.03,
            ofi=0.50,
            timeframe="5m",
            atr_percentile=50.0,
            bbw_percentile=15.0,
            regime="TRENDING"
        )
        self.assertFalse(res_low_bbw["blocked"])
        
        # Scenario 4: Timeframe is 15m, low volatility (ATR % = 18, BBW % = 50)
        res_15m_low = decide_signal(
            rsi=65.0,
            mom=0.03,
            ofi=0.50,
            timeframe="15m",
            atr_percentile=18.0,
            bbw_percentile=50.0,
            regime="TRENDING"
        )
        self.assertTrue(res_15m_low["blocked"])
        
        # Scenario 5: Reversal snipes should bypass the low-volatility veto (contrarian by design)
        # RSI = 14.0 (reversal UP, strictly less than 15.0 reversal_lo)
        res_reversal = decide_signal(
            rsi=14.0,
            mom=0.03,
            ofi=0.50,
            timeframe="5m",
            atr_percentile=10.0,
            bbw_percentile=10.0,
            regime="TRENDING"
        )
        self.assertEqual(res_reversal["direction"], "UP")
        self.assertTrue(res_reversal["is_reversal"])
        self.assertFalse(res_reversal["blocked"])

if __name__ == "__main__":
    unittest.main()
