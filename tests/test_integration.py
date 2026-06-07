import unittest
import asyncio
from app.main import TradingContext, _validate_trade_slot
from core.engine.updown_engine import UpDownEngine
from core.engine.session_governor import request_trade_slot, commit_trade_slot

class MockStateManager:
    def __init__(self):
        self.positions = []
        self.balance = 100.0

    def get_current_balance(self):
        return self.balance

    def get_closed_positions(self, limit=3):
        return self.positions

class TestIntegration(unittest.IsolatedAsyncioTestCase):
    async def test_integration_trade_flow(self):
        import core.engine.session_governor as governor
        # Reset in-memory slots to ensure total test isolation
        governor._btc_bucket_trades.clear()
        governor._candle_slots.clear()

        # Setup context
        state_mgr = MockStateManager()
        context = TradingContext(starting_balance=100.0)
        engine = UpDownEngine("BTC", "5m", state_mgr)
        
        # Define a simulated prediction signal
        signal = {
            "direction": "UP",
            "score": 0.85,
            "kelly_multiplier": 1.5,
            "signal_type": "TYPE_A_HIGH",
            "confidence": 9,
            "whale_aligned": True,
            "confluence_score": 2,
            "market": {
                "up_price": 0.45,
                "dn_price": 0.55,
                "up_market": {"id": "up_token_123"},
                "dn_market": {"id": "dn_token_456"},
                "event_id": "btc_event_5m",
                "event_title": "[UPDOWN][BTC][5m][SINGLE] BTC UPDOWN",
                "expiry_ts": 1779592200,
            },
        }


        # Validate trade slot
        from unittest.mock import patch
        from pathlib import Path
        orig_exists = Path.exists
        def mock_exists(self_path):
            if "regime_status.json" in str(self_path):
                return False
            return orig_exists(self_path)

        with patch("app.main.global_diagnostics.get_risk_multiplier", return_value=1.0), \
             patch("infrastructure.state.state_manager.get_open_positions", return_value=[]), \
             patch("pathlib.Path.exists", new=mock_exists):
            allowed, details = await _validate_trade_slot(
                context=context,
                engine=engine,
                asset="BTC",
                timeframe="5m",
                interval_minutes=5,
                signal=signal,
                current_balance=100.0,
            )

        self.assertTrue(allowed)
        self.assertEqual(details["direction"], "UP")
        self.assertGreaterEqual(details["bet_usd"], 1.0)
        self.assertFalse(details["is_dual"])

    async def test_integration_btc_governor_dedup(self):
        from unittest.mock import patch
        import core.engine.session_governor as governor
        # Reset in-memory slots
        governor._btc_bucket_trades.clear()
        governor._candle_slots.clear()

        with patch("infrastructure.state.state_manager.get_open_positions", return_value=[]):
            # Request first BTC trade slot
            allowed, reason = await request_trade_slot(
                asset="BTC",
                timeframe="5m",
                score=0.85,
                interval_minutes=5,
                open_positions=[],
                is_dual=False,
                direction="UP",
            )
            self.assertTrue(allowed)
            self.assertEqual(reason, "ok")

            # Commit slot
            await commit_trade_slot(
                asset="BTC",
                timeframe="5m",
                score=0.85,
                interval_minutes=5,
                is_dual=False,
                direction="UP",
            )

            # Request second identical BTC trade slot in same bucket (should be blocked as duplicate)
            allowed_dup, reason_dup = await request_trade_slot(
                asset="BTC",
                timeframe="5m",
                score=0.75,
                interval_minutes=5,
                open_positions=[],
                is_dual=False,
                direction="UP",
            )
            self.assertFalse(allowed_dup)
            self.assertEqual(reason_dup, "btc_duplicate_candle")
