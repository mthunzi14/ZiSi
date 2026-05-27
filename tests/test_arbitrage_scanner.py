import unittest
from unittest.mock import MagicMock, patch
import aiohttp
from strategies.arbitrage.arbitrage_scanner import (
    ArbitrageScanner,
    normalize_text,
    check_overlap,
)

class TestArbitrageScanner(unittest.IsolatedAsyncioTestCase):
    def test_normalize_text(self):
        self.assertEqual(normalize_text("Will BTC hit 80k?"), "will btc hit 80k")
        self.assertEqual(normalize_text("Ethereum is UP"), "ethereum is up")

    def test_check_overlap(self):
        self.assertTrue(check_overlap("Will Bitcoin hit 80k?", "Will Bitcoin hit 80k today?"))
        self.assertFalse(check_overlap("Will Ethereum close up?", "Solana price check"))

    def test_calculate_kelly_size(self):
        scanner = ArbitrageScanner()
        # Spread is favorable -> size > 0
        size = scanner.calculate_kelly_size(cost=0.50, spread=0.08, balance=100.0)
        self.assertGreater(size, 0.0)
        
        # Spread is negative -> size == 0
        size_neg = scanner.calculate_kelly_size(cost=0.50, spread=-0.02, balance=100.0)
        self.assertEqual(size_neg, 0.0)

    def test_scan_for_pairs(self):
        scanner = ArbitrageScanner()
        poly_markets = [
            {
                "question": "Will Bitcoin close positive today?", 
                "id": "poly_btc_1",
                "outcomePrices": [0.55, 0.45]
            }
        ]
        kalshi_events = [
            {
                "markets": [
                    {
                        "title": "Will Bitcoin close positive today?", 
                        "ticker": "KX_BTC_1"
                    }
                ]
            }
        ]
        
        pairs = scanner.scan_for_pairs(poly_markets, kalshi_events)
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0][0]["id"], "poly_btc_1")
        self.assertEqual(pairs[0][1]["ticker"], "KX_BTC_1")

if __name__ == "__main__":
    unittest.main()
