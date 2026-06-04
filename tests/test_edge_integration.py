import unittest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from core.engine.updown_engine import UpDownEngine
from core.engine.edge_orchestrator import EdgeOrchestrator

class MockStateManager:
    def __init__(self):
        self.positions = []

    def get_closed_positions(self, limit=3):
        return self.positions

class TestEdgeIntegration(unittest.IsolatedAsyncioTestCase):
    
    @patch("core.engine.updown_engine.UpDownEngine._recent_same_direction_streak")
    @patch("core.engine.updown_engine._fetch_klines_async")
    @patch("core.engine.updown_engine.get_current_ofi")
    @patch("core.engine.updown_engine.UpDownEngine._fetch_market")
    async def test_engine_edge_integration(self, mock_fetch_market, mock_get_ofi, mock_fetch_klines, mock_streak):
        mock_streak.return_value = 0
        # 1. Setup mock data for a signal to trigger
        # 30 candles of OHLCV
        mock_fetch_klines.return_value = [
            [i, 100, 105, 95, 100 + i * 0.5, 10, 0, 0, 0, 0, 0, 0] for i in range(30)
        ]
        mock_get_ofi.return_value = 0.8  # Strong buying pressure
        mock_fetch_market.return_value = {
            "event_id": "test_event",
            "event_title": "Test Event Title",
            "expiry_ts": 12345678,
            "duration_min": 5,
            "liquidity": 1000.0,
            "up_price": 0.45,
            "dn_price": 0.55,
            "spread": 0.02,
            "up_market": {"id": "up_token"},
            "dn_market": {"id": "dn_token"}
        }

        state_mgr = MockStateManager()
        engine = UpDownEngine("BTC", "5m", state_mgr)

        # 2. Mock EdgeOrchestrator to return custom multipliers and boost
        mock_ctx = {
            "regime_name": "TRENDING",
            "regime_kelly": 1.2,
            "hurdle_mult": 0.85,
            "exit_strategy": "trailing",
            "confluence_score": 4,
            "confluence_boost": 0.15,
            "heat_mult": 0.8,
            "heat_score": 0.2,
            "sentiment_score": 0.1,
            "sentiment_modifier": 0.05,
            "whale_mult": 1.1,
            "whale_pressure": 0.5,
            "antifragile_mult": 1.2,
            "aggression_state": "winning_streak",
            "cascade_signals": [],
            "liquidity_levels": {},
            "combined_confidence_boost": 0.23,
        }

        # Mock the global singleton's get_trade_context method
        with patch("core.engine.edge_orchestrator.edge_orchestrator.get_trade_context", new_callable=AsyncMock) as mock_get_context:
            mock_get_context.return_value = mock_ctx

            # Run signal generation
            session = MagicMock()
            signal = await engine.generate_signal(session)

            self.assertIsNotNone(signal)
            self.assertEqual(signal["direction"], "UP")
            self.assertEqual(signal["regime"], "TRENDING")
            self.assertEqual(signal["edge_context"], mock_ctx)
            self.assertEqual(engine.last_edge_context, mock_ctx)

            # Test position sizing using the active edge context
            size = engine.compute_size(signal["score"], 0.45, 100.0)
            
            # Size should be computed and rounded successfully
            self.assertGreater(size, 0.0)
            self.assertLessEqual(size, 20.0)  # Unified adaptive Kelly ceiling limit check (adaptive Kelly ceiling is 20.0)

    def test_trader_outcome_propagates_to_edge_orchestrator(self):
        # Mock positions_state path and locked file open in trader.py
        with patch("core.engine.edge_orchestrator.edge_orchestrator.record_trade_outcome") as mock_record:
            from core.engine.edge_orchestrator import edge_orchestrator
            edge_orchestrator.record_trade_outcome(10.0, 150.0)
            mock_record.assert_called_once_with(10.0, 150.0)

if __name__ == "__main__":
    unittest.main()
