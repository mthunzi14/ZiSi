import unittest
from core.risk.risk_manager import (
    calculate_kelly_fraction,
    calculate_binary_kelly,
    get_drawdown_multiplier,
    check_daily_loss_halt,
    entry_price_gate,
)

class TestRiskManager(unittest.TestCase):
    def test_calculate_kelly_fraction(self):
        # Favorable edge
        kelly = calculate_kelly_fraction(0.60, 0.02, 0.015)
        self.assertGreater(kelly, 0.0)
        self.assertLessEqual(kelly, 0.05)  # capped by Kelly safety cap

        # Unfavorable edge
        kelly = calculate_kelly_fraction(0.40, 0.02, 0.015)
        self.assertEqual(kelly, 0.005)  # minimum floor check

    def test_calculate_binary_kelly(self):
        # 60% win rate at 40c (favorable edge)
        kelly = calculate_binary_kelly(0.60, 0.40)
        self.assertGreater(kelly, 0.0)

        # 40% win rate at 60c (unfavorable edge)
        kelly = calculate_binary_kelly(0.40, 0.60)
        self.assertEqual(kelly, 0.0)

        # Neutral probability
        kelly = calculate_binary_kelly(0.50, 0.50)
        self.assertEqual(kelly, 0.0)

    def test_get_drawdown_multiplier(self):
        # No drawdown
        self.assertEqual(get_drawdown_multiplier(0.0), 1.0)
        
        # 4% drawdown (should map to 0.75 multiplier in staircase)
        self.assertEqual(get_drawdown_multiplier(0.04), 0.75)

        # 13% drawdown (circuit breaker threshold of 12% breached)
        self.assertEqual(get_drawdown_multiplier(0.13), 0.0)

    def test_check_daily_loss_halt(self):
        # Safe balance
        self.assertFalse(check_daily_loss_halt(100.0, 99.0))

        # 4% session loss (DAILY_LOSS_LIMIT_PCT is 0.03 = 3%) - deactivated, should always return False
        self.assertFalse(check_daily_loss_halt(100.0, 96.0))

    def test_entry_price_gate(self):
        # Low price YES contract with high score (pass)
        self.assertTrue(entry_price_gate(0.45, 0.85, is_dual=False))

        # High price contract with low score (fail)
        self.assertFalse(entry_price_gate(0.85, 0.40, is_dual=False))

        # Dual trade ignores entry gate checks (always True)
        self.assertTrue(entry_price_gate(0.95, 0.10, is_dual=True))
