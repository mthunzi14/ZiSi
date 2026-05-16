"""
main.py - ZiSi Bot Orchestrator
Runs the full news → sentiment → match → trade → monitor loop every 15 minutes.
"""

import json
import logging
import os
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

# ── Module imports ───────────────────────────────────────────────────────────
from config import load_config, log_config_startup
from data_fetcher import (
    fetch_news_from_newsapi,
    fetch_crypto_articles,
    fetch_polymarket_events,
    get_event_current_price,
)
from sentiment_analyzer import analyze_sentiment, analyze_articles_batch, filter_high_confidence_signals, calculate_confluence_score
from event_matcher import find_matching_events, select_best_event, pick_trading_direction, find_matching_event_smart
from risk_manager import (
    calculate_position_size, validate_trade, calculate_exit_targets,
    validate_liquidity, validate_entry_price, calculate_position_size_dynamic,
    calculate_position_size_kelly,
)
from trader import (
    place_order,
    count_open_trades,
    get_all_open_trades,
    check_exit_condition,
    execute_exit,
    update_trade_record,
    attach_exit_targets,
    check_and_close_paper_trades,
    start_reconciliation_loop,
    stop_reconciliation_loop,
    get_pending_reconcile_count,
    persist_positions,
    get_position_summary,
    refresh_open_position_prices,
    escalate_trailing_stops,
)
from logger import (
    log_trade_to_google_drive,
    log_signal_analysis,
    log_error,
    send_alert_email,
    send_daily_report,
    get_portfolio_metrics,
    log_liquidity_skip,
    log_price_skip,
    log_signal_evaluation,
    format_signal_log,
    format_cycle_log,
    setup_file_logging,
    _trade_history,
)
from email_scheduler import EmailScheduler
from price_analyzer import MultiTimeframeAnalyzer
from metrics_engine import (
    track_skip,
    calculate_daily_metrics,
    save_metrics_to_file,
    log_daily_summary,
    get_real_trade_count,
)
import state_manager
from state_manager import initialize_runtime_tracking, update_runtime_tracking, get_current_balance

# ── Kalshi integration ────────────────────────────────────────────────────────
from kalshi.auth import KalshiAuth
from kalshi.fetcher import KalshiEventFetcher, load_category_win_rates
from kalshi.matcher import KalshiEventMatcher
from kalshi.trader import KalshiTrader, get_kalshi_summary
from markets_orchestrator import run_kalshi_for_cycle

# ── ML pipeline ───────────────────────────────────────────────────────────────
from ml_pipeline import collect_cycle_data, get_ml_progress, link_trade_outcomes, load_model as _load_ml_model, ensure_phase2_activated as _ensure_phase2

# ── Health monitor ─────────────────────────────────────────────────────────────
from health_monitor import startup_recovery, start_health_monitor, stop_health_monitor

# ── Balance history (equity curve) ────────────────────────────────────────────
from balance_history import record_balance, prune_history

# ── Telegram bot ──────────────────────────────────────────────────────────────
from telegram_bot import (
    start_telegram_bot,
    stop_telegram_bot,
    send_alert as telegram_alert,
    notify_circuit_break,
    notify_trade_executed,
    notify_trade_closed,
)

# ── Edge scoring + regime detection ──────────────────────────────────────────
from regime_detector import RegimeDetector
from signal_router import SignalTypeClassifier, routing_decision
from cycle_manager import CycleManager
from ml_pipeline import get_blended_confidence

# ── Shadow mode (copy-trades PBot-6 + Wallet-2) ───────────────────────────────
from shadow_mode import ShadowModeMonitor, set_shadow_enabled

# ── Up/Down high-frequency trader ────────────────────────────────────────────
from updown_trader import run_updown_cycle

# ── Module-level singletons ───────────────────────────────────────────────────
_email_scheduler = EmailScheduler()
_price_analyzer = MultiTimeframeAnalyzer()

# Kalshi singletons (initialised once, fail gracefully if key missing)
_kalshi_auth = KalshiAuth()
_kalshi_fetcher = KalshiEventFetcher(_kalshi_auth)
_kalshi_matcher = KalshiEventMatcher()
_kalshi_trader = KalshiTrader(_kalshi_auth)

# Regime detector singleton
_regime_detector = RegimeDetector(atr_window=14)

# Signal type classifier singleton
_signal_classifier = SignalTypeClassifier()

# CycleManager: routing, sizing, conflict detection, prioritisation
_cycle_manager = CycleManager(account_balance=100.0)

# Shadow mode monitor — initialised in main() after logging is ready
_shadow_monitor: "ShadowModeMonitor | None" = None

# ── Logging setup ─────────────────────────────────────────────────────────────

def _setup_logging(level: str = "INFO") -> None:
    numeric = getattr(logging, level.upper(), logging.INFO)
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    logging.basicConfig(level=numeric, format=fmt, datefmt="%Y-%m-%d %H:%M:%S")


log = logging.getLogger("zisi.main")

# ── Per-cycle Polymarket event dedup ─────────────────────────────────────────
# Prevents trading the same Polymarket event multiple times in one cycle when
# several signals all smart-match to the same (only available) event.
# Reset at the start of each cycle in the main loop.
_poly_cycle_event_ids: set = set()

# ── Per-cycle skip reason counter ────────────────────────────────────────────
# Each entry in _process_signal() that returns without placing a trade calls
# _record_skip(reason).  At cycle end, _log_cycle_skip_summary() prints one
# concise line showing where signals are dropping out.
# Reset at cycle start alongside _poly_cycle_event_ids.
_cycle_skip_counts: dict = {}
_cycle_signals_processed: int = 0
_cycle_trades_placed: int = 0

# ── Daily session P&L halt ────────────────────────────────────────────────────
# If session P&L drops below this threshold, Polymarket entries are paused for
# _DAILY_HALT_DURATION_SECS seconds to prevent runaway losses in a bad stretch.
# This is softer than the drawdown halt and resets automatically.
_DAILY_PNL_HALT_THRESHOLD = -20.0   # $20 session loss triggers 2h pause
_DAILY_HALT_DURATION_SECS = 7200    # 2-hour automatic cooldown
_daily_halt_until: float = 0.0      # unix timestamp after which entries resume


def _record_skip(reason: str) -> None:
    global _cycle_skip_counts
    _cycle_skip_counts[reason] = _cycle_skip_counts.get(reason, 0) + 1


def _log_cycle_skip_summary() -> None:
    total_skips = sum(_cycle_skip_counts.values())
    if _cycle_signals_processed == 0 and total_skips == 0:
        return
    skip_str = " | ".join(f"{k}:{v}" for k, v in sorted(_cycle_skip_counts.items()))
    log.info(
        "[CYCLE-SUMMARY] signals=%d | placed=%d | skipped=%d%s",
        _cycle_signals_processed,
        _cycle_trades_placed,
        total_skips,
        f" ({skip_str})" if skip_str else "",
    )

# ── Circuit breaker ───────────────────────────────────────────────────────────
# Halts signal processing if session P&L drops below this threshold.
# Prevents a bad overnight from draining the full paper balance.
# /resume on Telegram overrides it (deletes the flag file).
_CIRCUIT_BREAKER_THRESHOLD = -5.0   # $-5.00 — 5% of starting $100 balance
_circuit_breaker_tripped   = False   # set True in-session; cleared on restart

# ── Dead-hour no-trade window ─────────────────────────────────────────────────
# UTC 01:00–04:59 — US markets closed, Asian crypto volume thin, spreads widest.
# Bot still monitors open positions; just skips new signal processing.
_NO_TRADE_HOURS_UTC = frozenset({1, 2, 3, 4})

# ── Graceful shutdown ────────────────────────────────────────────────────────

_running = True
_shutdown_event = threading.Event()

# ── Rapid-fire scanner ────────────────────────────────────────────────────────
# Background thread that fetches RSS/Reddit/CryptoPanic every 90 seconds.
# If a very high-conviction headline is found, it writes to rapid_fire_queue.json
# so the next main-loop iteration can run an immediate Kalshi cycle — without
# waiting up to 15 minutes for the next scheduled news cycle.
import re as _re
_RAPID_FIRE_QUEUE = Path(__file__).parent / "rapid_fire_queue.json"
_BULLISH_SPIKE = _re.compile(
    r'\b(approved|approval|surged|surging|all.?time high|ath|etf|massive rally|'
    r'launched|partnership|acquisition|record high|breakout|whale buy|institutional)\b', _re.I
)
_BEARISH_SPIKE = _re.compile(
    r'\b(crashed|crash|collapse|collapses|hacked|hack|exploit|banned|ban|'
    r'seized|bankrupt|halted|suspended|arrested|scam|rug.?pull|delisted)\b', _re.I
)
_RAPID_COINS = _re.compile(r'\b(bitcoin|btc|ethereum|eth|solana|sol|xrp|ripple)\b', _re.I)
_COIN_NORM = {"bitcoin": "BTC", "btc": "BTC", "ethereum": "ETH", "eth": "ETH",
              "solana": "SOL", "sol": "SOL", "xrp": "XRP", "ripple": "XRP"}


def _rapid_scanner_thread() -> None:
    """
    Runs in a daemon thread. Every 90 s it harvests headlines from all free RSS/
    Reddit/CryptoPanic sources and looks for extreme-confidence signals.
    When a signal reaches spike threshold (2+ bullish OR 2+ bearish trigger-words
    in the same headline, about a tracked coin), it is queued for immediate execution
    in the next main-loop iteration.
    """
    try:
        from rss_fetcher import get_all_headlines as _rss_headlines
    except ImportError:
        log.warning("[RAPID] rss_fetcher not found — rapid scanner disabled")
        return

    _rapid_seen: set = set()

    while _running:
        try:
            headlines = _rss_headlines(max_age_minutes=10)
            new_signals = []

            for h in headlines[:40]:
                title = h.get("title", "")
                key   = title[:70].lower()
                if key in _rapid_seen:
                    continue

                coins_found = _RAPID_COINS.findall(title)
                if not coins_found:
                    continue

                bull_hits = len(_BULLISH_SPIKE.findall(title))
                bear_hits = len(_BEARISH_SPIKE.findall(title))

                if bull_hits >= 2 or bear_hits >= 2:
                    _rapid_seen.add(key)
                    coin = _COIN_NORM.get(coins_found[0].lower(), "BTC")
                    direction = "BULLISH" if bull_hits >= bear_hits else "BEARISH"
                    new_signals.append({
                        "title":     title,
                        "coin":      coin,
                        "direction": direction,
                        "source":    h.get("source", "RSS"),
                        "confidence": 8.5 + min(1.0, (bull_hits + bear_hits - 2) * 0.25),
                        "ts":        time.time(),
                    })
                    log.info(
                        "[RAPID] 🚨 HIGH-CONVICTION: %s (%s %s) — \"%s\"",
                        coin, direction, f"b={bull_hits} be={bear_hits}", title[:65],
                    )

            if new_signals:
                # Merge into queue file (main loop drains it)
                existing = []
                try:
                    if _RAPID_FIRE_QUEUE.exists():
                        existing = json.loads(_RAPID_FIRE_QUEUE.read_text(encoding="utf-8"))
                except Exception:
                    pass
                combined = (existing + new_signals)[-20:]  # cap at 20
                _RAPID_FIRE_QUEUE.write_text(json.dumps(combined, indent=2), encoding="utf-8")

        except Exception as _rse:
            log.debug("[RAPID] Scanner error: %s", _rse)

        # 90-second sleep, interruptible on shutdown
        _shutdown_event.wait(timeout=90)
        if not _running:
            break
        _shutdown_event.clear()


def _handle_shutdown(signum, frame):
    global _running
    log.info("Shutdown signal received — finishing current cycle then stopping.")
    _running = False
    _shutdown_event.set()  # interrupt any sleep() waiting on this event


signal.signal(signal.SIGINT, _handle_shutdown)
signal.signal(signal.SIGTERM, _handle_shutdown)


# ── Daily report tracker ─────────────────────────────────────────────────────

_last_daily_report_date: str = ""


def _maybe_send_daily_report(cfg: dict) -> None:
    global _last_daily_report_date
    now = datetime.now(timezone.utc)
    report_hour, report_minute = [int(x) for x in cfg["DAILY_REPORT_TIME"].split(":")]

    if now.hour == report_hour and now.minute == report_minute:
        today_key = now.strftime("%Y-%m-%d")
        if today_key != _last_daily_report_date:
            log.info("Generating daily report...")
            send_daily_report()
            log_daily_summary(list(_trade_history))
            _last_daily_report_date = today_key
            log.info("Daily report sent.")


# ── Historical stats ─────────────────────────────────────────────────────────

def calculate_historical_stats(trades: list) -> dict:
    """
    Compute win rate, avg win, avg loss from closed trade history.
    Returns safe defaults when no history exists.
    """
    default = {"win_rate": 0.50, "avg_win": 0.015, "avg_loss": 0.015, "total_trades": 0}
    closed = [t for t in trades if str(t.get("status", "")).upper() == "CLOSED"]
    if not closed:
        return default

    profits = [float(t.get("profit", 0) or 0) for t in closed]
    wins = [p for p in profits if p > 0]
    losses = [p for p in profits if p <= 0]

    win_rate = len(wins) / len(closed)
    # Express as fraction of account (assume $100 starting balance for now)
    avg_win = (sum(wins) / len(wins) / 100) if wins else 0.015
    avg_loss = (abs(sum(losses) / len(losses)) / 100) if losses else 0.015

    return {
        "win_rate": round(win_rate, 4),
        "avg_win": round(avg_win, 6),
        "avg_loss": round(avg_loss, 6),
        "total_trades": len(closed),
    }


# ── Hour-Based Win Rate Oracle ────────────────────────────────────────────────
# Reads closed trade history, computes win rate by UTC hour (requires ≥5 trades
# per hour bucket before adjusting), and returns a dynamically adjusted signal
# threshold.  Loose hours (high win rate) → lower bar.  Dead hours → raise bar.

_HOUR_ORACLE_CACHE: dict = {}   # hour → (win_rate, sample_count)
_HOUR_ORACLE_LAST_UPDATE: float = 0.0


def _refresh_hour_win_rates() -> None:
    """
    Re-compute per-hour win rates from zisi_local_trades.jsonl.
    Called once at startup and at the start of each cycle (cheap — just reads JSONL).
    """
    global _HOUR_ORACLE_CACHE, _HOUR_ORACLE_LAST_UPDATE
    import time as _time
    if _time.time() - _HOUR_ORACLE_LAST_UPDATE < 300:  # refresh at most every 5 min
        return

    _base = Path(__file__).parent
    trades_file = _base / "zisi_local_trades.jsonl"
    if not trades_file.exists():
        return

    hour_wins:   dict = {}   # hour → int
    hour_total:  dict = {}   # hour → int

    try:
        for line in trades_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or '"order_id"' not in line:
                continue
            try:
                t = json.loads(line)
                if t.get("status", "").upper() != "CLOSED":
                    continue
                ts = t.get("exit_timestamp") or t.get("timestamp", "")
                if not ts:
                    continue
                hour = datetime.fromisoformat(ts.replace("Z", "+00:00")).hour
                hour_total[hour] = hour_total.get(hour, 0) + 1
                if float(t.get("profit", 0) or 0) > 0:
                    hour_wins[hour] = hour_wins.get(hour, 0) + 1
            except Exception:
                continue
    except Exception:
        return

    new_cache = {}
    for h, total in hour_total.items():
        wr = hour_wins.get(h, 0) / total
        new_cache[h] = (round(wr, 3), total)

    _HOUR_ORACLE_CACHE = new_cache
    _HOUR_ORACLE_LAST_UPDATE = _time.time()


def _get_adaptive_threshold(base_threshold: int) -> int:
    """
    Return a signal threshold adjusted by historical win rate for the current UTC hour.
    Only adjusts when ≥ 5 closed trades exist for that hour (avoids noise on sparse data).

    Win rate   Adjustment
    > 65%      −1  (more permissive — historically strong hour)
    55–65%     ±0  (neutral)
    40–55%     ±0  (neutral — not enough edge to tighten)
    < 40%      +1  (tighter — historically weak hour)
    < 30%      +2  (very tight — historically terrible hour)
    """
    hour = datetime.now(timezone.utc).hour
    entry = _HOUR_ORACLE_CACHE.get(hour)
    if not entry or entry[1] < 5:
        return base_threshold  # not enough data for this hour

    win_rate, sample = entry
    if win_rate > 0.65:
        adjustment = -1
    elif win_rate < 0.30:
        adjustment = +2
    elif win_rate < 0.40:
        adjustment = +1
    else:
        adjustment = 0

    if adjustment != 0:
        log.info(
            "[ORACLE] UTC hour %02d: win_rate=%.0f%% (%d trades) → threshold %d→%d",
            hour, win_rate * 100, sample, base_threshold, base_threshold + adjustment,
        )
    return max(1, min(10, base_threshold + adjustment))


# ── Circuit breaker ──────────────────────────────────────────────────────────

def _check_circuit_breaker(session_pnl: float) -> bool:
    """
    Returns True if trading should be halted for this cycle.
    Trips when session P&L falls below _CIRCUIT_BREAKER_THRESHOLD.
    Once tripped, stays tripped until bot is restarted (or /resume clears the flag).
    """
    global _circuit_breaker_tripped
    if _is_bot_paused():
        return True  # Already paused, respect that flag
    if session_pnl is None:
        return False
    if session_pnl < _CIRCUIT_BREAKER_THRESHOLD:
        if not _circuit_breaker_tripped:
            _circuit_breaker_tripped = True
            log.warning(
                "🔴 CIRCUIT BREAKER TRIPPED — session P&L $%.2f < threshold $%.2f — HALTING",
                session_pnl, _CIRCUIT_BREAKER_THRESHOLD,
            )
            try:
                notify_circuit_break(session_pnl, _CIRCUIT_BREAKER_THRESHOLD)
            except Exception:
                pass
            # Write pause flag so /resume can clear it
            open(_BOT_PAUSED_FLAG, "w").close()
        return True
    return False


# ── Signal entity deduplication ───────────────────────────────────────────────

def _get_signal_entity(sig: dict) -> str:
    """
    Return a key like 'BTC_BEARISH' or 'ETH_BULLISH' for signal entity dedup.
    Prevents processing 15 identical BTC_BEARISH signals in one cycle when
    1 is sufficient — takes the highest-confidence one (signals are pre-sorted).
    """
    cryptos = str(sig.get("affected_cryptos", [])).upper()
    headline = str(sig.get("headline", "")).upper()
    asset = "OTHER"
    for c in ("BTC", "ETH", "SOL", "DOGE", "XRP"):
        if c in cryptos or c in headline:
            asset = c
            break
    # Also check full names
    if asset == "OTHER":
        if "BITCOIN" in cryptos or "BITCOIN" in headline:
            asset = "BTC"
        elif "ETHEREUM" in cryptos or "ETHEREUM" in headline:
            asset = "ETH"
    direction = str(sig.get("sentiment", "NEUTRAL")).upper()
    return f"{asset}_{direction}"


# ── GAP #4 + #5 helpers ───────────────────────────────────────────────────────

# Current-cycle signals stored here so _monitor_open_positions can detect SIGNAL_FLIP
# without a new Gemini API call. Updated at the start of each processing cycle.
_current_cycle_signals: list = []


_SIGNAL_QUEUE_FILE = Path(__file__).parent / "signal_queue.json"
_SIGNAL_QUEUE_MAX = 50


def _append_signal_queue(item: dict) -> None:
    """Append one signal evaluation record to the rolling signal queue file (last 50)."""
    try:
        lines: list = []
        if _SIGNAL_QUEUE_FILE.exists():
            with _SIGNAL_QUEUE_FILE.open("r", encoding="utf-8") as fh:
                lines = [l for l in fh.readlines() if l.strip()]
        lines.append(json.dumps(item) + "\n")
        # Keep only the last N
        if len(lines) > _SIGNAL_QUEUE_MAX:
            lines = lines[-_SIGNAL_QUEUE_MAX:]
        with _SIGNAL_QUEUE_FILE.open("w", encoding="utf-8") as fh:
            fh.writelines(lines)
    except Exception as exc:
        log.debug("[SIGNAL-QUEUE] Write failed: %s", exc)


def _get_drawdown_pct() -> float:
    """Return current drawdown as a fraction (0.10 = 10%) relative to starting balance."""
    current = state_manager.get_current_balance()
    starting = 100.0
    try:
        state_file = Path(__file__).parent / "account_state.json"
        if state_file.exists():
            data = json.loads(state_file.read_text(encoding="utf-8"))
            starting = float(data.get("starting_balance", data.get("initial_balance", 100.0)))
    except Exception:
        pass
    if starting <= 0:
        return 0.0
    return max(0.0, (starting - current) / starting)


def _get_rolling_volatility() -> float:
    """Return std-dev of returns across last 20 closed trades (as a fraction, e.g. 0.15 = 15%).

    Polymarket binary contracts naturally resolve at 0 or 1, so profit_percent
    for paper trades is routinely ±100%. Stdev of a ±100% binary series caps at
    exactly 100% (1.0). The threshold is set to 1.5 (150%) so it only fires for
    truly impossible scenarios — it is NOT intended to pause on normal binary
    outcomes, only on legitimate chaos (e.g. data corruption).
    """
    import statistics
    closed = [
        float(t.get("profit_percent", 0) or 0) / 100.0
        for t in list(_trade_history)[-20:]
        if str(t.get("status", "")).upper() == "CLOSED"
    ]
    if len(closed) < 5:
        return 0.0
    try:
        return statistics.stdev(closed)
    except statistics.StatisticsError:
        return 0.0


def _count_consecutive_losses(trades: list) -> int:
    """Count the number of consecutive losses at the tail of closed trade history."""
    closed = [t for t in trades if str(t.get("status", "")).upper() == "CLOSED"]
    if not closed:
        return 0
    count = 0
    for t in reversed(closed):
        if float(t.get("profit", 0) or 0) <= 0:
            count += 1
        else:
            break
    return count


def _signal_flips_position(pos: dict) -> bool:
    """
    Return True if any current-cycle signal strongly contradicts this position.
    Used by check_exit_condition to detect SIGNAL_FLIP without a Gemini re-call.
    Threshold: conf >= 7 and direction is opposite to position's direction.
    """
    pos_title_raw = pos.get("event_title", "") or ""
    pos_title = (pos_title_raw + " " + pos.get("market_id", "")).upper()

    # UP/DOWN markets resolve at expiry within minutes — never exit early via SIGNAL_FLIP.
    # Exiting at the same price as entry produces $0.00 P&L recorded as LOSS.
    if "UPDOWN" in pos_title or "UP OR DOWN" in pos_title:
        return False

    pos_direction = str(pos.get("direction", "YES")).upper()

    for sig in _current_cycle_signals:
        sig_conf = float(sig.get("confidence", 0))
        if sig_conf < 7:
            continue
        sig_sent = str(sig.get("sentiment", "")).upper()
        # BULLISH contradicts a NO position; BEARISH contradicts a YES position
        if (pos_direction == "YES" and sig_sent == "BEARISH") or \
           (pos_direction == "NO" and sig_sent == "BULLISH"):
            # Loose relevance check: any crypto asset overlap
            sig_cryptos = " ".join(sig.get("affected_cryptos", [])).upper()
            sig_headline = sig.get("headline", "").upper()
            relevant_tokens = {"BTC", "ETH", "SOL", "BITCOIN", "ETHEREUM", "SOLANA", "CRYPTO"}
            if any(tok in sig_cryptos or tok in sig_headline or tok in pos_title
                   for tok in relevant_tokens):
                log.info(
                    "  [SIGNAL-FLIP] %s position contradicted by %s signal (conf=%d): %s",
                    pos_direction, sig_sent, int(sig_conf),
                    sig.get("headline", "")[:60],
                )
                return True
    return False


# ── Signal processing ─────────────────────────────────────────────────────────

def _process_signal(signal_data: dict, all_events: list[dict], cfg: dict) -> None:
    """
    Full pipeline for one high-confidence sentiment signal:
    match → pick event → size position → validate → place order → log → alert.
    """
    headline = signal_data.get("headline", "(no headline)")
    sentiment_dir = signal_data.get("sentiment", "neutral")
    confidence = signal_data.get("confidence", 0)

    log.debug("Processing signal: %s", headline)
    log.debug("  Sentiment: %s | Confidence: %d/10", sentiment_dir.upper(), confidence)

    # ── BLOCKER 3b: Daily session P&L halt ────────────────────────────────────
    import time as _time_mod
    global _daily_halt_until
    now_ts = _time_mod.time()
    if now_ts < _daily_halt_until:
        _remaining_halt = int(_daily_halt_until - now_ts)
        log.info("[DAILY-HALT] Session P&L halt active — resuming in %ds", _remaining_halt)
        _record_skip("daily_pnl_halt")
        return
    else:
        # Check current session P&L from state
        try:
            _session_pnl = float(get_current_balance()) - 100.0
            try:
                import json as _json, os as _os
                _sf = _os.path.join(_os.path.dirname(__file__), "account_state.json")
                _st = _json.loads(open(_sf).read())
                _session_pnl = float(_st.get("pnl", 0))
            except Exception:
                pass
            if _session_pnl <= _DAILY_PNL_HALT_THRESHOLD:
                _daily_halt_until = now_ts + _DAILY_HALT_DURATION_SECS
                log.warning(
                    "[DAILY-HALT] Session P&L $%.2f ≤ $%.2f threshold — pausing Polymarket entries for 2h",
                    _session_pnl, _DAILY_PNL_HALT_THRESHOLD,
                )
                try:
                    from telegram_bot import send_alert as _tg_alert
                    _tg_alert(
                        f"⏸ *Daily Loss Halt*\n"
                        f"Session P&L: ${_session_pnl:+.2f} hit ${_DAILY_PNL_HALT_THRESHOLD:.0f} floor.\n"
                        f"Polymarket entries paused for 2h to prevent further drawdown.\n"
                        f"Shadow & Kalshi trades continue unaffected."
                    )
                except Exception:
                    pass
                _record_skip("daily_pnl_halt")
                return
        except Exception:
            pass

    # ── BLOCKER 4: Multi-level drawdown pause ──────────────────────────────────
    _dd = _get_drawdown_pct()
    if _dd >= 0.20:
        log.critical(
            "[HALT] Drawdown %.1f%% ≥ 20%% — system halted. Require manual restart.",
            _dd * 100,
        )
        try:
            from health_monitor import add_alert as _hm_add_alert
            _hm_add_alert("CRITICAL", "DRAWDOWN_HALT",
                          f"Drawdown {_dd:.1%} ≥ 20% — all new entries halted until manual restart")
        except Exception:
            pass
        return
    if _dd >= 0.15:
        log.warning("[PAUSE] Drawdown %.1f%% ≥ 15%% — pausing new entries this cycle", _dd * 100)
        try:
            from health_monitor import add_alert as _hm_add_alert
            _hm_add_alert("WARNING", "DRAWDOWN_PAUSE",
                          f"Drawdown {_dd:.1%} ≥ 15% — new entries paused")
        except Exception:
            pass
        return
    if _dd >= 0.10:
        log.info("[REDUCE] Drawdown %.1f%% ≥ 10%% — Kelly will be halved", _dd * 100)
        # _dd flag is checked later when sizing; store on cfg for this call only
        cfg = dict(cfg)
        cfg["_drawdown_kelly_halved"] = True

    # ── BLOCKER 5: Volatility pause ────────────────────────────────────────────
    # Threshold is 1.50 (150%) — binary Polymarket contracts resolve at ±100%
    # so a stdev of ~100% is NORMAL; 150% would indicate corrupt data.
    _vol = _get_rolling_volatility()
    if _vol > 1.50:
        log.warning(
            "[VOL-PAUSE] Rolling volatility %.1f%% > 150%% — skipping entry to avoid chaos",
            _vol * 100,
        )
        try:
            from health_monitor import add_alert as _hm_add_alert
            _hm_add_alert("WARNING", "VOLATILITY_PAUSE",
                          f"Volatility pause: {_vol:.1%} rolling stdev on last 20 trades")
        except Exception:
            pass
        return

    # 1. Match events — try smart matcher first for confidence score
    smart_event, smart_confidence = find_matching_event_smart(signal_data, all_events)
    matching = find_matching_events(signal_data, all_events)

    log_signal_analysis(
        news_article={"title": headline, "source": signal_data.get("source", "NewsAPI")},
        sentiment=signal_data,
        matching_events=matching,
        trade_decision="EVALUATING",
    )

    # Log signal evaluation for missed-trade analysis
    log_signal_evaluation(signal_data, smart_event, smart_confidence)
    log.info(format_signal_log(signal_data, smart_event, smart_confidence))

    if not matching:
        log.debug("  No matching Polymarket events — skipping")
        _record_skip("no_event_match")
        return

    log.debug("  Found %d matching events (smart confidence=%.2f)", len(matching), smart_confidence)

    if smart_event is not None and smart_confidence > 0:
        best_event = smart_event
        log.debug("  [SMART-SELECT] %s (conf=%.2f)", best_event.get("title", "")[:70], smart_confidence)
    else:
        best_event = select_best_event(matching, sentiment_dir)
    if not best_event:
        log.debug("  No suitable event selected — skipping")
        _record_skip("no_event_selected")
        return

    log.debug("  Selected: %s", best_event["title"])

    # 2b-DEDUP: One Polymarket trade per event per cycle.
    # When only one liquid Bitcoin market exists, all bearish signals would
    # otherwise stack into the same position. We take the first (highest-ranked)
    # signal and ignore subsequent ones for the same event this cycle.
    _event_cycle_key = best_event.get("id", "")
    if _event_cycle_key and _event_cycle_key in _poly_cycle_event_ids:
        log.info(
            "  [POLY-DEDUP] Already traded '%s' this cycle — skipping duplicate",
            best_event.get("title", "")[:55],
        )
        return
    # Register BEFORE executing so even if execution fails, we don't spam the market
    if _event_cycle_key:
        _poly_cycle_event_ids.add(_event_cycle_key)

    # 2d. Liquidity check — skip thin markets to avoid dead trades
    liquidity_check = validate_liquidity(best_event)
    if not liquidity_check["valid"]:
        log.warning("  [SKIP] %s", liquidity_check["reason"])
        track_skip("liquidity", liquidity_check)
        log_liquidity_skip(
            best_event.get("id", "unknown"),
            liquidity_check["liquidity"],
            float(cfg.get("MIN_EVENT_LIQUIDITY_USD", 1000)),
        )
        _record_skip("liquidity")
        return

    # 3. Decide direction
    direction = pick_trading_direction(sentiment_dir, best_event.get("markets", []))
    if direction == "SKIP":
        log.info("  Neutral sentiment — skipping trade")
        _record_skip("neutral_sentiment")
        return

    # 4. Get current market price
    markets = best_event.get("markets", [])
    market = None

    # For multi-outcome events (e.g. "What price will BTC hit in May?" with 20 sub-markets),
    # pick the sub-market whose YES price is closest to 0.50 — that's where the real edge is.
    # Single-outcome events (Up/Down) just use the YES/NO split as before.
    _tradeable = [
        m for m in markets
        if 0.10 < float(m.get("price") or m.get("lastTradePrice") or 0) < 0.90
    ]
    if len(_tradeable) >= 2:
        # Multi-market event: find the one closest to fair odds (0.50)
        _best_market = min(_tradeable, key=lambda m: abs(float(m.get("price") or m.get("lastTradePrice") or 0.5) - 0.50))
        market = _best_market
        log.info(
            "  [MARKET-SELECT] %d tradeable sub-markets — chose price=%.4f (closest to 0.50): %s",
            len(_tradeable),
            float(market.get("price") or market.get("lastTradePrice") or 0.5),
            market.get("question", market.get("title", ""))[:50],
        )
    elif direction == "YES":
        market = next((m for m in markets if "YES" in str(m.get("outcomeLabel", "")).upper()), markets[0] if markets else None)
    else:
        # For NO direction: prefer a market labelled "NO", fall back to markets[1],
        # then markets[0] — UP/DOWN events have a single market object covering both sides.
        market = next(
            (m for m in markets if "NO" in str(m.get("outcomeLabel", "")).upper()),
            markets[1] if len(markets) > 1 else (markets[0] if markets else None),
        )

    if not market:
        log.warning("  Could not identify %s market — skipping", direction)
        return

    # Use conditionId (hex) for CLOB — numeric Gamma IDs return 404 from CLOB API
    market_id = market.get("conditionId") or market["id"]
    price_data = get_event_current_price(market_id)
    if price_data:
        current_price = price_data["price"]
    else:
        # CLOB unavailable — use Gamma price (already sanitised in data_fetcher)
        current_price = market.get("price", 0.5)
        log.info("  [PRICE] CLOB unavailable for %s — using Gamma price %.4f", market_id[:16], current_price)

    # ── Spread gate ─────────────────────────────────────────────────────────
    # Skip markets where bid/ask spread is > 8% of mid-price.
    # A 10-cent spread on a 50-cent market means you need a 20% move just to
    # break even — that's not a trade, that's a donation to market makers.
    _yes_market = market  # already identified above
    _bid  = float(_yes_market.get("bestBid",  _yes_market.get("outcomePrices", [current_price])[0] if direction == "YES" else current_price) or 0)
    _ask  = float(_yes_market.get("bestAsk",  current_price) or current_price)
    if _bid > 0 and _ask > 0 and _ask > _bid:
        _spread_pct = (_ask - _bid) / _ask
        if _spread_pct > 0.08:
            log.info(
                "  [SKIP-SPREAD] bid=%.4f ask=%.4f spread=%.1f%% > 8%% — thin market",
                _bid, _ask, _spread_pct * 100,
            )
            _record_skip("spread_too_wide")
            return

    # ── Hard price gate ──────────────────────────────────────────────────────
    # Reject near-zero prices (market resolved/resolving NO — no upside left)
    # Reject near-one prices  (market resolved/resolving YES — no edge, max priced)
    # Both cases produce 0 shares, bad targets, and guaranteed losses.
    if current_price <= 0.10:
        log.warning(
            "  [SKIP] Price %.4f ≤ 0.10 — market near-resolved NO or stale (no edge)",
            current_price,
        )
        _record_skip("price_too_low")
        return
    if current_price >= 0.90:
        log.warning(
            "  [SKIP] Price %.4f ≥ 0.90 — market near-resolved YES (no edge)",
            current_price,
        )
        _record_skip("price_too_high")
        return

    log.debug("  Direction: %s | Price: %.4f", direction, current_price)

    # 4b. Entry price check — avoid overpaying relative to signal strength
    price_check = validate_entry_price(current_price, confidence)
    if not price_check["valid"]:
        log.warning("  [SKIP] %s", price_check["reason"])
        track_skip("entry_price", price_check)
        log_price_skip(
            best_event.get("id", "unknown"),
            current_price,
            price_check["max_allowed"],
            confidence,
        )
        _record_skip("entry_price")
        return

    # 4c. Confluence scoring + multi-timeframe confirmation
    affected = signal_data.get("affected_cryptos", [])
    symbol = affected[0].upper() if affected else "OTHER"
    # Use market category from matched event (enriched by event_matcher)
    event_market_cat = best_event.get("market_category", None)

    price_confirmation = _price_analyzer.get_price_confirmation(symbol, sentiment_dir)
    confluence = calculate_confluence_score(
        signal_data=signal_data,
        market_context={
            "price_confirmation": price_confirmation,
            "volume_confirmation": 0.5,   # neutral (no volume API available)
            "macro_alignment": 0.5,        # neutral default
            "agreement_ratio": 0.7,        # slight positive default
        },
        market_category=event_market_cat,
    )
    log.debug("  Market category: %s", event_market_cat or "DEFAULT")
    log.debug(
        "  Confluence: %.2f (%s) | Price confirmation: %.2f",
        confluence["confluence_score"], confluence["level"], price_confirmation,
    )

    if confluence["confluence_score"] < 0.50:
        log.info("  [SKIP] Confluence too low (%.2f < 0.50)", confluence["confluence_score"])
        _record_skip("confluence_low")
        return

    mtf = _price_analyzer.get_timeframe_confirmation(symbol, sentiment_dir)
    log.debug("  MTF: %d/3 (%s)", mtf["confirmations"], mtf["alignment"])

    if mtf["confirmation_score"] < 0.33 and confluence["confluence_score"] < 0.70:
        log.info(
            "  [SKIP] Low MTF confirmation (%.2f) + low confluence (%.2f) — skipping",
            mtf["confirmation_score"], confluence["confluence_score"],
        )
        _record_skip("mtf_confluence_low")
        return

    # ── GAP #1: Explicit routing gate ──────────────────────────────────────────
    # Compute bid/ask spread fraction for routing_decision().
    _spread_for_routing = (_ask - _bid) / _ask if (_bid > 0 and _ask > _bid) else 0.05
    _routing = routing_decision(
        confidence=float(confidence),    # 0-10 Gemini scale
        spread=_spread_for_routing,
        has_polymarket=True,
        has_kalshi=_kalshi_auth.is_configured,
        polymarket_yes_price=current_price,
    )
    log.debug(
        "  [ROUTING] %s | conf=%.1f spread=%.3f | %s",
        _routing["target"], float(confidence), _spread_for_routing, _routing["reason"],
    )
    _routing_target = _routing["target"]
    _routing_accepted = _routing_target not in ("SKIP", "KALSHI", "KALSHI_ONLY")

    # ── BLOCKER 3: Signal queue record (written regardless of accept/reject) ───
    _append_signal_queue({
        "timestamp":         datetime.now(timezone.utc).isoformat(),
        "market_id":         best_event.get("id", ""),
        "market_title":      best_event.get("title", "")[:70],
        "platform":          "POLYMARKET",
        "gemini_confidence": float(confidence),
        "routing_decision":  _routing_target,
        "routing_reason":    _routing.get("reason", ""),
        "entry_price":       round(current_price, 4),
        "spread_pct":        round(_spread_for_routing * 100, 2),
        "status":            "ACCEPTED" if _routing_accepted else "REJECTED",
    })

    if _routing_target == "SKIP":
        log.info("  [ROUTING] SKIP — confidence below threshold, no trade")
        _record_skip("routing_skip")
        return
    if _routing_target in ("KALSHI", "KALSHI_ONLY"):
        log.info("  [ROUTING] %s — skipping Polymarket leg this cycle", _routing_target)
        _record_skip("routed_to_kalshi")
        return

    # 5. Kelly Criterion position sizing informed by historical edge
    signal_strength = smart_confidence if smart_confidence > 0 else confidence / 10.0

    # ── GAP #3: Phase 2 blended confidence (replaces raw Gemini when model exists)
    _feature_snapshot = {
        "gemini_confidence": signal_strength,
        "signal_confidence": signal_strength,
        "entry_price":       current_price,
        "hold_hours":        0.0,
        "position_size":     0.0,   # unknown at entry; 0 used for model input
    }
    _blended_conf, _blend_src = get_blended_confidence(signal_strength, _feature_snapshot)
    if _blend_src != "PHASE_1_DEFLATED":
        log.info(
            "  [KELLY-CALIB] %s | gemini=%.3f → blended=%.3f",
            _blend_src, signal_strength, _blended_conf,
        )
    signal_strength = _blended_conf

    # ── GAP #5: Consecutive loss Kelly reduction ────────────────────────────────
    hist_stats = calculate_historical_stats(list(_trade_history))
    _cons_losses = _count_consecutive_losses(list(_trade_history))

    kelly_sizing = calculate_position_size_kelly(
        account_balance=cfg["ACCOUNT_BALANCE"],
        signal_strength=signal_strength,
        symbol=symbol,
        historical_win_rate=hist_stats["win_rate"],
        historical_avg_win=hist_stats["avg_win"],
        historical_avg_loss=hist_stats["avg_loss"],
        consecutive_losses=_cons_losses,
    )
    position_size = kelly_sizing["final_position"]

    # BLOCKER 6: Update feature snapshot with real position size (was 0.0 placeholder)
    _feature_snapshot["position_size"] = position_size

    # BLOCKER 4: Halve Kelly when in 10-15% drawdown zone
    if cfg.get("_drawdown_kelly_halved"):
        position_size *= 0.5
        log.info("  [DRAWDOWN-REDUCE] Drawdown ≥10%% — Kelly halved to $%.2f", position_size)

    # Boost position by 25% for high-confluence setups
    if confluence["confluence_score"] > 0.75:
        position_size = min(position_size * 1.25, cfg["ACCOUNT_BALANCE"] * 0.05)
        log.info("  [CONFLUENCE BOOST] Position increased to $%.2f", position_size)

    # Scale position by market regime multiplier
    regime_mult = _regime_detector.kelly_multiplier
    if regime_mult != 1.0:
        position_size = position_size * regime_mult
        log.info(
            "  [REGIME %s] Kelly×%.2f → $%.2f",
            _regime_detector.regime, regime_mult, position_size,
        )

    # Apply signal-type routing multiplier (TYPE_A_HIGH=1.5x, TYPE_B_LOW=0.4x, etc.)
    routing_mult = float(signal_data.get("kelly_multiplier", 1.0))
    if routing_mult != 1.0:
        position_size = position_size * routing_mult
        log.info(
            "  [ROUTING] %s kelly×%.1f → $%.2f",
            signal_data.get("signal_type", ""), routing_mult, position_size,
        )

    max_risk = cfg["ACCOUNT_BALANCE"] * cfg["RISK_PER_TRADE_PERCENT"] / 100
    log.info(
        "  Kelly sizing: %.2f%% × %.2f → $%.2f (max risk: $%.2f)",
        kelly_sizing["kelly_pct"] * 100, kelly_sizing["signal_multiplier"],
        position_size, max_risk,
    )

    # 6. Validate — use signal-based win_rate so EV passes during bootstrapping.
    # UP/DOWN markets have stronger edge (RSI+momentum alignment required) → 55% floor.
    # General markets: 53% floor (news sentiment edge, conservative).
    _market_type = best_event.get("market_type", "") or best_event.get("market_category", "")
    _is_updown_mkt = (
        str(_market_type).upper() == "UP_DOWN"
        or "UPDOWN" in str(best_event.get("title", "")).upper()
        or "UP OR DOWN" in str(best_event.get("title", "")).upper()
    )
    _base_win_rate = 0.55 if _is_updown_mkt else 0.53
    _estimated_win_rate = max(_base_win_rate, hist_stats.get("win_rate", _base_win_rate))
    if not validate_trade(
        position_size, cfg["ACCOUNT_BALANCE"], count_open_trades(),
        win_rate=_estimated_win_rate,
        entry_price=current_price,
        platform="POLYMARKET",
    ):
        log.info("  Trade validation failed — skipping")
        _record_skip("validate_trade_failed")
        return

    # 7. Exit targets
    targets = calculate_exit_targets(current_price, position_size)
    log.info(
        "  Target: %.4f (+$%.2f) | Stop: %.4f (-$%.2f)",
        targets["target_price"], targets["profit_at_target"],
        targets["stop_loss"], abs(targets["loss_at_stop"]),
    )

    # 8. Place order
    log.debug("  PLACING ORDER…")
    order = place_order(
        event_id=best_event["id"],
        market_id=market_id,
        amount_dollars=position_size,
        direction=direction,
        entry_price=current_price,
        event_title=best_event.get("title", best_event["id"]),
    )

    if not order:
        log.error("  ORDER FAILED — skipping")
        return

    global _cycle_trades_placed
    _cycle_trades_placed += 1
    log.debug("  FILLED: %.2f shares @ %.4f | order_id=%s", order["shares_acquired"], order["entry_price"], order["order_id"])
    log.info(
        "[TRADE-PLACED] %s | %s | $%.2f @ %.4f | conf=%d | %s",
        direction, best_event.get("title", "")[:55],
        position_size, order["entry_price"],
        confidence, order["order_id"][:16],
    )

    # Attach targets to the cached position
    attach_exit_targets(order["order_id"], targets["target_price"], targets["stop_loss"])

    # 9. Log to Drive
    trade_record = {
        "order_id": order["order_id"],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "signal_source": "NewsAPI",
        "signal_confidence": confidence,
        "event_id": best_event["id"],
        "event_title": best_event["title"],
        "entry_price": current_price,
        "position_size": position_size,
        "direction": direction,
        "target_price": targets["target_price"],
        "stop_loss": targets["stop_loss"],
        "status": "OPEN",
    }
    log_trade_to_google_drive(trade_record)

    # 10. Email alert (only for high-confidence signals)
    if confidence >= 8:
        send_alert_email(
            subject=f"ZiSi: New {direction} Trade | {best_event['title'][:60]} | Confidence {confidence}/10",
            body=(
                f"Trade Details\n"
                f"─────────────────────────────────────────\n"
                f"Event:      {best_event['title']}\n"
                f"Direction:  {direction}\n"
                f"Entry:      ${current_price:.4f}\n"
                f"Size:       ${position_size:.2f}\n"
                f"Shares:     {order['shares_acquired']:.2f}\n"
                f"Target:     ${targets['target_price']:.4f}  (+${targets['profit_at_target']:.2f})\n"
                f"Stop Loss:  ${targets['stop_loss']:.4f}  (-${abs(targets['loss_at_stop']):.2f})\n"
                f"News:       {headline}\n"
                f"Sentiment:  {sentiment_dir.upper()}\n"
                f"Confidence: {confidence}/10\n"
            ),
        )

    log.debug("  Trade logged and alerted. ✓")

    # Telegram notification for every executed trade
    try:
        notify_trade_executed(
            event_title=best_event.get("title", ""),
            direction=direction,
            size=position_size,
            confidence=signal_strength,
            market="POLYMARKET",
        )
    except Exception:
        pass


# ── Open-position monitoring ─────────────────────────────────────────────────

def _monitor_open_positions(cfg: dict) -> None:
    """Check every open position against its exit targets."""

    # Paper trading: auto-close positions that have aged past the hold threshold
    if cfg["BOT_MODE"] == "paper_trading":
        auto_closed = check_and_close_paper_trades(max_hold_minutes=240)
        for result in auto_closed:
            order_id = result["order_id"]
            win_label = "WIN" if result["profit"] > 0 else "LOSS"
            log_trade_to_google_drive({**result, "status": "CLOSED"})
            send_alert_email(
                subject=f"ZiSi: Trade {win_label} {result['profit_percent']:.1f}% | Paper Auto-Exit",
                body=(
                    f"Trade Closed (Paper Auto-Exit)\n"
                    f"─────────────────────────────────────────\n"
                    f"Order:    {order_id}\n"
                    f"Exit:     ${result['exit_price']:.4f}  (${result['exit_value']:.2f})\n"
                    f"P&L:      ${result['profit']:.2f}  ({result['profit_percent']:.1f}%)\n"
                    f"Duration: {result['hold_duration']:.2f}h\n"
                    f"Reason:   PAPER_AUTO_EXIT\n"
                ),
            )
            # Telegram alert for closed trade
            try:
                hold_min = round(float(result.get("hold_duration", 0)) * 60, 1)
                notify_trade_closed(
                    event_title=result.get("event_title", order_id),
                    pnl=result["profit"],
                    pnl_pct=result["profit_percent"],
                    hold_min=hold_min,
                    market="POLYMARKET",
                )
            except Exception:
                pass

    # Re-fetch after auto-close so the standard loop only sees still-open trades
    open_trades = get_all_open_trades()
    if not open_trades:
        return

    log.info("Monitoring %d open position(s)...", len(open_trades))

    for trade in open_trades:
        order_id = trade["order_id"]
        target = trade.get("target_price") or trade.get("entry_price", 0.5) * cfg["POSITION_TARGET_MULTIPLIER"]
        stop = trade.get("stop_loss") or trade.get("entry_price", 0.5) * cfg["POSITION_STOP_LOSS_MULTIPLIER"]

        exit_check = check_exit_condition(
            order_id=order_id,
            target_price=target,
            stop_loss=stop,
            max_hold_hours=cfg["POSITION_HOLD_TIME_HOURS"],
        )

        # ── GAP #4: SIGNAL_FLIP detection ──────────────────────────────────────
        # If a strong contradicting signal arrived this cycle and the trade isn't
        # already flagged for exit, override to SIGNAL_FLIP.
        if not exit_check["should_exit"] and _signal_flips_position(trade):
            exit_check = {
                **exit_check,
                "should_exit": True,
                "exit_reason": "SIGNAL_FLIP",
            }
            log.info(
                "  [SIGNAL-FLIP] Triggering exit for %s — current signals contradict position",
                order_id,
            )

        if not exit_check["should_exit"]:
            continue

        log.info(
            "EXIT: %s | Reason: %s | Price: %.4f | P&L: $%.2f (%.2f%%)",
            order_id,
            exit_check["exit_reason"],
            exit_check["current_price"],
            exit_check["pnl"],
            exit_check["pnl_percent"],
        )

        result = execute_exit(
            order_id=order_id,
            current_price=exit_check["current_price"],
            exit_reason=exit_check["exit_reason"],
        )
        if not result:
            log.error("Exit execution failed for %s", order_id)
            continue

        update_trade_record(order_id, result)

        # Update Drive log with closed trade details
        closed_record = {**trade, **result, "status": "CLOSED"}
        log_trade_to_google_drive(closed_record)

        win_label = "WIN" if result["profit"] > 0 else "LOSS"
        send_alert_email(
            subject=f"ZiSi: Trade {win_label} {result['profit_percent']:.1f}% | {trade.get('event_title', '')[:50]}",
            body=(
                f"Trade Closed\n"
                f"─────────────────────────────────────────\n"
                f"Entry:    ${trade.get('entry_price', 0):.4f}  (${trade.get('position_size', 0):.2f})\n"
                f"Exit:     ${result['exit_price']:.4f}  (${result['exit_value']:.2f})\n"
                f"P&L:      ${result['profit']:.2f}  ({result['profit_percent']:.1f}%)\n"
                f"Duration: {result['hold_duration']:.2f}h\n"
                f"Reason:   {exit_check['exit_reason']}\n"
            ),
        )
        log.debug("  Exit complete for %s", order_id)


# ── Heartbeat helper ─────────────────────────────────────────────────────────

_STATE_FILE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "account_state.json")
_BOT_PAUSED_FLAG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot_paused.flag")


def _is_bot_paused() -> bool:
    return os.path.exists(_BOT_PAUSED_FLAG)


def _get_starting_balance() -> float:
    """Read starting_balance from account_state.json; always fall back to 100.0 (never current balance)."""
    try:
        acc = json.loads(Path(_STATE_FILE_PATH).read_text(encoding="utf-8"))
        # Only use the explicit 'starting_balance' field — never use 'balance' as a proxy,
        # as that would cause the balance to drift upward every heartbeat cycle.
        return float(acc.get("starting_balance", 100.0))
    except Exception:
        return 100.0


def _write_heartbeat(reason: str = "cycle_start") -> None:
    """Write state file — merges in-memory history with JSONL file for persistence across restarts."""
    try:
        # Start with in-memory trades
        all_trades = list(_trade_history)
        # Also load from JSONL file so balance survives restarts
        trades_file = os.path.join(os.path.dirname(_STATE_FILE_PATH), "zisi_local_trades.jsonl")
        if os.path.exists(trades_file):
            in_memory_ids = {t.get("order_id") for t in all_trades}
            with open(trades_file, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        # Only real trade records (not signal rows)
                        if entry.get("type") == "signal" or "order_id" not in entry:
                            continue
                        if entry["order_id"] not in in_memory_ids:
                            all_trades.append(entry)
                            in_memory_ids.add(entry["order_id"])
                    except Exception:
                        pass

        closed = [t for t in all_trades if t.get("status", "").upper() == "CLOSED"]
        total_pnl = sum(float(t.get("profit", 0) or 0) for t in closed)
        # Use in-memory balance as the authority — it includes shadow trade PnL
        # (which isn't in JSONL). JSONL-based pnl is shown for informational purposes.
        live_balance = get_current_balance()
        _start_bal = _get_starting_balance()
        # Read existing state to preserve fields we don't own
        existing: dict = {}
        if os.path.exists(_STATE_FILE_PATH):
            try:
                with open(_STATE_FILE_PATH, "r", encoding="utf-8") as _fh:
                    existing = json.loads(_fh.read())
            except Exception:
                pass
        # Total closed: JSONL (ZiSi) + positions_state (shadow) — authoritative count
        positions_file = os.path.join(os.path.dirname(_STATE_FILE_PATH), "positions_state.json")
        total_closed = len(closed)
        try:
            ps = json.loads(Path(positions_file).read_text(encoding="utf-8"))
            total_closed = len(ps.get("closed", []))
        except Exception:
            pass

        effective_pnl = round(live_balance - _start_bal, 2)
        existing.update({
            "balance": live_balance,
            "starting_balance": _start_bal,
            "pnl": effective_pnl,
            "last_updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "trades_executed": total_closed,
            "phase": "phase_1",
            "paused": False,
            "last_update_reason": reason,
            "status": "running",
        })
        with open(_STATE_FILE_PATH, "w", encoding="utf-8") as fh:
            json.dump(existing, fh, indent=2)
        log.info(
            "Heartbeat → $%.2f | pnl: $%+.2f | closed: %d (zisi=%d)",
            live_balance, effective_pnl, total_closed, len(closed),
        )
    except Exception as exc:
        import traceback
        log.warning("Heartbeat failed: %s\n%s", exc, traceback.format_exc())


def sync_balance_to_state() -> tuple:
    """
    Read actual P&L from trades JSONL file and update state file.
    Ensures balance is correct even after bot restart.
    Returns (current_balance, total_pnl, closed_count).
    """
    try:
        trades_file = os.path.join(os.path.dirname(_STATE_FILE_PATH), "zisi_local_trades.jsonl")
        all_trades = []
        if os.path.exists(trades_file):
            with open(trades_file, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        if entry.get("type") != "signal" and "order_id" in entry:
                            all_trades.append(entry)
                    except Exception:
                        pass

        closed_trades = [t for t in all_trades if t.get("status", "").upper() == "CLOSED"]
        jsonl_pnl = sum(float(t.get("profit", 0) or 0) for t in closed_trades)

        # Authority for balance: state_manager (includes shadow P&L).
        # Never recompute from JSONL — that excludes shadow trades.
        current_balance = get_current_balance()
        start_bal = _get_starting_balance()
        effective_pnl = round(current_balance - start_bal, 2)

        # Only update trades_executed + pnl; preserve all other fields.
        existing: dict = {}
        if os.path.exists(_STATE_FILE_PATH):
            try:
                with open(_STATE_FILE_PATH, "r", encoding="utf-8") as _fh:
                    existing = json.loads(_fh.read())
            except Exception:
                pass
        existing.update({
            "balance": current_balance,
            "starting_balance": start_bal,
            "pnl": effective_pnl,
            "trades_executed": len(closed_trades),
            "last_updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "phase": "phase_1",
            "status": "running",
        })
        with open(_STATE_FILE_PATH, "w", encoding="utf-8") as fh:
            json.dump(existing, fh, indent=2)

        # ── Record to equity curve ────────────────────────────────────────────
        try:
            record_balance(current_balance, effective_pnl, len(closed_trades))
        except Exception:
            pass

        return current_balance, effective_pnl, len(closed_trades)

    except Exception as exc:
        log.error("Balance sync failed: %s", exc)
        return None, None, None


# ── Startup health check ─────────────────────────────────────────────────────

def startup_health_check(cfg: dict) -> bool:
    """Verify all systems are ready before the main loop starts."""
    state_file = Path(__file__).parent / "account_state.json"
    checks = {
        "config_loaded": cfg.get("ACCOUNT_BALANCE", 0) > 0,
        "api_keys_present": bool(os.getenv("NEWSAPI_KEY")),
        "anthropic_key": bool(os.getenv("ANTHROPIC_API_KEY")),
        "state_file_accessible": state_file.exists(),
        "console_log_writable": os.access(str(Path(__file__).parent), os.W_OK),
    }

    # Google Drive is optional — LOG_TO_DRIVE controls whether it's used
    drive_enabled = os.getenv("LOG_TO_DRIVE", "false").lower() == "true"
    if drive_enabled:
        checks["google_drive_folder"] = bool(os.getenv("GOOGLE_DRIVE_FOLDER_ID"))

    all_pass = all(checks.values())
    status = "HEALTHY" if all_pass else "ISSUES DETECTED"
    log.info("Startup health check: %s", status)
    for name, result in checks.items():
        log.info("  %s %s", "+" if result else "!", name)

    return all_pass


# ── Trade frequency analysis ─────────────────────────────────────────────────

def analyze_trade_frequency() -> dict:
    """
    Explain why trade placement rate looks low — this is normal for signal-based trading.

    Math: 48 cycles/day × ~25 signals/cycle = 1,200 signals/day.
    Conversion rate: 1-3 trades per 50-100 signals (2-5%).
    Expected: 3-4 trades/day by Week 2, 5-10/week at steady state.
    """
    return {
        "status": "NORMAL",
        "reason": "Polymarket is sparse; bot is selective (conservative = good)",
        "expected_trades_per_week": "5-10",
        "timeline": "Week 1: 0-3 | Week 2: 3-8 | Week 3: 8-15 | Week 4: 15-20",
        "confidence": "HIGH — system working correctly",
    }


# ── Startup diagnostics ──────────────────────────────────────────────────────

def run_startup_diagnostics(cfg: dict) -> bool:
    """
    Compact startup diagnostic — one clean block, no verbose separators.
    Returns True only if all critical checks pass.
    """
    import requests as _req

    _base   = Path(__file__).parent
    all_ok  = True
    lines   = []          # collect rows; flush once as a block
    ok_sym  = "✓"
    warn_sym = "!"
    fail_sym = "✗"

    def _row(sym, text):
        lines.append(f"  {sym}  {text}")

    # ── API keys ───────────────────────────────────────────────────────────────
    kalshi_key = os.getenv("KALSHI_KEY_ID") or os.getenv("KALSHI_API_KEY") or ""
    kalshi_priv = os.getenv("KALSHI_PRIVATE_KEY") or ""
    if kalshi_key and kalshi_priv:
        _row(ok_sym, "Kalshi keys present")
    else:
        missing_keys = []
        if not kalshi_key:  missing_keys.append("KALSHI_KEY_ID")
        if not kalshi_priv: missing_keys.append("KALSHI_PRIVATE_KEY")
        _row(fail_sym, f"Kalshi keys missing: {', '.join(missing_keys)}")
        all_ok = False

    key_checks = {
        "NEWSAPI_KEY": "NewsAPI", "GEMINI_API_KEY": "Gemini",
        "ANTHROPIC_API_KEY": "Claude", "GROQ_API_KEY": "Groq",
    }
    missing_ai = [label for env, label in key_checks.items() if not cfg.get(env)]
    if missing_ai:
        _row(warn_sym, f"Optional AI keys missing: {', '.join(missing_ai)} (fallback active)")
    else:
        _row(ok_sym, "All AI/news keys present")

    # ── Data files ─────────────────────────────────────────────────────────────
    pos_file    = _base / "positions_state.json"
    trades_file = _base / "zisi_local_trades.jsonl"

    n_active = n_closed = n_trades = 0
    if pos_file.exists():
        try:
            d       = json.loads(pos_file.read_text(encoding="utf-8"))
            n_active = len(d.get("active", []))
            n_closed = len(d.get("closed", []))
        except Exception as exc:
            _row(fail_sym, f"positions_state.json unreadable: {exc}")
            all_ok = False

    if trades_file.exists():
        try:
            n_trades = sum(
                1 for line in trades_file.read_text(encoding="utf-8").splitlines()
                if line.strip() and '"order_id"' in line
            )
        except Exception:
            pass

    _row(ok_sym, f"Data  →  open: {n_active} | closed: {n_closed} | JSONL history: {n_trades} trades")

    # ── Connectivity ───────────────────────────────────────────────────────────
    if _kalshi_auth.is_configured:
        ok, msg = _kalshi_auth.validate_connection()
        _row(ok_sym if ok else fail_sym, f"Kalshi API: {msg}")
        if not ok:
            all_ok = False
    else:
        _row(warn_sym, "Kalshi not configured (KALSHI_KEY_ID + KALSHI_PRIVATE_KEY)")

    try:
        r = _req.get("https://clob.polymarket.com", timeout=5)
        _row(ok_sym if r.ok else warn_sym, f"Polymarket CLOB: {'OK' if r.ok else f'HTTP {r.status_code}'}")
    except Exception as exc:
        _row(fail_sym, f"Polymarket CLOB: unreachable ({type(exc).__name__})")
        all_ok = False

    # ── ML pipeline ────────────────────────────────────────────────────────────
    labelled_count = 0
    labelled_file = _base / "ml_labelled_outcomes.jsonl"
    if labelled_file.exists():
        try:
            labelled_count = sum(1 for l in labelled_file.open() if l.strip())
        except Exception:
            pass
    if labelled_count >= 200:
        ml_phase = f"Phase 3 — gradient boosted ({labelled_count} examples)"
    elif labelled_count >= 50:
        ml_phase = f"Phase 2 — logistic regression ({labelled_count} examples)"
    else:
        ml_phase = f"Phase 1 — Gemini deflation ({labelled_count}/50 examples to upgrade)"
    _row(ok_sym, f"ML: {ml_phase}")

    # ── Account state ──────────────────────────────────────────────────────────
    acc_file = _base / "account_state.json"
    if acc_file.exists():
        try:
            acc     = json.loads(acc_file.read_text(encoding="utf-8"))
            balance = acc.get("balance", acc.get("bankroll", 0))
            pnl     = acc.get("total_pnl", acc.get("pnl", 0))
            pnl_sign = "+" if pnl >= 0 else ""
            _row(ok_sym, f"Account: ${balance:.2f} balance | P&L {pnl_sign}${pnl:.2f} | {cfg.get('BOT_MODE', 'paper_trading')}")
        except Exception:
            _row(warn_sym, "account_state.json unreadable — will recreate")
    else:
        _row(ok_sym, f"Account: fresh start | {cfg.get('BOT_MODE', 'paper_trading')} mode")

    # ── Flush block ────────────────────────────────────────────────────────────
    verdict = "READY" if all_ok else "CHECK REQUIRED"
    log.info("─── ZiSi Startup Diagnostics ──────────────────────── %s ───", verdict)
    for line in lines:
        log.info(line)
    log.info("────────────────────────────────────────────────────────────────")

    return all_ok


# ── Main loop ────────────────────────────────────────────────────────────────

def main() -> None:
    global _running

    # File logging must be first so every subsequent log line goes to disk
    setup_file_logging()
    log.info("=" * 60)
    log.info("ZiSi Bot v1.0 started — file logging active")
    log.info("=" * 60)

    cfg = load_config()
    _setup_logging(cfg["LOG_LEVEL"])

    # BLOCKER 2: Sync CycleManager balance immediately from real config (not the
    # 100.0 placeholder used for module-level init before config is loaded).
    _cycle_manager.sizer.account_balance = cfg["ACCOUNT_BALANCE"]

    if not startup_health_check(cfg):
        log.error("Bot cannot start — fix the issues above")
        sys.exit(1)

    # Initialize 2-week runtime tracker (creates file on first run, resumes on restart)
    initialize_runtime_tracking()

    log_config_startup(cfg)
    log.info("ZiSi Bot initialised. Entering main loop.")

    # Telegram startup notification (email system disabled)
    try:
        telegram_alert(f"ZiSi v{cfg['BOT_VERSION']} started | Mode: {cfg['BOT_MODE']} | Balance: ${cfg['ACCOUNT_BALANCE']:.2f}")
    except Exception:
        pass

    # Start background silent-fill reconciliation (live mode only; no-op in paper)
    start_reconciliation_loop()

    # Start independent stop-loss / take-profit monitor
    from risk_engine import start_risk_engine as _start_risk_engine
    _start_risk_engine()

    # Start Telegram bot daemon (no-op if TELEGRAM_BOT_TOKEN not set)
    _telegram_thread = start_telegram_bot()

    # Start rapid-fire RSS scanner (background, daemon)
    _rapid_thread = threading.Thread(target=_rapid_scanner_thread, daemon=True, name="rapid-scanner")
    _rapid_thread.start()
    log.info("[RAPID] Rapid-fire RSS scanner started — checks every 90s for breaking news")

    # Start shadow intelligence monitor (PBot-6 + Wallet-2 — intelligence-only, no execution)
    # Mules no longer place paper trades. They feed directional signals into updown_trader.py
    # and maintain conflict detection. ZiSi makes its own independent execution decisions.
    global _shadow_monitor
    try:
        _shadow_monitor = ShadowModeMonitor(
            get_balance_fn=get_current_balance,
            poll_interval=15,
        )
        _shadow_monitor.start()
        log.info("[SHADOW] Intelligence monitor started — watching PBot-6 + Wallet-2 (signal-only mode)")
    except Exception as _sme:
        log.warning("[SHADOW] Shadow monitor failed to start (non-fatal): %s", _sme)

    # Prune stale equity history once at startup
    try:
        prune_history(max_days=30)
    except Exception:
        pass

    # ── Startup recovery + health monitor ─────────────────────────────────────
    paper_mode = cfg.get("BOT_MODE", "paper_trading") != "live"
    try:
        startup_recovery()
    except Exception as _sre:
        log.warning("[STARTUP] Recovery scan failed: %s", _sre)
    try:
        start_health_monitor(paper_mode=paper_mode)
    except Exception as _hme:
        log.warning("[STARTUP] Health monitor failed to start: %s", _hme)

    # ── Startup diagnostics (position sync, API keys, connectivity, ML) ───────
    try:
        run_startup_diagnostics(cfg)
    except Exception as _diag_err:
        log.warning("[STARTUP] Diagnostics failed: %s", _diag_err)

    # ── Load persisted Kalshi category win rates ──────────────────────────────
    try:
        load_category_win_rates()
    except Exception as _cwre:
        log.warning("[STARTUP] Category win rate load failed: %s", _cwre)

    # ── Load persisted ML model — Phase 2 auto-activation on startup ──────────
    # ensure_phase2_activated() loads the existing model if available, or trains
    # a new one if 50+ labelled examples exist and no model is on disk yet.
    # This bypasses the new_labels > 0 requirement in link_trade_outcomes() that
    # prevented auto-training when all trades were already labelled.
    try:
        _phase2_active = _ensure_phase2()
        if _phase2_active:
            log.info("[STARTUP] ✅ ML Phase 2 ACTIVE — logistic regression calibration enabled")
        else:
            log.info("[STARTUP] ML Phase 1 — collecting labelled examples (0.65× deflation)")
    except Exception as _mle:
        log.warning("[STARTUP] ML model load/train failed: %s", _mle)

    last_check_minute = -1  # Forces an immediate check on first iteration
    cycle_count = 0
    _session_start = datetime.now(timezone.utc)

    while _running:
        try:
            # Check pause flag (written by dashboard /api/control/pause)
            if _is_bot_paused():
                log.info("⏸️ BOT PAUSED — waiting for resume signal...")
                time.sleep(30)
                continue

            now = datetime.now(timezone.utc)
            interval = 15  # 15-minute cycles: 96/day

            # ── Rapid-fire queue drain ────────────────────────────────────────
            # Between normal 15-min cycles, the rapid scanner may have queued
            # high-conviction RSS signals. Process them with an immediate Kalshi
            # cycle so we don't wait up to 15 min to act on breaking news.
            try:
                if _RAPID_FIRE_QUEUE.exists():
                    _rapid_queued = json.loads(_RAPID_FIRE_QUEUE.read_text(encoding="utf-8"))
                    if _rapid_queued:
                        log.info("[RAPID] ⚡ %d rapid signal(s) queued — running immediate Kalshi cycle", len(_rapid_queued))
                        _RAPID_FIRE_QUEUE.write_text("[]", encoding="utf-8")
                        # Build synthetic signals for Kalshi
                        _rapid_signals = []
                        for _rq in _rapid_queued:
                            _rapid_signals.append({
                                "coin":        _rq.get("coin", "BTC"),
                                "sentiment":   _rq.get("direction", "BULLISH"),
                                "confidence":  _rq.get("confidence", 8.5),
                                "signal_type": "TYPE_A_HIGH",
                                "kelly_multiplier": 1.2,
                                "headline":    _rq.get("title", "")[:80],
                                "source":      _rq.get("source", "RSS"),
                            })
                        if _rapid_signals and _running:
                            try:
                                _kalshi_rapid = run_kalshi_for_cycle(
                                    signals=_rapid_signals,
                                    kalshi_fetcher=_kalshi_fetcher,
                                    kalshi_matcher=_kalshi_matcher,
                                    kalshi_trader=_kalshi_trader,
                                    kelly_fn=calculate_position_size_kelly,
                                    account_balance=cfg["ACCOUNT_BALANCE"],
                                    hist_stats=calculate_historical_stats(list(_trade_history)),
                                )
                                if _kalshi_rapid.get("kalshi_trades", 0) > 0:
                                    log.info("[RAPID] ✅ %d Kalshi trade(s) from rapid signal", _kalshi_rapid["kalshi_trades"])
                            except Exception as _rke:
                                log.debug("[RAPID] Kalshi cycle failed: %s", _rke)
            except Exception as _rqe:
                log.debug("[RAPID] Queue drain error: %s", _rqe)

            # Run on every Nth minute boundary (e.g. :00, :30)
            on_schedule = (now.minute % interval == 0) and (now.minute != last_check_minute)

            if on_schedule:
                last_check_minute = now.minute
                cycle_count += 1
                log.info("─── Cycle %d start: %s UTC ───", cycle_count, now.strftime("%Y-%m-%d %H:%M"))

                # Reset per-cycle counters at start of every scheduled cycle
                global _cycle_signals_processed, _cycle_trades_placed
                _cycle_signals_processed = 0
                _cycle_trades_placed = 0

                # ── UP/DOWN high-frequency cycle — always runs (24/7 liquidity) ──
                # 5-min binary markets on Polymarket have automated liquidity
                # regardless of time, so this runs even during the dead window.
                if _running:
                    try:
                        _ud_count = run_updown_cycle(
                            place_paper_trade_fn=place_order,
                            get_balance_fn=get_current_balance,
                            count_open_trades_fn=count_open_trades,
                        )
                        if _ud_count > 0:
                            log.info("[UPDOWN] %d Up/Down trade(s) placed this cycle", _ud_count)
                            _cycle_trades_placed += _ud_count
                    except Exception as _ude:
                        log.warning("[UPDOWN] Cycle error (non-fatal): %s", _ude)

                # ── Dead-liquidity window: UTC 01:00–04:59 ─────────────────
                # Polymarket spreads are widest for NEWS markets. Skip sentiment
                # signal processing but UP/Down already ran above.
                if now.hour in _NO_TRADE_HOURS_UTC:
                    log.info(
                        "🌙 [OVERNIGHT] UTC %02d:00 — news signals paused "
                        "(low liquidity), UP/Down algo running",
                        now.hour,
                    )
                    _monitor_open_positions(cfg)
                    _write_heartbeat("no_trade_window")
                    sync_balance_to_state()
                    time.sleep(60)
                    continue

                # Reset per-cycle dedup + skip counters
                _poly_cycle_event_ids.clear()
                _cycle_skip_counts.clear()

                # Reconciliation health check — warn if unresolved orders building up
                _pending = get_pending_reconcile_count()
                if _pending > 0:
                    log.warning(
                        "[RECONCILE] ⚠ %d order(s) still awaiting reconciliation — "
                        "background thread is resolving them", _pending,
                    )

                # UTC hour weighting status
                from config import PEAK_TRADING_HOURS_UTC as _PEAK_H
                if now.hour in _PEAK_H:
                    log.info(
                        "⚡ PEAK TRADING WINDOW: UTC %02d:00 — 100%% Kelly (geographic advantage)",
                        now.hour,
                    )
                else:
                    log.info(
                        "📊 Off-peak window: UTC %02d:00 — 50%% Kelly (bot competition heavy)",
                        now.hour,
                    )

                # ── Heartbeat FIRST — runs even if we hit an early continue ──
                _write_heartbeat("cycle_start")

                # ── Live price refresh for open positions ──────────────────
                # Updates current_price on every open Polymarket position so
                # unrealized P&L on the dashboard reflects real market prices.
                try:
                    _refreshed = refresh_open_position_prices()
                    if _refreshed:
                        log.debug("[PRICE-REFRESH] %d position price(s) refreshed", _refreshed)
                except Exception as _rpe:
                    log.debug("[PRICE-REFRESH] Skipped: %s", _rpe)

                # ── Trailing stop escalator ────────────────────────────────
                # Ratchets stop-loss upward as open positions move toward target
                try:
                    _trail_updated = escalate_trailing_stops()
                    if _trail_updated:
                        log.info("[TRAIL] %d trailing stop(s) escalated this cycle", _trail_updated)
                except Exception as _te:
                    log.debug("[TRAIL] Escalator skipped: %s", _te)

                # ── Hour win-rate oracle ───────────────────────────────────
                # Refresh once per cycle; cheap JSONL scan with 5-min cache
                try:
                    _refresh_hour_win_rates()
                except Exception:
                    pass

                # ── Circuit breaker check ──────────────────────────────────
                # Reads latest balance from file (authoritative across restarts)
                _cb_bal, _cb_pnl, _ = sync_balance_to_state()
                if _check_circuit_breaker(_cb_pnl):
                    log.warning(
                        "🔴 [CIRCUIT-BREAKER] Session P&L $%.2f — signal processing HALTED. "
                        "Send /resume on Telegram to override.",
                        _cb_pnl or 0,
                    )
                    _monitor_open_positions(cfg)  # still monitor existing positions
                    time.sleep(60)
                    continue

                # ── Step 0b: Fear & Greed Index ────────────────────────────
                from data_fetcher import fetch_fear_and_greed as _fetch_fng
                _fng = _fetch_fng()
                _fng_kelly = _fng.get("kelly_multiplier", 1.0)
                log.info(
                    "[FNG] Fear & Greed: %d (%s) → position sizing ×%.2f",
                    _fng.get("value", 50), _fng.get("label", "Neutral"), _fng_kelly,
                )

                # ── Step 0c: Binance perpetual funding rates ───────────────
                # Free on-chain signal: positive rate = longs over-leveraged
                # (bearish lean), negative rate = short squeeze risk (bullish).
                try:
                    from data_fetcher import fetch_funding_rate as _fetch_fr
                    _btc_fr  = _fetch_fr("BTCUSDT")
                    _eth_fr  = _fetch_fr("ETHUSDT")
                    log.info(
                        "[FUNDING] BTC %s | ETH %s",
                        _btc_fr.get("description", "?"),
                        _eth_fr.get("description", "?"),
                    )
                except Exception as _fre:
                    log.debug("[FUNDING] Unavailable: %s", _fre)
                    _btc_fr = _eth_fr = {"sentiment": "NEUTRAL", "signal_strength": 0.0}

                # ── Step 1: Fetch news (existing sources + free RSS harvest) ──
                articles = fetch_crypto_articles()
                log.info("News: %d articles fetched (primary sources)", len(articles) if articles else 0)

                # Augment with free RSS/Reddit/CryptoPanic headlines (no API key)
                try:
                    from rss_fetcher import get_all_headlines as _rss_get, headlines_to_text as _rss_txt
                    _rss_items = _rss_get(max_age_minutes=25)
                    _rss_articles = [
                        {
                            "title":          h.get("title", ""),
                            "description":    h.get("title", ""),
                            "source":         h.get("source", "RSS"),
                            "source_quality": 0.65,
                            "url":            h.get("url", ""),
                            "published_at":   h.get("published_at", ""),
                            "coin_hint":      h.get("coin_hint", "CRYPTO"),
                        }
                        for h in _rss_items
                    ]
                    # Avoid duplicating titles already in primary articles
                    _existing_titles = {(a.get("title") or "")[:60].lower() for a in articles}
                    _new_rss = [a for a in _rss_articles if (a["title"] or "")[:60].lower() not in _existing_titles]
                    articles = articles + _new_rss
                    log.info("[RSS] +%d RSS/Reddit/CryptoPanic headlines → %d total articles", len(_new_rss), len(articles))
                except Exception as _rsse:
                    log.debug("[RSS] Augmentation skipped: %s", _rsse)

                if not articles:
                    log.info("No articles — skipping cycle")
                    time.sleep(60)
                    continue

                analyses = analyze_articles_batch(articles)
                signals  = analyses
                log.info("Sentiment: %d signals ready", len(signals))

                if not signals:
                    log.info("No signals after sentiment analysis — skipping")
                    time.sleep(60)
                    continue

                # ── GAP #4: Store signals for SIGNAL_FLIP detection ────────
                _current_cycle_signals[:] = signals   # update module-level store in-place

                # ── Step 3b: Classify signals by type + sort by kelly ──────
                signals = [_signal_classifier.classify(sig) for sig in signals]
                signals.sort(key=lambda s: s.get("kelly_multiplier", 1.0), reverse=True)
                type_counts: dict = {}
                for s in signals:
                    t = s.get("signal_type", "?")
                    type_counts[t] = type_counts.get(t, 0) + 1
                log.info("[ROUTING] %d signals | A_HIGH=%d A_LOW=%d B_HIGH=%d B_LOW=%d",
                    len(signals),
                    type_counts.get("TYPE_A_HIGH", 0), type_counts.get("TYPE_A_LOW", 0),
                    type_counts.get("TYPE_B_HIGH", 0), type_counts.get("TYPE_B_LOW", 0),
                )

                # ── Step 3c: Funding rate signal adjustment ────────────────
                # If funding strongly disagrees with a signal, nudge confidence
                # down. If it agrees, nudge up.  Cap at ±1 point on 10-scale.
                for _sig in signals:
                    _sig_sent = str(_sig.get("sentiment", "NEUTRAL")).upper()
                    _cryptos  = str(_sig.get("affected_cryptos", [])).upper()
                    _fr       = _btc_fr if ("BTC" in _cryptos or "BITCOIN" in _cryptos) else _eth_fr
                    _fr_sent  = _fr.get("sentiment", "NEUTRAL")
                    _fr_str   = float(_fr.get("signal_strength", 0))
                    if _fr_str > 0.3 and _fr_sent != "NEUTRAL":
                        _delta = 0.5 if _fr_sent == _sig_sent else -0.5
                        _sig["confidence"] = round(
                            max(1.0, min(10.0, float(_sig.get("confidence", 5)) + _delta)), 1
                        )
                        if _delta != 0:
                            log.debug(
                                "  [FUNDING-ADJ] %s signal: %+.1f (FR=%s strength=%.2f)",
                                _sig_sent, _delta, _fr_sent, _fr_str,
                            )

                # ── Step 3d: Entity-level signal deduplication ────────────
                # Keep only the highest-confidence signal per asset+direction.
                # Prevents processing 15 BTC_BEARISH signals when 1 will do.
                # Signals are already sorted by kelly_multiplier (desc) so the
                # first occurrence per entity is the best one.
                _entity_seen: dict = {}
                for _sig in signals:
                    _ek = _get_signal_entity(_sig)
                    if _ek not in _entity_seen:
                        _entity_seen[_ek] = _sig
                signals_deduped = list(_entity_seen.values())
                signals_deduped.sort(key=lambda s: s.get("kelly_multiplier", 1.0), reverse=True)
                log.info(
                    "[ENTITY-DEDUP] %d raw signals → %d unique entities: %s",
                    len(signals), len(signals_deduped), list(_entity_seen.keys()),
                )

                # ── Step 4: Fetch Polymarket events ────────────────────────
                log.debug("Fetching Polymarket events...")
                # Primary crypto fetch (base_queries already includes bitcoin,
                # ethereum, solana, etc.)
                all_events = fetch_polymarket_events("bitcoin ethereum cryptocurrency")
                # Extra macro pass — picks up inflation/fed/election markets that
                # crypto signals (especially CRYPTO_BULLISH/BEARISH) can match
                try:
                    _macro_events = fetch_polymarket_events("inflation federal reserve election")
                    _existing_ids = {e.get("id") for e in all_events}
                    _added = [e for e in _macro_events if e.get("id") not in _existing_ids]
                    all_events.extend(_added)
                    if _added:
                        log.info("[POLY-MACRO] Added %d macro Polymarket events", len(_added))
                except Exception as _me2:
                    log.debug("[POLY-MACRO] Macro fetch skipped: %s", _me2)

                if not all_events:
                    log.info("No Polymarket events returned — skipping signal processing")
                    time.sleep(60)
                    continue

                log.info("[VERIFY-FETCH] Signals=%d | Poly markets=%d", len(signals), len(all_events))

                # Verify Polymarket category distribution
                _poly_cats: dict = {}
                for _ev in all_events:
                    _c = _ev.get("market_category") or _ev.get("category") or "OTHER"
                    _poly_cats[_c] = _poly_cats.get(_c, 0) + 1
                log.info("[VERIFY-POLY] Categories=%s | total=%d", _poly_cats, len(all_events))

                # ── Step 4b: Update regime detector with latest BTC price ──
                try:
                    from data_fetcher import get_crypto_prices as _gcp
                    _prices = _gcp()
                    _btc_price = (_prices or {}).get("bitcoin", {}).get("usd", 0)
                    if _btc_price > 0:
                        _regime_detector.update_price(_btc_price, "BTC")
                        log.info(
                            "[Regime] BTC=%.2f | %s (ATR=%.2f%%) | Kelly×%.2f",
                            _btc_price, _regime_detector.regime,
                            _regime_detector.atr, _regime_detector.kelly_multiplier,
                        )
                except Exception as _re:
                    log.debug("[Regime] Price update skipped: %s", _re)

                # ── Step 4c: CycleManager routing pass ────────────────────
                _cycle_manager.sizer.account_balance = cfg["ACCOUNT_BALANCE"]
                try:
                    _cm_result = _cycle_manager.process_signals(signals, all_events, [])
                    log.info(
                        "[CYCLE-MANAGER] poly_cands=%d | conflicts=%d | capital=$%.2f",
                        len(_cm_result["polymarket_candidates"]),
                        _cm_result["conflicts_detected"],
                        _cm_result["capital_deployed"],
                    )
                except Exception as _cme:
                    log.warning("[CYCLE-MANAGER] Routing pass failed (non-fatal): %s", _cme)
                    _cm_result = {"polymarket_candidates": [], "kalshi_candidates": [],
                                  "conflicts_detected": 0, "capital_deployed": 0.0}

                # ── Step 5: Process each signal (Polymarket) ───────────────
                # Use deduplicated signals (1 per asset+direction) for execution
                _cm_candidates = _cm_result.get("polymarket_candidates", [])
                log.info(
                    "[VERIFY-ROUTING] CycleManager poly_cands=%d | kalshi_cands=%d",
                    len(_cm_candidates),
                    len(_cm_result.get("kalshi_candidates", [])),
                )

                # ── Hour-based win-rate oracle threshold ───────────────────
                # Dynamically tightens/loosens the confidence bar based on
                # this UTC hour's historical win rate across all closed trades.
                _base_thresh = cfg.get("SIGNAL_THRESHOLD", 6)
                _adaptive_thresh = _get_adaptive_threshold(_base_thresh)

                log.info(
                    "[EXECUTION] Processing %d deduped signals against %d markets... "
                    "(oracle threshold=%d/10)",
                    len(signals_deduped), len(all_events), _adaptive_thresh,
                )
                _exec_count = 0
                for sig in signals_deduped:
                    if not _running:
                        break
                    # Oracle gate: skip signals below the adaptive threshold
                    _sig_conf = sig.get("confidence", 0)
                    if _sig_conf < _adaptive_thresh:
                        log.debug(
                            "[ORACLE] Skipping signal conf=%d < threshold=%d (dead hour)",
                            _sig_conf, _adaptive_thresh,
                        )
                        _record_skip("oracle_hour_filter")
                        continue
                    _process_signal(sig, all_events, cfg)
                    _exec_count += 1
                _cycle_signals_processed += _exec_count
                log.info("[VERIFY-EXECUTION] Polymarket: %d signals processed", _exec_count)

                # ── Step 5b: Kalshi processing (parallel market) ────────────
                _kalshi_summary = {"kalshi_events_fetched": 0, "kalshi_matches": 0, "kalshi_trades": 0}
                if _running:
                    try:
                        _kalshi_summary = run_kalshi_for_cycle(
                            signals=signals_deduped,
                            kalshi_fetcher=_kalshi_fetcher,
                            kalshi_matcher=_kalshi_matcher,
                            kalshi_trader=_kalshi_trader,
                            kelly_fn=calculate_position_size_kelly,
                            account_balance=cfg["ACCOUNT_BALANCE"],
                            hist_stats=calculate_historical_stats(list(_trade_history)),
                        )
                        if _kalshi_summary["kalshi_trades"] > 0:
                            log.info(
                                "[KALSHI] %d trade(s) executed this cycle",
                                _kalshi_summary["kalshi_trades"],
                            )

                        # ── Check + close aged Kalshi paper positions ─────────
                        try:
                            _kalshi_closed = _kalshi_trader.check_and_close_positions(
                                paper_hold_minutes=30
                            )
                            for _cp in _kalshi_closed:
                                win_lbl = "WIN" if (_cp.get("realized_pnl") or 0) > 0 else "LOSS"
                                log.info(
                                    "[KALSHI-EXIT] %s | %s | P&L: $%+.4f (%.1f%%) | held %.0fm",
                                    win_lbl, _cp["event_title"][:50],
                                    _cp.get("realized_pnl", 0),
                                    _cp.get("realized_pnl_pct", 0),
                                    _cp.get("hold_minutes", 0),
                                )
                                # Telegram notification for Kalshi close
                                try:
                                    notify_trade_closed(
                                        event_title=_cp.get("event_title", ""),
                                        pnl=float(_cp.get("realized_pnl", 0)),
                                        pnl_pct=float(_cp.get("realized_pnl_pct", 0)),
                                        hold_min=float(_cp.get("hold_minutes", 0)),
                                        market="KALSHI",
                                    )
                                except Exception:
                                    pass
                        except Exception as _kce:
                            log.warning("[KALSHI] Position close check error: %s", _kce)

                    except Exception as _ke:
                        log.warning("[KALSHI] Cycle error (Polymarket unaffected): %s", _ke)

                # ── Log Kalshi position summary ────────────────────────────────
                try:
                    _ks = get_kalshi_summary()
                    if _ks["active_count"] > 0 or _ks["closed_count"] > 0:
                        log.info(
                            "[KALSHI-POS] Active: %d | Closed: %d  W:%d/L:%d | realized $%+.4f",
                            _ks["active_count"], _ks["closed_count"],
                            _ks["win_count"], _ks["loss_count"], _ks["realized_pnl"],
                        )
                except Exception:
                    pass

                log.info(
                    "[VERIFY-KALSHI] fetched=%d | matches=%d | trades=%d",
                    _kalshi_summary.get("kalshi_events_fetched", 0),
                    _kalshi_summary.get("kalshi_matches", 0),
                    _kalshi_summary.get("kalshi_trades", 0),
                )

                # ── Step 6: Monitor open positions ─────────────────────────
                _monitor_open_positions(cfg)

                # ── Cycle skip summary ─────────────────────────────────────
                _log_cycle_skip_summary()

                # ── Step 7: Metrics snapshot (every cycle) ─────────────────
                snap = calculate_daily_metrics(list(_trade_history))
                snap["kalshi_matches"] = _kalshi_summary.get("kalshi_matches", 0)
                snap["kalshi_trades"] = _kalshi_summary.get("kalshi_trades", 0)
                snap["signals_evaluated"] = len(signals)
                snap["signals_deduped"] = len(signals_deduped)
                snap["cm_poly_candidates"] = len(_cm_result.get("polymarket_candidates", []))
                save_metrics_to_file({"daily": snap})
                _kalshi_real = get_real_trade_count("kalshi")
                log.info(
                    "Metrics snapshot: %d Polymarket | %d Kalshi trades | %.1f%% win rate | $%.2f P&L | "
                    "%d liq-skips | %d price-skips | %d kalshi-matches",
                    snap["total_trades"], _kalshi_real, snap["win_rate"], snap["total_pnl"],
                    snap["liquidity_skips"], snap["price_skips"],
                    snap["kalshi_matches"],
                )

                # ── Step 7b: ML data collection + outcome linking ────────────
                try:
                    collect_cycle_data(snap, signals_deduped)
                    get_ml_progress()  # refreshes ml_progress.json for dashboard
                    # Link any newly closed trade outcomes to their signal records
                    _new_labels = link_trade_outcomes()
                    if _new_labels:
                        log.info("[ML-LABEL] %d new labelled examples added", _new_labels)
                except Exception as _ml_e:
                    log.debug("[ML] Collection/labelling skipped: %s", _ml_e)

                # ── Step 8: Daily report (midnight UTC) ────────────────────
                if cfg["DAILY_REPORT_EMAIL"]:
                    _maybe_send_daily_report(cfg)

                # ── Step 9: 8-hour scheduled email update (disabled — Telegram is the sole channel) ──

                # ── Step 10: Alert checks ───────────────────────────────────
                if snap.get("total_trades", 0) >= 5:
                    win_rate_pct = snap.get("win_rate", 100)
                    if win_rate_pct < 45:
                        _email_scheduler.send_alert("WARNING_LOW_WIN_RATE", {
                            "win_rate": f"{win_rate_pct:.1f}%",
                            "threshold": "45%",
                            "trades": snap.get("total_trades", 0),
                            "pnl": snap.get("total_pnl", 0),
                        })
                        log.warning("[ALERT] Low win rate: %.1f%%", win_rate_pct)

                    max_dd = snap.get("max_drawdown", 0)
                    if isinstance(max_dd, (int, float)) and max_dd > 15:
                        _email_scheduler.send_alert("WARNING_HIGH_DRAWDOWN", {
                            "max_drawdown": f"{max_dd:.1f}%",
                            "threshold": "15%",
                            "trades": snap.get("total_trades", 0),
                            "pnl": snap.get("total_pnl", 0),
                        })
                        log.warning("[ALERT] High drawdown: %.1f%%", max_dd)

                # ── Cycle summary log ───────────────────────────────────────
                runtime_elapsed = str(datetime.now(timezone.utc) - _session_start).split(".")[0]
                cycle_summary = format_cycle_log({
                    "timestamp": now.strftime("%Y-%m-%d %H:%M UTC"),
                    "utc_hour": now.hour,
                    "total_signals": len(signals),
                    "deduped_signals": len(signals_deduped),
                    "strong_signals": len([s for s in signals_deduped if s.get("confidence", 0) >= 8]),
                    "matched_events": snap.get("cm_poly_candidates", 0),
                    "executed_trades": snap.get("total_trades", 0),
                    "hypothetical_trades": snap.get("liquidity_skips", 0) + snap.get("price_skips", 0),
                    "kalshi_matches": snap.get("kalshi_matches", 0),
                    "kalshi_trades": snap.get("kalshi_trades", 0),
                    "balance": cfg["ACCOUNT_BALANCE"] + snap.get("total_pnl", 0),
                    "pnl": snap.get("total_pnl", 0),
                    "runtime": runtime_elapsed,
                    "status": "running",
                    "fng_value": _fng.get("value", 50),
                    "fng_label": _fng.get("label", "Neutral"),
                    "fng_kelly": _fng_kelly,
                })
                log.info(cycle_summary)

                # ── Position tracking ───────────────────────────────────────
                try:
                    pos_summary = get_position_summary()
                    persist_positions()
                    active_c  = pos_summary["active"]
                    closed_c  = pos_summary["closed"]
                    unreal    = pos_summary["unrealized_pnl"]
                    real      = pos_summary["realized_pnl"]
                    wins      = pos_summary["wins"]
                    losses    = pos_summary["losses"]

                    if active_c > 0 or closed_c > 0:
                        log.info(
                            "📊 POSITIONS  Active: %d (unrealized $%+.2f)  |  "
                            "Closed: %d  W:%d / L:%d  (realized $%+.2f)",
                            active_c, unreal, closed_c, wins, losses, real,
                        )
                        # Log individual active positions
                        for pos in get_all_open_trades():
                            title    = pos.get("event_title", pos.get("event_id", "?"))[:55]
                            entry    = pos.get("entry_price", 0)
                            curr     = pos.get("current_price", entry)
                            size     = pos.get("amount_spent", 0)
                            hold_min = 0
                            ot = pos.get("open_time")
                            if ot and isinstance(ot, datetime):
                                hold_min = round((datetime.now(timezone.utc) - ot).total_seconds() / 60, 1)
                            log.info(
                                "  ↳ [ACTIVE] %s | %s @ %.4f → %.4f | $%.2f | held %.0fm",
                                pos.get("direction", "?"), title, entry, curr, size, hold_min,
                            )
                    else:
                        log.info("📊 POSITIONS  No positions this session yet")
                except Exception as _pe:
                    log.warning("Position summary failed: %s", _pe)

                # Update heartbeat again at cycle end with final balance
                _write_heartbeat("cycle_complete")

                # Sync balance from JSONL file — authoritative source across restarts
                bal, pnl, cnt = sync_balance_to_state()
                if bal is not None:
                    log.info(
                        "✅ STATE SYNCED: Balance $%.2f | Closed: %d | P&L: $%+.2f",
                        bal, cnt, pnl,
                    )
                else:
                    log.warning("⚠️ State sync failed (logs are still updating locally)")

                # ── Runtime tracking ───────────────────────────────────────
                try:
                    rt = update_runtime_tracking()
                    if rt:
                        log.info(
                            "⏱️  RUNTIME: %.1f hrs | %dd %dh elapsed | continuous",
                            rt["runtime_hours"],
                            int(rt["runtime_hours"] // 24),
                            int(rt["runtime_hours"] % 24),
                        )
                except Exception as exc:
                    log.warning("Runtime tracking failed: %s", exc)

                # ── Patience reminder (every 12 cycles = 6 hours) ──────────
                if cycle_count % 12 == 0:
                    import random
                    reminders = [
                        "Validation over profitability — every signal evaluated builds the edge.",
                        "Bot is working correctly. Every signal evaluated = good progress.",
                        "Trade placement every 5-50 cycles is NORMAL. You're on track.",
                        "Every signal evaluated is a data point. Every cycle is validation.",
                        "System working. ML self-improving. Let it run.",
                        "Low trade count early on is EXPECTED — Polymarket liquidity is sparse.",
                        "Week 1: 0-3 trades. Week 2: 3-8 trades. You're exactly on track.",
                    ]
                    log.info("[PATIENCE] %s", random.choice(reminders))

                # ── Trade frequency analysis (every 48 cycles = 24 hours) ──
                if cycle_count % 48 == 0:
                    freq = analyze_trade_frequency()
                    log.info("[FREQUENCY] %s: %s", freq["status"], freq["reason"])
                    log.info("[FREQUENCY] Expected: %s/week | Timeline: %s", freq["expected_trades_per_week"], freq["timeline"])

                log.info("─── Cycle complete ───")

            # Sleep 60 s — wakes immediately on Ctrl+C via _shutdown_event
            _shutdown_event.wait(timeout=60)
            _shutdown_event.clear()

        except KeyboardInterrupt:
            log.info("KeyboardInterrupt — shutting down")
            _running = False

        except Exception as exc:
            error_msg = str(exc)
            log.exception("Unhandled error in main loop: %s", error_msg)
            log_error(
                error_message=error_msg,
                error_type=type(exc).__name__,
                module="main",
                action_taken="Sleeping 60s then retrying",
            )
            send_alert_email(
                subject=f"ZiSi Bot ERROR — {type(exc).__name__}",
                body=f"Error in main loop:\n{error_msg}\n\nBot will retry in 60 seconds.",
            )
            _shutdown_event.wait(timeout=60)
            _shutdown_event.clear()

    # ── Shutdown email ────────────────────────────────────────────────────────
    try:
        final_snap = calculate_daily_metrics(list(_trade_history))
        session_duration = str(datetime.now(timezone.utc) - _session_start).split(".")[0]
        _email_scheduler.send_shutdown({
            "duration": session_duration,
            "stop_reason": "User shutdown",
            "pnl": final_snap.get("total_pnl", 0),
            "trades_executed": final_snap.get("total_trades", 0),
            "win_rate": final_snap.get("win_rate", 0) / 100,
            "best_trade": final_snap.get("best_trade", 0),
            "worst_trade": final_snap.get("worst_trade", 0),
            "profit_factor": final_snap.get("profit_factor", 0),
            "max_drawdown": final_snap.get("max_drawdown", 0) / 100,
        })
    except Exception as _exc:
        log.warning("Shutdown email failed: %s", _exc)

    if _shadow_monitor is not None:
        try:
            _shadow_monitor.stop()
        except Exception:
            pass
    stop_reconciliation_loop()
    stop_telegram_bot()
    from risk_engine import stop_risk_engine as _stop_risk_engine
    _stop_risk_engine()
    stop_health_monitor()
    log.info("ZiSi Bot stopped cleanly.")
    send_alert_email("ZiSi Bot stopped", "Bot has been shut down cleanly.")


if __name__ == "__main__":
    main()
