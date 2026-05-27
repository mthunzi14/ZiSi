import unittest
from unittest.mock import MagicMock, patch
from core.engine.reconciliation import _run_reconcile_pass

class MockStateManager:
    def __init__(self):
        self.positions = []
        self.confirmed_calls = []

    def get_open_positions(self):
        return self.positions

    def force_confirm(self, pos):
        self.confirmed_calls.append(pos)

class TestReconciliation(unittest.TestCase):
    @patch("infrastructure.exchange.trader.check_and_close_paper_trades")
    @patch("requests.get")
    def test_run_reconcile_pass(self, mock_get, mock_close_paper):
        # Setup mocks
        mock_close_paper.return_value = [{"id": "pos_expired"}]
        
        # Mock requests.get response for order info
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": "FILLED"}
        mock_get.return_value = mock_resp

        state_mgr = MockStateManager()
        # Active position that is unconfirmed (confirmed = False)
        state_mgr.positions = [
            {"id": "order_xyz", "asset": "BTC", "confirmed": False}
        ]

        telegram_calls = []
        def mock_telegram(msg):
            telegram_calls.append(msg)

        corrected = _run_reconcile_pass(state_mgr, telegram_fn=mock_telegram)
        
        self.assertEqual(corrected, 1)
        self.assertEqual(len(state_mgr.confirmed_calls), 1)
        self.assertEqual(state_mgr.confirmed_calls[0]["id"], "order_xyz")
        self.assertEqual(len(telegram_calls), 1)
        self.assertIn("Ghost fill detected", telegram_calls[0])

if __name__ == "__main__":
    unittest.main()
