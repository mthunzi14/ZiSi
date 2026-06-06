import unittest
from core.engine.fair_value import fair_prob_up, decide_value_entry, DEFAULT_VALUE_PARAMS


TEST_PARAMS = {
    "edge_margin": 0.05,
    "edge_target": 0.10,
    "near_certainty_prob": 0.90,
    "near_certainty_t_frac": 0.85,
    "sigma_scale": 1.0,
}


class TestFairValue(unittest.TestCase):
    def test_atm_is_half(self):
        self.assertAlmostEqual(fair_prob_up(100.0, 100.0, 0.01, 0.0, 15.0), 0.5, places=4)

    def test_monotonic_in_move(self):
        lo = fair_prob_up(100.2, 100.0, 0.01, 7.5, 15.0)
        hi = fair_prob_up(100.8, 100.0, 0.01, 7.5, 15.0)
        self.assertGreater(hi, lo)

    def test_clamped(self):
        self.assertLessEqual(fair_prob_up(200.0, 100.0, 0.001, 14.9, 15.0), 0.99)
        self.assertGreaterEqual(fair_prob_up(1.0, 100.0, 0.001, 14.9, 15.0), 0.01)

    def test_no_entry_when_no_edge(self):
        r = decide_value_entry(0.55, up_price=0.54, dn_price=0.46, t_min=1.0, total_min=15.0, params=TEST_PARAMS)
        self.assertIsNone(r["direction"])

    def test_enters_underpriced_up(self):
        r = decide_value_entry(0.62, up_price=0.50, dn_price=0.50, t_min=1.0, total_min=15.0, params=TEST_PARAMS)
        self.assertEqual(r["direction"], "UP")
        self.assertAlmostEqual(r["edge"], 0.12, places=4)
        self.assertEqual(r["archetype"], "moderate")

    def test_enters_underpriced_down(self):
        r = decide_value_entry(0.30, up_price=0.45, dn_price=0.55, t_min=1.0, total_min=15.0, params=TEST_PARAMS)
        self.assertEqual(r["direction"], "DOWN")

    def test_near_certainty_late_window(self):
        r = decide_value_entry(0.95, up_price=0.88, dn_price=0.12, t_min=14.0, total_min=15.0, params=TEST_PARAMS)
        self.assertEqual(r["direction"], "UP")
        self.assertEqual(r["archetype"], "near_certainty")

    def test_high_prob_but_early_is_moderate(self):
        r = decide_value_entry(0.95, up_price=0.80, dn_price=0.20, t_min=1.0, total_min=15.0, params=TEST_PARAMS)
        self.assertEqual(r["archetype"], "moderate")
