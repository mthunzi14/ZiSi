import unittest
import os
import asyncio
from core.engine.block_bundler import BlockBundler, TRADE_JOURNAL

class TestBlockBundler(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Backup existing trade journal if present
        self.backup_created = False
        if os.path.exists(TRADE_JOURNAL):
            os.rename(TRADE_JOURNAL, TRADE_JOURNAL + ".bak")
            self.backup_created = True

    def tearDown(self):
        # Clean up simulated journal
        if os.path.exists(TRADE_JOURNAL):
            os.remove(TRADE_JOURNAL)
        # Restore backup
        if self.backup_created:
            os.rename(TRADE_JOURNAL + ".bak", TRADE_JOURNAL)

    async def test_init(self):
        bundler = BlockBundler("PAPER")
        self.assertEqual(bundler.mode, "PAPER")

    async def test_submit_atomic_bundle_paper(self):
        bundler = BlockBundler("PAPER")
        orders = [
            {"symbol": "BTC", "direction": "BUY", "price": 0.52, "amount": 10.0, "market_slug": "btc-updown-5m-0521"},
            {"symbol": "ETH", "direction": "SELL", "price": 0.48, "amount": 5.0, "market_slug": "eth-updown-5m-0521"}
        ]
        res = await bundler.submit_atomic_bundle(orders)
        
        self.assertTrue(res["success"])
        self.assertEqual(len(res["transactions"]), 2)
        self.assertEqual(res["transactions"][0]["symbol"], "BTC")
        self.assertEqual(res["transactions"][1]["amount"], 5.0)
        self.assertTrue(os.path.exists(TRADE_JOURNAL))

    async def test_submit_atomic_bundle_live_unloaded(self):
        bundler = BlockBundler("LIVE")
        res = await bundler.submit_atomic_bundle([])
        self.assertFalse(res["success"])
        self.assertIn("keys not loaded", res["error"])

if __name__ == "__main__":
    unittest.main()
