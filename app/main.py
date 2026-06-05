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

# Global entry rate limiter — prevents burst entries across all asset loops.
# Max one new position every ENTRY_COOLDOWN_S seconds globally.
_last_entry_ts: float = 0.0
ENTRY_COOLDOWN_S: float = 15.0
_entry_lock: asyncio.Lock | None = None  # lazy-initialized inside the event loop

# FV global rate limiter — max 3 FV entries per 60s.
# Prevents correlated macro wipeouts where 5 assets fire simultaneously on the same bad candle.
_fv_entry_times: list = []
_FV_MAX_PER_60S: int = 3

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

    # SIG 10¢ floor: block only extreme-consensus entries where market is 90%+ against signal
    if _entry_source != "FAIR_VAL" and entry_price < 0.10:
        log.info("[SIG-FLOOR] %s/%s: SIG %.0fc < 10c — market too extreme against signal — skip",
                 asset, timeframe, entry_price * 100)
        context.log_skip("sig_floor_10c", asset, timeframe)
        return False, {}

    # FV global rate limiter — max 3 FV entries per 60s.
    # Prevents the correlated macro wipeout pattern: 5 assets firing simultaneously
    # on the same bad macro candle, each sized at $6-8, wiping the full session P&L.
    if _entry_source == "FAIR_VAL" and not _fv_rate_ok():
        log.info("[FV-RATE] %s/%s: %d FV entries in last 60s — global rate cap, skip",
                 asset, timeframe, _FV_MAX_PER_60S)
        context.log_skip("fv_rate_limit", asset, timeframe)
        return False, {}

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


    if is_dual and (up_price + dn_price) >= 0.92:
        is_dual = False

    open_positions = state_manager.get_open_positions()

    # Same-direction exposure cap: max 2 open positions in same direction at once.
    # Prevents piling into an exhausted trend and magnifying directional loss clusters.
    signal_is_up = direction == "UP"
    same_dir_open = sum(
        1 for p in open_positions
        if (p.get("direction") in ("YES", "UP")) == signal_is_up
    )
    if same_dir_open >= 2:
        context.log_skip("same_dir_cap", asset, timeframe,
                         {"direction": direction, "open_same_dir": same_dir_open})
        log.info("[RISK] %s/%s SAME_DIR_CAP: already %d open %s positions — skip",
                 asset, timeframe, same_dir_open, direction)
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

    raw_bet_usd = engine.compute_size(score, entry_price, current_balance)
    corr_mult = signal.get("corroboration_multiplier", 1.0)
    bet_usd = raw_bet_usd * risk_multiplier * corr_mult
    if corr_mult != 1.0:
        log.info("[RISK] %s/%s corroboration_mult=%.1f → bet $%.2f",
                 asset, timeframe, corr_mult, bet_usd)

    # SIGNAL/5m premium: 75%+ WR confirmed — allocate proportionally more capital.
    # Only applies to pure SIG entries on 5m candles, not FV or LAT-ARB.
    if _entry_source == "SIG" and timeframe == "5m":
        bet_usd *= 1.35
        log.info("[RISK] SIG/5m premium +35%%: $%.2f", bet_usd)

    # ── Optimal Altcoin Sizing Gates (Fix A - Maximize P&L safely) ──
    if asset in ["SOL", "XRP"]:
        bet_usd = bet_usd * 0.60
        log.info("[RISK] SOL/XRP Sizing calibrated to 60%%: $%.2f", bet_usd)
    elif asset in ["ADA", "DOGE", "AVAX", "SUI"]:
        bet_usd = min(bet_usd * 0.35, 35.0)
        log.info("[RISK] Altcoin %s Sizing calibrated to 35%% (max $35): $%.2f", asset, bet_usd)

    # Safety cap: Max 15% of current_balance per trade slot to prevent black-swan drawdowns
    max_safety_size = current_balance * 0.15
    if bet_usd > max_safety_size:
        log.info(
            "[RISK] Sizing capped at 15%% safety limit: $%.2f -> $%.2f",
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

        dual_main_tag = "FAIR_VAL" if entry_source == "FAIR_VAL" else "DUAL_MAIN"
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
        single_tag = "FAIR_VAL" if entry_source == "FAIR_VAL" else "SINGLE"
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
                context, engine, asset, timeframe, interval_minutes, signal, current_balance
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

            update_runtime_tracking()

        except Exception as exc:
            log.error("[MAIN] %s/%s loop error: %s", asset, timeframe, exc, exc_info=True)

        await _sleep_to_next_candle(interval_minutes, asset, timeframe, session, context)


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
