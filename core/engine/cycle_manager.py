"""
cycle_manager.py - Signal-to-trade orchestration for each bot cycle.

Wires together: SignalTypeClassifier → RoutingEngine → PositionSizer →
ConflictDetector → PriorityQueue.

The CycleManager does NOT execute trades.  It returns a structured dict of
classified, sized, and prioritised trade candidates that the main loop (or
markets_orchestrator.py) passes to the actual order executors.

Usage in main.py:
    from cycle_manager import CycleManager
    _cycle_manager = CycleManager(account_balance=cfg["ACCOUNT_BALANCE"])

    # Inside the main cycle:
    result = _cycle_manager.process_signals(signals, all_events, kalshi_events)
    for sig in result["enriched_signals"]:
        _process_signal(sig, result["eligible_events"][sig["signal_type"]], cfg)
"""
import logging
import time
import aiohttp
import asyncio
from typing import Dict, List

from core.engine.signal_router import SignalTypeClassifier, RoutingEngine, CategoryConfidenceWeighter
from core.risk.position_sizer import PositionSizer
from core.engine.conflict_detector import ConflictDetector
from core.engine.trade_priority_queue import PriorityQueue, FeedbackTracker

log = logging.getLogger("zisi.cycle_manager")


class CycleManager:
    """
    Orchestrate signal classification, routing, sizing, conflict detection,
    and prioritisation for a single 15/30-minute cycle.
    """

    def __init__(self, account_balance: float = 100.0) -> None:
        self.account_balance = account_balance
        self.classifier   = SignalTypeClassifier()
        self.router       = RoutingEngine()
        self.weighter     = CategoryConfidenceWeighter()
        self.sizer        = PositionSizer(account_balance)
        self.detector     = ConflictDetector()
        self.queue        = PriorityQueue()
        self.feedback     = FeedbackTracker()

    def process_signals(
        self,
        signals: List[Dict],
        polymarket_events: List[Dict],
        kalshi_events: List[Dict],
    ) -> Dict:
        """
        Run all signals through the full pipeline.

        Returns:
            {
              "enriched_signals":  [...],  # signals with signal_type / kelly_multiplier added
              "polymarket_candidates": [...],  # (event, position_size) tuples
              "kalshi_candidates": [...],      # same for Kalshi
              "capital_deployed":  float,
              "trade_count":       int,
              "conflicts_detected": int,
            }
        """
        self.sizer.reset_cycle()

        enriched:    List[Dict] = []
        poly_cands:  List[Dict] = []
        kalshi_cands:List[Dict] = []

        for signal in signals:
            # 1. Classify
            signal = self.classifier.classify(signal)
            enriched.append(signal)

            # 2. Route
            eligible = self.router.get_eligible_markets(
                signal, polymarket_events, kalshi_events
            )

            # 3. Size + collect polymarket candidates
            for ev in eligible["polymarket"]:
                cat = ev.get("market_category") or ev.get("category") or "OTHER"
                cat_wt = self.weighter.get_weight("Polymarket", cat)
                size = self.sizer.calculate(signal, ev, cat_wt)
                if size > 0:
                    poly_cands.append({
                        "signal": signal,
                        "market": ev,
                        "position_size": size,
                        "category_weight": cat_wt,
                        "exchange": "polymarket",
                    })

            # 4. Size + collect Kalshi candidates
            for ev in eligible["kalshi"]:
                cat = ev.get("_category") or "OTHER"
                cat_wt = self.weighter.get_weight("Kalshi", cat)
                size = self.sizer.calculate(signal, ev, cat_wt)
                if size > 0:
                    kalshi_cands.append({
                        "signal": signal,
                        "event": ev,
                        "position_size": size,
                        "category_weight": cat_wt,
                        "exchange": "kalshi",
                    })

        # 5. Conflict detection (reduce Poly positions where Kalshi overlaps)
        conflicts = self.detector.detect(poly_cands, kalshi_cands)
        poly_cands = self.detector.apply(poly_cands, conflicts)

        # 6. Prioritise
        poly_cands   = self.queue.prioritize(poly_cands)
        kalshi_cands = self.queue.prioritize(kalshi_cands)

        # 7. Cap at 15 poly + 10 kalshi per cycle
        poly_cands   = poly_cands[:15]
        kalshi_cands = kalshi_cands[:10]

        total_trades = len(poly_cands) + len(kalshi_cands)

        log.info(
            "[CYCLE-MANAGER] signals=%d | poly_cands=%d | kalshi_cands=%d"
            " | conflicts=%d | capital=$%.2f",
            len(enriched), len(poly_cands), len(kalshi_cands),
            len(conflicts), self.sizer.capital_used,
        )

        return {
            "enriched_signals":      enriched,
            "polymarket_candidates": poly_cands,
            "kalshi_candidates":     kalshi_cands,
            "capital_deployed":      self.sizer.capital_used,
            "trade_count":           total_trades,
            "conflicts_detected":    len(conflicts),
        }

    def record_outcome(
        self,
        signal_type: str,
        category: str,
        confidence: float,
        result: str,
    ) -> None:
        """Log a resolved trade outcome for win-rate tracking."""
        self.feedback.record(signal_type, category, confidence, result)

    def feedback_summary(self) -> Dict:
        """Return win-rate breakdown by signal_type × category."""
        return self.feedback.summary()

async def start_latency_edge_scanner(session: aiohttp.ClientSession, engines: dict) -> None:
    """
    Background daemon task running a T-15s candle close scanner to exploit the Pyth-vs-Polymarket latency edge.
    """
    log.info("[LATENCY-ARB] Starting T-15s latency arbitrage scanner daemon...")
    last_scanned_close = {}  # (asset, timeframe) -> next_close_ts

    async def scan_and_trade(engine, next_close, time_left):
        asset = engine.asset
        timeframe = engine.timeframe
        interval_minutes = int(timeframe.rstrip("m"))
        
        try:
            # 1. Fetch Pyth Price
            from core.pyth_oracle_service import GLOBAL_ORACLE_CACHE
            pyth_price = GLOBAL_ORACLE_CACHE.get(asset, {}).get("price", 0.0)
            if pyth_price <= 0.0:
                log.warning("[LATENCY-ARB] No Pyth price available for %s", asset)
                return
                
            # 2. Fetch candle open price (klines[-1][1])
            from core.engine.updown_engine import _fetch_klines_async
            tf_map = {"5m": ("5m", 30), "15m": ("15m", 30)}
            interval, limit = tf_map.get(timeframe, ("5m", 30))
            klines = await _fetch_klines_async(session, asset, interval, limit)
            if len(klines) < 2:
                log.warning("[LATENCY-ARB] Insufficient klines for %s/%s", asset, timeframe)
                return
                
            open_price = float(klines[-1][1])
            pct_move = (pyth_price - open_price) / open_price
            
            # Check threshold (0.2%)
            if abs(pct_move) < 0.002:
                return  # Move is too small
                
            direction = "UP" if pct_move > 0.002 else "DOWN"
            log.info("[LATENCY-ARB] Potential %s move detected for %s/%s (move: %.4f%%, Pyth: %.4f, Open: %.4f)",
                     direction, asset, timeframe, pct_move * 100, pyth_price, open_price)
            
            # 3. Check if we already have an active position for this candle
            import infrastructure.state.state_manager as state_mgr
            open_positions = state_mgr.get_open_positions()
            
            # Use fast-path for latency scan!
            market = await engine._fetch_market(session, is_latency_scan=True)
            if not market:
                log.warning("[LATENCY-ARB] Active market not found for %s/%s", asset, timeframe)
                return
                
            already_entered = False
            for pos in open_positions:
                if pos.get("event_id") == market["event_id"]:
                    already_entered = True
                    break
                    
            if already_entered:
                log.info("[LATENCY-ARB] Already entered market for %s/%s in this candle, skipping.", asset, timeframe)
                return
                
            # 4. Check prices and implied probability
            abs_move = abs(pct_move)
            if abs_move >= 0.004:
                implied_prob = 0.99
            elif abs_move >= 0.003:
                implied_prob = 0.97
            else:
                implied_prob = 0.95
                
            up_price = market["up_price"]
            dn_price = market["dn_price"]
            
            if direction == "UP":
                entry_price = up_price
                market_id = market["up_market"]["id"]
            else:
                entry_price = dn_price
                market_id = market["dn_market"]["id"]
                
            # Retrieve active session discount hurdle (Sprint 11)
            discount_hurdle = 0.06
            try:
                from core.shared.session_manager import TradingSessionManager
                session_params = TradingSessionManager.get_active_session_params()
                discount_hurdle = session_params.get("discount_hurdle", 0.06)
                log.info("[LATENCY-ARB] Active session discount hurdle is %.2fc", discount_hurdle * 100)
            except Exception as e:
                log.warning("[LATENCY-ARB] Failed to load session discount hurdle: %s", e)

            # Arbitrage entry discount gate check
            if entry_price >= (implied_prob - discount_hurdle):
                log.info("[LATENCY-ARB] %s/%s %s price %.2f does not offer enough discount vs implied prob %.2f (requires < %.2f)",
                         asset, timeframe, direction, entry_price, implied_prob, implied_prob - discount_hurdle)
                return
                
            # 5. Position sizing (half Kelly)
            from infrastructure.state.state_manager import get_current_balance
            current_balance = get_current_balance()
            
            normal_usd = engine.compute_size(0.85, entry_price, current_balance)
            usd_size = max(1.0, normal_usd * 0.5)
            
            # Apply Altcoin Sizing Gates
            if asset in ["SOL", "XRP"]:
                usd_size *= 0.60
            elif asset in ["BNB", "HYPE"]:
                usd_size *= 0.50
            elif asset in ["ADA", "LINK", "DOGE", "AVAX", "SUI"]:
                usd_size = min(usd_size * 0.35, 35.0)
                
            # Safety cap
            max_safety_size = current_balance * 0.15
            if usd_size > max_safety_size:
                usd_size = max_safety_size
                
            if usd_size < 1.00:
                log.info("[LATENCY-ARB] Position size $%.2f too small, skipping.", usd_size)
                return
                
            # ABSOLUTE LATE-ENTRY SAFETY GUARD:
            # Prevent entering trade if the candle has already closed due to any processing lags
            if time.time() >= next_close:
                log.warning("[LATENCY-ARB] Scan completed after candle close (%d >= %d), aborting order for %s/%s.",
                            time.time(), next_close, asset, timeframe)
                return

            # 6. Execute order
            from infrastructure.exchange.trader import place_order
            from core.engine.session_governor import commit_trade_slot
            
            order = place_order(
                event_id=market["event_id"],
                market_id=market_id,
                amount_dollars=usd_size,
                direction="YES" if direction == "UP" else "NO",
                entry_price=entry_price,
                event_title=f"[UPDOWN][{asset}][{timeframe}][LATENCY_ARB] {market['event_title']}",
                expiry_ts=market["expiry_ts"],
            )
            
            if order:
                await commit_trade_slot(asset, timeframe, 0.85, interval_minutes, is_dual=False, direction=direction)
                log.info("[LATENCY-ARB SUCCESSFULLY ENTERED] Entered %s/%s %s: $%.2f at %.0f¢ (implied prob: %.2f)",
                         asset, timeframe, direction, usd_size, entry_price * 100, implied_prob)
                try:
                    from app.telegram_bot import send_alert
                    send_alert(f"LATENCY ARB {asset}/{timeframe} {direction} | ${usd_size:.2f} @ {entry_price*100:.0f}c")
                except Exception:
                    pass
        except Exception as e:
            log.error("[LATENCY-ARB] Error scanning %s/%s: %s", asset, timeframe, e, exc_info=True)

    while True:
        try:
            now = time.time()
            # Loop over all registered engines
            for key, engine in engines.items():
                asset = engine.asset
                timeframe = engine.timeframe
                interval_minutes = int(timeframe.rstrip("m"))
                interval_secs = interval_minutes * 60
                
                next_close = ((int(now) // interval_secs) + 1) * interval_secs
                time_left = next_close - now
                
                # We target the window T-15s to T-8s
                if 8.0 <= time_left <= 15.5:
                    if last_scanned_close.get((asset, timeframe)) == next_close:
                        continue  # Already scanned this candle
                        
                    last_scanned_close[(asset, timeframe)] = next_close
                    log.info("[LATENCY-ARB] Spawning concurrent scan for %s/%s at T-%.1fs before close", asset, timeframe, time_left)
                    
                    # Spawn concurrently!
                    asyncio.create_task(scan_and_trade(engine, next_close, time_left))
                    
        except Exception as e:
            log.error("[LATENCY-ARB] Scanner loop error: %s", e, exc_info=True)
            
        await asyncio.sleep(1.0)


async def start_reversal_sniper(session: aiohttp.ClientSession, engines: dict) -> None:
    """
    Background daemon that snipes the cheap losing side (≤10¢) of near-certain (≥90¢)
    binary markets when Pyth data contradicts the market consensus.
    Entry window: T-90s to T-45s before candle close.
    Size: 0.5% of balance, hard cap $2.
    """
    log.info("[REVERSAL-SNIPE] Starting cheap reversal sniper daemon...")
    last_scanned = {}  # (asset, tf) -> next_close_ts

    async def _snipe(engine, next_close):
        asset = engine.asset
        timeframe = engine.timeframe
        interval_minutes = int(timeframe.rstrip("m"))
        try:
            from core.pyth_oracle_service import GLOBAL_ORACLE_CACHE
            pyth_price = GLOBAL_ORACLE_CACHE.get(asset, {}).get("price", 0.0)
            if pyth_price <= 0.0:
                return

            from core.engine.updown_engine import _fetch_klines_async
            tf_map = {"5m": ("5m", 30), "15m": ("15m", 30)}
            interval, limit = tf_map.get(timeframe, ("5m", 30))
            klines = await _fetch_klines_async(session, asset, interval, limit)
            if len(klines) < 2:
                return

            open_price = float(klines[-1][1])
            pct_move = (pyth_price - open_price) / open_price if open_price > 0 else 0.0

            market = await engine._fetch_market(session, is_latency_scan=True)
            if not market:
                return

            up_price = market["up_price"]
            dn_price = market["dn_price"]

            # Identify snipe direction: Pyth contradicts the near-certain side
            snipe_direction = None
            snipe_price = None

            if up_price >= 0.90 and dn_price <= 0.10 and pct_move <= -0.004:
                # Market says UP is certain, but Pyth price is falling → snipe DOWN
                snipe_direction = "DOWN"
                snipe_price = dn_price
            elif dn_price >= 0.90 and up_price <= 0.10 and pct_move >= 0.004:
                # Market says DOWN is certain, but Pyth price is rising → snipe UP
                snipe_direction = "UP"
                snipe_price = up_price

            if not snipe_direction:
                return

            # Skip if already in this market
            import infrastructure.state.state_manager as state_mgr
            for pos in state_mgr.get_open_positions():
                if pos.get("event_id") == market["event_id"]:
                    return

            # Abort if candle already closed
            if time.time() >= next_close:
                log.warning("[REVERSAL-SNIPE] Candle closed, aborting %s/%s", asset, timeframe)
                return

            from infrastructure.state.state_manager import get_current_balance
            balance = get_current_balance()
            usd_size = min(balance * 0.005, 2.0)
            if usd_size < 0.50:
                log.info("[REVERSAL-SNIPE] Size $%.2f too small, skipping %s/%s", usd_size, asset, timeframe)
                return

            market_id = market["dn_market"]["id"] if snipe_direction == "DOWN" else market["up_market"]["id"]

            from infrastructure.exchange.trader import place_order
            from core.engine.session_governor import commit_trade_slot
            order = place_order(
                event_id=market["event_id"],
                market_id=market_id,
                amount_dollars=usd_size,
                direction="NO" if snipe_direction == "DOWN" else "YES",
                entry_price=snipe_price,
                event_title=f"[UPDOWN][{asset}][{timeframe}][REVERSAL_SNIPE] {market['event_title']}",
                expiry_ts=market["expiry_ts"],
            )
            if order:
                await commit_trade_slot(asset, timeframe, 0.50, interval_minutes, is_dual=False, direction=snipe_direction)
                log.info(
                    "[REVERSAL-SNIPE ENTERED] %s/%s %s: $%.2f @ %.0f¢ (Pyth move=%.2f%%)",
                    asset, timeframe, snipe_direction, usd_size, snipe_price * 100, pct_move * 100,
                )
                try:
                    from app.telegram_bot import send_alert
                    send_alert(f"REV-SNIPE {asset}/{timeframe} {snipe_direction} | ${usd_size:.2f} @ {snipe_price*100:.0f}c")
                except Exception:
                    pass
        except Exception as e:
            log.error("[REVERSAL-SNIPE] Error for %s/%s: %s", asset, timeframe, e, exc_info=True)

    while True:
        try:
            now = time.time()
            for key, engine in engines.items():
                asset = engine.asset
                timeframe = engine.timeframe
                interval_minutes = int(timeframe.rstrip("m"))
                interval_secs = interval_minutes * 60

                next_close = ((int(now) // interval_secs) + 1) * interval_secs
                time_left = next_close - now

                # T-90s to T-45s window
                if 45.0 <= time_left <= 90.0:
                    if last_scanned.get((asset, timeframe)) == next_close:
                        continue
                    last_scanned[(asset, timeframe)] = next_close
                    log.info("[REVERSAL-SNIPE] Scanning %s/%s at T-%.0fs", asset, timeframe, time_left)
                    asyncio.create_task(_snipe(engine, next_close))
        except Exception as e:
            log.error("[REVERSAL-SNIPE] Loop error: %s", e)

        await asyncio.sleep(5.0)
