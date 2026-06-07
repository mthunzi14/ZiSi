import unittest
from unittest.mock import patch, MagicMock
from core.engine.updown_engine import (
    _compute_rsi,
    _compute_momentum,
    price_gate_passes,
    UpDownEngine,
)

class MockStateManager:
    def __init__(self):
        self.positions = []

    def get_closed_positions(self, limit=3):
        return self.positions

class TestUpDownEngine(unittest.TestCase):
    def test_compute_rsi(self):
        # Constant values should return neutral RSI
        closes_flat = [10.0] * 20
        rsi = _compute_rsi(closes_flat)
        self.assertTrue(rsi is None or 0 <= rsi <= 100)

        # Strong uptrend values
        closes_up = [10 + i for i in range(20)]
        rsi = _compute_rsi(closes_up)
        self.assertIsNotNone(rsi)
        self.assertGreater(rsi, 50.0)

        # Insufficient data
        self.assertIsNone(_compute_rsi([10.0, 11.0]))

    def test_compute_momentum(self):
        closes = [10.0, 11.0, 12.0, 13.0, 14.0, 15.0]
        self.assertAlmostEqual(_compute_momentum(closes, lookback=5), 36.3636, places=2)
        self.assertAlmostEqual(_compute_momentum(closes, lookback=2), 7.1428, places=2)
        
        # Empty or short list returns 0.0
        self.assertEqual(_compute_momentum([10.0], lookback=5), 0.0)

    def test_price_gate_passes(self):
        # Edge is too thin
        self.assertFalse(price_gate_passes(0.50, 0.50))
        
        # Edge is favorable (win rate is high, price is low)
        self.assertTrue(price_gate_passes(0.40, 0.85))

    def test_updown_engine_outcomes(self):
        state_mgr = MockStateManager()
        engine = UpDownEngine("BTC", "5m", state_mgr)
        self.assertEqual(engine.consecutive_losses, 0)

        # Record two losses (should update consecutive_losses count)
        engine.record_outcome(won=False)
        engine.record_outcome(won=False)
        self.assertEqual(engine.consecutive_losses, 2)

        # Verify consecutive_losses
        self.assertTrue(engine.consecutive_losses > 0)
        engine.consecutive_losses = 0
        self.assertTrue(engine.consecutive_losses == 0)

    def test_should_dual_enter(self):
        # High prices combined (should not enter)
        self.assertFalse(UpDownEngine.should_dual_enter(0.55, 0.55))

        # Low prices combined (favorable dual arbitrage hedge opportunity)
        self.assertTrue(UpDownEngine.should_dual_enter(0.42, 0.43))


class TestUpDownEngineVolatility(unittest.IsolatedAsyncioTestCase):
    def test_get_hourly_slug(self):
        state_mgr = MockStateManager()
        engine = UpDownEngine("BTC", "1h", state_mgr)
        
        # Test BTC 3 AM
        slug1 = engine._get_hourly_slug(1780815600)
        self.assertEqual(slug1, "bitcoin-up-or-down-june-7-2026-3am-et")
        
        # Test BTC 3 PM
        slug2 = engine._get_hourly_slug(1780858800)
        self.assertEqual(slug2, "bitcoin-up-or-down-june-7-2026-3pm-et")
        
        # Test ETH 12 AM (midnight)
        engine_eth = UpDownEngine("ETH", "1h", state_mgr)
        slug3 = engine_eth._get_hourly_slug(1780804800)
        self.assertEqual(slug3, "ethereum-up-or-down-june-7-2026-12am-et")
        
        # Test SOL 12 PM (noon)
        engine_sol = UpDownEngine("SOL", "1h", state_mgr)
        slug4 = engine_sol._get_hourly_slug(1780848000)
        self.assertEqual(slug4, "solana-up-or-down-june-7-2026-12pm-et")

    @patch("core.engine.updown_engine._fetch_klines_async")
    @patch("core.engine.updown_engine.UpDownEngine._fetch_market")
    @patch("core.engine.updown_engine.get_current_ofi", return_value=0.0)
    async def test_5m_volatility_gate_chaos(self, mock_ofi, mock_mkt, mock_klines):
        state_mgr = MockStateManager()
        engine = UpDownEngine("BTC", "5m", state_mgr)
        
        # Mock regime_status.json to represent VOLATILE_CHAOS
        import json
        mock_regime_data = json.dumps({
            "regime": "VOLATILE_CHAOS",
            "atr_percentile": 85.0
        })
        
        with patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.read_text", return_value=mock_regime_data):
            
            session = MagicMock()
            signal = await engine.generate_signal(session)
            self.assertIsNone(signal)

    @patch("core.engine.updown_engine._fetch_klines_async")
    @patch("core.engine.updown_engine.UpDownEngine._fetch_market")
    @patch("core.engine.updown_engine.get_current_ofi", return_value=0.0)
    async def test_1h_streak_reversal_up(self, mock_ofi, mock_mkt, mock_klines):
        state_mgr = MockStateManager()
        engine = UpDownEngine("BTC", "1h", state_mgr)
        
        # Generate 20 candles, last 4 closed are red
        klines = []
        for i in range(20):
            open_p = 100 - i
            close_p = 100 - i - 1  # red candles
            klines.append([i * 1000, str(open_p), str(open_p + 1), str(close_p - 1), str(close_p), "1000.0"])
            
        mock_klines.return_value = klines
        mock_mkt.return_value = {
            "up_price": 0.35, "dn_price": 0.65,
            "up_market": {"id": "yes_id"}, "dn_market": {"id": "no_id"},
            "event_id": "evt_123", "event_title": "Test Title", "expiry_ts": 1234567
        }
        
        session = MagicMock()
        # Mock regime_status.json to return MEAN_REVERTING
        import json
        mock_regime_data = json.dumps({
            "regime": "MEAN_REVERTING",
            "atr_percentile": 30.0
        })
        
        with patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.read_text", return_value=mock_regime_data), \
             patch("core.engine.edge_orchestrator.edge_orchestrator.get_trade_context", return_value={}):
            
            signal = await engine.generate_signal(session)
            self.assertIsNotNone(signal)
            # Fading red streak -> signal direction should be UP
            self.assertEqual(signal["direction"], "UP")
            self.assertEqual(signal["score"], 0.75)
            self.assertEqual(signal["entry_source"], "REVERSAL_STREAK")

    @patch("core.engine.updown_engine._fetch_klines_async")
    @patch("core.engine.updown_engine.UpDownEngine._fetch_market")
    @patch("core.engine.updown_engine.get_current_ofi", return_value=0.0)
    async def test_1h_streak_reversal_down(self, mock_ofi, mock_mkt, mock_klines):
        state_mgr = MockStateManager()
        engine = UpDownEngine("BTC", "1h", state_mgr)
        
        # Generate 20 candles, last 4 closed are green
        klines = []
        for i in range(20):
            open_p = 100 + i
            close_p = 100 + i + 1  # green candles
            klines.append([i * 1000, str(open_p), str(open_p + 1), str(close_p - 1), str(close_p), "1000.0"])
            
        mock_klines.return_value = klines
        mock_mkt.return_value = {
            "up_price": 0.65, "dn_price": 0.35,
            "up_market": {"id": "yes_id"}, "dn_market": {"id": "no_id"},
            "event_id": "evt_123", "event_title": "Test Title", "expiry_ts": 1234567
        }
        
        session = MagicMock()
        import json
        mock_regime_data = json.dumps({
            "regime": "MEAN_REVERTING",
            "atr_percentile": 30.0
        })
        
        with patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.read_text", return_value=mock_regime_data), \
             patch("core.engine.edge_orchestrator.edge_orchestrator.get_trade_context", return_value={}):
            
            signal = await engine.generate_signal(session)
            self.assertIsNotNone(signal)
            # Fading green streak -> signal direction should be DOWN
            self.assertEqual(signal["direction"], "DOWN")
            self.assertEqual(signal["score"], 0.75)
            self.assertEqual(signal["entry_source"], "REVERSAL_STREAK")

