import unittest
from core.engine.updown_engine import UpDownEngine


class _FakeState:
    def get_open_positions(self): return []


class TestFairValueEntry(unittest.TestCase):
    def _engine(self):
        return UpDownEngine("BTC", "15m", _FakeState(), lambda *a, **k: None)

    def _klines(self, last_open, last_close):
        ks = [[i * 900000, 100.0, 100.1, 99.9, 100.0, 50.0] for i in range(20)]
        ks.append([20 * 900000, last_open, max(last_open, last_close) + 0.1,
                   min(last_open, last_close) - 0.1, last_close, 50.0])
        return ks

    def test_no_edge_returns_none(self):
        eng = self._engine()
        r = eng._fair_value_entry(self._klines(100.0, 100.0), spot=100.0,
                                  up_price=0.50, dn_price=0.50, elapsed_min=1.0)
        self.assertIsNone(r["direction"])

    def test_underpriced_up_fires(self):
        eng = self._engine()
        r = eng._fair_value_entry(self._klines(100.0, 100.0), spot=100.6,
                                  up_price=0.50, dn_price=0.50, elapsed_min=7.5)
        self.assertEqual(r["direction"], "UP")
        self.assertGreater(r["edge"], 0.0)
        self.assertIn(r["archetype"], ("moderate", "near_certainty"))
