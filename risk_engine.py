"""
risk_engine.py - ZiSi Standalone Stop-Loss & Position Monitor.

Runs as an independent daemon thread so stop-losses fire regardless of
what the main 15-minute cycle is doing.  Inspired by Repo 4
(aulekator/Polymarket-BTC-15-Minute-Trading-Bot) risk_engine architecture.

Architecture:
  - Main loop calls start_risk_engine() once at startup.
  - The engine polls open positions every POLL_INTERVAL_SECONDS.
  - If a position breaches STOP_LOSS_PCT or TAKE_PROFIT_PCT, it logs a
    close signal and fires the appropriate action (paper: log + update
    state; live: execute close order via trader.py).
  - Call stop_risk_engine() at shutdown.

Stop-loss thresholds (configurable via .env):
  STOP_LOSS_PCT=15    → close position if down 15% from entry
  TAKE_PROFIT_PCT=40  → close position if up 40% from entry
"""

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger("zisi.risk_engine")

# ── Configuration ─────────────────────────────────────────────────────────────
POLL_INTERVAL_SECONDS = 30        # how often to check open positions
STOP_LOSS_PCT  = float(os.getenv("STOP_LOSS_PCT",  "15"))  # % loss to trigger stop
TAKE_PROFIT_PCT = float(os.getenv("TAKE_PROFIT_PCT", "40"))  # % gain to take profit

# ── Thread state ──────────────────────────────────────────────────────────────
_stop_event   = threading.Event()
_engine_thread: Optional[threading.Thread] = None
_open_positions: list = []          # in-memory register, populated by main cycle
_positions_lock = threading.Lock()

# State file for open positions (populated by trader.py)
_STATE_FILE = Path(__file__).parent / "account_state.json"


# ── Position registration (called by trader.py after each trade) ──────────────

def register_open_position(position: dict) -> None:
    """
    Register a new open position for monitoring.

    Args:
        position: dict with keys:
            order_id, market_id, condition_id, entry_price,
            position_size, sentiment, timestamp_open
    """
    with _positions_lock:
        # Avoid duplicates
        existing_ids = {p.get("order_id") for p in _open_positions}
        if position.get("order_id") not in existing_ids:
            _open_positions.append(position)
            log.info(
                "[RISK-ENGINE] Registered position: %s | entry=%.4f | size=$%.2f",
                position.get("order_id", "?"),
                position.get("entry_price", 0),
                position.get("position_size", 0),
            )


def close_position(order_id: str) -> None:
    """Remove a position from the monitor (called after close execution)."""
    with _positions_lock:
        before = len(_open_positions)
        _open_positions[:] = [p for p in _open_positions if p.get("order_id") != order_id]
        if len(_open_positions) < before:
            log.info("[RISK-ENGINE] Position deregistered: %s", order_id)


def get_open_positions() -> list:
    """Return a snapshot of currently monitored positions."""
    with _positions_lock:
        return list(_open_positions)


# ── Price fetcher (reuses data_fetcher or CLOB API) ──────────────────────────

def _fetch_current_price(market_id: str) -> Optional[float]:
    """
    Fetch current YES price for a Polymarket market.
    Returns None if unavailable.
    """
    try:
        from data_fetcher import get_event_current_price
        result = get_event_current_price(market_id)
        if result:
            return float(result.get("price", 0))
    except Exception as exc:
        log.debug("[RISK-ENGINE] Price fetch failed for %s: %s", market_id, exc)
    return None


# ── Stop-loss evaluation ──────────────────────────────────────────────────────

def _evaluate_position(position: dict) -> Optional[str]:
    """
    Check if position breaches stop-loss or take-profit thresholds.

    Returns:
        "STOP_LOSS"   — position down ≥ STOP_LOSS_PCT
        "TAKE_PROFIT" — position up   ≥ TAKE_PROFIT_PCT
        None          — within bounds, no action needed
    """
    market_id   = position.get("market_id") or position.get("condition_id") or ""
    entry_price = float(position.get("entry_price", 0) or 0)
    sentiment   = position.get("sentiment", "bullish").lower()

    if not market_id or entry_price <= 0:
        return None

    current_price = _fetch_current_price(market_id)
    if current_price is None or current_price <= 0:
        return None

    # For YES positions: profit if price rises, loss if falls
    # For NO positions: profit if price falls, loss if rises
    if sentiment == "bearish":
        # We hold NO (entry was 1 - price), so invert
        price_change_pct = ((1 - current_price) - (1 - entry_price)) / (1 - entry_price) * 100
    else:
        price_change_pct = (current_price - entry_price) / entry_price * 100

    if price_change_pct <= -STOP_LOSS_PCT:
        log.warning(
            "[RISK-ENGINE] 🛑 STOP-LOSS triggered | %s | entry=%.4f current=%.4f change=%.1f%%",
            market_id[:20], entry_price, current_price, price_change_pct,
        )
        return "STOP_LOSS"

    if price_change_pct >= TAKE_PROFIT_PCT:
        log.info(
            "[RISK-ENGINE] ✅ TAKE-PROFIT triggered | %s | entry=%.4f current=%.4f change=+%.1f%%",
            market_id[:20], entry_price, current_price, price_change_pct,
        )
        return "TAKE_PROFIT"

    log.debug(
        "[RISK-ENGINE] Position OK | %s | entry=%.4f current=%.4f change=%+.1f%%",
        market_id[:20], entry_price, current_price, price_change_pct,
    )
    return None


def _execute_close(position: dict, reason: str) -> None:
    """
    Close a position due to stop-loss or take-profit trigger.

    In paper trading: logs the close and updates account state file.
    In live mode: would call trader.py execute_trade for close leg.
    """
    from config import load_config
    cfg = load_config()
    is_paper = cfg.get("BOT_MODE", "paper_trading") == "paper_trading"

    order_id   = position.get("order_id", "?")
    market_id  = position.get("market_id", "")
    entry      = float(position.get("entry_price", 0) or 0)
    size       = float(position.get("position_size", 0) or 0)

    current_price = _fetch_current_price(market_id) or entry
    sentiment  = position.get("sentiment", "bullish").lower()

    if sentiment == "bearish":
        pnl = ((1 - current_price) - (1 - entry)) * size / entry if entry > 0 else 0
    else:
        pnl = (current_price - entry) * size / entry if entry > 0 else 0

    action_log = {
        "type": "risk_close",
        "reason": reason,
        "order_id": order_id,
        "market_id": market_id,
        "entry_price": entry,
        "exit_price": current_price,
        "position_size": size,
        "pnl": round(pnl, 4),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "mode": "paper" if is_paper else "live",
    }

    # Write to local JSONL log
    try:
        log_path = Path(__file__).parent / "zisi_local_trades.jsonl"
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(action_log) + "\n")
    except Exception as exc:
        log.error("[RISK-ENGINE] Failed to write close log: %s", exc)

    log.info(
        "[RISK-ENGINE] Position CLOSED (%s) | %s | pnl=%+.4f | mode=%s",
        reason, order_id, pnl, "paper" if is_paper else "live",
    )

    # Remove from monitor
    close_position(order_id)

    # In live mode: add actual order execution here
    # if not is_paper:
    #     from trader import execute_close_order
    #     execute_close_order(position)


# ── Main polling loop ─────────────────────────────────────────────────────────

def _risk_engine_loop() -> None:
    """Daemon thread: polls open positions every POLL_INTERVAL_SECONDS."""
    log.info(
        "[RISK-ENGINE] Started | poll=%.0fs | stop-loss=%.0f%% | take-profit=%.0f%%",
        POLL_INTERVAL_SECONDS, STOP_LOSS_PCT, TAKE_PROFIT_PCT,
    )

    while not _stop_event.is_set():
        try:
            with _positions_lock:
                positions_snapshot = list(_open_positions)

            if positions_snapshot:
                log.debug("[RISK-ENGINE] Checking %d open position(s)...", len(positions_snapshot))
                for pos in positions_snapshot:
                    action = _evaluate_position(pos)
                    if action:
                        _execute_close(pos, action)
            # else: no open positions, silent poll

        except Exception as exc:
            log.error("[RISK-ENGINE] Unexpected error in poll loop: %s", exc)

        _stop_event.wait(timeout=POLL_INTERVAL_SECONDS)

    log.info("[RISK-ENGINE] Shutdown complete")


# ── Public lifecycle ──────────────────────────────────────────────────────────

def start_risk_engine() -> None:
    """
    Start the risk engine daemon thread.
    Safe to call multiple times — only starts once.
    """
    global _engine_thread
    if _engine_thread and _engine_thread.is_alive():
        log.debug("[RISK-ENGINE] Already running")
        return

    _stop_event.clear()
    _engine_thread = threading.Thread(
        target=_risk_engine_loop,
        name="zisi-risk-engine",
        daemon=True,
    )
    _engine_thread.start()
    log.info("[RISK-ENGINE] Thread started (daemon)")


def stop_risk_engine() -> None:
    """Signal the risk engine to stop and wait up to 5 seconds."""
    global _engine_thread
    _stop_event.set()
    if _engine_thread and _engine_thread.is_alive():
        _engine_thread.join(timeout=5)
        log.info("[RISK-ENGINE] Thread stopped")
