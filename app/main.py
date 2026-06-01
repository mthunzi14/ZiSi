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
        
        # Volatility-Adaptive Entry Gate: 10s for VOLATILE/SHOCK, 15s for NORMAL/RANGE
        gate_limit = 12.0 # baseline fallback
        try:
            import json
            regime_path = Path("regime_status.json")
            if regime_path.exists():
                data = json.loads(regime_path.read_text(encoding="utf-8"))
                # Read the canonical regime (kept for future per-regime tuning),
                # but hold the entry window FLAT at 15s across all regimes.
                # Rationale: the 8W/2L track record was earned at a 15s gate, and
                # PnL in chaos is already protected by the 0.30x regime size
                # multiplier (floored at MIN_USD). Tightening the gate in
                # VOLATILE_CHAOS would only shave trade volume — disallowed by
                # the mandate that no change may lower volume/win-rate/PnL.
                regime = str(data.get("regime", "NORMAL")).upper()
                gate_limit = 15.0
        except Exception as e:
            pass

        if elapsed > gate_limit:
            log.warning(
                "[MAIN] %s/%s LATE_ENTRY_ABORT: elapsed time %.1fs exceeds %.1fs adaptive execution gate. Skipping candle.",
                asset, timeframe, elapsed, gate_limit
            )
            return None

        signal = await engine.generate_signal(session)
        if signal is not None:
            break

        now_ts_after = datetime.now(timezone.utc).timestamp()
        elapsed_after = now_ts_after - candle_start
        if elapsed_after > gate_limit:
            log.warning(
                "[MAIN] %s/%s LATE_ENTRY_ABORT: post-evaluation elapsed time %.1fs exceeds %.1fs gate.",
                asset, timeframe, elapsed_after, gate_limit
            )
            return None

        log.info(
            "[MAIN] %s/%s: No L2 book/signal at %.1fs — retrying within %.1fs gate...",
            asset, timeframe, elapsed_after, gate_limit
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
    direction = signal["direction"]
    score = signal["score"]
    market = signal["market"]
    entry_price = market["up_price"] if direction == "UP" else market["dn_price"]
    up_price = market["up_price"]
    dn_price = market["dn_price"]
    is_dual = signal.get("is_dual_eligible") or UpDownEngine.should_dual_enter(up_price, dn_price)

    from config import DUAL_ENTRY_MAX_COMBINED
    if is_dual and (up_price + dn_price) >= DUAL_ENTRY_MAX_COMBINED:
        is_dual = False

    # Score-Tiered Price Ceiling — preserves edge on high-conviction signals while
    # still protecting against buying near-resolved expensive contracts on weak signals.
    # EV analysis: score=0.76 at 58¢ DOWN → est WR 65% → EV = 0.65×0.42 - 0.35×0.58 = +8.7¢/$ edge.
    if not is_dual and (timeframe in ("5m", "15m")):
        if score >= 0.70:
            price_ceiling = 0.62   # High conviction: allow up to 62¢
        elif score >= 0.62:
            price_ceiling = 0.57   # Moderate conviction: up to 57¢
        else:
            price_ceiling = 0.53   # Low conviction: original strict ceiling
        if entry_price > price_ceiling:
            context.log_skip("entry_price_expensive", asset, timeframe, {"price": entry_price, "ceiling": price_ceiling, "score": score})
            log.info(
                "[MAIN] %s/%s PRICE_CEILING_ABORT: entry price %.4f exceeds %.2f¢ ceiling for score=%.2f. Skipping.",
                asset, timeframe, entry_price, price_ceiling * 100, score
            )
            return False, {}

    if not is_dual and not entry_price_gate(entry_price, score, is_dual=False):
        context.log_skip("entry_price", asset, timeframe, {"price": entry_price, "score": score})
        return False, {}


    if is_dual and (up_price + dn_price) >= 0.92:
        is_dual = False

    open_positions = state_manager.get_open_positions()
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

    raw_bet_usd = engine.compute_size(score, entry_price, current_balance)
    bet_usd = raw_bet_usd * risk_multiplier

    # ── Optimal Altcoin Sizing Gates (Fix A - Maximize P&L safely) ──
    if asset in ["SOL", "XRP"]:
        bet_usd = bet_usd * 0.60
        log.info("[RISK] SOL/XRP Sizing calibrated to 60%%: $%.2f", bet_usd)
    elif asset in ["BNB", "HYPE"]:
        bet_usd = bet_usd * 0.50
        log.info("[RISK] Altcoin %s Sizing calibrated to 50%%: $%.2f", asset, bet_usd)
    elif asset in ["ADA", "LINK", "DOGE", "AVAX", "SUI"]:
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
        "direction": direction,
        "score": score,
        "market": market,
        "entry_price": entry_price,
        "up_price": up_price,
        "dn_price": dn_price,
        "is_dual": is_dual,
        "risk_multiplier": risk_multiplier,
        "bet_usd": bet_usd,
    }
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
    direction = details["direction"]
    score = details["score"]
    market = details["market"]
    entry_price = details["entry_price"]
    up_price = details["up_price"]
    dn_price = details["dn_price"]
    is_dual = details["is_dual"]
    risk_multiplier = details["risk_multiplier"]
    bet_usd = details["bet_usd"]

    traded = False
    if is_dual:
        main_usd, hedge_usd = engine.compute_dual_sizes(
            score, entry_price,
            dn_price if direction == "UP" else up_price,
            current_balance,
        )
        main_usd = max(1.0, main_usd * risk_multiplier)
        hedge_usd = max(1.0, hedge_usd * risk_multiplier)

        main_order = _place_trade(asset, timeframe, direction, market, main_usd, entry_price, score, "DUAL_MAIN")
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
        order = _place_trade(asset, timeframe, direction, market, bet_usd, entry_price, score, "SINGLE")
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

    interval_minutes = int(timeframe.rstrip("m"))
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
            _try_telegram(
                f"TRADE {asset}/{timeframe} {direction} | ${actual_cost:.2f} @ {entry_price*100:.0f}c | {trade_type}"
            )
            return order
    except Exception as exc:
        log.error("[MAIN] Trade placement failed: %s", exc)
    return None


async def main() -> None:
    # Initialize persistent account state explicitly during bot startup (Issue E fix)
    initialize_state()
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

            # Start latency edge arbitrage scanner (Sprint 3)
            try:
                from core.engine.cycle_manager import start_latency_edge_scanner
                tasks.append(start_latency_edge_scanner(session, context.engines))
                log.info("[MAIN] Latency edge scanner background task registered.")
            except Exception as e:
                log.error("[MAIN] Failed to import start_latency_edge_scanner: %s", e)

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
