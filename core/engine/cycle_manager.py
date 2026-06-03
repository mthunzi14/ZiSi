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

_LAT_LAST_ENTRY_TS: float = 0.0  # global: 2s cooldown — only blocks exact same-second double fires


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
    last_scanned_close = {}   # (asset, timeframe) -> next_close_ts  [T-15s window]
    last_scanned_t5 = {}      # (asset, timeframe) -> next_close_ts  [T-5s window]
    lat_arb_count = {}        # next_close_ts -> number of tasks spawned (no cap)

    async def scan_and_trade(engine, next_close, time_left, t_minus=15):
        asset = engine.asset
        timeframe = engine.timeframe
        interval_minutes = int(timeframe.rstrip("m"))

        # DOGE excluded — Pyth signal too noisy for reliable latency edge
        if asset == "DOGE":
            return

        try:
            # 1. Fetch Pyth Price
            from core.pyth_oracle_service import GLOBAL_ORACLE_CACHE
            pyth_price = GLOBAL_ORACLE_CACHE.get(asset, {}).get("price", 0.0)
            if pyth_price <= 0.0:
                log.warning("[LATENCY-ARB] No Pyth price available for %s", asset)
                return

            # T-5s freshness gate: near-certainty requires very fresh oracle
            if t_minus == 5:
                pyth_ts = GLOBAL_ORACLE_CACHE.get(asset, {}).get("timestamp", 0.0)
                pyth_age = time.time() - pyth_ts
                if pyth_age > 3.0:
                    log.info("[T5-SCANNER] %s/%s: Pyth stale (%.1fs > 3s) — skip",
                             asset, timeframe, pyth_age)
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
            
            if t_minus == 5:
                _lat_threshold = 0.003  # T-5s: candle direction just needs to be clear
            elif timeframe == "5m":
                _lat_threshold = 0.005  # T-15s 5m: stronger requirement
            else:
                _lat_threshold = 0.004  # T-15s 15m: standard
            if abs(pct_move) < _lat_threshold:
                return

            # Regime gate: if last 2 closed candles flipped direction → choppy market, skip (XRP/SOL only)
            # BTC/ETH exempt — Bone Reaper fires every candle regardless of prior candle direction
            if t_minus != 5 and asset not in ("BTC", "ETH") and len(klines) >= 3:
                c_last = klines[-2]
                c_prev = klines[-3]
                last_bull = float(c_last[4]) > float(c_last[1])
                prev_bull = float(c_prev[4]) > float(c_prev[1])
                if last_bull != prev_bull:
                    log.info("[LATENCY-ARB] %s/%s REGIME_GATE: last 2 candles flipped (%s→%s) — choppy, skipping",
                             asset, timeframe,
                             "UP" if prev_bull else "DN",
                             "UP" if last_bull else "DN")
                    return

            direction = "UP" if pct_move > 0 else "DOWN"
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

            # Same-direction exposure cap: max 5 open positions in same direction
            signal_is_up = direction == "UP"
            same_dir_open = sum(
                1 for p in open_positions
                if (p.get("direction") in ("YES", "UP")) == signal_is_up
            )
            if same_dir_open >= 5:
                log.info("[LATENCY-ARB] %s/%s SAME_DIR_CAP: %d open %s positions — skip",
                         asset, timeframe, same_dir_open, direction)
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

            # Dynamic price floor: only block very-low entries on weak Pyth moves
            if entry_price < 0.15 and abs(pct_move) < 0.004:
                log.info("[PRICE-FLOOR] %s/%s: %.0fc with weak move %.4f%% — skip",
                         asset, timeframe, entry_price * 100, abs(pct_move) * 100)
                return

            # ATM gate removed — at T-15s Pyth signal IS the edge regardless of current market price

            # Discount gate: only block if we'd have negative EV (entry >= our own probability estimate)
            # At 99% confidence, any entry < 99c is positive EV — Bone Reaper enters at 72-99c
            if entry_price >= implied_prob:
                log.info("[LATENCY-ARB] %s/%s %s price %.2f >= implied_prob %.2f — negative EV, skip",
                         asset, timeframe, direction, entry_price, implied_prob)
                return
                
            # 5. Position sizing — 15m gets 1.5× premium (82% WR earns it), 5m stays conservative
            from infrastructure.state.state_manager import get_current_balance
            current_balance = get_current_balance()

            normal_usd = engine.compute_size(0.85, entry_price, current_balance)
            if t_minus == 5:
                usd_size = max(1.0, normal_usd * 0.35)  # T-5s: small-ROI near-certainty
                log.info("[T5-SCANNER] %s/%s near-certainty sizing 0.35x: $%.2f", asset, timeframe, usd_size)
            else:
                usd_size = max(1.0, normal_usd * 0.5)
                if timeframe == "15m":
                    usd_size *= 1.5
                    log.info("[LATENCY-ARB] 15m premium: 1.5x size -> $%.2f", usd_size)
            
            # Apply Altcoin Sizing Gates
            if asset in ["SOL", "XRP"]:
                usd_size *= 0.60
            elif asset in ["ADA", "DOGE", "AVAX", "SUI"]:
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

            # Same-second dedup only: 2s window prevents exact same-signal double fires
            # 60s was killing multi-asset concurrent entries (BTC fires, ETH blocked for 60s)
            global _LAT_LAST_ENTRY_TS
            if time.time() - _LAT_LAST_ENTRY_TS < 2.0:
                log.info("[LAT-DEDUP] %s/%s: same-second dedup (%.1fs) — skip",
                         asset, timeframe, time.time() - _LAT_LAST_ENTRY_TS)
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
                _LAT_LAST_ENTRY_TS = time.time()
                await commit_trade_slot(asset, timeframe, 0.85, interval_minutes, is_dual=False, direction=direction)
                log.info("[LATENCY-ARB SUCCESSFULLY ENTERED] Entered %s/%s %s: $%.2f at %.0f¢ (implied prob: %.2f)",
                         asset, timeframe, direction, usd_size, entry_price * 100, implied_prob)
                try:
                    from app.telegram_bot import send_alert
                    send_alert(f"LATENCY ARB {asset}/{timeframe} {direction} | ${usd_size:.2f} @ {entry_price*100:.0f}c")
                except Exception:
                    pass

                # Spawn early-exit monitor for 15m positions only — enough runway to act.
                if timeframe == "15m":
                    asyncio.create_task(
                        _monitor_lat_exit(
                            order_id=order["order_id"],
                            asset=asset,
                            timeframe=timeframe,
                            direction=direction,
                            token_id=market_id,
                            boundary_ts=next_close,
                        )
                    )

                # Cross-asset lag: if BTC/ETH fired strongly, check if peer is lagging
                if asset in ("BTC", "ETH") and abs(pct_move) >= 0.005 and t_minus != 5:
                    asyncio.create_task(
                        check_cross_asset_lag(asset, direction, next_close)
                    )
        except Exception as e:
            log.error("[LATENCY-ARB] Error scanning %s/%s: %s", asset, timeframe, e, exc_info=True)

    async def check_cross_asset_lag(lead_asset: str, lead_direction: str, next_close: float):
        """Enter the peer asset when it hasn't priced in the lead asset's strong move yet."""
        peer = "ETH" if lead_asset == "BTC" else ("BTC" if lead_asset == "ETH" else None)
        if peer is None:
            return

        peer_engine = engines.get(f"{peer}/5m")
        if peer_engine is None:
            return

        try:
            import infrastructure.state.state_manager as state_mgr
            open_positions = state_mgr.get_open_positions()
            from core.engine.session_governor import has_open_asset_tf_exposure
            if has_open_asset_tf_exposure(open_positions, peer, "5m"):
                log.info("[LAG-TRADE] %s/5m already open — skip lag from %s", peer, lead_asset)
                return

            market = await peer_engine._fetch_market(session, is_latency_scan=True)
            if not market:
                return

            up_price = market["up_price"]
            dn_price = market["dn_price"]

            # Lag condition: peer market priced OPPOSITE to lead direction (hasn't followed yet)
            if lead_direction == "UP":
                peer_entry_price = up_price
                market_id = market["up_market"]["id"]
                order_direction = "YES"
                if up_price >= 0.45:
                    return  # Market already agrees — not a lag
            else:
                peer_entry_price = dn_price
                market_id = market["dn_market"]["id"]
                order_direction = "NO"
                if dn_price >= 0.45:
                    return  # Not a lag

            if peer_entry_price < 0.05:
                return  # Too extreme

            if time.time() >= next_close:
                return  # Candle already closed

            from infrastructure.state.state_manager import get_current_balance
            current_balance = get_current_balance()
            normal_usd = peer_engine.compute_size(0.80, peer_entry_price, current_balance)
            usd_size = max(1.0, normal_usd * 0.50)

            from infrastructure.exchange.trader import place_order
            from core.engine.session_governor import commit_trade_slot
            order = place_order(
                event_id=market["event_id"],
                market_id=market_id,
                amount_dollars=usd_size,
                direction=order_direction,
                entry_price=peer_entry_price,
                event_title=f"[UPDOWN][{peer}][5m][LAG_TRADE] {market['event_title']}",
                expiry_ts=market["expiry_ts"],
            )
            if order:
                await commit_trade_slot(peer, "5m", 0.80, 5, is_dual=False, direction=lead_direction)
                log.info("[LAG-TRADE] %s follows %s %s: $%.2f @ %.0f¢",
                         peer, lead_asset, lead_direction, usd_size, peer_entry_price * 100)
        except Exception as e:
            log.warning("[LAG-TRADE] %s→%s check failed: %s", lead_asset, peer, e)

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

                    # Concurrent cap REMOVED — fire all assets every candle like Bone Reaper
                    last_scanned_close[(asset, timeframe)] = next_close
                    lat_arb_count[next_close] = lat_arb_count.get(next_close, 0) + 1
                    log.info("[LATENCY-ARB] Spawning concurrent scan for %s/%s at T-%.1fs before close (slot %d)",
                             asset, timeframe, time_left, lat_arb_count[next_close])

                    # Spawn concurrently!
                    asyncio.create_task(scan_and_trade(engine, next_close, time_left))

                    # Prune stale boundary counts (keep only last 30 minutes)
                    cutoff = int(now) - 1800
                    lat_arb_count = {k: v for k, v in lat_arb_count.items() if k > cutoff}

                # T-5s near-certainty window
                elif 2.5 <= time_left <= 6.5:
                    if last_scanned_t5.get((asset, timeframe)) == next_close:
                        continue
                    if asset == "DOGE":
                        continue  # DOGE excluded: noisy Pyth
                    last_scanned_t5[(asset, timeframe)] = next_close
                    log.info("[T5-SCANNER] Spawning near-certainty scan %s/%s at T-%.1fs",
                             asset, timeframe, time_left)
                    asyncio.create_task(scan_and_trade(engine, next_close, time_left, t_minus=5))

        except Exception as e:
            log.error("[LATENCY-ARB] Scanner loop error: %s", e, exc_info=True)
            
        await asyncio.sleep(1.0)


async def _monitor_lat_exit(
    order_id: str,
    asset: str,
    timeframe: str,
    direction: str,
    token_id: str,
    boundary_ts: float,
) -> None:
    """
    Background monitor for an open 15m LAT-ARB position.
    Checks the live quote every 60s and bails early if the market has moved
    ≥75% against our direction (token quote drops below 0.25).
    """
    import time as _t
    BAIL_THRESHOLD = 0.25  # quote < 0.25 = 75%+ wrong

    while True:
        await asyncio.sleep(60)
        try:
            now = _t.time()
            if now >= boundary_ts:
                break  # candle has expired — let normal resolution handle it

            from infrastructure.websocket.extraterrestrial_ws_gateway import polymarket_l2_gateway
            quote, _ = polymarket_l2_gateway.get_price(token_id)
            if quote is None:
                continue

            if quote < BAIL_THRESHOLD:
                log.info(
                    "[LAT-EXIT] %s/%s %s: quote %.2f < %.2f — bailing early",
                    asset, timeframe, direction, quote, BAIL_THRESHOLD,
                )
                from infrastructure.exchange.trader import execute_exit
                execute_exit(order_id, quote, exit_reason="STOP_HIT")
                try:
                    from app.telegram_bot import send_alert
                    send_alert(
                        f"LAT-EXIT {asset}/{timeframe} {direction} | quote {quote:.2f} — early bail"
                    )
                except Exception:
                    pass
                break
        except Exception as e:
            log.warning("[LAT-EXIT] Monitor error for %s/%s: %s", asset, timeframe, e)


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
