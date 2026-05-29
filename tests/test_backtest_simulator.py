import unittest
from tools.backtest.simulator import pnl, ConcurrencyGate


class TestSimulator(unittest.TestCase):
    def test_pnl_matches_engine_formula(self):
        # XRP: 50 shares (3/0.06) * (0.50-0.06) = 22.0
        self.assertAlmostEqual(pnl(size=3.0, entry=0.06, exit=0.50), 22.0, places=2)
        # ATM NO win: 40 shares (20/0.50) * (0.89-0.50) = 15.6
        self.assertAlmostEqual(pnl(size=20.0, entry=0.50, exit=0.89), 15.6, places=2)
        # Loser: 4 shares (2.4/0.60) * (0.50-0.60) = -0.40
        self.assertAlmostEqual(pnl(size=2.4, entry=0.60, exit=0.50), -0.40, places=2)

    def test_concurrency_caps(self):
        gate = ConcurrencyGate(max_per_asset=2, max_total=6)
        self.assertTrue(gate.try_open("BTC"))
        self.assertTrue(gate.try_open("BTC"))
        self.assertFalse(gate.try_open("BTC"))   # per-asset cap hit
        self.assertTrue(gate.try_open("ETH"))
        gate.close("BTC")
        self.assertTrue(gate.try_open("BTC"))    # freed a slot

    def test_total_cap(self):
        gate = ConcurrencyGate(max_per_asset=6, max_total=2)
        self.assertTrue(gate.try_open("BTC"))
        self.assertTrue(gate.try_open("ETH"))
        self.assertFalse(gate.try_open("SOL"))   # total cap hit
