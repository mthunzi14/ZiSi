"""
metrics_engine.py - ZiSi Bot Comprehensive Metrics
Calculates, formats, and persists trading performance metrics.
Tracks per-trade data, daily summaries, hourly breakdowns, coin analysis,
and signal-strength performance.
"""

import json
import logging
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger("zisi.metrics")

_METRICS_DIR = Path(__file__).parent

# Session-scoped skip counters — reset each time the bot restarts
_skip_counts: dict[str, int] = {"liquidity": 0, "entry_price": 0}
_skip_log: list[dict] = []

# Session-scoped real-trade log (Kalshi + future markets)
_real_trade_log: list[dict] = []


def log_real_trade(
    market_type: str,
    market_id: str,
    market_name: str,
    side: str,
    position_size: float,
    entry_price: float,
    sentiment: str = "UNKNOWN",
    confidence: float = 0.5,
    timestamp: str = "",
) -> None:
    """Record a real (paper or live) trade execution for metrics tracking."""
    record = {
        "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
        "market_type": market_type,
        "market_id": market_id,
        "market_name": market_name,
        "side": side,
        "position_size": position_size,
        "entry_price": entry_price,
        "sentiment": sentiment,
        "confidence": confidence,
    }
    _real_trade_log.append(record)
    log.debug("[METRICS] Real trade logged: %s %s $%.2f", market_type.upper(), side, position_size)


def get_real_trade_count(market_type: str = None) -> int:
    """Return count of logged real trades, optionally filtered by market_type."""
    if market_type:
        return sum(1 for t in _real_trade_log if t.get("market_type") == market_type)
    return len(_real_trade_log)


# ---------------------------------------------------------------------------
# Skip tracking
# ---------------------------------------------------------------------------

def track_skip(reason: str, details: dict) -> None:
    """
    Record a skipped trade with its reason and supporting details.

    Args:
        reason:  'liquidity' or 'entry_price'
        details: The validation result dict from risk_manager.
    """
    _skip_counts[reason] = _skip_counts.get(reason, 0) + 1
    _skip_log.append({
        "reason": reason,
        "details": details,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    log.debug("Skip recorded — %s: %s", reason, details.get("reason", ""))


def get_skip_counts() -> dict[str, int]:
    """Return current session skip counts keyed by reason."""
    return dict(_skip_counts)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _closed_trades(trades_list: list[dict]) -> list[dict]:
    """Return only trades that have a resolved profit field."""
    return [t for t in trades_list if "profit" in t and t.get("profit") is not None]


def _detect_coin(trade: dict) -> str:
    """Identify the primary coin from trade metadata fields."""
    blob = " ".join([
        trade.get("event_title", ""),
        " ".join(trade.get("affected_cryptos", [])),
        trade.get("headline", ""),
    ]).upper()

    if "BITCOIN" in blob or " BTC" in blob:
        return "BTC"
    if "ETHEREUM" in blob or " ETH" in blob:
        return "ETH"
    if "SOLANA" in blob or " SOL" in blob:
        return "SOL"
    if "DOGECOIN" in blob or " DOGE" in blob:
        return "DOGE"
    if "RIPPLE" in blob or " XRP" in blob:
        return "XRP"
    return "OTHER"


# ---------------------------------------------------------------------------
# Core calculation functions
# ---------------------------------------------------------------------------

def calculate_daily_metrics(trades_list: list[dict]) -> dict:
    """
    Compute overall daily performance metrics from a list of trades.

    Args:
        trades_list: All trade dicts (open + closed) from the session.
    Returns:
        Dict containing win_rate, profit_factor, sharpe_ratio, max_drawdown,
        total_pnl, expectancy, and current skip counts.
    """
    closed = _closed_trades(trades_list)
    skip_counts = get_skip_counts()

    if not closed:
        return {
            "total_trades": 0,
            "profitable": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "expectancy": 0.0,
            "sharpe_ratio": 0.0,
            "max_drawdown": 0.0,
            "total_pnl": 0.0,
            "avg_trade": 0.0,
            "liquidity_skips": skip_counts.get("liquidity", 0),
            "price_skips": skip_counts.get("entry_price", 0),
        }

    profits = [float(t.get("profit", 0) or 0) for t in closed]
    wins    = [p for p in profits if p > 0]
    losses  = [p for p in profits if p < 0]

    total_pnl  = sum(profits)
    avg_win    = sum(wins) / len(wins) if wins else 0.0
    avg_loss   = abs(sum(losses) / len(losses)) if losses else 0.0
    win_rate   = len(wins) / len(closed)
    loss_rate  = 1.0 - win_rate

    profit_factor_raw = (sum(wins) / abs(sum(losses))) if losses and sum(losses) != 0 else None
    profit_factor     = round(profit_factor_raw, 2) if profit_factor_raw is not None else "inf"

    expectancy = (avg_win * win_rate) - (avg_loss * loss_rate)

    # Simplified Sharpe: per-trade mean / stdev, scaled to 252-day year
    if len(profits) > 1:
        mean_r = statistics.mean(profits)
        std_r  = statistics.stdev(profits)
        sharpe = (mean_r / std_r * (252 ** 0.5)) if std_r > 0 else 0.0
    else:
        sharpe = 0.0

    # Max drawdown from equity curve
    equity = 0.0
    peak   = 0.0
    max_dd = 0.0
    for p in profits:
        equity += p
        peak    = max(peak, equity)
        dd      = (peak - equity) / peak if peak > 0 else 0.0
        max_dd  = max(max_dd, dd)

    return {
        "total_trades":    len(closed),
        "profitable":      len(wins),
        "win_rate":        round(win_rate * 100, 1),
        "profit_factor":   profit_factor,
        "avg_win":         round(avg_win, 2),
        "avg_loss":        round(avg_loss, 2),
        "expectancy":      round(expectancy, 2),
        "sharpe_ratio":    round(sharpe, 2),
        "max_drawdown":    round(max_dd * 100, 1),
        "total_pnl":       round(total_pnl, 2),
        "avg_trade":       round(total_pnl / len(closed), 2),
        "liquidity_skips": skip_counts.get("liquidity", 0),
        "price_skips":     skip_counts.get("entry_price", 0),
    }


def calculate_hourly_metrics(trades_list: list[dict]) -> dict:
    """
    Break down win rate and average profit by UTC hour (0–23).

    Returns:
        Dict keyed by hour int; only hours with at least one trade are included.
    """
    closed = _closed_trades(trades_list)
    buckets: dict[int, dict] = {h: {"trades": 0, "wins": 0, "profits": []} for h in range(24)}

    for trade in closed:
        ts = trade.get("timestamp", "")
        try:
            dt   = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            hour = dt.hour
        except (ValueError, AttributeError):
            continue

        profit = float(trade.get("profit", 0) or 0)
        buckets[hour]["trades"] += 1
        buckets[hour]["profits"].append(profit)
        if profit > 0:
            buckets[hour]["wins"] += 1

    result = {}
    for h, data in buckets.items():
        if data["trades"] == 0:
            continue
        result[h] = {
            "trades":       data["trades"],
            "wins":         data["wins"],
            "win_rate":     round(data["wins"] / data["trades"] * 100, 1),
            "avg_profit":   round(sum(data["profits"]) / len(data["profits"]), 2),
            "total_profit": round(sum(data["profits"]), 2),
        }

    return result


def calculate_coin_metrics(trades_list: list[dict]) -> dict:
    """
    Break down win rate and P&L by coin (BTC, ETH, SOL, etc.).

    Returns:
        Dict keyed by coin symbol.
    """
    closed = _closed_trades(trades_list)
    buckets: dict[str, dict] = {}

    for trade in closed:
        coin = _detect_coin(trade)
        if coin not in buckets:
            buckets[coin] = {"trades": 0, "wins": 0, "profits": []}

        profit = float(trade.get("profit", 0) or 0)
        buckets[coin]["trades"] += 1
        buckets[coin]["profits"].append(profit)
        if profit > 0:
            buckets[coin]["wins"] += 1

    return {
        coin: {
            "trades":       data["trades"],
            "wins":         data["wins"],
            "win_rate":     round(data["wins"] / data["trades"] * 100, 1),
            "total_profit": round(sum(data["profits"]), 2),
        }
        for coin, data in buckets.items()
    }


def calculate_signal_metrics(trades_list: list[dict]) -> dict:
    """
    Break down win rate and P&L by signal confidence level (7–10).

    Returns:
        Dict keyed by signal strength int; only populated strengths included.
    """
    closed = _closed_trades(trades_list)
    buckets: dict[int, dict] = {}

    for trade in closed:
        conf = int(trade.get("signal_confidence", 0) or 0)
        if conf not in buckets:
            buckets[conf] = {"trades": 0, "wins": 0, "profits": []}

        profit = float(trade.get("profit", 0) or 0)
        buckets[conf]["trades"] += 1
        buckets[conf]["profits"].append(profit)
        if profit > 0:
            buckets[conf]["wins"] += 1

    return {
        strength: {
            "trades":       data["trades"],
            "wins":         data["wins"],
            "win_rate":     round(data["wins"] / data["trades"] * 100, 1),
            "total_profit": round(sum(data["profits"]), 2),
        }
        for strength, data in buckets.items()
        if data["trades"] > 0
    }


# ---------------------------------------------------------------------------
# Formatting & persistence
# ---------------------------------------------------------------------------

def format_pretty_report(daily: dict, hourly: dict, coin: dict, signal: dict) -> str:
    """
    Format all four metric dicts into a single human-readable console report.

    Args:
        daily:  Output of calculate_daily_metrics()
        hourly: Output of calculate_hourly_metrics()
        coin:   Output of calculate_coin_metrics()
        signal: Output of calculate_signal_metrics()
    Returns:
        Multi-line string ready for print() or logging.
    """
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    lines = [
        "=" * 50,
        f"ZiSi Daily Metrics - {date_str}",
        "=" * 50,
        "",
        "OVERALL PERFORMANCE:",
        f"  Total Trades:    {daily.get('total_trades', 0)}",
        f"  Profitable:      {daily.get('profitable', 0)}",
        f"  Win Rate:        {daily.get('win_rate', 0.0)}%",
        f"  Profit Factor:   {daily.get('profit_factor', 0.0)}",
        f"  Total P&L:       ${daily.get('total_pnl', 0.0):.2f}",
        f"  Avg Trade:       ${daily.get('avg_trade', 0.0):.2f}",
        f"  Expectancy:      ${daily.get('expectancy', 0.0):.2f}",
        f"  Sharpe Ratio:    {daily.get('sharpe_ratio', 0.0)}",
        f"  Max Drawdown:    -{daily.get('max_drawdown', 0.0):.1f}%",
        "",
        "QUALITY FILTERS:",
        f"  Liquidity skips:    {daily.get('liquidity_skips', 0)}",
        f"  Entry price skips:  {daily.get('price_skips', 0)}",
        "",
    ]

    if signal:
        lines.append("BY SIGNAL STRENGTH:")
        for strength in sorted(signal.keys()):
            d = signal[strength]
            lines.append(
                f"  {strength}/10 confidence: {d['trades']} trades "
                f"({d['win_rate']}% win rate, ${d['total_profit']:.2f})"
            )
        lines.append("")

    if coin:
        lines.append("BY COIN:")
        for c in sorted(coin.keys()):
            d = coin[c]
            lines.append(
                f"  {c}: {d['trades']} trades "
                f"({d['win_rate']}% win rate, ${d['total_profit']:.2f})"
            )
        lines.append("")

    if hourly:
        lines.append("BY HOUR (UTC):")
        for h in sorted(hourly.keys()):
            d = hourly[h]
            lines.append(
                f"  {h:02d}:00-{(h + 1) % 24:02d}:00: {d['trades']} trades "
                f"({d['win_rate']}% win rate, ${d['avg_profit']:.2f} avg)"
            )
        lines.append("")

    lines.append("=" * 50)
    return "\n".join(lines)


def save_metrics_to_file(metrics: dict, date: Optional[str] = None) -> Path:
    """
    Append a timestamped metrics snapshot to metrics_YYYY-MM-DD.json (one JSON
    object per line so the file is easy to tail or parse incrementally).

    Args:
        metrics: Any metrics dict to persist.
        date:    Date string 'YYYY-MM-DD'; defaults to today (UTC).
    Returns:
        Path of the file written.
    """
    if date is None:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    filepath = _METRICS_DIR / f"metrics_{date}.json"
    record   = {"timestamp": datetime.now(timezone.utc).isoformat(), **metrics}

    import os
    if os.getenv("ZERO_DISK_LOGGING", "false").lower() == "true":
        logging.getLogger("zisi.metrics_snapshot").info(record)
        return filepath

    try:
        with filepath.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
        log.info("Metrics saved → %s", filepath.name)
    except Exception as exc:
        log.error("Failed to save metrics file: %s", exc)

    return filepath


# ---------------------------------------------------------------------------
# Summary entry-point
# ---------------------------------------------------------------------------

def log_daily_summary(trades_list: Optional[list[dict]] = None) -> None:
    """
    Calculate all metrics, print the report, save to JSON, and email the summary.

    Args:
        trades_list: List of trade dicts.  If None, reads logger._trade_history.
    """
    if trades_list is None:
        try:
            from logger import _trade_history      # noqa: PLC0415
            trades_list = list(_trade_history)
        except ImportError:
            log.warning("Cannot import logger._trade_history — no trades available")
            trades_list = []

    daily  = calculate_daily_metrics(trades_list)
    hourly = calculate_hourly_metrics(trades_list)
    coin   = calculate_coin_metrics(trades_list)
    signal = calculate_signal_metrics(trades_list)

    report = format_pretty_report(daily, hourly, coin, signal)
    print(report)
    log.info("Daily metrics report generated")

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    save_metrics_to_file(
        {"daily": daily, "hourly": hourly, "coin": coin, "signal": signal},
        date_str,
    )

    try:
        from logger import send_alert_email        # noqa: PLC0415
        send_alert_email(
            subject=f"ZiSi Daily Summary - {date_str}",
            body=_format_email_body(daily, hourly, coin, signal, date_str),
        )
    except Exception as exc:
        log.warning("Could not send daily summary email: %s", exc)


def _format_email_body(
    daily: dict,
    hourly: dict,
    coin: dict,
    signal: dict,
    date_str: str,
) -> str:
    """Build the plain-text email body for the daily summary."""
    best_coin   = max(coin.items(),   key=lambda x: x[1]["win_rate"]) if coin   else (None, {})
    best_signal = max(signal.items(), key=lambda x: x[1]["win_rate"]) if signal else (None, {})
    best_hour   = max(hourly.items(), key=lambda x: x[1]["win_rate"]) if hourly else (None, {})

    lines = [
        "ZiSi Daily Performance Report",
        f"Date: {date_str}",
        "",
        "OVERALL:",
        f"  Win Rate:       {daily.get('win_rate', 0)}%",
        f"  Total P&L:      ${daily.get('total_pnl', 0):.2f}",
        f"  Profit Factor:  {daily.get('profit_factor', 0)}",
        f"  Sharpe Ratio:   {daily.get('sharpe_ratio', 0)}",
        f"  Max Drawdown:   -{daily.get('max_drawdown', 0)}%",
        "",
        "QUALITY FILTERS:",
        f"  Liquidity skips:    {daily.get('liquidity_skips', 0)} trades",
        f"  Entry price skips:  {daily.get('price_skips', 0)} trades",
        f"  Actual trades:      {daily.get('total_trades', 0)}",
        "",
    ]

    top_lines = []
    if best_coin[0]:
        top_lines.append(f"  Best coin:    {best_coin[0]} ({best_coin[1].get('win_rate', 0)}% win rate)")
    if best_signal[0] is not None:
        top_lines.append(f"  Best signal:  {best_signal[0]}/10 ({best_signal[1].get('win_rate', 0)}% win rate)")
    if best_hour[0] is not None:
        top_lines.append(f"  Best hour:    {best_hour[0]:02d}:00 UTC ({best_hour[1].get('win_rate', 0)}% win rate)")

    if top_lines:
        lines.append("TOP PERFORMING:")
        lines.extend(top_lines)
        lines.append("")

    return "\n".join(lines)

# ---------------------------------------------------------------------------
# Market-type tracking
# ---------------------------------------------------------------------------

_market_type_counts: dict[str, dict] = {}


def track_by_market_type(market_type: str, result: str) -> None:
    """Record a WIN or LOSS outcome for a given market type."""
    if market_type not in _market_type_counts:
        _market_type_counts[market_type] = {"wins": 0, "losses": 0}
    if result.upper() == "WIN":
        _market_type_counts[market_type]["wins"] += 1
    else:
        _market_type_counts[market_type]["losses"] += 1


def get_market_type_breakdown() -> dict:
    """Return win-rate breakdown by market type."""
    breakdown = {}
    for mtype, data in _market_type_counts.items():
        total = data["wins"] + data["losses"]
        if total > 0:
            breakdown[mtype] = {
                "wins":     data["wins"],
                "losses":   data["losses"],
                "win_rate": round(data["wins"] / total, 4),
            }
    return breakdown


def track_polymarket_execution(matches: list, trades_executed: int) -> None:
    """Log Polymarket match → trade conversion rate."""
    match_count = len(matches)
    conversion = trades_executed / match_count if match_count > 0 else 0.0
    log.info(
        "[POLY-EXEC] Matches=%d | Trades=%d | Conversion=%.0f%%",
        match_count, trades_executed, conversion * 100,
    )


# ── Inversion monitor (Session 10) ───────────────────────────────────────────

_inversion_state: dict[str, bool] = {}       # asset/tf key -> inverted bool
_recent_outcomes: dict[str, list] = {}        # asset/tf key -> rolling 40 outcomes


def record_updown_outcome(asset: str, timeframe: str, won: bool) -> dict:
    """
    Record a resolved Up/Down trade outcome and check inversion threshold.
    Returns state dict with rolling_wr and invert_signal.
    """
    from config import INVERSION_WINDOW, INVERSION_TRIGGER_WR, INVERSION_RECOVERY_WR
    key = f"{asset}/{timeframe}"
    outcomes = _recent_outcomes.setdefault(key, [])
    outcomes.append(won)
    if len(outcomes) > INVERSION_WINDOW:
        outcomes.pop(0)

    rolling_wr = sum(outcomes) / len(outcomes) if outcomes else 0.5
    inverted   = _inversion_state.get(key, False)

    if len(outcomes) >= INVERSION_WINDOW:
        if rolling_wr < INVERSION_TRIGGER_WR and not inverted:
            _inversion_state[key] = True
            log.warning(
                "[METRICS] %s WR=%.0f%% over %d trades — INVERTING signal",
                key, rolling_wr * 100, INVERSION_WINDOW,
            )
        elif rolling_wr > INVERSION_RECOVERY_WR and inverted:
            _inversion_state[key] = False
            log.info("[METRICS] %s WR=%.0f%% recovered — inversion REVERTED", key, rolling_wr * 100)

    return {
        "key":        key,
        "rolling_wr": round(rolling_wr, 4),
        "inverted":   _inversion_state.get(key, False),
        "samples":    len(outcomes),
    }


def get_inversion_state() -> dict:
    """Return full inversion state for all tracked asset/timeframe pairs."""
    return {
        key: {
            "inverted":   _inversion_state.get(key, False),
            "rolling_wr": round(sum(v) / len(v), 4) if v else 0.5,
            "samples":    len(v),
        }
        for key, v in _recent_outcomes.items()
    }

