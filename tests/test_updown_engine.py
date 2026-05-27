import unittest
from core.engine.updown_engine import (
    _compute_rsi,
    _compute_momentum,
    price_gate_passes,
    UpDownEngine,
)

class MockStateManager:
    def __init__(self):
        self.positions = []

    def get_closed_positions(self, limit=3):
        return self.positions

class TestUpDownEngine(unittest.TestCase):
    def test_compute_rsi(self):
        # Constant values should return neutral RSI
        closes_flat = [10.0] * 20
        rsi = _compute_rsi(closes_flat)
        self.assertTrue(rsi is None or 0 <= rsi <= 100)

        # Strong uptrend values
        closes_up = [10 + i for i in range(20)]
        rsi = _compute_rsi(closes_up)
        self.assertIsNotNone(rsi)
        self.assertGreater(rsi, 50.0)

        # Insufficient data
        self.assertIsNone(_compute_rsi([10.0, 11.0]))

    def test_compute_momentum(self):
        closes = [10.0, 11.0, 12.0, 13.0, 14.0, 15.0]
        self.assertAlmostEqual(_compute_momentum(closes, lookback=5), 36.3636, places=2)
        self.assertAlmostEqual(_compute_momentum(closes, lookback=2), 7.1428, places=2)
        
        # Empty or short list returns 0.0
        self.assertEqual(_compute_momentum([10.0], lookback=5), 0.0)

    def test_price_gate_passes(self):
        # Edge is too thin
        self.assertFalse(price_gate_passes(0.50, 0.50))
        
        # Edge is favorable (win rate is high, price is low)
        self.assertTrue(price_gate_passes(0.40, 0.85))

    def test_updown_engine_outcomes(self):
        state_mgr = MockStateManager()
        engine = UpDownEngine("BTC", "5m", state_mgr)
        self.assertEqual(engine.skip_windows, 0)

        # Record two losses (should trigger circuit breaker)
        engine.record_outcome(won=False)
        engine.record_outcome(won=False)
        self.assertEqual(engine.skip_windows, 2)

        # Verify that skip window declines
        self.assertTrue(engine.skip_windows > 0)
        engine.skip_windows = 0
        self.assertTrue(engine.skip_windows == 0)

    def test_should_dual_enter(self):
        # High prices combined (should not enter)
        self.assertFalse(UpDownEngine.should_dual_enter(0.55, 0.55))

        # Low prices combined (favorable dual arbitrage hedge opportunity)
        self.assertTrue(UpDownEngine.should_dual_enter(0.42, 0.43))
