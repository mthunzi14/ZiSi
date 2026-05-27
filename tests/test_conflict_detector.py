import unittest
from core.engine.conflict_detector import ConflictDetector, _extract_asset, _extract_direction, _is_conflict

class TestConflictDetector(unittest.TestCase):
    def setUp(self):
        self.detector = ConflictDetector()

    def test_extract_asset(self):
        self.assertEqual(_extract_asset("Will Bitcoin exceed 80k?"), "bitcoin")
        self.assertEqual(_extract_asset("Will ETH price hit 4k?"), "ethereum")
        self.assertEqual(_extract_asset("Will SOL close positive?"), "solana")
        self.assertEqual(_extract_asset("Random market"), None)

    def test_extract_direction(self):
        self.assertEqual(_extract_direction({"sentiment": "UP"}), "up")
        self.assertEqual(_extract_direction({"sentiment": "neutral"}), "neutral")

    def test_is_conflict(self):
        # Same asset, same direction -> CONFLICT
        self.assertTrue(_is_conflict("bitcoin", "bitcoin", "up", "up"))
        # Same asset, different direction -> HEDGE (NO CONFLICT)
        self.assertFalse(_is_conflict("bitcoin", "bitcoin", "up", "down"))
        # Correlated assets (BTC & ETH), same direction -> CONFLICT
        self.assertTrue(_is_conflict("bitcoin", "ethereum", "up", "up"))
        # Low correlation assets, same direction -> NO CONFLICT
        self.assertFalse(_is_conflict("solana", "xrp", "up", "up"))

    def test_detect_conflicts(self):
        poly_trades = [
            {"market": {"title": "Bitcoin Up or Down 5m"}, "signal": {"sentiment": "UP"}, "position_size": 10.0}
        ]
        kalshi_trades = [
            {"event": {"title": "Will Bitcoin close positive?"}, "signal": {"sentiment": "UP"}}
        ]
        
        conflicts = self.detector.detect(poly_trades, kalshi_trades)
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0], (0, 0.5))

    def test_apply_conflicts(self):
        poly_trades = [
            {"market": {"title": "Bitcoin Up or Down 5m"}, "signal": {"sentiment": "UP"}, "position_size": 10.0}
        ]
        conflicts = [(0, 0.5)]
        adjusted = self.detector.apply(poly_trades, conflicts)
        self.assertEqual(adjusted[0]["position_size"], 5.0)
        self.assertTrue(adjusted[0]["conflict_adjusted"])

if __name__ == "__main__":
    unittest.main()
