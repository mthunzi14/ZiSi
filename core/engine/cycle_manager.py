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
import os
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
_ACTIVE_MARKET_IDS: set = set()  # prevents FV + LAT-ARB race condition entering same market twice


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
    last_scanned_t2 = {}      # (asset, timeframe) -> next_close_ts  [T-2s sweeper window]
    lat_arb_count = {}        # next_close_ts -> number of tasks spawned (no cap)
    # Cache T-5s market data so T-2s sweeper can reuse without a new HTTP call
    _t5_market_cache: dict = {}  # (asset, timeframe, next_close) -> market dict

    async def scan_and_trade(engine, next_close, time_left, t_minus=15):
        asset = engine.asset
        timeframe = engine.timeframe
        interval_minutes = 60 if timeframe == "1h" else int(timeframe.rstrip("m"))

        # DOGE excluded — Pyth signal too noisy for reliable latency edge
        if asset == "DOGE":
            return

        # 5m T-15s and SOL T-15s previously disabled (25%/20% WR without CVD/OBI).
        # Re-enabled: CVD+OBI gate below enforces stricter thresholds for these,
        # so only high-conviction candles fire — same mechanism BoneReaper uses on 5m.
        _strict_cvd_obi = (timeframe == "5m" and t_minus == 15) or (asset == "SOL" and t_minus == 15)

        try:
            # 1. Fetch Pyth Price
            from core.pyth_oracle_service import GLOBAL_ORACLE_CACHE
            pyth_price = GLOBAL_ORACLE_CACHE.get(asset, {}).get("price", 0.0)
            if pyth_price <= 0.0:
                log.warning("[LATENCY-ARB] No Pyth price available for %s", asset)
                return

            # T-5s / T-2s freshness gate: near-certainty requires very fresh oracle
            if t_minus == 5:
                pyth_ts = GLOBAL_ORACLE_CACHE.get(asset, {}).get("timestamp", 0.0)
                pyth_age = time.time() - pyth_ts
                if pyth_age > 3.0:
                    log.info("[T5-SCANNER] %s/%s: Pyth stale (%.1fs > 3s) — skip",
                             asset, timeframe, pyth_age)
                    return
            elif t_minus == 2:
                pyth_ts = GLOBAL_ORACLE_CACHE.get(asset, {}).get("timestamp", 0.0)
                pyth_age = time.time() - pyth_ts
                if pyth_age > 2.0:
                    log.info("[T2-SWEEPER] %s/%s: Pyth stale (%.1fs > 2s) — skip",
                             asset, timeframe, pyth_age)
                    return

            # 2. Fetch candle open price (klines[-1][1])
            from core.engine.updown_engine import _fetch_klines_async
            tf_map = {"5m": ("5m", 30), "15m": ("15m", 30), "1h": ("1h", 30)}
            interval, limit = tf_map.get(timeframe, ("5m", 30))
            klines = await _fetch_klines_async(session, asset, interval, limit)
            if len(klines) < 2:
                log.warning("[LATENCY-ARB] Insufficient klines for %s/%s", asset, timeframe)
                return
                
            open_price = float(klines[-1][1])
            pct_move = (pyth_price - open_price) / open_price

            # Fast ATR-sigma for threshold scaling (uses same logic as line 403)
            try:
                _closed_k = klines[:-1]
                _atr_vals_q = [
                    abs(float(k[2]) - float(k[3])) / max(float(k[4]), 1e-9)
                    for k in _closed_k[-14:]
                ]
                sigma_frac = sum(_atr_vals_q) / len(_atr_vals_q) if _atr_vals_q else 0.004
            except Exception:
                sigma_frac = 0.004  # fallback: 0.4% is a conservative floor

            import infrastructure.state.state_manager as state_mgr
            open_positions = state_mgr.get_open_positions()

            if t_minus == 2:
                _lat_threshold = max(0.006, sigma_frac * 0.40)   # 40% of 1-candle sigma — near-certainty
            elif t_minus == 5:
                _lat_threshold = max(0.002, sigma_frac * 0.15)   # 15% of sigma — direction just needs to be clear
            elif timeframe == "5m":
                _lat_threshold = max(0.003, sigma_frac * 0.25)   # 25% of sigma — T-15s 5m
            else:
                _lat_threshold = max(0.002, sigma_frac * 0.20)   # 20% of sigma — T-15s 15m/1h
            if abs(pct_move) < _lat_threshold:
                return

            # Regime gate: if last 2 closed candles flipped direction → choppy market, skip (XRP/SOL only)
            # BTC/ETH and T-2s exempt — at T-2s the candle is already decided
            if t_minus not in (5, 2) and asset not in ("BTC", "ETH") and len(klines) >= 3:
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

            # TREND-BIAS: 3-candle trend check — require 0.8%+ move to bet against trend
            # Catches the pattern: market recovering UP, each candle opens high, pct_move<0 → bad DOWN signal
            if len(klines) >= 4 and t_minus not in (5, 2):
                c1 = float(klines[-2][4])  # last closed candle close
                c2 = float(klines[-3][4])  # 2nd last closed
                c3 = float(klines[-4][4])  # 3rd last closed
                trend_is_up = (c1 > c2) and (c2 > c3)
                trend_is_dn = (c1 < c2) and (c2 < c3)
                _contra_threshold = 0.008  # 0.8%+ move needed to contradict clear 3-candle trend
                if trend_is_up and direction == "DOWN" and abs(pct_move) < _contra_threshold:
                    log.info("[TREND-BIAS] %s/%s: 3-candle UP trend (%.0f→%.0f→%.0f) contradicts DOWN %.4f%% — need 0.8%% — skip",
                             asset, timeframe, c3, c2, c1, abs(pct_move) * 100)
                    return
                if trend_is_dn and direction == "UP" and abs(pct_move) < _contra_threshold:
                    log.info("[TREND-BIAS] %s/%s: 3-candle DN trend (%.0f→%.0f→%.0f) contradicts UP %.4f%% — need 0.8%% — skip",
                             asset, timeframe, c3, c2, c1, abs(pct_move) * 100)
                    return

            # WHALE-VETO in LAT-ARB: use cached whale data from last engine cycle
            try:
                from core.engine.edge_orchestrator import edge_orchestrator as _eo
                if _eo and _eo._whale_tracker:
                    _whale = _eo._whale_tracker.get_whale_signal(asset)
                    _whale_pressure = _whale.get("whale_pressure", 0.0)
                    if _whale_pressure > 0.70 and direction == "DOWN":
                        log.warning("[LAT-ARB WHALE-VETO] %s/%s: bullish pressure %.2f contradicts DOWN — skip",
                                    asset, timeframe, _whale_pressure)
                        return
                    elif _whale_pressure < -0.70 and direction == "UP":
                        log.warning("[LAT-ARB WHALE-VETO] %s/%s: bearish pressure %.2f contradicts UP — skip",
                                    asset, timeframe, abs(_whale_pressure))
                        return
            except Exception:
                pass

            if timeframe == "5m" and asset in ("BTC", "ETH") and t_minus == 15:
                import infrastructure.state.state_manager as _sm_cf
                for _pos in _sm_cf.get_open_positions():
                    _ptitle = _pos.get("event_title", "")
                    if "[" + asset + "]" not in _ptitle:
                        continue
                    if "[15m]" not in _ptitle:
                        continue
                    _pos_up = _pos.get("direction") in ("YES", "UP")
                    _our_up = direction == "UP"
                    if _pos_up != _our_up:
                        log.info("[CONFLICT-SKIP] %s/5m: %s conflicts with open 15m %s", asset, direction, "UP" if _pos_up else "DOWN")
                        return

            log.info("[LATENCY-ARB] Potential %s move detected for %s/%s (move: %.4f%%, Pyth: %.4f, Open: %.4f)",
                     direction, asset, timeframe, pct_move * 100, pyth_price, open_price)

            # CVD + OBI + 1m alignment gate (skip for T-2s sweeper — already near-certain)
            if t_minus != 2:
                from infrastructure.websocket.spot_websocket_ingest import (
                    get_cvd_metrics, get_binance_obi, get_m1_candle_alignment, _has_cvd_data
                )
                # Thresholds: strict for 5m/SOL T-15s (previously disabled assets),
                # standard for 15m/1h BTC/ETH
                _cvd_mult   = 0.40 if _strict_cvd_obi else 0.25
                _obi_thresh = 0.20 if _strict_cvd_obi else 0.10

                fast_cvd, slow_cvd = await get_cvd_metrics(asset)
                binance_obi = await get_binance_obi(asset)
                has_data = _has_cvd_data(asset)

                # Only apply gate when we have live data (don't block on cold start)
                if has_data:
                    if direction == "UP":
                        cvd_ok = fast_cvd > 0 and fast_cvd > _cvd_mult * abs(slow_cvd)
                        obi_ok = binance_obi > _obi_thresh
                    else:
                        cvd_ok = fast_cvd < 0 and abs(fast_cvd) > _cvd_mult * abs(slow_cvd)
                        obi_ok = binance_obi < -_obi_thresh

                    if not cvd_ok or not obi_ok:
                        log.info(
                            "[CVD-OBI] %s/%s %s: cvd_ok=%s (fast=%.1f slow=%.1f thresh=%.0f%%) "
                            "obi_ok=%s (obi=%.3f thresh=%.2f) — filtered",
                            asset, timeframe, direction,
                            cvd_ok, fast_cvd, slow_cvd, _cvd_mult * 100,
                            obi_ok, binance_obi, _obi_thresh,
                        )
                        return

                    # 1-minute candle direction + CVD alignment (T-5s: relax to direction only)
                    if t_minus != 5:
                        m1_ok = await get_m1_candle_alignment(asset, direction)
                        if not m1_ok:
                            log.info("[CVD-OBI] %s/%s %s: 1m candle misaligned — filtered", asset, timeframe, direction)
                            return

                    log.info("[CVD-OBI] %s/%s %s: PASS (fast=%.1f obi=%.3f)", asset, timeframe, direction, fast_cvd, binance_obi)

            # Governor request to prevent latency duplicate race conditions
            from core.engine.session_governor import request_trade_slot, commit_trade_slot, cancel_trade_slot
            allowed, slot_reason = await request_trade_slot(
                asset, timeframe, 0.85, interval_minutes, open_positions, is_dual=True, direction=direction
            )
            if not allowed:
                log.info("[LAT-GOVERNOR-BLOCKED] %s/%s %s blocked by governor: %s", asset, timeframe, direction, slot_reason)
                return

            slot_committed = False
            try:
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

                # Global market-level dedup: prevents FV engine + LAT-ARB race condition entering same market
                global _ACTIVE_MARKET_IDS
                _event_id = market.get("event_id", "")
                if _event_id and _event_id in _ACTIVE_MARKET_IDS:
                    log.info("[MARKET-DEDUP] %s/%s: market %s already being entered — skip duplicate",
                             asset, timeframe, _event_id[:12])
                    return
                if _event_id:
                    _ACTIVE_MARKET_IDS.add(_event_id)
                    asyncio.create_task(_expire_market_lock(_event_id))

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

                # 4. Compute fair win probability via normal-CDF (same model as FV engine)
                # sigma_frac = mean high-low range / close over last 14 closed candles
                closed = klines[:-1]
                _atr_vals = [
                    abs(float(k[2]) - float(k[3])) / max(float(k[4]), 1e-9)
                    for k in closed[-14:]
                ]
                sigma_frac = sum(_atr_vals) / len(_atr_vals) if _atr_vals else 0.02
                elapsed_min = (time.time() - float(klines[-1][0]) / 1000.0) / 60.0
                elapsed_min = max(0.1, min(elapsed_min, float(interval_minutes) - 0.1))

                from core.engine.fair_value import fair_prob_up as _fair_prob_up
                p_up = _fair_prob_up(pyth_price, open_price, sigma_frac, elapsed_min, float(interval_minutes))
                implied_prob = p_up if direction == "UP" else (1.0 - p_up)
                implied_prob = max(0.55, implied_prob)  # floor: never treat a weak move as near-certain
                    
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

                # BoneReaper confirmation gate: on 15m and 1h candles at T-15s, only enter when
                # market already agrees (65%+). ATM guessing at 43-55¢ on these candles has no edge.
                # T-5s and T-2s bypass this — near-certainty entries are different
                if t_minus == 15 and timeframe in ("15m", "1h") and entry_price < 0.65:
                    log.info("[BONE-CONFIRM] %s/%s: entry %.0fc below 65c floor for T-15s — skip ATM guess",
                             asset, timeframe, entry_price * 100)
                    return

                # ATM divergence floor: block entries within ±10¢ of 50¢ at T-15s AND T-5s
                # Near-ATM (40-60¢) means CLOB sees ~50/50 — Pyth divergence is noise, not edge.
                # BTC/5m at 48¢ and SOL/5m at 46¢ both fired at T-5s AFTER the T-15s gate was added.
                # T-2s has its own CLOB floor (≥75¢) above — T-2s does NOT bypass this gate.
                if t_minus in (5, 15) and abs(entry_price - 0.50) < 0.10:
                    log.info("[ATM-GATE] %s/%s T-%ds: entry %.0fc within 10¢ of ATM — skip (no divergence edge)",
                             asset, timeframe, t_minus, entry_price * 100)
                    return

                # Discount gate: Polymarket must lag our fair probability by ≥6¢
                # BoneReaper enters at 14¢ when fair prob is 95%+ — the lag is the edge
                # T-5s entries relax to 4¢ — typical lag is 4-5¢ at that window
                _discount = implied_prob - entry_price
                _discount_min = 0.04 if t_minus == 5 else 0.06
                if _discount < _discount_min:
                    log.info(
                        "[DISCOUNT] %s/%s %s: entry=%.2f fair=%.2f discount=%.2f < %.2f — no lag, skip",
                        asset, timeframe, direction, entry_price, implied_prob, _discount, _discount_min,
                    )
                    return
                    
                # T2_SWEEPER CLOB confirmation floor: at T-2s, the CLOB must already confirm direction.
                # If CLOB says 50¢ (50/50) with 2s left, Pyth noise is not edge — it's gambling.
                # ETH/15m T2_SWEEPER at 50¢ = -$5.05 loss: the canonical reason for this gate.
                # Require ≥75% CLOB confidence in our direction before sweeping.
                if t_minus == 2 and entry_price < 0.75:
                    log.info("[T2-CLOB-FLOOR] %s/%s: entry %.0fc < 75c — CLOB not yet confirmed — skip sweep",
                             asset, timeframe, entry_price * 100)
                    return

                # For T-2s sweeper: implied_prob is always 0.999 — any price < 99.9¢ is positive EV
                if t_minus == 2:
                    implied_prob = 0.999

                # 5. Position sizing — 15m gets 1.5× premium (82% WR earns it), 5m stays conservative
                from infrastructure.state.state_manager import get_current_balance
                current_balance = get_current_balance()

                normal_usd = engine.compute_size(0.85, entry_price, current_balance)
                if t_minus == 2:
                    # Sweeper: conservative sizing — near-zero risk ONLY when CLOB confirms (≥75¢ floor above)
                    # 4% of balance up to $3 — high WR but tiny ROI (+5-25%), keep loss risk low
                    usd_size = max(1.50, min(current_balance * 0.04, 3.0))
                    log.info("[T2-SWEEPER] %s/%s sweep sizing 4%% of balance: $%.2f", asset, timeframe, usd_size)
                elif t_minus == 5:
                    if entry_price < 0.10:
                        usd_size = max(1.0, normal_usd * 1.0)
                        log.info("[T5-SCANNER] %s/%s near-certainty <10c: full sizing 1.0x: $%.2f", asset, timeframe, usd_size)
                    elif entry_price < 0.25:
                        usd_size = max(1.0, normal_usd * 0.70)
                        log.info("[T5-SCANNER] %s/%s high-conf <25c: 0.7x sizing: $%.2f", asset, timeframe, usd_size)
                    else:
                        usd_size = max(1.0, normal_usd * 0.35)
                        log.info("[T5-SCANNER] %s/%s moderate T-5s: 0.35x sizing: $%.2f", asset, timeframe, usd_size)
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

                # Re-apply minimum after altcoin discount — VOLATILE_CHAOS (0.30x) + altcoin (0.60x)
                # can compound to $0.90 which gets skipped. Floor at $1.50 to keep trades alive.
                usd_size = max(1.50, usd_size)

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

                _trade_tag = "T2_SWEEPER" if t_minus == 2 else "LATENCY_ARB"
                order = place_order(
                    event_id=market["event_id"],
                    market_id=market_id,
                    amount_dollars=usd_size,
                    direction="YES" if direction == "UP" else "NO",
                    entry_price=entry_price,
                    event_title=f"[UPDOWN][{asset}][{timeframe}][{_trade_tag}] {market['event_title']}",
                    expiry_ts=market["expiry_ts"],
                )

                if order:
                    _LAT_LAST_ENTRY_TS = time.time()
                    await commit_trade_slot(asset, timeframe, 0.85, interval_minutes, is_dual=True, direction=direction)
                    slot_committed = True
                    if t_minus == 2:
                        log.info("[T2-SWEEPER ENTERED] %s/%s %s: $%.2f at %.0f¢ — sweeping winning side",
                                 asset, timeframe, direction, usd_size, entry_price * 100)
                        try:
                            from app.telegram_bot import send_alert
                            send_alert(f"SWEEP {asset}/{timeframe} {direction} | ${usd_size:.2f} @ {entry_price*100:.0f}c")
                        except Exception:
                            pass
                    else:
                        log.info("[LATENCY-ARB SUCCESSFULLY ENTERED] Entered %s/%s %s: $%.2f at %.0f¢ (implied prob: %.2f)",
                                 asset, timeframe, direction, usd_size, entry_price * 100, implied_prob)
                        try:
                            from app.telegram_bot import send_alert
                            send_alert(f"LATENCY ARB {asset}/{timeframe} {direction} | ${usd_size:.2f} @ {entry_price*100:.0f}c")
                        except Exception:
                            pass

                    # Spawn early-exit monitor for 15m LAT-ARB only — sweeper holds to expiry (2s left anyway)
                    if timeframe == "15m" and t_minus != 2:
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

                    # LAG_TRADE removed — spawning concurrent bets on peer asset causes
                    # 3 simultaneous DOWN entries during recoveries, catastrophic when wrong
                    if False and asset in ("BTC", "ETH") and abs(pct_move) >= 0.005 and t_minus not in (5, 2):
                        asyncio.create_task(
                            check_cross_asset_lag(asset, direction, next_close)
                        )
            finally:
                if not slot_committed:
                    await cancel_trade_slot(asset, timeframe)
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

    async def _expire_market_lock(event_id: str, delay: float = 15.0) -> None:
        """Release the market dedup lock after delay seconds."""
        await asyncio.sleep(delay)
        _ACTIVE_MARKET_IDS.discard(event_id)

    while True:
        try:
            now = time.time()
            # Loop over all registered engines
            for key, engine in engines.items():
                asset = engine.asset
                timeframe = engine.timeframe
                interval_minutes = 60 if engine.timeframe == "1h" else int(engine.timeframe.rstrip("m"))
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

                # T-2s sweeper window — Punisher strategy, enter winning side at 95-99¢
                elif 0.3 <= time_left <= 2.4:
                    if last_scanned_t2.get((asset, timeframe)) == next_close:
                        continue
                    if asset == "DOGE":
                        continue  # DOGE excluded: noisy Pyth
                    last_scanned_t2[(asset, timeframe)] = next_close
                    log.info("[T2-SWEEPER] Spawning sweeper scan %s/%s at T-%.1fs before close",
                             asset, timeframe, time_left)
                    asyncio.create_task(scan_and_trade(engine, next_close, time_left, t_minus=2))

        except Exception as e:
            log.error("[LATENCY-ARB] Scanner loop error: %s", e, exc_info=True)

        try:
            from infrastructure.websocket.spot_websocket_ingest import _price_move_events
            await asyncio.wait_for(_price_move_events.get(), timeout=1.0)
        except asyncio.TimeoutError:
            pass
        except Exception:
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
        interval_minutes = 60 if timeframe == "1h" else int(timeframe.rstrip("m"))
        try:
            from core.pyth_oracle_service import GLOBAL_ORACLE_CACHE
            pyth_price = GLOBAL_ORACLE_CACHE.get(asset, {}).get("price", 0.0)
            if pyth_price <= 0.0:
                return

            from core.engine.updown_engine import _fetch_klines_async
            tf_map = {"5m": ("5m", 30), "15m": ("15m", 30), "1h": ("1h", 30)}
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

            _snipe_size_mult = 1.0
            if up_price >= 0.85 and dn_price <= 0.20 and pct_move <= -0.002:
                snipe_direction = "DOWN"
                snipe_price = dn_price
            elif dn_price >= 0.85 and up_price <= 0.20 and pct_move >= 0.002:
                snipe_direction = "UP"
                snipe_price = up_price
            elif up_price >= 0.60 and dn_price <= 0.40 and pct_move <= -0.003:
                snipe_direction = "DOWN"
                snipe_price = dn_price
                _snipe_size_mult = 0.5
            elif dn_price >= 0.60 and up_price <= 0.40 and pct_move >= 0.003:
                snipe_direction = "UP"
                snipe_price = up_price
                _snipe_size_mult = 0.5

            if not snipe_direction:
                return

            # Market-level dedup: prevents simultaneous reversal snipe + LAT-ARB on same market
            global _ACTIVE_MARKET_IDS
            _snipe_event_id = market.get("event_id", "")
            if _snipe_event_id and _snipe_event_id in _ACTIVE_MARKET_IDS:
                log.info("[REVERSAL-SNIPE] %s/%s market already being entered — skip", asset, timeframe)
                return
            if _snipe_event_id:
                _ACTIVE_MARKET_IDS.add(_snipe_event_id)
                asyncio.create_task(_expire_market_lock(_snipe_event_id))

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
            # Dynamic fractional size with a $0.50 floor to prevent disabling on small accounts
            usd_size = max(1.50, min(balance * 0.005, 5.0)) * _snipe_size_mult  # $1.50 floor clears Polymarket $1 CLOB minimum

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
                interval_minutes = 60 if timeframe == "1h" else int(timeframe.rstrip("m"))
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


async def start_resolution_sweeper(session, engines):
    log.info("[SWEEPER] Post-resolution queue sweeper daemon started...")
    _swept = {}
    while True:
        try:
            from infrastructure.state.state_manager import get_current_balance
            import infrastructure.state.state_manager as state_mgr
            balance = get_current_balance()
            # Dynamic fractional size with a $0.50 floor to prevent disabling on small accounts
            usd_size = max(1.50, min(balance * 0.005, 5.0))  # $1.50 floor clears Polymarket $1 CLOB minimum
            now = time.time()
            for key, engine in engines.items():
                asset = engine.asset
                timeframe = engine.timeframe
                if asset == "DOGE":
                    continue
                try:
                    market = await engine._fetch_market(session, is_latency_scan=True)
                    if not market:
                        continue
                    up_price = market["up_price"]
                    dn_price = market["dn_price"]
                    event_id = market["event_id"]
                    if now - _swept.get(event_id, 0) < 30.0:
                        continue
                    sweep_dir = None
                    sweep_price = None
                    sweep_mid = None
                    if up_price >= 0.99:
                        sweep_dir = "YES"
                        sweep_price = up_price
                        sweep_mid = market["up_market"]["id"]
                    elif dn_price >= 0.99:
                        sweep_dir = "NO"
                        sweep_price = dn_price
                        sweep_mid = market["dn_market"]["id"]
                    if not sweep_dir:
                        continue
                    for pos in state_mgr.get_open_positions():
                        if pos.get("event_id") == event_id:
                            sweep_dir = None
                            break
                    if not sweep_dir:
                        continue
                    interval_minutes = 60 if timeframe == "1h" else int(timeframe.rstrip("m"))
                    next_close = ((int(now) // (interval_minutes * 60)) + 1) * (interval_minutes * 60)
                    if now >= next_close:
                        continue
                    from infrastructure.exchange.trader import place_order
                    from core.engine.session_governor import commit_trade_slot
                    order = place_order(
                        event_id=event_id,
                        market_id=sweep_mid,
                        amount_dollars=usd_size,
                        direction=sweep_dir,
                        entry_price=sweep_price,
                        event_title="[UPDOWN][" + asset + "][" + timeframe + "][RESOLUTION_SWEEP] " + market["event_title"],
                        expiry_ts=market["expiry_ts"],
                    )
                    if order:
                        _swept[event_id] = now
                        log.info("[SWEEPER] %s/%s %s @ %.0fc $%.2f", asset, timeframe, sweep_dir, sweep_price*100, usd_size)
                        try:
                            from app.telegram_bot import send_alert
                            send_alert("SWEEP " + asset + "/" + timeframe + " " + sweep_dir + " @ " + str(int(sweep_price*100)) + "c")
                        except Exception:
                            pass
                except Exception as ex:
                    log.debug("[SWEEPER] %s/%s: %s", asset, timeframe, ex)
        except Exception as ex:
            log.error("[SWEEPER] loop error: %s", ex)
        await asyncio.sleep(5.0)


async def start_close_sniper(session, engines):
    log.info("[CLOSE-SNIPER] Bonereaper-style near-close sniper daemon started...")
    _sniped = {}
    while True:
        try:
            now_ts = int(time.time())
            import infrastructure.state.state_manager as state_mgr
            open_positions = state_mgr.get_open_positions()
            open_event_ids = {p.get("event_id") for p in open_positions if p.get("event_id")}

            for key, engine in engines.items():
                asset = engine.asset
                timeframe = engine.timeframe
                if asset == "DOGE":
                    continue
                try:
                    market = await engine._fetch_market(session, is_latency_scan=True)
                    if not market:
                        continue
                    event_id = market["event_id"]
                    expiry_ts = market.get("expiry_ts", 0)
                    if not expiry_ts:
                        continue
                    ttl = expiry_ts - now_ts
                    # Snipe window: 8 to 45 seconds before expiry
                    if not (8 <= ttl <= 45):
                        continue

                    # Skip if we already have an open position in this event
                    if event_id in open_event_ids:
                        continue
                    # Local dedup check
                    if event_id in _sniped and now_ts - _sniped[event_id] < 60:
                        continue

                    up_price = market.get("up_price")
                    dn_price = market.get("dn_price")
                    if up_price is None or dn_price is None:
                        continue

                    snipe_dir = None
                    snipe_price = None
                    market_id = None
                    snipe_mode = None

                    _mode2_threshold = float(os.getenv("NCS_MODE2_THRESHOLD", "0.90"))

                    # Mode 1: terminal snipe — 95¢ and above, 8-45s TTL
                    if up_price >= 0.95:
                        snipe_dir = "YES"
                        snipe_price = up_price
                        market_id = market["up_market"]["id"]
                        snipe_mode = "CLOSE-SNIPE"
                    elif dn_price >= 0.95:
                        snipe_dir = "NO"
                        snipe_price = dn_price
                        market_id = market["dn_market"]["id"]
                        snipe_mode = "CLOSE-SNIPE"
                    # Mode 2: early certainty — 88-94¢, tighter 8-25s TTL window
                    elif _mode2_threshold <= up_price < 0.95 and 8 <= ttl <= 25:
                        snipe_dir = "YES"
                        snipe_price = up_price
                        market_id = market["up_market"]["id"]
                        snipe_mode = "CLOSE-SNIPE-EARLY"
                    elif _mode2_threshold <= dn_price < 0.95 and 8 <= ttl <= 25:
                        snipe_dir = "NO"
                        snipe_price = dn_price
                        market_id = market["dn_market"]["id"]
                        snipe_mode = "CLOSE-SNIPE-EARLY"

                    # High-price opposing-resolution guard for CLOSE-SNIPE (Mode 1 only)
                    # At ep > 0.93 the tail loss is ~92c per dollar — one wrong resolution
                    # wipes 10-15 wins. Require at least 2 of last 3 CLOB ticks to be
                    # moving toward resolution (price increasing, not flat/reversing).
                    if snipe_mode == "CLOSE-SNIPE" and snipe_price is not None and snipe_price > 0.93:
                        # Fetch last 3 ticks from recent orderbook snapshot cache if available
                        try:
                            from infrastructure.state.state_manager import get_recent_price_ticks
                            _ticks = get_recent_price_ticks(asset, timeframe, n=3)
                            if _ticks and len(_ticks) >= 2:
                                _tick_increasing = sum(1 for i in range(1, len(_ticks)) if _ticks[i] >= _ticks[i-1])
                                if _tick_increasing < 1:   # all ticks flat or declining — price stalling
                                    log.info(
                                        "[NCS-MOMENTUM] %s/%s: CLOSE-SNIPE %.0fc but last %d ticks not advancing — opposing resolution risk — skip",
                                        asset, timeframe, snipe_price * 100, len(_ticks)
                                    )
                                    snipe_dir = None
                                    snipe_price = None
                                    market_id = None
                        except Exception:
                            pass  # no tick cache available — allow trade (fail open)

                    if not snipe_dir or not market_id:
                        continue

                    import infrastructure.state.state_manager as _smgr

                    # Same-asset NCS dedup: block if any NCS position already active on this asset.
                    # ETH/5m + ETH/15m NCS simultaneously creates correlated double-exposure;
                    # when asset reverses both lose together (-$8.43 observed 2026-06-08).
                    _open_ncs = [
                        p for p in _smgr.get_open_positions()
                        if f"[{asset}]" in p.get("event_title", "")
                        and p.get("entry_type", "") in ("CLOSE-SNIPE", "CLOSE-SNIPE-EARLY")
                    ]
                    if _open_ncs:
                        log.info(
                            "[NCS-DEDUP] %s/%s: active NCS on %s already — skip (double-exposure prevention)",
                            asset, timeframe, asset,
                        )
                        continue

                    # NCS candle-direction gate: prior closed candle must align with bet direction.
                    # Prevents NCS firing into reversal setups (e.g. NCS YES after a DOWN candle).
                    # Fail-open: if klines unavailable, allow trade.
                    if snipe_mode == "CLOSE-SNIPE" and snipe_dir and hasattr(engine, "klines") and engine.klines and len(engine.klines) >= 2:
                        try:
                            _prev_kline = engine.klines[-2]
                            _prev_open  = float(_prev_kline[1])
                            _prev_close = float(_prev_kline[4])
                            _prev_was_up = _prev_close >= _prev_open
                            _ncs_expects_up = (snipe_dir == "YES")
                            if _prev_was_up != _ncs_expects_up:
                                log.info(
                                    "[NCS-CANDLE-GATE] %s/%s: prior candle %s but NCS=%s — reversal risk — skip",
                                    asset, timeframe,
                                    "UP" if _prev_was_up else "DN", snipe_dir
                                )
                                snipe_dir = None
                        except Exception:
                            pass  # fail-open

                    if not snipe_dir or not market_id:
                        continue

                    current_balance = _smgr.get_current_balance()

                    if snipe_mode == "CLOSE-SNIPE":
                        # Mode 1: balance-proportional terminal sizing
                        base = max(current_balance * 0.10, 2.50)
                        max_add = min(current_balance * 0.20, 12.50)
                        certainty = max(0.0, min(1.0, (snipe_price - 0.95) / 0.04))
                        amount_dollars = round(base + certainty * max_add, 2)
                        # Tail-risk cap: at ep > 0.90 wrong resolution costs ~89c/$
                        # Cap at $2.00 to prevent one reversal from wiping all prior wins
                        if snipe_price > 0.90:
                            _ncs_tail_cap = float(os.getenv("NCS_TAIL_CAP", "12.50"))  # Raised: $2 cap made wins too small vs losses
                            if amount_dollars > _ncs_tail_cap:
                                log.info("[NCS-TAIL-CAP] CLOSE-SNIPE %.0fc: size $%.2f -> $%.2f (tail-risk cap ep>90c)",
                                         snipe_price * 100, amount_dollars, _ncs_tail_cap)
                                amount_dollars = _ncs_tail_cap
                    else:
                        # Mode 2: quarter-Kelly sizing
                        _p = snipe_price
                        _gain = (0.99 - _p) / _p if _p < 0.99 else 0.0
                        _loss = (_p - 0.01) / _p if _p > 0.01 else 0.0
                        _kelly = (_p * _gain - (1.0 - _p) * _loss) / _gain if _gain > 0 else 0.0
                        amount_dollars = round(max(3.0, min(current_balance * max(0.0, _kelly) * 0.25, 15.0)), 2)

                    log.info(
                        "[CLOSE-SNIPER] %s triggered for %s/%s: %s @ %.0fc — %ds to expiry — size=$%.2f",
                        snipe_mode, asset, timeframe, snipe_dir, snipe_price * 100, ttl, amount_dollars
                    )

                    from infrastructure.exchange.trader import place_order
                    order = place_order(
                        event_id=event_id,
                        market_id=market_id,
                        amount_dollars=amount_dollars,
                        direction=snipe_dir,
                        entry_price=snipe_price,
                        event_title=f"[UPDOWN][{asset}][{timeframe}][{snipe_mode}] {market['event_title']}",
                        expiry_ts=expiry_ts,
                    )
                    if order:
                        _sniped[event_id] = now_ts
                        log.info("[CLOSE-SNIPER] ✅ Successfully sniped %s/%s %s @ %.0fc", asset, timeframe, snipe_dir, snipe_price * 100)
                        try:
                            from app.telegram_bot import send_alert
                            send_alert(f"{snipe_mode} {asset}/{timeframe} {snipe_dir} @ {int(snipe_price*100)}c $${amount_dollars:.2f}")
                        except Exception:
                            pass
                except Exception as ex:
                    log.debug("[CLOSE-SNIPER] %s/%s: %s", asset, timeframe, ex)
        except Exception as ex:
            log.error("[CLOSE-SNIPER] loop error: %s", ex)
        # Scan every 4 seconds
        await asyncio.sleep(4.0)
