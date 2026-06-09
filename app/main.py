"""
main.py - ZiSi Bot — Polymarket Up/Down asyncio engine
6 independent asyncio tasks: BTC-5m, BTC-15m, ETH-5m, SOL-5m, XRP-5m, reconciliation.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import logging
import time
BOOT_TIME = time.time()

# Global entry rate limiter — prevents burst entries across all asset loops.
# Max one new position every ENTRY_COOLDOWN_S seconds globally.
_last_entry_ts: float = 0.0
ENTRY_COOLDOWN_S: float = 3.0  # REBUILD: 15s dropped candle-boundary bursts; 3s gives throughput for hundreds/day
_entry_lock: asyncio.Lock | None = None  # lazy-initialized inside the event loop

# FV global rate limiter — max 3 FV entries per 60s.
# Prevents correlated macro wipeouts where 5 assets fire simultaneously on the same bad candle.
_fv_entry_times: list = []
_FV_MAX_PER_60S: int = 6  # REBUILD: FV is the primary engine — allow more concurrent FV bursts

def _fv_rate_ok() -> bool:
    """True if fewer than 3 FV entries have fired in the last 60 seconds."""
    now = time.time()
    _fv_entry_times[:] = [ts for ts in _fv_entry_times if now - ts < 60.0]
    return len(_fv_entry_times) < _FV_MAX_PER_60S

def _fv_rate_record() -> None:
    """Record a FV entry in the sliding window."""
    _fv_entry_times.append(time.time())
import aiohttp
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from config import load_config, log_config_startup, ASSETS, TIMEFRAMES
try:
    from infrastructure.state.logger import setup_file_logging
except ImportError:
    def setup_file_logging(level="INFO"): pass
from infrastructure.state.state_manager import (
    initialize_runtime_tracking, update_runtime_tracking,
    update_heartbeat, get_current_balance, initialize_state,
    _get_trades_count,
)
from core.engine.updown_engine import UpDownEngine, register_engine
from core.risk.risk_manager import entry_price_gate, check_daily_loss_halt, check_exposure_caps
from core.engine.reconciliation import reconciliation_loop
from core.engine.regime_filter import time_gate_open
from core.engine.session_governor import (
    request_trade_slot, commit_trade_slot, has_open_asset_exposure,
)
from infrastructure.state import state_manager
from infrastructure.state.diagnostics import global_diagnostics
from core.engine.metrics_engine import track_skip
from strategies.arbitrage.arbitrage_scanner import arbitrage_scanner_loop
from infrastructure.exchange.trader import place_order, execute_exit
from infrastructure.state.logger import log_signal_evaluation

from dataclasses import dataclass, field

log = logging.getLogger("zisi.main")

# Cross-asset corroboration: when a non-NCS trade fires on a lead asset,
# shadow it onto correlated assets on the same timeframe without re-running gates.
# BTC is the primary leader (all alts follow); ETH is secondary (SOL+XRP follow, not BTC).
# DOGE excluded — insufficient correlation with BTC/ETH price action.
_CORR_MAP: dict[str, list[str]] = {
    "BTC": ["ETH", "SOL", "XRP"],
    "ETH": ["SOL", "XRP"],
}
_NCS_SOURCES = frozenset({"CLOSE-SNIPE", "CLOSE-SNIPE-EARLY"})


@dataclass
class TradingContext:
    engines: dict[str, UpDownEngine] = field(default_factory=dict)
    starting_balance: float = 0.0
    funnel_stats: dict[str, int] = field(default_factory=lambda: {
        "windows_evaluated": 0,
        "signals_generated": 0,
        "skipped": 0,
        "executed": 0,
    })

    def get_engine(self, asset: str, timeframe: str) -> Optional[UpDownEngine]:
        return self.engines.get(f"{asset}/{timeframe}")

    def log_skip(self, reason: str, asset: str, timeframe: str, details: dict = None) -> None:
        self.funnel_stats["skipped"] += 1
        track_skip(reason, {"asset": asset, "timeframe": timeframe, **(details or {})})
        log.info("[MAIN] %s/%s SKIP (%s) %s", asset, timeframe, reason, details or "")


def _try_telegram(msg: str) -> None:
    try:
        from app.telegram_bot import send_alert
        send_alert(msg)
    except Exception:
        try:
            from telegram_bot import send_alert
            send_alert(msg)
        except Exception:
            pass


async def _sleep_to_next_candle(
    interval_minutes: int,
    asset: Optional[str] = None,
    timeframe: Optional[str] = None,
    session: Optional[aiohttp.ClientSession] = None,
    context: Optional["TradingContext"] = None,
) -> None:
    interval_secs = interval_minutes * 60
    now = datetime.now(timezone.utc).timestamp()
    next_boundary = (int(now) // interval_secs + 1) * interval_secs
    
    # Pre-fetch 20 seconds before the boundary if we have engine/session details
    lead_time = 20.0
    sleep_first_stage = (next_boundary - lead_time) - now
    
    if sleep_first_stage > 5.0 and asset and timeframe and session and context:
        # Sleep until the prefetch trigger point
        await asyncio.sleep(sleep_first_stage)
        
        # Trigger pre-fetch in the background
        engine = context.get_engine(asset, timeframe)
        if engine:
            asyncio.create_task(engine.prefetch_upcoming_market(session, next_boundary))
            
        # Recalculate remaining sleep until 0.5s past boundary to allow prices to populate
        now = datetime.now(timezone.utc).timestamp()
        sleep_secs = next_boundary - now + 0.5
        if sleep_secs > 0:
            await asyncio.sleep(sleep_secs)
    else:
        # Standard sleep fallback (1.5 seconds past boundary for safety)
        sleep_secs = next_boundary - now + 1.5
        if sleep_secs > 0:
            await asyncio.sleep(sleep_secs)


async def heartbeat_daemon() -> None:
    """
    Independent background heartbeat daemon task.
    Updates the account state file timestamp every 30 seconds to keep
    the self-healing watchdog active and prevent false restarts.
    """
    log.info("[HEARTBEAT] Starting self-healing heartbeat daemon...")
    while True:
        try:
            # Check if paused flag exists
            paused = Path("bot_paused.flag").exists()
            # Get closed trades count
            trades = _get_trades_count()
            # Call state manager update
            update_heartbeat(trades_executed=trades, paused=paused, reason="daemon-tick")
            log.debug("[HEARTBEAT] Heartbeat written successfully (trades=%d, paused=%s)", trades, paused)
            
            # Check process runtime for scheduled restart (4 hours = 14400 seconds)
            elapsed = time.time() - BOOT_TIME
            if elapsed >= 14400:
                from infrastructure.exchange.trader import count_open_trades, get_pending_reconcile_count
                open_trades = count_open_trades()
                pending_count = get_pending_reconcile_count()
                if open_trades == 0 and pending_count == 0:
                    log.info("[HEARTBEAT] Process age is %.1f hours. Desk is clear. Clean exit (code 100) for PM2 auto-restart.", elapsed / 3600)
                    import os
                    os._exit(100)
                else:
                    log.debug("[HEARTBEAT] Process age is %.1f hours but desk has %d active trades and %d pending. Deferring clean exit.", elapsed / 3600, open_trades, pending_count)
        except Exception as e:
            log.error("[HEARTBEAT] Heartbeat daemon tick error: %s", e)
        await asyncio.sleep(30)


async def _evaluate_market_signals(
    engine: UpDownEngine,
    session: aiohttp.ClientSession,
    interval_minutes: int,
    asset: str,
    timeframe: str,
) -> Optional[dict]:
    # Strictly check the circuit breaker skip windows before entering the signal retry loop
    if engine.skip_windows > 0:
        engine.skip_windows -= 1
        log.info("[MAIN] %s/%s Circuit Breaker Active: skipping this window (%d left)", asset, timeframe, engine.skip_windows)
        return None

    start_time = time.time()
    signal = None

    while True:
        now_ts = datetime.now(timezone.utc).timestamp()
        candle_start = (int(now_ts) // (interval_minutes * 60)) * (interval_minutes * 60)
        elapsed = now_ts - candle_start
        
        signal = await engine.generate_signal(session)
        if signal is not None:
            break

        now_ts_after = datetime.now(timezone.utc).timestamp()
        elapsed_after = now_ts_after - candle_start

        log.info(
            "[MAIN] %s/%s: No L2 book/signal at %.1fs — retrying...",
            asset, timeframe, elapsed_after,
        )
        await asyncio.sleep(2.0)

    signal_gen_ms = (time.time() - start_time) * 1000
    if signal is not None:
        signal["signal_gen_ms"] = signal_gen_ms
    return signal


async def _validate_trade_slot(
    context: TradingContext,
    engine: UpDownEngine,
    asset: str,
    timeframe: str,
    interval_minutes: int,
    signal: dict,
    current_balance: float,
    session=None,
) -> tuple[bool, dict]:
    """
    Enforces risk and entry gates. Returns (allowed, details).
    """
    _entry_source = signal.get("entry_source", "SIG")
    direction = signal["direction"]
    score = signal["score"]
    market = signal["market"]
    entry_price = market["up_price"] if direction == "UP" else market["dn_price"]
    up_price = market["up_price"]
    dn_price = market["dn_price"]

    # SIG 40¢ floor: below 40¢ the crowd is >60% against the signal — momentum lag can't overcome
    # that consensus. Raised from 20¢ after two clean-slate losses at 24.5¢ and 40¢ NO entries.
    # CORR trades (already vetted by lead asset) and FV/NCS are exempt.
    if _entry_source not in ("FAIR_VAL", "CLOSE-SNIPE", "CORR") and entry_price < 0.40:
        log.info("[SIG-FLOOR] %s/%s: SIG %.0fc < 40c — crowd >60%% against signal — skip",
                 asset, timeframe, entry_price * 100)
        context.log_skip("sig_floor_40c", asset, timeframe)
        return False, {}

    # P5: SIG price ceiling — block buying the expensive side of an overextended market.
    # SIG/YES (UP) at >60¢ on 5m or >65¢ on 15m means market already priced it heavily;
    # edge evaporates and losses at 65¢ have confirmed this repeatedly.
    if _entry_source not in ("FAIR_VAL", "LATENCY_ARB", "CLOSE-SNIPE", "CORR"):
        _sig_ceil = 0.60 if timeframe == "5m" else 0.65
        if entry_price > _sig_ceil:
            log.info("[SIG-CEIL] %s/%s: SIG %.0fc > %.0fc ceiling — overextended — skip",
                     asset, timeframe, entry_price * 100, _sig_ceil * 100)
            context.log_skip("sig_ceiling", asset, timeframe)
            return False, {}

    # SIG mid-range quality guard (REBUILD Phase 4): the deleted dead zone let weak
    # cheap/contrarian SIG back in (26.5c NO -$4.08, 57.5c NO -$0.52). Re-protect the
    # 35-57c band — only allow SIG here on a strong score; FV carries direction at ATM.
    # BTC↔ETH corroboration bypass: if the correlated pair is already in a same-direction
    # same-timeframe position, the MIDGUARD score bar is dropped — both assets move together
    # and the open position is live confirmation of the edge.
    if (_entry_source not in ("FAIR_VAL", "LATENCY_ARB", "CLOSE-SNIPE", "T2_SWEEPER", "REVERSAL_STREAK", "CORR")
            and 0.35 < entry_price < 0.57 and score < 0.70):
        _corr_bypass = False
        _corr_pair = {"BTC": "ETH", "ETH": "BTC"}.get(asset.upper())
        if _corr_pair:
            try:
                from infrastructure.state.state_manager import state_manager as _sm
                for _p in _sm.get_open_positions():
                    _pt = _p.get("event_title", "")
                    if (_corr_pair in _pt
                            and f"[{timeframe}]" in _pt
                            and _p.get("direction", "").upper() == direction.upper()):
                        _corr_bypass = True
                        log.info(
                            "[SIG-CORROBORATE] %s/%s: %s open same-dir %s — MIDGUARD bypassed",
                            asset, timeframe, _corr_pair, direction,
                        )
                        break
            except Exception:
                pass
        if not _corr_bypass:
            log.info("[SIG-MIDGUARD] %s/%s: SIG %.0fc in 35-57c with weak score %.2f < 0.70 — skip",
                     asset, timeframe, entry_price * 100, score)
            context.log_skip("sig_midrange_guard", asset, timeframe)
            return False, {}

    # FV global rate limiter — max 3 FV entries per 60s.
    # Prevents the correlated macro wipeout pattern: 5 assets firing simultaneously
    # on the same bad macro candle, each sized at $6-8, wiping the full session P&L.
    if _entry_source == "FAIR_VAL" and not _fv_rate_ok():
        log.info("[FV-RATE] %s/%s: %d FV entries in last 60s — global rate cap, skip",
                 asset, timeframe, _FV_MAX_PER_60S)
        context.log_skip("fv_rate_limit", asset, timeframe)
        return False, {}

    # FV directional cooldown: block FV re-entry in the same direction on the same asset for 10 min
    # after ANY FV trade closes (win or loss). Prevents consecutive same-candle doubles like:
    # BTC FV DOWN @ 60.5c (WIN) → BTC FV DOWN @ 56.5c (LOSS 5 min later) — the loss wiped the win.
    # Loss cooldown (2 min) only blocked same-direction LOSSES; this covers wins too.
    if _entry_source == "FAIR_VAL":
        import json as _json_cd
        import time as _time_cd
        from pathlib import Path as _path_cd
        try:
            _ps_path = _path_cd(__file__).parent.parent / "infrastructure" / "exchange" / "positions_state.json"
            if _ps_path.exists():
                _ps_data = _json_cd.loads(_ps_path.read_text(encoding="utf-8"))
                for _ct in reversed(_ps_data.get("closed", [])[-30:]):
                    _et_title = _ct.get("event_title", "")
                    _ct_dir = _ct.get("direction", "").upper()
                    _same_dir = (
                        (_ct_dir in ("YES", "UP")) == (direction == "UP")
                        or (_ct_dir in ("NO", "DOWN")) == (direction == "DOWN")
                    )
                    if f"[{asset}]" in _et_title and "FAIR_VAL" in _et_title and _same_dir:
                        _exit_ts = _ct.get("exit_time", "")
                        _pnl = _ct.get("realized_pnl", 0.0)
                        try:
                            from datetime import datetime as _dt_cd
                            _et = _dt_cd.fromisoformat(_exit_ts.replace("Z", "+00:00"))
                            _age_s = _time_cd.time() - _et.timestamp()
                            _cooldown = 120 if _pnl < 0 else 600  # 2 min after loss, 10 min after win
                            if 0 < _age_s < _cooldown:
                                log.info(
                                    "[FV-COOLDOWN] %s/%s: FV %s %s %.0fs ago (pnl=%.2f) — %ds cooldown",
                                    asset, timeframe, direction,
                                    "LOSS" if _pnl < 0 else "WIN",
                                    _age_s, _pnl, _cooldown,
                                )
                                context.log_skip("fv_dir_cooldown", asset, timeframe)
                                return False, {}
                        except Exception:
                            pass
                        break  # most recent same-direction FV for this asset — stop
        except Exception:
            pass

    is_dual = signal.get("is_dual_eligible") or UpDownEngine.should_dual_enter(up_price, dn_price)

    from config import DUAL_ENTRY_MAX_COMBINED
    if is_dual and (up_price + dn_price) >= DUAL_ENTRY_MAX_COMBINED:
        is_dual = False

    # ATM gate removed — PBot enters at 47-52¢ with high WR; FV/SIG signal already validated edge

    # Score-Tiered Price Ceiling — preserves edge on high-conviction signals while
    # still protecting against buying near-resolved expensive contracts on weak signals.
    # EV analysis: score=0.76 at 58¢ DOWN → est WR 65% → EV = 0.65×0.42 - 0.35×0.58 = +8.7¢/$ edge.
    # Price ceiling removed — BoneReaper enters at 62-99¢; FV/LAT-ARB signal carries the edge

    if not is_dual and not entry_price_gate(entry_price, score, is_dual=False):
        context.log_skip("entry_price", asset, timeframe, {"price": entry_price, "score": score})
        return False, {}

    # SIGNAL dead zone: REMOVED to restore Friday June 5th trading volume
    # if _entry_source not in ("FAIR_VAL", "LATENCY_ARB", "CLOSE-SNIPE", "T2_SWEEPER", "REVERSAL_STREAK") and 0.35 < entry_price < 0.57:
    #     log.info(
    #         "[SIGNAL-DEAD-ZONE] %s/%s: %.0fc SIGNAL in 35-57c dead zone — 0%%WR historically — skip",
    #         asset, timeframe, entry_price * 100
    #     )
    #     context.log_skip("signal_dead_zone", asset, timeframe)
    #     return False, {}

    # Altcoin Market Leader Corroboration Guard
    # Altcoins correlate highly to BTC and ETH. Block if BOTH leaders are against our trade.
    _ALTCOINS = {"SOL", "XRP", "DOGE", "ADA", "LINK", "AVAX", "SUI"}
    if asset in _ALTCOINS and _entry_source not in ("LATENCY_ARB", "T2_SWEEPER"):
        tf_map = {"5m": ("5m", 2), "15m": ("15m", 2), "1h": ("1h", 2)}
        interval, limit = tf_map.get(timeframe, ("5m", 2))
        
        leaders_against = 0
        leaders_confirming = 0
        leaders_checked = 0

        for leader in ["BTC", "ETH"]:
            try:
                from core.engine.updown_engine import _fetch_klines_async
                from core.pyth_oracle_service import GLOBAL_ORACLE_CACHE
                
                leader_klines = await _fetch_klines_async(session, leader, interval, limit)
                if leader_klines:
                    leader_open = float(leader_klines[-1][1])
                    leader_spot = GLOBAL_ORACLE_CACHE.get(leader, {}).get("price", 0.0)
                    
                    if leader_spot > 0:
                        leaders_checked += 1
                        is_leader_up = leader_spot > leader_open
                        is_leader_dn = leader_spot < leader_open
                        
                        if direction in ("NO", "DOWN") and is_leader_up:
                            leaders_against += 1
                        elif direction in ("YES", "UP") and is_leader_dn:
                            leaders_against += 1
                        elif direction in ("YES", "UP") and is_leader_up:
                            leaders_confirming += 1
                        elif direction in ("NO", "DOWN") and is_leader_dn:
                            leaders_confirming += 1
            except Exception as e:
                log.warning("[LEADER-GUARD] Failed to check leader %s correlation: %s", leader, e)
                
        # Block if BOTH leaders are against the trade...
        if leaders_checked == 2 and leaders_against == 2:
            log.info(
                "[LEADER-GUARD] %s/%s %s: blocked because BOTH leaders (BTC & ETH) are against the trade direction",
                asset, timeframe, direction
            )
            context.log_skip("leader_corroboration_guard", asset, timeframe)
            return False, {}
        # ...but PROPAGATE when both leaders CONFIRM (REBUILD Phase 5): Bonereaper fires the
        # same direction across BTC/ETH/SOL/XRP/DOGE on a macro move. Boost the alt's conviction
        # (corroboration multiplier) so sizing scales with the cross-asset signal.
        elif leaders_checked == 2 and leaders_confirming == 2:
            _boosted = max(float(signal.get("corroboration_multiplier", 1.0)), 1.3)
            signal["corroboration_multiplier"] = _boosted
            log.info(
                "[LEADER-PROP] %s/%s %s: BOTH leaders (BTC & ETH) confirm — propagating conviction (corr×%.1f)",
                asset, timeframe, direction, _boosted,
            )

    if is_dual and (up_price + dn_price) >= 0.92:
        is_dual = False

    open_positions = state_manager.get_open_positions()

    # FV same-asset active dedup: block new FV entry if another FV position is already active on this asset.
    # Fixes race condition where candle-open FV signal fires before the previous candle's exit is written
    # to closed[] in positions_state.json — causing the disk-based cooldown to miss back-to-back losses.
    # REBUILD: dedup only within the SAME timeframe — mentors (PBot-6) run 5m + 15m
    # on the same asset concurrently, so a live 5m FV must not block a 15m FV entry.
    if _entry_source == "FAIR_VAL":
        _tf_tag = f"[{timeframe}]"
        _active_fv_on_asset = any(
            f"[{asset}]" in p.get("event_title", "") and "FAIR_VAL" in p.get("event_title", "")
            and _tf_tag in p.get("event_title", "")
            for p in open_positions
        )
        if _active_fv_on_asset:
            log.info(
                "[FV-ACTIVE-DEDUP] %s/%s: active FV position on %s — skip (prev candle still live)",
                asset, timeframe, asset
            )
            context.log_skip("fv_active_dedup", asset, timeframe)
            return False, {}

    # NCS same-asset dedup: block new NCS entry on an asset if an active NCS position exists on that asset.
    # ETH/5m NCS + ETH/15m NCS firing simultaneously creates correlated double-exposure;
    # when ETH reverses both lose together (-$8.43 observed 2026-06-08 08:00 ET).
    if _entry_source in ("CLOSE-SNIPE", "CLOSE-SNIPE-EARLY"):
        _active_ncs_on_asset = any(
            f"[{asset}]" in p.get("event_title", "") and
            p.get("entry_type", "") in ("CLOSE-SNIPE", "CLOSE-SNIPE-EARLY")
            for p in open_positions
        )
        if _active_ncs_on_asset:
            log.info(
                "[NCS-DEDUP] %s/%s: active NCS on %s already — skip (double-exposure prevention)",
                asset, timeframe, asset
            )
            context.log_skip("ncs_same_asset_dedup", asset, timeframe)
            return False, {}

    # Tier 1: FV Correlated Exposure Cap — max 2 FV positions open in same direction.
    # BTC+ETH+SOL all firing FV DOWN simultaneously creates correlated cluster risk:
    # a single candle reversal destroys all three at once. Cap at 2 same-direction FV.
    if _entry_source == "FAIR_VAL":
        _fv_same_dir = sum(
            1 for p in open_positions
            if "FAIR_VAL" in p.get("event_title", "")
            and (p.get("direction", "").upper() in ("UP", "YES")) == (direction == "UP")
        )
        if _fv_same_dir >= 2:
            log.info(
                "[FV-CORR-CAP] %s/%s: %d FV %s positions open — corr cap (max 2) — skip",
                asset, timeframe, _fv_same_dir, direction,
            )
            context.log_skip("fv_corr_cap", asset, timeframe)
            return False, {}

    # Same-direction quality gate: moderate ATM FV + ≥3 open same-direction → require high score.
    # Near-certainty FV (≤38¢ or ≥57¢) is exempt — stacking those is exactly what we want.
    if _entry_source == "FAIR_VAL" and 0.38 < entry_price < 0.57:
        _same_dir_count = sum(
            1 for p in open_positions
            if (p.get("direction", "").upper() in ("UP", "YES")) == (direction == "UP")
        )
        if _same_dir_count >= 4 and score < 0.75:
            log.info(
                "[SAME-DIR-GATE] %s/%s: %d open %s + moderate FV (%.0fc) + score %.2f < 0.82 — skip",
                asset, timeframe, _same_dir_count, direction, entry_price * 100, score,
            )
            context.log_skip("same_dir_quality_gate", asset, timeframe)
            return False, {}

    # ─── FV ATM confidence guard (REBUILD: replaces the old 44-65c hard dead zones) ───
    # The CDF sweet spot 42-65c is where the mentors (esp. PBot-6) make MOST of their money —
    # 23 of 30 of his wins sit in 46-54c. The old FV-UPPER-DEAD + FV-ATM-CORE + FV-COIN-FLIP
    # blocks banned FV from its single highest-EV band. We now ALLOW FV across 42-65c, but
    # only when the directional edge model (Phase 2) reports genuine confidence; otherwise
    # skip. No blanket ban — being right at the coin-flip IS the edge.
    if _entry_source == "FAIR_VAL" and 0.42 < entry_price < 0.65:
        _fv_conf = float(signal.get("fv_confidence", score))
        # 42-57c is near coin-flip territory — fv_confidence is often null so score is used as
        # fallback, but score gets boosted by sentiment/edge and overstates true FV directional
        # confidence. Two losses at 47.5c and 56.5c came from this zone. Require 0.70 here.
        # 57-65c has shown consistent FV edge (4 wins vs 1 loss) — keep at 0.60.
        if timeframe in ("15m", "1h"):
            _fv_atm_min = 0.55
        elif entry_price < 0.57:
            _fv_atm_min = 0.70  # near-ATM 5m: need real conviction, not score-boosted proxy
        else:
            _fv_atm_min = 0.60
        if _fv_conf < _fv_atm_min:
            log.info(
                "[FV-ATM-CONF] %s/%s: %.0fc ATM, confidence %.2f < %.2f — no directional edge, skip",
                asset, timeframe, entry_price * 100, _fv_conf, _fv_atm_min,
            )
            context.log_skip("fv_atm_low_confidence", asset, timeframe)
            return False, {}

    # Correlation cap: max 2 simultaneous 15m positions open at any time.
    # At 22:45, 4 correlated 15m positions (BTC+ETH+XRP+SOL) all expired wrong → -$10.91 in 2s.
    # Cap at 2 so one directional regime shift can lose at most 2 trades simultaneously.
    if timeframe == "15m":
        _open_15m = sum(1 for p in open_positions if "[15m]" in p.get("event_title", ""))
        if _open_15m >= 2:
            log.info(
                "[15M-CORR-CAP] %s/15m: %d open 15m positions — correlation cap reached, skip",
                asset, _open_15m,
            )
            context.log_skip("15m_correlation_cap", asset, timeframe)
            return False, {}

    allowed, slot_reason = await request_trade_slot(
        asset, timeframe, score, interval_minutes, open_positions, is_dual=is_dual, direction=direction,
    )
    if not allowed:
        context.log_skip(slot_reason, asset, timeframe, {"score": score})
        return False, {}

    risk_multiplier = global_diagnostics.get_risk_multiplier()
    if risk_multiplier <= 0:
        context.log_skip("diagnostics_halt", asset, timeframe)
        return False, {}

    _entry_source = signal.get("entry_source", "SIG")

    raw_bet_usd = engine.compute_size(score, entry_price, current_balance,
                                      confidence=(signal.get("fv_confidence") or None))
    corr_mult = signal.get("corroboration_multiplier", 1.0)
    bet_usd = raw_bet_usd * risk_multiplier * corr_mult

    # Tier 2C: Apply dynamic alpha weight multiplier (rolling strategy performance)
    try:
        from core.engine.alpha_weight_manager import alpha_weights
        _aw_mult = alpha_weights.get_multiplier(_entry_source)
        if _aw_mult != 1.0:
            log.info("[ALPHA-WEIGHT] %s: strategy mult=%.2f → bet $%.2f → $%.2f",
                     _entry_source, _aw_mult, bet_usd, bet_usd * _aw_mult)
            bet_usd *= _aw_mult
    except Exception:
        pass

    # Tier 3G: Fear & Greed extreme-market size reducer
    try:
        from core.analytics.sentiment_daemon import sentiment_filter
        _fng_mult = sentiment_filter.get_size_multiplier()
        if _fng_mult != 1.0:
            log.info("[SENTIMENT] F&G=%d extreme → size ×%.2f: $%.2f → $%.2f",
                     sentiment_filter.get_fear_greed_index(), _fng_mult, bet_usd, bet_usd * _fng_mult)
            bet_usd *= _fng_mult
    except Exception:
        pass
    if corr_mult != 1.0:
        log.info("[RISK] %s/%s corroboration_mult=%.1f → bet $%.2f",
                 asset, timeframe, corr_mult, bet_usd)

    # SIGNAL/5m premium: 75%+ WR confirmed — allocate proportionally more capital.
    # Only applies to pure SIG entries on 5m candles, not FV or LAT-ARB.
    if _entry_source == "SIG" and timeframe == "5m":
        bet_usd *= 1.35
        log.info("[RISK] SIG/5m premium +35%%: $%.2f", bet_usd)

    # ── REVERSAL sizing: quarter-Kelly proportional to actual edge at entry price ──
    # is_reversal entries (RSI <reversal_lo or >reversal_hi) have the highest edge in the engine.
    # Bonereaper sizes these at $56-655. Quarter-Kelly at current balance gives $6-10 vs old $0.89.
    if signal.get("is_reversal") and _entry_source in ("SIG", "SIGNAL"):
        _ep = entry_price
        if 0.01 < _ep < 0.99:
            _gain = (0.99 - _ep) / _ep
            _loss = (_ep - 0.01) / _ep
            _rev_wr = float(os.getenv("REVERSAL_WIN_RATE", "0.72"))
            _kelly = (_rev_wr * _gain - (1.0 - _rev_wr) * _loss) / _gain if _gain > 0 else 0.0
            _qk_size = max(3.0, min(current_balance * max(0.0, _kelly) * 0.25, 15.0))
            if _qk_size > bet_usd:
                log.info("[REVERSAL-SIZE] %s/%s ep=%.0fc kelly=%.1f%% → $%.2f (was $%.2f)",
                         asset, timeframe, _ep * 100, _kelly * 100, _qk_size, bet_usd)
                bet_usd = _qk_size

    # ── P2: Global Bet Cap — differentiated by timeframe / entry conviction ──
    # Bonereaper bets 13-50% of account per trade. ZiSi raised to match proportionally.
    # REVERSAL_STREAK / 1h = highest conviction → 30% Kelly. Standard → 12%.
    if timeframe == "1h" or _entry_source == "REVERSAL_STREAK":
        global_max_bet = min(current_balance * 0.30, 50.0)
        _cap_label = "HIGH-CONV"
    elif _entry_source == "FAIR_VAL" and entry_price < 0.40:
        global_max_bet = min(current_balance * 0.30, 50.0)
        _cap_label = "FV-DEEP"
    else:
        global_max_bet = min(current_balance * 0.12, 20.0)
        _cap_label = "STANDARD"
    if bet_usd > global_max_bet:
        log.info("[RISK] %s bet cap $%.2f -> $%.2f", _cap_label, bet_usd, global_max_bet)
        bet_usd = global_max_bet

    # ── P3: SIGNAL-specific Bet Cap ($10.0) ──
    if _entry_source in ("SIG", "SIGNAL"):
        if bet_usd > 10.0:
            log.info("[RISK] SIGNAL trade size capped at $10.0: $%.2f -> $10.00", bet_usd)
            bet_usd = 10.0

    # ── Tier 0: FV 1h hard cap ──
    # Until FV probability is calibrated (Platt scaling), cap 1h FV at 10%/balance or $8.
    # A single 1h FV loss at $10+ on a $22 balance = -45% drawdown; this prevents runaway.
    if _entry_source == "FAIR_VAL" and timeframe == "1h":
        _fv_1h_cap = min(current_balance * 0.10, 8.0)
        if bet_usd > _fv_1h_cap:
            log.info("[RISK] FV-1h cap: $%.2f → $%.2f (uncalibrated — env FV_1H_MAX_BET to override)", bet_usd, _fv_1h_cap)
            bet_usd = float(os.getenv("FV_1H_MAX_BET", str(_fv_1h_cap)))

    # ── Optimal Altcoin Sizing Gates (Fix A - Maximize P&L safely) ──
    # Exempt FV deep contrarian (<40c): sized by edge, not asset volatility.
    _fv_deep_exempt = (_entry_source == "FAIR_VAL" and entry_price < 0.40)
    if not _fv_deep_exempt:
        if asset in ["SOL", "XRP"]:
            bet_usd = bet_usd * 0.60
            log.info("[RISK] SOL/XRP Sizing calibrated to 60%%: $%.2f", bet_usd)
        elif asset in ["ADA", "DOGE", "AVAX", "SUI"]:
            bet_usd = min(bet_usd * 0.35, 35.0)
            log.info("[RISK] Altcoin %s Sizing calibrated to 35%% (max $35): $%.2f", asset, bet_usd)

    # Safety cap: Max 35% of current_balance per trade slot — Bonereaper-scale sizing.
    # 35% allows $17.50 at $50 balance, $35 at $100, $70 at $200 — matches mentor's proportional bets.
    max_safety_size = current_balance * 0.35
    if bet_usd > max_safety_size:
        log.info(
            "[RISK] Sizing capped at 35%% safety limit: $%.2f -> $%.2f",
            bet_usd, max_safety_size
        )
        bet_usd = max_safety_size

    if bet_usd < 1.00 and not is_dual:
        context.log_skip("size_too_small", asset, timeframe, {"bet_usd": bet_usd})
        return False, {}

    validation_details = {
        "direction":    direction,
        "score":        score,
        "market":       market,
        "entry_price":  entry_price,
        "up_price":     up_price,
        "dn_price":     dn_price,
        "is_dual":      is_dual,
        "risk_multiplier": risk_multiplier,
        "bet_usd":      bet_usd,
        "entry_source": _entry_source,
    }
    # Record FV approval in sliding window so rate limiter tracks in-flight entries
    if _entry_source == "FAIR_VAL":
        _fv_rate_record()
    return True, validation_details


async def _execute_order_flow(
    engine: UpDownEngine,
    asset: str,
    timeframe: str,
    interval_minutes: int,
    details: dict,
    current_balance: float,
) -> bool:
    """
    Executes placing orders (including DUAL hedges) and handles recovery.
    """
    global _entry_lock, _last_entry_ts
    if _entry_lock is None:
        _entry_lock = asyncio.Lock()

    async with _entry_lock:
        now = time.time()
        if now - _last_entry_ts < ENTRY_COOLDOWN_S:
            wait = ENTRY_COOLDOWN_S - (now - _last_entry_ts)
            log.info("[MAIN] %s/%s COOLDOWN skip — next entry in %.1fs", asset, timeframe, wait)
            return False
        _last_entry_ts = now  # claim the slot before releasing the lock

    direction    = details["direction"]
    score        = details["score"]
    market       = details["market"]
    entry_price  = details["entry_price"]
    up_price     = details["up_price"]
    dn_price     = details["dn_price"]
    is_dual      = details["is_dual"]
    risk_multiplier = details["risk_multiplier"]
    bet_usd      = details["bet_usd"]
    entry_source = details.get("entry_source", "SIG")

    traded = False
    if is_dual:
        main_usd, hedge_usd = engine.compute_dual_sizes(
            score, entry_price,
            dn_price if direction == "UP" else up_price,
            current_balance,
        )
        main_usd = max(1.0, main_usd * risk_multiplier)
        hedge_usd = max(1.0, hedge_usd * risk_multiplier)

        if entry_source == "FAIR_VAL":
            dual_main_tag = "FAIR_VAL"
        elif entry_source == "REVERSAL_STREAK":
            dual_main_tag = "REVERSAL_STREAK"
        else:
            dual_main_tag = "DUAL_MAIN"
        main_order = _place_trade(asset, timeframe, direction, market, main_usd, entry_price, score, dual_main_tag)
        hedge_dir = "DOWN" if direction == "UP" else "UP"
        hedge_price = dn_price if direction == "UP" else up_price
        hedge_order = _place_trade(asset, timeframe, hedge_dir, market, hedge_usd, hedge_price, score, "DUAL_HEDGE")

        if main_order or hedge_order:
            traded = True
            await commit_trade_slot(asset, timeframe, score, interval_minutes, is_dual=True, direction=direction)

        if (main_order is not None) != (hedge_order is not None):
            log.critical(
                "[MAIN] %s/%s: ASYMMETRIC FILL main=%s hedge=%s",
                asset, timeframe, main_order is not None, hedge_order is not None,
            )
            global_diagnostics.log_execution(150.0, 5.0, successful_hedge=False)
            _try_telegram(f"EMERGENCY: Asymmetric fill on {asset}/{timeframe}!")
            if main_order:
                execute_exit(main_order["order_id"], entry_price, exit_reason="EMERGENCY_ASYMMETRIC_UNWIND")
            if hedge_order:
                execute_exit(hedge_order["order_id"], hedge_price, exit_reason="EMERGENCY_ASYMMETRIC_UNWIND")
    else:
        if entry_source == "FAIR_VAL":
            single_tag = "FAIR_VAL"
        elif entry_source == "REVERSAL_STREAK":
            single_tag = "REVERSAL_STREAK"
        else:
            single_tag = "SINGLE"
        order = _place_trade(asset, timeframe, direction, market, bet_usd, entry_price, score, single_tag)
        if order:
            traded = True
            await commit_trade_slot(asset, timeframe, score, interval_minutes, is_dual=False, direction=direction)

    return traded


async def asset_loop(
    asset: str,
    timeframe: str,
    session: aiohttp.ClientSession,
    context: TradingContext,
    offset_seconds: int = 0,
) -> None:
    if offset_seconds > 0:
        await asyncio.sleep(offset_seconds)

    interval_minutes = 60 if timeframe == "1h" else int(timeframe.rstrip("m"))
    engine = context.get_engine(asset, timeframe)
    if not engine:
        log.error("[MAIN] Engine not found for %s/%s", asset, timeframe)
        return

    log.info("[MAIN] %s/%s task started — aligning to next candle boundary", asset, timeframe)
    await _sleep_to_next_candle(interval_minutes, asset, timeframe, session, context)

    while True:
        try:
            update_heartbeat(reason=f"loop-{asset}-{timeframe}")
            context.funnel_stats["windows_evaluated"] += 1

            if not time_gate_open():
                await _sleep_to_next_candle(interval_minutes, asset, timeframe, session, context)
                continue

            if Path("bot_paused.flag").exists():
                log.info("[MAIN] Bot is paused via flag - skipping %s/%s cycle", asset, timeframe)
                update_heartbeat(paused=True, reason=f"paused-{asset}-{timeframe}")
                await _sleep_to_next_candle(interval_minutes, asset, timeframe, session, context)
                continue

            current_balance = get_current_balance()
            if check_daily_loss_halt(context.starting_balance, current_balance):
                log.warning("[MAIN] Daily loss halt active — all trading paused")
                _try_telegram("HALT ZiSi: daily loss halt triggered — trading paused for today")
                await asyncio.sleep(3600)
                continue

            # 1. Evaluate Market Signals
            signal = await _evaluate_market_signals(engine, session, interval_minutes, asset, timeframe)
            if signal is None:
                context.log_skip("no_signal", asset, timeframe)
                await _sleep_to_next_candle(interval_minutes, asset, timeframe, session, context)
                continue

            context.funnel_stats["signals_generated"] += 1

            # 2. Validate Risk & Entry Gates
            allowed, details = await _validate_trade_slot(
                context, engine, asset, timeframe, interval_minutes, signal, current_balance,
                session=session,
            )
            if not allowed:
                try:
                    eval_signal_data = {
                        "confidence": signal["score"],
                        "sentiment": signal["direction"],
                        "coin": asset,
                        "source": "EnsembleML",
                    }
                    log_signal_evaluation(eval_signal_data, None, signal["score"])
                except Exception as eval_err:
                    log.error("[MAIN] Failed to log missed signal evaluation: %s", eval_err)
                await _sleep_to_next_candle(interval_minutes, asset, timeframe, session, context)
                continue

            # 3. Execute Order Flow
            execution_start = time.time()
            traded = await _execute_order_flow(
                engine, asset, timeframe, interval_minutes, details, current_balance
            )
            execution_time_ms = (time.time() - execution_start) * 1000

            try:
                eval_signal_data = {
                    "confidence": signal["score"],
                    "sentiment": signal["direction"],
                    "coin": asset,
                    "source": "EnsembleML",
                }
                log_signal_evaluation(eval_signal_data, signal["market"] if traded else None, signal["score"])
            except Exception as eval_err:
                log.error("[MAIN] Failed to log signal evaluation: %s", eval_err)

            if traded:
                context.funnel_stats["executed"] += 1
                global_diagnostics.log_execution(execution_time_ms, 0.0, successful_hedge=True)
                # Corroboration: shadow onto correlated assets at same size as lead trade
                if asset in _CORR_MAP and details.get("entry_source") not in _NCS_SOURCES:
                    asyncio.create_task(_place_corr_trades(
                        context, session, asset, timeframe,
                        details["direction"], details.get("entry_source", "SIG"),
                        lead_bet_usd=details.get("bet_usd", 0.0),
                        lead_score=details.get("score", 0.75),
                    ))

            update_runtime_tracking()

        except Exception as exc:
            log.error("[MAIN] %s/%s loop error: %s", asset, timeframe, exc, exc_info=True)

        await _sleep_to_next_candle(interval_minutes, asset, timeframe, session, context)


async def _place_corr_trades(
    context: "TradingContext",
    session: aiohttp.ClientSession,
    lead_asset: str,
    timeframe: str,
    direction: str,
    lead_source: str,
    lead_bet_usd: float = 0.0,
    lead_score: float = 0.75,
) -> None:
    """Shadow a confirmed non-NCS trade onto correlated assets at the same size as the lead."""
    from infrastructure.state.state_manager import get_open_positions, get_current_balance as _gcb
    targets = list(_CORR_MAP.get(lead_asset.upper(), []))
    # DOGE joins the shadow only on a very strong BTC/ETH signal (score >= 0.80)
    if lead_asset.upper() in ("BTC", "ETH") and lead_score >= 0.80 and "DOGE" not in targets:
        targets.append("DOGE")
    if not targets:
        return
    current_balance = _gcb()
    open_positions = get_open_positions()
    interval_minutes = 60 if timeframe == "1h" else int(timeframe.rstrip("m"))
    # Use the lead trade's bet size — all shadows match the lead dollar-for-dollar
    bet_usd = lead_bet_usd if lead_bet_usd > 1.0 else max(1.0, current_balance * 0.03)

    for corr_asset in targets:
        engine = context.get_engine(corr_asset, timeframe)
        if not engine:
            continue
        # Skip if already in a position on this asset/timeframe
        if any(
            corr_asset in p.get("event_title", "") and f"[{timeframe}]" in p.get("event_title", "")
            for p in open_positions
        ):
            log.info("[CORR] %s/%s: open position exists — skip shadow of %s", corr_asset, timeframe, lead_asset)
            continue
        # Fetch fresh market for the corr asset
        try:
            market = await engine._fetch_market(session)
        except Exception as _mfe:
            log.warning("[CORR] %s/%s: market fetch error — %s", corr_asset, timeframe, _mfe)
            continue
        if not market:
            continue
        entry_price = market["up_price"] if direction == "UP" else market["dn_price"]
        # Only shadow if market is reasonably liquid, not at extremes, and crowd isn't >60% against.
        # ETH/SOL CORR at 38.5c lost -$5.25/-$1.12: crowd was 61.5% against direction — no gate caught it.
        if entry_price < 0.40 or entry_price > 0.95:
            log.info("[CORR] %s/%s: price %.0fc out of bounds (min 40c) — skip shadow", corr_asset, timeframe, entry_price * 100)
            continue
        # Log CORR with the lead's trade type so analysis correctly attributes source
        _lead_type_map = {
            "FAIR_VAL": "FAIR-VAL", "SIG": "SIGNAL", "SIGNAL": "SIGNAL",
            "REVERSAL_STREAK": "REVERSAL_STREAK", "LATENCY_ARB": "LATENCY-ARB",
        }
        _corr_trade_type = _lead_type_map.get(lead_source, lead_source)
        order = _place_trade(corr_asset, timeframe, direction, market, bet_usd, entry_price, lead_score, _corr_trade_type)
        if order:
            log.info(
                "[CORR] %s/%s %s | $%.2f @ %.0fc | shadow of %s/%s [%s] → logged as %s",
                corr_asset, timeframe, direction, bet_usd, entry_price * 100,
                lead_asset, timeframe, lead_source, _corr_trade_type,
            )
            await commit_trade_slot(corr_asset, timeframe, 0.75, interval_minutes, is_dual=False, direction=direction)


def _place_trade(asset, timeframe, direction, market, usd_amount, entry_price, score, trade_type="SINGLE") -> Optional[dict]:
    try:
        market_id = (market["up_market"] if direction == "UP" else market["dn_market"]).get("id", "")

        # Strict 5.0¢ Max Slippage Guard (Live-matching defense)
        try:
            from infrastructure.websocket.extraterrestrial_ws_gateway import polymarket_l2_gateway
            live_price, _ = polymarket_l2_gateway.get_price(market_id)
            if live_price and abs(live_price - entry_price) > 0.05:
                log.warning(
                    "[TRADE] SLIPPAGE_ABORT: %s/%s Live price %.4f deviated from signal price %.4f by > 5.0¢. Aborting trade execution.",
                    asset, timeframe, live_price, entry_price
                )
                return None
        except Exception as slip_err:
            log.debug("[TRADE] Slippage guard skipped (could not read L2 book): %s", slip_err)

        shares = max(1, round(usd_amount / entry_price)) if entry_price > 0 else 1
        actual_cost = shares * entry_price

        order = place_order(
            event_id=market["event_id"],
            market_id=market_id,
            amount_dollars=actual_cost,
            direction="YES" if direction == "UP" else "NO",
            entry_price=entry_price,
            event_title=f"[UPDOWN][{asset}][{timeframe}][{trade_type}] {market['event_title']}",
            expiry_ts=market["expiry_ts"],
        )

        if order:
            log.info(
                "[TRADE OPENED] %s/%s %s | $%.2f @ %.0f¢ | score=%.2f | %s",
                asset, timeframe, direction, actual_cost, entry_price * 100, score, trade_type,
            )
            # Stamp the regime at entry time for Session×Regime analytics
            try:
                import json as _j
                from pathlib import Path as _P
                _rs = _P("regime_status.json")
                _regime_now = _j.loads(_rs.read_text(encoding="utf-8")).get("regime", "UNKNOWN") if _rs.exists() else "UNKNOWN"
                from infrastructure.exchange.trader import annotate_position
                annotate_position(order["order_id"], regime=_regime_now)
            except Exception:
                pass
            _try_telegram(
                f"TRADE {asset}/{timeframe} {direction} | ${actual_cost:.2f} @ {entry_price*100:.0f}c | {trade_type}"
            )
            return order
    except Exception as exc:
        log.error("[MAIN] Trade placement failed: %s", exc)
    return None


async def _zombie_cleanup_loop() -> None:
    """Periodically delete positions whose expiry_ts has passed."""
    while True:
        await asyncio.sleep(300)  # every 5 minutes
        try:
            from infrastructure.state.state_manager import cleanup_expired_positions
            deleted = cleanup_expired_positions()
            if deleted:
                log.info("[ZOMBIE-LOOP] Cleaned %d zombie positions", deleted)
        except Exception as e:
            log.warning("[ZOMBIE-LOOP] Error: %s", e)


async def main() -> None:
    # Initialize persistent account state explicitly during bot startup (Issue E fix)
    initialize_state()
    # Clean up any zombie positions from prior session at startup
    try:
        from infrastructure.state.state_manager import cleanup_expired_positions
        _cleaned = cleanup_expired_positions()
        if _cleaned:
            log.info("[STARTUP] Deleted %d zombie positions from prior session", _cleaned)
    except Exception as _ze:
        log.warning("[STARTUP] Zombie cleanup failed: %s", _ze)
    update_heartbeat(reason="bot-booting")

    cfg = load_config()
    setup_file_logging(cfg.get("LOG_LEVEL", "INFO"))
    log_config_startup(cfg)

    context = TradingContext(starting_balance=get_current_balance())
    initialize_runtime_tracking()
    # Pre-load PyTorch and initialize the AI Injector at boot time to prevent CPU-blocking event loop freeze during trading
    try:
        log.info("[MAIN] Pre-loading AI Predictor & PyTorch LSTM model in-memory...")
        from core.ml.ai_injector import injector
        log.info("[MAIN] AI Predictor pre-loaded successfully (observe-only: %s)", not injector.is_trained)
    except Exception as e:
        log.warning("[MAIN] Failed to pre-load AI Predictor: %s", e)

    for asset in ASSETS:
        for tf in TIMEFRAMES.get(asset, ["5m"]):
            key = f"{asset}/{tf}"
            context.engines[key] = UpDownEngine(asset, tf, state_manager, _try_telegram)
            register_engine(context.engines[key])
            log.info("[MAIN] Engine registered: %s", key)

    from infrastructure.websocket.spot_websocket_ingest import BinanceWebSocketIngest
    ingest = BinanceWebSocketIngest(symbols=ASSETS)
    ingest.start()

    # ── Pyth Hermes Real-Time SSE Price Stream Service Integration ──────────
    # Starts Pyth Hermes streaming as a persistent background daemon, bypassing rate limits.
    # Enables global in-memory sub-0.1ms oracle spot price caching.
    from core.pyth_oracle_service import PythOracleService
    pyth_service = PythOracleService()
    await pyth_service.start()

    try:
        # Robust TCP connection pooling configuration
        connector = aiohttp.TCPConnector(
            limit=100,
            limit_per_host=20,
            keepalive_timeout=30,
            enable_cleanup_closed=True,
            force_close=False,
        )
        timeout = aiohttp.ClientTimeout(total=30)
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "Accept": "application/json",
        }
        async with aiohttp.ClientSession(connector=connector, timeout=timeout, headers=headers) as session:
            # Dynamically generate and stagger asset loops based on configured assets and timeframes (Fix A & C)
            tasks = []
            stagger = 0
            for asset in ASSETS:
                for tf in TIMEFRAMES.get(asset, ["5m"]):
                    tasks.append(asset_loop(asset, tf, session, context, offset_seconds=stagger))
                    stagger += 15  # Stagger startup by 15s to distribute WebSocket and RPC load evenly

            tasks.append(reconciliation_loop(state_manager, _try_telegram))
            tasks.append(arbitrage_scanner_loop(_try_telegram))
            tasks.append(heartbeat_daemon())
            asyncio.create_task(_zombie_cleanup_loop())

            # Start latency edge arbitrage scanner (Sprint 3)
            try:
                from core.engine.cycle_manager import start_latency_edge_scanner
                tasks.append(start_latency_edge_scanner(session, context.engines))
                log.info("[MAIN] Latency edge scanner background task registered.")
            except Exception as e:
                log.error("[MAIN] Failed to import start_latency_edge_scanner: %s", e)

            try:
                from core.engine.cycle_manager import start_reversal_sniper
                tasks.append(start_reversal_sniper(session, context.engines))
                log.info("[MAIN] Reversal sniper background task registered.")
            except Exception as e:
                log.error("[MAIN] Failed to import start_reversal_sniper: %s", e)

            try:
                from core.engine.cycle_manager import start_resolution_sweeper
                tasks.append(start_resolution_sweeper(session, context.engines))
                log.info("[MAIN] Resolution sweeper background task registered.")
            except Exception as e:
                log.error("[MAIN] Failed to import start_resolution_sweeper: %s", e)

            # Tier 3G: Fear & Greed sentiment daemon (macro size filter)
            try:
                from core.analytics.sentiment_daemon import sentiment_filter
                tasks.append(sentiment_filter.start_poll_loop())
                log.info("[MAIN] Sentiment (F&G) daemon registered.")
            except Exception as e:
                log.error("[MAIN] Failed to start sentiment daemon: %s", e)

            try:
                from core.engine.cycle_manager import start_close_sniper
                tasks.append(start_close_sniper(session, context.engines))
                log.info("[MAIN] Close sniper background task registered.")
            except Exception as e:
                log.error("[MAIN] Failed to import start_close_sniper: %s", e)

            log.info("[MAIN] Launching %d asyncio tasks (Dynamic asset loops + reconciliation + arbitrage scanner)", len(tasks))
            await asyncio.gather(*tasks)
    finally:
        await pyth_service.stop()
        log.info("[MAIN] Halting HFT WebSocket ingest daemon...")
        ingest.stop()
        log.info(
            "[MAIN] Funnel: evaluated=%d signals=%d executed=%d skipped=%d",
            context.funnel_stats["windows_evaluated"],
            context.funnel_stats["signals_generated"],
            context.funnel_stats["executed"],
            context.funnel_stats["skipped"],
        )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("[MAIN] Shutdown requested")
        sys.exit(0)
