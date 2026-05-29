import unittest
from tools.backtest.simulator import pnl, ConcurrencyGate, sized_bet


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


class TestSizedBet(unittest.TestCase):
    def test_15pct_bankroll_cap(self):
        # score=0.95, balance=1000, price=0.40 (scalar=1.0)
        # kelly=0.05, raw=0.05*1000=50
        # max_usd_cap = max(5, min(20, 5+(0.95-0.50)*40)) = max(5, min(20, 23)) = max(5, 20) = 20
        # usd = max(1, min(50, 20)) = 20; 15% of 1000=150; min(20, 150)=20
        result = sized_bet(score=0.95, price=0.40, balance=1000.0)
        self.assertAlmostEqual(result, 20.0, places=2)

    def test_bankroll_cap_is_binding(self):
        # score=0.95, balance=10 -> 15% = 1.50
        # raw=0.05*10=0.5, max_usd_cap=20, usd=max(1,0.5)=1.0; min(1, 1.5)=1.0
        result = sized_bet(score=0.95, price=0.40, balance=10.0)
        self.assertLessEqual(result, 10.0 * 0.15 + 0.01)  # within 15% (+tiny rounding)

    def test_floor_at_one_dollar(self):
        # Very low score and normal balance: floor at $1 should apply when 15% cap > $1
        # score=0.51, balance=100 -> kelly=0.01, raw=0.01*100=1.0, max_usd_cap=5.4
        # usd=max(1, min(1.0, 5.4))=1.0; 15% of 100=15; min(1,15)=1.0
        result = sized_bet(score=0.51, price=0.40, balance=100.0)
        self.assertGreaterEqual(result, 1.0)

    def test_15pct_hard_cap(self):
        # balance=20, score=0.95 -> raw = 0.05*20=1.0, max_usd_cap=23, 15% of 20=3 -> result=1.0
        result = sized_bet(score=0.95, price=0.40, balance=20.0)
        self.assertLessEqual(result, 20.0 * 0.15)

    def test_price_scalar_high_price(self):
        # price > 0.78 -> scalar=0.25: score=0.85, balance=100
        # kelly=0.03, raw=0.03*100*0.25=0.75 -> floor at $1
        result = sized_bet(score=0.85, price=0.80, balance=100.0)
        self.assertGreaterEqual(result, 1.0)
        # should be less than ATM version
        atm = sized_bet(score=0.85, price=0.40, balance=100.0)
        self.assertLessEqual(result, atm)

    def test_regime_mult_scales_bet(self):
        base = sized_bet(score=0.75, price=0.40, balance=100.0, regime_mult=1.0)
        scaled = sized_bet(score=0.75, price=0.40, balance=100.0, regime_mult=2.0)
        # Scaled should be larger (or equal if cap binds)
        self.assertGreaterEqual(scaled, base)
