import unittest
import json
import os
import time
from unittest.mock import patch, mock_open
from core.risk.position_sizer import PositionSizer, get_rolling_wr_multiplier, _expiry_multiplier

class TestPositionSizer(unittest.TestCase):
    def test_init_and_reset(self):
        sizer = PositionSizer(account_balance=100.0, max_cycle_capital=50.0, max_trades_per_cycle=10)
        self.assertEqual(sizer.account_balance, 100.0)
        self.assertEqual(sizer.capital_used, 0.0)
        self.assertEqual(sizer.trades_this_cycle, 0)
        
        sizer._capital_used = 10.0
        sizer._trades = 2
        sizer.reset_cycle()
        self.assertEqual(sizer.capital_used, 0.0)
        self.assertEqual(sizer.trades_this_cycle, 0)

    def test_expiry_multiplier(self):
        # Far expiry -> 1.0 multiplier
        m_far = {"resolutionDate": "2030-12-31T23:59:59Z"}
        self.assertAlmostEqual(_expiry_multiplier(m_far), 1.0)
        
        # Missing expiry -> 1.0 multiplier
        self.assertAlmostEqual(_expiry_multiplier({}), 1.0)

    def test_calculate_size_limit(self):
        sizer = PositionSizer(account_balance=100.0, max_cycle_capital=10.0, max_trades_per_cycle=2)
        signal = {
            "signal_type": "TYPE_A_HIGH",
            "kelly_multiplier": 1.0,
            "confidence": 9,
            "affected_cryptos": ["BTC"]
        }
        market = {
            "market_type": "UP_DOWN",
            "resolutionDate": "2030-12-31T23:59:59Z"
        }
        
        # First trade
        size1 = sizer.calculate(signal, market, category_weight=1.0)
        self.assertGreater(size1, 0.0)
        
        # Second trade
        size2 = sizer.calculate(signal, market, category_weight=1.0)
        self.assertGreater(size2, 0.0)
        
        # Third trade should return 0.0 due to max_trades_per_cycle = 2 limit
        size3 = sizer.calculate(signal, market, category_weight=1.0)
        self.assertEqual(size3, 0.0)

    @patch("core.risk.position_sizer.get_rolling_wr_multiplier")
    def test_calculate_kelly_sizing(self, mock_wr):
        mock_wr.return_value = 1.2
        sizer = PositionSizer(account_balance=100.0, max_cycle_capital=100.0, max_trades_per_cycle=10)
        
        signal = {
            "signal_type": "TYPE_A_HIGH",
            "kelly_multiplier": 1.0,
            "confidence": 9,
            "affected_cryptos": ["BTC"]
        }
        market = {
            "market_type": "UP_DOWN",
            "resolutionDate": "2030-12-31T23:59:59Z"
        }
        
        size = sizer.calculate(signal, market, category_weight=1.0)
        # Sizing should factor in account balance * 0.005 * sig_mult (1.5) * mkt_mult (1.0) * kelly_mult (1.0) * wr_mult (1.2) = 0.90
        self.assertAlmostEqual(size, 0.90)

if __name__ == "__main__":
    unittest.main()
