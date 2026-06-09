import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch, AsyncMock

# Add imports for our tested modules
from app.health_monitor import get_effective_max_hold_minutes
from core.engine.fair_value import decide_value_entry
from core.engine.updown_engine import UpDownEngine
from infrastructure.exchange.trader import check_and_close_paper_trades

class TestEdgesAndFilters(unittest.IsolatedAsyncioTestCase):

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
        # 1. Entry price < 0.35 (e.g. 0.30) should now pass since safety floor is removed
        # Spot is 101, strike is 100, so fp_up is high (~0.75).
        # up_price = 0.30. Expected edge_up = 0.75 - 0.30 = 0.45 (clears edge_margin).
        dec = decide_value_entry(fp_up=0.75, up_price=0.30, dn_price=0.70, t_min=2.0, total_min=5.0)
        self.assertEqual(dec["direction"], "UP")
        self.assertAlmostEqual(dec["edge"], 0.45, places=4)

        # 2. Entry price >= 0.35 (e.g. 0.40) should pass
        dec_pass = decide_value_entry(fp_up=0.75, up_price=0.40, dn_price=0.60, t_min=2.0, total_min=5.0)
        self.assertEqual(dec_pass["direction"], "UP")
        self.assertGreater(dec_pass["edge"], 0.0)

    def test_fv_15m_edge_margin(self):
        # 1. 5m contract with edge 0.11 (above default 0.10 margin) should pass
        # fp_up = 0.70, up_price = 0.59 => edge = 0.11
        dec_5m = decide_value_entry(fp_up=0.70, up_price=0.59, dn_price=0.41, t_min=2.0, total_min=5.0, timeframe="5m")
        self.assertEqual(dec_5m["direction"], "UP")

        # 2. 15m contract with edge 0.11 should be blocked because it requires 0.12 margin
        dec_15m = decide_value_entry(fp_up=0.70, up_price=0.59, dn_price=0.41, t_min=2.0, total_min=15.0, timeframe="15m")
        self.assertEqual(dec_15m["direction"], None)

        # 3. 15m contract with edge 0.13 should pass
        # fp_up = 0.70, up_price = 0.57 => edge = 0.13
        dec_15m_pass = decide_value_entry(fp_up=0.70, up_price=0.57, dn_price=0.43, t_min=2.0, total_min=15.0, timeframe="15m")
        self.assertEqual(dec_15m_pass["direction"], "UP")

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

    async def test_session_governor_is_dual_dedup_and_cooldown(self):
        import core.engine.session_governor as governor
        from core.engine.session_governor import request_trade_slot, commit_trade_slot, cancel_trade_slot
        
        # Clean state
        governor._lat_arb_in_flight.clear()
        governor._lat_arb_cooldowns.clear()

        with patch("infrastructure.state.state_manager.get_open_positions", return_value=[]):
            # First request allowed
            allowed, reason = await request_trade_slot("BTC", "5m", 0.85, 5, [], is_dual=True, direction="UP")
            self.assertTrue(allowed)
            self.assertEqual(reason, "dual_ok")
            self.assertIn(("BTC", "5m"), governor._lat_arb_in_flight)

            # Second request blocked (in-flight)
            allowed2, reason2 = await request_trade_slot("BTC", "5m", 0.85, 5, [], is_dual=True, direction="UP")
            self.assertFalse(allowed2)
            self.assertEqual(reason2, "lat_inflight_BTC_5m")

            # Cancel release
            await cancel_trade_slot("BTC", "5m")
            self.assertNotIn(("BTC", "5m"), governor._lat_arb_in_flight)

            # Re-request allowed
            allowed3, reason3 = await request_trade_slot("BTC", "5m", 0.85, 5, [], is_dual=True, direction="UP")
            self.assertTrue(allowed3)

            # Commit slot removes from in-flight and starts cooldown
            await commit_trade_slot("BTC", "5m", 0.85, 5, is_dual=True, direction="UP")
            self.assertNotIn(("BTC", "5m"), governor._lat_arb_in_flight)
            self.assertIn(("BTC", "5m"), governor._lat_arb_cooldowns)

            # Request blocked by cooldown
            allowed4, reason4 = await request_trade_slot("BTC", "5m", 0.85, 5, [], is_dual=True, direction="UP")
            self.assertFalse(allowed4)
            self.assertEqual(reason4, "lat_cooldown_BTC_5m")

    @patch("app.main.request_trade_slot", return_value=(True, "slot_ok"))
    @patch("app.main.global_diagnostics.get_risk_multiplier", return_value=1.0)
    @patch("infrastructure.state.state_manager.get_open_positions", return_value=[])
    @patch("core.analytics.sentiment_daemon.sentiment_filter.get_size_multiplier", return_value=1.0)
    async def test_sizing_caps_risk_control(self, mock_fng, mock_open, mock_risk, mock_request):
        from app.main import _validate_trade_slot
        
        # Mock engine
        engine = MagicMock()
        # Mock compute_size to return large sizing (e.g. $50.00)
        engine.compute_size.return_value = 50.00
        
        # Test Global Kelly Cap (6% balance or $12)
        # Balance = $100 -> 6% is $6.00. bet_usd should be capped to $6.00.
        context = MagicMock()
        context.log_skip = MagicMock()
        
        # Use score=0.92 to pass the FV coin-flip gate (requires ≥0.88 at 40-60¢)
        signal_fv = {
            "direction": "UP",
            "score": 0.92,
            "entry_source": "FAIR_VAL",
            "market": {"up_price": 0.35, "dn_price": 0.65}
        }

        allowed, details = await _validate_trade_slot(
            context, engine, "BTC", "5m", 5, signal_fv, current_balance=100.0
        )
        self.assertTrue(allowed)
        self.assertAlmostEqual(details["bet_usd"], 30.00) # capped to 30% of $100

        # Balance = $300 -> 30% is $90.00. bet_usd should be capped to $50.00 (global bet cap ceiling).
        allowed2, details2 = await _validate_trade_slot(
            context, engine, "BTC", "5m", 5, signal_fv, current_balance=300.0
        )
        self.assertTrue(allowed2)
        self.assertAlmostEqual(details2["bet_usd"], 50.00) # capped to global max $50
        
        # Test SIGNAL specific Cap ($10.00)
        # Let's say we have high balance, e.g. $200. 6% of balance is $12.00.
        # But this is a SIG trade, so it should be capped to $10.00.
        signal_sig = {
            "direction": "UP",
            "score": 0.85,
            "entry_source": "SIG",
            "market": {"up_price": 0.42, "dn_price": 0.58},  # above 40c floor, outside MIDGUARD score gate
            "whale_aligned": True,
            "confluence_score": 2,
        }

        allowed3, details3 = await _validate_trade_slot(
            context, engine, "BTC", "5m", 5, signal_sig, current_balance=200.0
        )
        self.assertTrue(allowed3)
        self.assertAlmostEqual(details3["bet_usd"], 10.00) # capped to signal limit $10

    @patch("core.engine.updown_engine._fetch_klines_async")
    @patch("core.engine.updown_engine._cache")
    async def test_fv_archetype_and_sig_price_gates(self, mock_cache, mock_klines):
        from core.engine.updown_engine import UpDownEngine
        from datetime import datetime
        
        mock_klines.return_value = [
            [0, 100.0, 105.0, 95.0, 102.0, 1000.0] for _ in range(30)
        ]
        
        state_mgr = MagicMock()
        state_mgr.get_closed_positions.return_value = []
        
        # REBUILD: FV-ARCH-GATE REMOVED. A valid moderate FV (edge 0.15, fp 0.75) in a
        # daytime non-RANGE regime is no longer blocked — FV is gated by directional
        # confidence + edge now, so this must PRODUCE a FAIR_VAL signal.
        from unittest.mock import AsyncMock
        engine = UpDownEngine("BTC", "15m", state_mgr)
        engine._fair_value_entry = MagicMock(return_value={
            "direction": "UP", "edge": 0.15, "archetype": "moderate", "fp_up": 0.75,
            "sigma_frac": 0.01, "confidence": 0.72,
        })
        _benign_ctx = {"regime_name": "MEAN_REVERTING", "whale_pressure": 0.0,
                       "confluence_score": 2, "regime_kelly": 1.0}
        import json as _json
        _regime_json = _json.dumps({"regime": "MEAN_REVERTING", "atr_percentile": 30.0})
        with patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.read_text", return_value=_regime_json), \
             patch("core.engine.edge_orchestrator.edge_orchestrator.get_trade_context",
                   new_callable=AsyncMock, return_value=_benign_ctx), \
             patch("core.engine.updown_engine.UpDownEngine._fetch_market",
                   return_value={"up_price": 0.45, "dn_price": 0.55,
                                 "up_market": {"id": "yes_id"}, "dn_market": {"id": "no_id"},
                                 "event_id": "evt_fv",
                                 "event_title": "[UPDOWN][BTC][15m][FAIR_VAL] BTC Up or Down",
                                 "expiry_ts": 1234567}), \
             patch("datetime.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 6, 5, 12, 0, 0)
            mock_dt.fromtimestamp = datetime.fromtimestamp
            session = MagicMock()
            signal = await engine.generate_signal(session)
            self.assertIsNotNone(signal)
            self.assertEqual(signal["entry_source"], "FAIR_VAL")

        # Test P5 & P6 SIGNAL Price Gates
        engine_5m = UpDownEngine("BTC", "5m", state_mgr)
        
        # Mock decide_signal to return a valid SIG signal (direction "UP")
        _trend_ctx = {"regime_name": "TRENDING", "whale_pressure": 0.0,
                      "confluence_score": 2, "regime_kelly": 1.0}
        with patch("config.FAIR_VALUE_MODE", False), \
             patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.read_text", return_value=_json.dumps({"regime": "TRENDING", "atr_percentile": 30.0})), \
             patch("core.engine.edge_orchestrator.edge_orchestrator.get_trade_context",
                   new_callable=AsyncMock, return_value=_trend_ctx), \
             patch("core.engine.signal_core.decide_signal", return_value={"direction": "UP", "score": 0.85, "is_reversal": False, "blocked": False}), \
             patch("core.engine.updown_engine.UpDownEngine._fetch_market") as mock_mkt:
            
            # Scenario A: YES quote is 0.62 (>0.60 on 5m) -> price ceilings are removed, so allowed
            mock_mkt.return_value = {
                "up_price": 0.62, "dn_price": 0.38,
                "up_market": {"id": "yes_id"}, "dn_market": {"id": "no_id"},
                "event_id": "evt_123", "event_title": "Test Title", "expiry_ts": 1234567
            }
            sig_blocked_ceil = await engine_5m.generate_signal(session)
            self.assertIsNotNone(sig_blocked_ceil)
            
            # Scenario B: YES quote is 0.18 (<0.20 floor) -> should be blocked downstream (generate_signal returns it)
            mock_mkt.return_value = {
                "up_price": 0.18, "dn_price": 0.82,
                "up_market": {"id": "yes_id"}, "dn_market": {"id": "no_id"},
                "event_id": "evt_123", "event_title": "Test Title", "expiry_ts": 1234567
            }
            sig_blocked_floor = await engine_5m.generate_signal(session)
            self.assertIsNotNone(sig_blocked_floor)

    async def test_session_governor_opposing_exposure_block(self):
        import core.engine.session_governor as governor
        from core.engine.session_governor import request_trade_slot
        
        # Clean state
        governor._lat_arb_in_flight.clear()
        governor._lat_arb_cooldowns.clear()

        # Mock open positions with a BTC UP trade
        open_positions = [
            {
                "order_id": "zisi_1",
                "event_title": "[UPDOWN][BTC][5m][SINGLE] Bitcoin Up or Down",
                "direction": "YES",
                "asset": "BTC",
            }
        ]

        # 1. Opposing request: BTC DOWN (is_dual=False) -> should be BLOCKED
        allowed, reason = await request_trade_slot(
            "BTC", "15m", 0.85, 15, open_positions, is_dual=False, direction="DOWN"
        )
        self.assertFalse(allowed)
        self.assertEqual(reason, "opposing_exposure_BTC")

        # 2. Same direction request: BTC UP (is_dual=False) -> should be ALLOWED (subject to other caps)
        allowed_same, reason_same = await request_trade_slot(
            "BTC", "15m", 0.85, 15, open_positions, is_dual=False, direction="UP"
        )
        self.assertTrue(allowed_same)
        self.assertEqual(reason_same, "ok")

        # 3. Opposing request but is_dual=True (latency arb) -> should be ALLOWED
        allowed_dual, reason_dual = await request_trade_slot(
            "BTC", "15m", 0.85, 15, open_positions, is_dual=True, direction="DOWN"
        )
        self.assertTrue(allowed_dual)
        self.assertEqual(reason_dual, "dual_ok")

    async def test_correlated_asset_block(self):
        """BTC/ETH correlated group: UP open blocks opposing DOWN. Same dir allowed. DOGE unaffected."""
        import core.engine.session_governor as governor
        from core.engine.session_governor import request_trade_slot

        # Clean state
        governor._lat_arb_in_flight.clear()
        governor._lat_arb_cooldowns.clear()
        governor._candle_slots.clear()

        # Open position: BTC UP
        open_positions_btc_up = [
            {
                "order_id": "zisi_corr_1",
                "event_title": "[UPDOWN][BTC][5m][SINGLE] Bitcoin Up or Down",
                "direction": "YES",
                "asset": "BTC",
            }
        ]

        # 1. ETH DOWN should be BLOCKED (BTC UP open, ETH is correlated, DOWN is opposing)
        with patch("infrastructure.state.state_manager.get_open_positions", return_value=open_positions_btc_up):
            allowed, reason = await request_trade_slot(
                "ETH", "5m", 0.80, 5, open_positions_btc_up, is_dual=False, direction="DOWN"
            )
        self.assertFalse(allowed, "ETH DOWN should be blocked when BTC UP is open (correlated opposing)")
        self.assertEqual(reason, "correlated_opposing_ETH")

        # 2. ETH UP should be ALLOWED (same direction as BTC UP — no self-hedge)
        with patch("infrastructure.state.state_manager.get_open_positions", return_value=open_positions_btc_up):
            allowed2, reason2 = await request_trade_slot(
                "ETH", "5m", 0.80, 5, open_positions_btc_up, is_dual=False, direction="UP"
            )
        self.assertTrue(allowed2, "ETH UP should be allowed when BTC UP is open (same direction)")

        # 3. DOGE DOWN should NOT be blocked by BTC UP (DOGE is not in BTC/ETH group)
        with patch("infrastructure.state.state_manager.get_open_positions", return_value=open_positions_btc_up):
            allowed3, reason3 = await request_trade_slot(
                "DOGE", "5m", 0.80, 5, open_positions_btc_up, is_dual=False, direction="DOWN"
            )
        self.assertTrue(allowed3, "DOGE DOWN should not be blocked by BTC UP (different correlation group)")
        self.assertNotEqual(reason3, "correlated_opposing_DOGE")

    @patch("app.main.request_trade_slot", return_value=(True, "slot_ok"))
    @patch("app.main.global_diagnostics.get_risk_multiplier", return_value=1.0)
    @patch("infrastructure.state.state_manager.get_open_positions", return_value=[])
    async def test_atm_precision_gate(self, mock_open, mock_risk, mock_request):
        """ATM entry at 48c with whale_aligned=False should be blocked by ATM Precision Gate."""
        from app.main import _validate_trade_slot

        engine = MagicMock()
        engine.compute_size.return_value = 5.0

        context = MagicMock()
        context.log_skip = MagicMock()

        # Signal: SIG entry at 44c (SIGNAL dead zone removed) → allowed
        signal_dead_zone = {
            "direction": "UP",
            "score": 0.95,
            "entry_source": "SIG",
            "market": {"up_price": 0.44, "dn_price": 0.56},
            "whale_aligned": True,
            "confluence_score": 3,
        }

        allowed, details = await _validate_trade_slot(
            context, engine, "BTC", "5m", 5, signal_dead_zone, current_balance=200.0
        )
        self.assertTrue(allowed, "SIG entry at 44c should be allowed as dead zone is removed")

        # Signal: SIG entry at 48c (above dead zone) → allowed
        signal_above_dead_zone = {
            "direction": "UP",
            "score": 0.80,
            "entry_source": "SIG",
            "market": {"up_price": 0.48, "dn_price": 0.52},
            "whale_aligned": False,
            "confluence_score": 1,
        }
        context2 = MagicMock()
        context2.log_skip = MagicMock()

        allowed2, details2 = await _validate_trade_slot(
            context2, engine, "BTC", "5m", 5, signal_above_dead_zone, current_balance=200.0
        )
        self.assertTrue(allowed2, "SIG entry at 48c (above dead zone) should be allowed")

        # REBUILD: FAIR_VAL at 43c with LOW confidence (0.50 < 0.58) is blocked by the ATM
        # confidence guard (the old fixed coin-flip score gate was replaced by an edge-score guard).
        signal_atm_fv_low = {
            "direction": "UP",
            "score": 0.80,
            "entry_source": "FAIR_VAL",
            "fv_confidence": 0.50,
            "market": {"up_price": 0.43, "dn_price": 0.57},
            "whale_aligned": False,
            "confluence_score": 0,
        }
        context3 = MagicMock()
        context3.log_skip = MagicMock()

        allowed3, details3 = await _validate_trade_slot(
            context3, engine, "BTC", "5m", 5, signal_atm_fv_low, current_balance=200.0
        )
        self.assertFalse(allowed3, "FAIR_VAL at 43c with confidence=0.50 should be blocked by the ATM confidence guard")

        # REBUILD: FAIR_VAL at 43c with confidence 0.70 (>= 0.58) is allowed — genuine directional edge.
        signal_atm_fv_high = {
            "direction": "UP",
            "score": 0.92,
            "entry_source": "FAIR_VAL",
            "fv_confidence": 0.70,
            "market": {"up_price": 0.43, "dn_price": 0.57},
            "whale_aligned": False,
            "confluence_score": 0,
        }
        context3b = MagicMock()
        context3b.log_skip = MagicMock()

        allowed3b, details3b = await _validate_trade_slot(
            context3b, engine, "BTC", "5m", 5, signal_atm_fv_high, current_balance=200.0
        )
        self.assertTrue(allowed3b, "FAIR_VAL at 43c with confidence=0.70 should be allowed")

    @patch("infrastructure.exchange.trader.place_order")
    @patch("infrastructure.state.state_manager.get_open_positions", return_value=[])
    @patch("infrastructure.state.state_manager.get_current_balance", return_value=100.0)
    async def test_close_sniper(self, mock_balance, mock_get_positions, mock_place_order):
        from core.engine.cycle_manager import start_close_sniper
        import time

        # Mock engine and market
        engine = MagicMock()
        engine.asset = "BTC"
        engine.timeframe = "5m"
        # Green prior candle so candle gate passes for YES (UP) snipe at 0.98
        engine.klines = [
            [0, "98000", "98500", "97800", "98400", "100"],
            [0, "98400", "98700", "98200", "98600", "120"],   # [-2]: green (close > open)
            [0, "98600", "98800", "98500", "98750", "110"],   # [-1]: current (not closed)
        ]

        # Scenario: Price is 0.98, time is T-30s before expiry -> should trigger YES Mode 1 trade
        now = int(time.time())
        market_data_yes = {
            "event_id": "test_event_yes",
            "expiry_ts": now + 30,
            "event_title": "Bitcoin Up or Down Test",
            "up_price": 0.98,
            "dn_price": 0.02,
            "up_market": {"id": "up_token_123"},
            "dn_market": {"id": "dn_token_123"}
        }

        engine._fetch_market = AsyncMock(return_value=market_data_yes)
        engines = {"BTC/5m": engine}

        import asyncio
        # Run close sniper, cancel on second iteration
        with patch("asyncio.sleep", side_effect=[None, asyncio.CancelledError()]):
            try:
                await start_close_sniper(None, engines)
            except asyncio.CancelledError:
                pass

        # REBUILD NCS sizing: snipe @ 98c -> profit_per_share=0.01, target $1.00 ->
        # (1.00/0.01)*0.98 = $98, capped to min($98, tail_cap $20, 45% of $100 = $45) = $20.
        _bal = 100.0
        _snipe = 0.98
        _pps = 0.99 - _snipe
        _amount = (1.00 / _pps) * _snipe if _pps > 0.005 else 20.0
        _expected = round(max(2.50, min(_amount, 20.0, _bal * 0.45)), 2)
        mock_place_order.assert_called_once_with(
            event_id="test_event_yes",
            market_id="up_token_123",
            amount_dollars=_expected,
            direction="YES",
            entry_price=0.98,
            event_title="[UPDOWN][BTC][5m][CLOSE-SNIPE] Bitcoin Up or Down Test",
            expiry_ts=now + 30
        )


if __name__ == "__main__":
    unittest.main()

