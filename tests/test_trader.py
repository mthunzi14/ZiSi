import unittest
from infrastructure.exchange.trader import (
    place_order,
    get_current_position,
    count_open_trades,
    has_open_position,
    get_all_open_trades,
)

class TestTrader(unittest.TestCase):
    def tearDown(self):
        import infrastructure.exchange.trader as trader
        trader._open_positions.clear()
        trader.persist_positions()

    def test_paper_trading_execution(self):
        # Clear any old test positions in-memory
        import infrastructure.exchange.trader as trader
        trader._open_positions.clear()

        # Place a simulated YES order
        order = place_order(
            event_id="test_event_123",
            market_id="test_market_abc",
            amount_dollars=10.0,
            direction="YES",
            entry_price=0.50,
            event_title="[UPDOWN][BTC][5m][SINGLE] Test BTC prediction",
        )

        self.assertIsNotNone(order)
        self.assertEqual(order["status"], "FILLED")
        self.assertEqual(order["shares_acquired"], 20)  # 10.0 / 0.50 = 20 shares
        self.assertEqual(order["amount_spent"], 10.0)

        # Test query states
        self.assertEqual(count_open_trades(), 1)
        self.assertTrue(has_open_position(order["order_id"]))

        # Check position retrieval
        pos = get_current_position(order["order_id"])
        self.assertIsNotNone(pos)
        self.assertEqual(pos["shares_held"], 20)
        self.assertEqual(pos["current_value"], 10.0)
        self.assertEqual(pos["unrealized_pnl"], 0.0)

        # Prune and verify list
        all_open = get_all_open_trades()
        self.assertEqual(len(all_open), 1)
        self.assertEqual(all_open[0]["order_id"], order["order_id"])
