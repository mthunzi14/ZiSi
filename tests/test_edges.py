import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch, AsyncMock

# Add imports for our tested modules
from app.health_monitor import get_effective_max_hold_minutes
from core.engine.fair_value import decide_value_entry
from core.engine.updown_engine import UpDownEngine
from infrastructure.exchange.trader import check_and_close_paper_trades

class TestEdgesAndFilters(unittest.TestCase):

    def test_dynamic_max_hold_minutes_parsing(self):
        # 1. 5m contract title
        pos_5m = {"event_title": "[UPDOWN][BTC][5m][LATENCY_ARB] Bitcoin Up or Down"}
        self.assertEqual(get_effective_max_hold_minutes(pos_5m, 4.0), 5.0)

        # 2. 15m contract title
        pos_15m = {"event_title": "[UPDOWN][ETH][15m][FAIR_VAL] Ethereum Up or Down"}
        self.assertEqual(get_effective_max_hold_minutes(pos_15m, 4.0), 15.0)

        # 3. Default fallback for standard contract
        pos_default = {"event_title": "Will Bitcoin go to $100k?"}
        self.assertEqual(get_effective_max_hold_minutes(pos_default, 4.0), 240.0)

    def test_fair_value_safety_price_floor(self):
        # Default value params: edge_margin = 0.05
        # 1. Entry price < 0.35 (e.g. 0.30) should be clamped and blocked (returns None direction)
        # Spot is 101, strike is 100, so fp_up is high (~0.75).
        # up_price = 0.30. Expected edge_up = 0.75 - 0.30 = 0.45 (clears edge_margin),
        # but contract price 0.30 is < 0.35 safety floor.
        dec = decide_value_entry(fp_up=0.75, up_price=0.30, dn_price=0.70, t_min=2.0, total_min=5.0)
        self.assertIsNone(dec["direction"])
        self.assertEqual(dec["edge"], 0.0)

        # 2. Entry price >= 0.35 (e.g. 0.40) should pass
        dec_pass = decide_value_entry(fp_up=0.75, up_price=0.40, dn_price=0.60, t_min=2.0, total_min=5.0)
        self.assertEqual(dec_pass["direction"], "UP")
        self.assertGreater(dec_pass["edge"], 0.0)

    @patch("infrastructure.exchange.trader._open_positions")
    @patch("infrastructure.exchange.trader.execute_exit")
    @patch("infrastructure.websocket.extraterrestrial_ws_gateway.polymarket_l2_gateway")
    @patch("infrastructure.exchange.data_fetcher.get_event_current_price")
    @patch("infrastructure.exchange.data_fetcher.fetch_market_resolution")
    def test_force_exit_fallback_for_stale_trades(
        self, mock_resolution, mock_curr_price, mock_l2, mock_exit, mock_open
    ):
        # Setup mocks
        mock_resolution.return_value = None
        mock_curr_price.return_value = None
        mock_l2.get_price.return_value = (None, None)
        
        # Mock active positions: one expired trade (age 40m, limit 5m)
        now = datetime.now(timezone.utc)
        open_time = now - timedelta(minutes=40)
        
        mock_open.items.return_value = [
            ("test_order_stale", {
                "order_id": "test_order_stale",
                "market_id": "test_market_stale",
                "event_title": "[UPDOWN][BTC][5m][LATENCY_ARB]",
                "entry_price": 0.48,
                "current_price": 0.52,
                "open_time": open_time,
                "status": "OPEN",
                "direction": "YES"
            })
        ]
        
        # Run paper exit checker
        check_and_close_paper_trades()
        
        # Verify that execute_exit was called (st stale fallback triggered)
        mock_exit.assert_called_once()
        args, kwargs = mock_exit.call_args
        self.assertEqual(args[0], "test_order_stale")
        self.assertEqual(args[1], 0.52)  # Should settle at stored current_price
        self.assertEqual(kwargs.get("exit_reason"), "MARKET_EXPIRED")

if __name__ == "__main__":
    unittest.main()
