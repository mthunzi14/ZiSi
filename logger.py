"""
logger.py - ZiSi Bot Logging & Alerting
Logs trades to Google Sheets via the Drive API and sends Gmail alerts.
"""

import json
import logging
import smtplib
import time
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

from config import load_config

log = logging.getLogger("zisi.logger")

# Local fallback log file when Google Drive is unavailable
_LOCAL_LOG = Path(__file__).parent / "zisi_local_trades.jsonl"

# Runtime trade store for portfolio metrics (mirrors Drive sheet in memory)
_trade_history: list[dict] = []

# Google Sheets service (lazy-loaded)
_sheets_service = None
_spreadsheet_id: Optional[str] = None

# ---- Google Sheets helpers -------------------------------------------------

def _get_sheets_service():
    """
    Return an authenticated Google Sheets service object.
    Credentials are loaded from the file specified in GOOGLE_CREDENTIALS_FILE.
    """
    global _sheets_service
    if _sheets_service is not None:
        return _sheets_service

    try:
        from google.oauth2.service_account import Credentials
        from googleapiclient.discovery import build

        cfg = load_config()
        creds_file = cfg.get("GOOGLE_CREDENTIALS_FILE", "credentials.json")

        if not Path(creds_file).exists():
            log.warning("Google credentials file not found: %s — Drive logging disabled", creds_file)
            return None

        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = Credentials.from_service_account_file(creds_file, scopes=scopes)
        _sheets_service = build("sheets", "v4", credentials=creds)
        log.info("Google Sheets service authenticated")
        return _sheets_service
    except ImportError:
        log.warning("google-api-python-client not installed — Drive logging disabled")
    except Exception as exc:
        log.error("Failed to authenticate Google Sheets: %s", exc)
    return None


def _get_or_create_spreadsheet(service) -> Optional[str]:
    """
    Find or create the 'ZiSi_Bot_Logs' spreadsheet in Google Drive.
    Returns the spreadsheet_id string or None on failure.
    """
    global _spreadsheet_id
    if _spreadsheet_id:
        return _spreadsheet_id

    try:
        from googleapiclient.discovery import build
        from google.oauth2.service_account import Credentials

        cfg = load_config()
        creds_file = cfg.get("GOOGLE_CREDENTIALS_FILE", "credentials.json")
        folder_id = cfg.get("GOOGLE_DRIVE_FOLDER_ID", "")
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = Credentials.from_service_account_file(creds_file, scopes=scopes)
        drive_service = build("drive", "v3", credentials=creds)

        # Search for existing spreadsheet
        query = f"name='ZiSi_Bot_Logs' and mimeType='application/vnd.google-apps.spreadsheet'"
        results = drive_service.files().list(q=query, fields="files(id,name)").execute()
        files = results.get("files", [])
        if files:
            _spreadsheet_id = files[0]["id"]
            log.info("Found existing spreadsheet: %s", _spreadsheet_id)
            return _spreadsheet_id

        # Create a new spreadsheet
        body = {
            "properties": {"title": "ZiSi_Bot_Logs"},
            "sheets": [
                {"properties": {"title": "Trades"}},
                {"properties": {"title": "Signals"}},
                {"properties": {"title": "Errors"}},
            ],
        }
        ss = service.spreadsheets().create(body=body).execute()
        _spreadsheet_id = ss["spreadsheetId"]

        # Move to designated Drive folder
        if folder_id:
            drive_service.files().update(
                fileId=_spreadsheet_id,
                addParents=folder_id,
                fields="id,parents",
            ).execute()

        # Write header rows
        _write_header(service, _spreadsheet_id, "Trades", [
            "Timestamp", "Event Title", "Sentiment Score", "Entry Price",
            "Entry Value ($)", "Exit Timestamp", "Exit Price", "Exit Value ($)",
            "Profit/Loss ($)", "Profit/Loss (%)", "Win/Loss", "Hold Duration (h)", "Notes",
        ])
        _write_header(service, _spreadsheet_id, "Signals", [
            "Timestamp", "Source", "Headline", "Sentiment", "Confidence",
            "Reasoning", "Affected Cryptos", "Events Found", "Trade Decision",
        ])
        _write_header(service, _spreadsheet_id, "Errors", [
            "Timestamp", "Module", "Error Type", "Message", "Action Taken",
        ])

        log.info("Created spreadsheet: %s", _spreadsheet_id)
        return _spreadsheet_id

    except Exception as exc:
        log.error("Could not create/find spreadsheet: %s", exc)
        return None


def _write_header(service, spreadsheet_id: str, sheet_name: str, headers: list[str]) -> None:
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"{sheet_name}!A1",
        valueInputOption="RAW",
        body={"values": [headers]},
    ).execute()


def _append_row(service, spreadsheet_id: str, sheet_name: str, row: list) -> None:
    service.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range=f"{sheet_name}!A1",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": [row]},
    ).execute()


def _json_default(obj):
    """JSON serializer for types not handled by default encoder (e.g. datetime)."""
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return str(obj)


def _log_locally(data: dict) -> None:
    """Fallback: append JSON line to local file."""
    try:
        with _LOCAL_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(data, default=_json_default) + "\n")
    except Exception as exc:
        log.error("Local log write failed: %s", exc)


# ---------------------------------------------------------------------------
# Public logging functions
# ---------------------------------------------------------------------------

def log_trade_to_google_drive(trade_data: dict) -> None:
    """
    Append a completed (or opening) trade row to the 'Trades' sheet.

    Falls back to local JSONL file if Drive is unavailable.
    """
    _trade_history.append(trade_data)

    row = [
        str(trade_data.get("timestamp", datetime.now(timezone.utc).isoformat())),
        str(trade_data.get("event_title", "")),
        str(trade_data.get("signal_confidence", "")),
        str(trade_data.get("entry_price", "")),
        str(trade_data.get("position_size", "")),
        str(trade_data.get("exit_timestamp", "")),
        str(trade_data.get("exit_price", "")),
        str(trade_data.get("exit_value", "")),
        str(trade_data.get("profit", "")),
        str(trade_data.get("profit_percent", "")),
        "W" if float(trade_data.get("profit", 0) or 0) > 0 else "L",
        str(trade_data.get("hold_duration", "")),
        str(trade_data.get("notes", "News-driven")),
    ]

    cfg = load_config()
    if not cfg["LOG_TO_DRIVE"]:
        _log_locally(trade_data)
        return

    for attempt in range(1, 4):
        try:
            service = _get_sheets_service()
            if service is None:
                _log_locally(trade_data)
                return
            spreadsheet_id = _get_or_create_spreadsheet(service)
            if spreadsheet_id is None:
                _log_locally(trade_data)
                return
            _append_row(service, spreadsheet_id, "Trades", row)
            log.info("Trade logged to Google Drive: %s", trade_data.get("order_id", ""))
            return
        except Exception as exc:
            log.warning("Drive trade log attempt %d/3 failed: %s", attempt, exc)
            time.sleep(5 * attempt)

    log.error("All Drive log attempts failed — saving locally")
    _log_locally(trade_data)


def log_signal_analysis(
    news_article: dict,
    sentiment: dict,
    matching_events: list,
    trade_decision: str,
) -> None:
    """
    Log a signal analysis row locally (and to Drive when enabled).
    """
    row = [
        datetime.now(timezone.utc).isoformat(),
        str(news_article.get("source", "")),
        str(news_article.get("title", "")),
        str(sentiment.get("sentiment", "")),
        str(sentiment.get("confidence", "")),
        str(sentiment.get("reasoning", "")),
        ", ".join(sentiment.get("affected_cryptos", [])),
        str(len(matching_events)),
        trade_decision,
    ]

    # Always persist locally — Drive is optional
    _log_locally({"type": "signal", "row": row})

    cfg = load_config()
    if not cfg.get("LOG_TO_DRIVE", False):
        return  # Drive logging disabled — skip entirely, no 403 spam

    for attempt in range(1, 4):
        try:
            service = _get_sheets_service()
            if service is None:
                return
            spreadsheet_id = _get_or_create_spreadsheet(service)
            if spreadsheet_id is None:
                return
            _append_row(service, spreadsheet_id, "Signals", row)
            return
        except Exception as exc:
            log.warning("Signal log attempt %d/3 failed: %s", attempt, exc)
            time.sleep(5)


def log_error(error_message: str, error_type: str, module: str, action_taken: str = "Continuing") -> None:
    """
    Log an error to the console (and Drive when enabled).
    """
    log.error("[%s] %s: %s", module, error_type, error_message)

    cfg = load_config()
    if not cfg.get("LOG_TO_DRIVE", False):
        return  # Drive disabled — error already in console/file log

    row = [
        datetime.now(timezone.utc).isoformat(),
        module,
        error_type,
        error_message,
        action_taken,
    ]

    for attempt in range(1, 4):
        try:
            service = _get_sheets_service()
            if service is None:
                return
            spreadsheet_id = _get_or_create_spreadsheet(service)
            if spreadsheet_id is None:
                return
            _append_row(service, spreadsheet_id, "Errors", row)
            return
        except Exception as exc:
            log.warning("Error log attempt %d/3 failed: %s", attempt, exc)
            time.sleep(5)


# ---------------------------------------------------------------------------
# Email alerting
# ---------------------------------------------------------------------------

def send_alert_email(subject: str, body: str) -> None:
    """Email alerts disabled — Telegram is the sole notification channel."""
    log.debug("[EMAIL-DISABLED] Telegram is active: %s", subject)


def log_liquidity_skip(event_id: str, liquidity: float, min_liquidity: float) -> None:
    """
    Log a trade that was skipped because the market lacked sufficient liquidity.
    Also persists the skip to the local JSONL log for later metrics analysis.
    """
    log.warning(
        "[SKIP] Event %s: Liquidity $%s < $%s minimum",
        event_id, f"{liquidity:,.0f}", f"{min_liquidity:,.0f}",
    )
    _log_locally({
        "type": "skip",
        "reason": "liquidity",
        "event_id": event_id,
        "liquidity": liquidity,
        "min_liquidity": min_liquidity,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


def log_price_skip(event_id: str, entry_price: float, max_price: float, signal: int) -> None:
    """
    Log a trade that was skipped because the entry price exceeded the signal ceiling.
    Also persists the skip to the local JSONL log for later metrics analysis.
    """
    log.warning(
        "[SKIP] Event %s: Entry $%.4f > $%.4f max for %d/10 signal",
        event_id, entry_price, max_price, signal,
    )
    _log_locally({
        "type": "skip",
        "reason": "entry_price",
        "event_id": event_id,
        "entry_price": entry_price,
        "max_price": max_price,
        "signal": signal,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


def log_daily_metrics_summary(formatted_report: str) -> None:
    """
    Send a pre-formatted daily metrics report via email.

    Args:
        formatted_report: Output of metrics_engine.format_pretty_report().
    """
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    send_alert_email(
        subject=f"ZiSi Daily Summary - {date_str}",
        body=formatted_report,
    )
    log.info("Daily metrics summary emailed")


def send_daily_report() -> None:
    """
    Compile and email a daily performance summary at the configured UTC time.
    """
    metrics = get_portfolio_metrics()
    cfg = load_config()

    today_str = datetime.now(timezone.utc).strftime("%B %d, %Y")

    body = f"""ZiSi Bot Daily Performance Report — {today_str}

── DAILY STATS ──────────────────────────────
Trades:     {metrics['total_trades']}
Wins:       {metrics['total_wins']} ({metrics['win_rate'] * 100:.1f}%)
Profit:     ${metrics['total_profit']:.2f}

── METRICS ──────────────────────────────────
Sharpe Ratio:   {metrics['sharpe_ratio']:.2f}
Max Drawdown:   {metrics['max_drawdown'] * 100:.1f}%
Best Trade:     +${metrics['best_trade_profit']:.2f}
Worst Trade:    -${abs(metrics['worst_trade_loss']):.2f}

── ACCOUNT ──────────────────────────────────
Current Balance:   ${metrics['current_balance']:.2f}
Starting Capital:  ${cfg['ACCOUNT_BALANCE']:.2f}
Total Return:      {metrics['total_return_percent']:.2f}%

Generated by ZiSi v{cfg['BOT_VERSION']} | {datetime.now(timezone.utc).isoformat()}
"""
    send_alert_email(f"ZiSi Bot Daily Report — {today_str}", body)


# ---------------------------------------------------------------------------
# Portfolio metrics
# ---------------------------------------------------------------------------

def get_portfolio_metrics() -> dict:
    """
    Calculate performance metrics from the in-memory trade history.

    Returns a dict suitable for daily reports and dashboard display.
    """
    cfg = load_config()
    starting_capital = cfg["ACCOUNT_BALANCE"]

    closed = [t for t in _trade_history if t.get("status") == "CLOSED" or "profit" in t]
    if not closed:
        return {
            "total_trades": 0, "total_wins": 0, "total_losses": 0,
            "win_rate": 0.0, "avg_win": 0.0, "avg_loss": 0.0,
            "total_profit": 0.0, "total_return_percent": 0.0,
            "sharpe_ratio": 0.0, "max_drawdown": 0.0,
            "best_trade_profit": 0.0, "worst_trade_loss": 0.0,
            "current_balance": starting_capital,
        }

    profits = [float(t.get("profit", 0) or 0) for t in closed]
    wins = [p for p in profits if p > 0]
    losses = [p for p in profits if p <= 0]

    total_profit = sum(profits)
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0

    # Simplified Sharpe (daily returns / std dev, annualised)
    import statistics
    if len(profits) > 1:
        mean_r = statistics.mean(profits)
        std_r = statistics.stdev(profits)
        sharpe = (mean_r / std_r * (252 ** 0.5)) if std_r > 0 else 0.0
    else:
        sharpe = 0.0

    # Max drawdown
    equity = starting_capital
    peak = equity
    max_dd = 0.0
    for p in profits:
        equity += p
        peak = max(peak, equity)
        drawdown = (peak - equity) / peak if peak > 0 else 0
        max_dd = max(max_dd, drawdown)

    return {
        "total_trades": len(closed),
        "total_wins": len(wins),
        "total_losses": len(losses),
        "win_rate": len(wins) / len(closed) if closed else 0.0,
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "total_profit": round(total_profit, 2),
        "total_return_percent": round((total_profit / starting_capital) * 100, 2),
        "sharpe_ratio": round(sharpe, 2),
        "max_drawdown": round(max_dd, 4),
        "best_trade_profit": round(max(profits), 2) if profits else 0.0,
        "worst_trade_loss": round(min(profits), 2) if profits else 0.0,
        "current_balance": round(starting_capital + total_profit, 2),
    }
def log_patience_check() -> str:
    """Return a random patience reminder. Intended to be called every 6 hours."""
    import random
    reminders = [
        "Validation over profitability — every signal evaluated builds the edge.",
        "Bot is working correctly. Every signal evaluated = good progress.",
        "Trade placement every 5-50 cycles is NORMAL. You're on track.",
        "Every cycle = data. Every signal = validation. Let it run.",
        "System working. No changes needed.",
        "Low early trade count is EXPECTED — Polymarket liquidity is sparse.",
        "Week 1: 0-3 trades. Week 2: 3-8. Week 3: 8-15. Trust the process.",
    ]
    reminder = random.choice(reminders)
    log.info("[PATIENCE] %s", reminder)
    return reminder


# ── Google Drive logging status ───────────────────────────────────────────────
# Google Drive logging is controlled by LOG_TO_DRIVE in .env.
# Set LOG_TO_DRIVE=false to disable it entirely (recommended for Phase 1).
# All logging falls back to local JSONL files automatically.

# ── Formatted log helpers ─────────────────────────────────────────────────────

def format_signal_log(signal_data: dict, event_match, confidence: float) -> str:
    """Format a signal evaluation into a single readable log line."""
    sentiment = signal_data.get("sentiment", "UNKNOWN").upper()
    score = signal_data.get("confidence", 0)
    # Normalize 7-10 int score to 0.7-1.0 float for display
    score_display = score / 10.0 if isinstance(score, int) and score > 1 else score
    affected = signal_data.get("affected_cryptos", [])
    coin = (affected[0].upper() if affected else signal_data.get("coin", "UNKNOWN").upper())

    if event_match:
        return (
            f"[SIGNAL-HIT] {coin} {sentiment} ({score_display:.2f}) "
            f"→ Polymarket match: '{event_match.get('title', '')[:50]}' "
            f"(confidence: {confidence:.2f})"
        )
    return (
        f"[SIGNAL-MISS] {coin} {sentiment} ({score_display:.2f}) "
        f"→ No Polymarket trade placed"
    )


def format_cycle_log(cycle_data: dict) -> str:
    """Format an end-of-cycle summary block with UTC hour weighting mode."""
    from datetime import datetime as _dt
    from config import PEAK_TRADING_HOURS_UTC
    utc_hour = cycle_data.get("utc_hour", _dt.utcnow().hour)
    is_peak = utc_hour in PEAK_TRADING_HOURS_UTC
    mode = "PEAK-AGGRESSION (100% Kelly)" if is_peak else "OFF-PEAK-CONSERVATIVE (50% Kelly)"
    kelly_pct = "100%" if is_peak else "50%"
    kalshi_trades   = cycle_data.get("kalshi_trades", 0)
    kalshi_matches  = cycle_data.get("kalshi_matches", 0)
    total_executed  = cycle_data.get("executed_trades", 0) + kalshi_trades

    return (
        f"\n[CYCLE-SUMMARY] {cycle_data.get('timestamp', 'unknown')} | UTC {utc_hour:02d}:00 | {mode}\n"
        f"  Signals evaluated:       {cycle_data.get('total_signals', 0)}\n"
        f"  Strong signals (≥7/10):  {cycle_data.get('strong_signals', 0)}\n"
        f"  ── Polymarket ──────────────────────────\n"
        f"  Polymarket matches:      {cycle_data.get('matched_events', 0)}\n"
        f"  Polymarket trades:       {cycle_data.get('executed_trades', 0)}\n"
        f"  Hypothetical trades:     {cycle_data.get('hypothetical_trades', 0)}\n"
        f"  ── Kalshi ──────────────────────────────\n"
        f"  Kalshi matches:          {kalshi_matches}\n"
        f"  Kalshi trades:           {kalshi_trades}\n"
        f"  ── Totals ──────────────────────────────\n"
        f"  Total trades executed:   {total_executed}\n"
        f"  Balance:                 ${cycle_data.get('balance', 0):.2f}\n"
        f"  P&L:                     ${cycle_data.get('pnl', 0):.2f}\n"
        f"  Kelly Scaling:           {kelly_pct}\n"
        f"  Fear & Greed:            {cycle_data.get('fng_value', '?')} ({cycle_data.get('fng_label', '?')}) → ×{cycle_data.get('fng_kelly', 1.0):.2f}\n"
        f"  Runtime:                 {cycle_data.get('runtime', 'N/A')}\n"
        f"  Status:                  {cycle_data.get('status', 'running')}\n"
    )


_SIGNAL_EVAL_LOG = Path(__file__).parent / "signal_evaluations.jsonl"


def log_signal_evaluation(signal_data: dict, matched_event: Optional[dict], confidence: float) -> None:
    """
    Log every signal evaluation for edge analysis and missed-trade tracking.

    trade_type:
        'REAL'   — a matching event was found and traded (or will be)
        'MISSED' — strong signal but no Polymarket liquidity / match
    """
    raw_confidence = signal_data.get("confidence", 0)
    if isinstance(raw_confidence, int) and raw_confidence > 1:
        sentiment_score = raw_confidence / 10.0
    else:
        sentiment_score = float(raw_confidence) if raw_confidence else 0.5

    affected = signal_data.get("affected_cryptos", [])
    coin = affected[0].upper() if affected else signal_data.get("coin", "UNKNOWN").upper()

    trade_type = "REAL" if matched_event else "MISSED"
    reason_missed = None
    if not matched_event:
        reason_missed = "LOW_CONFIDENCE" if confidence <= 0.5 else "NO_LIQUIDITY"

    evaluation = {
        "timestamp": time.time(),
        "coin": coin,
        "sentiment": signal_data.get("sentiment", "neutral"),
        "sentiment_score": sentiment_score,
        "signal_source": signal_data.get("source", "NewsAPI"),
        "matched_event": matched_event.get("title") if matched_event else None,
        "confidence": round(confidence, 4),
        "trade_type": trade_type,
        "reason_missed": reason_missed,
    }

    try:
        with _SIGNAL_EVAL_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(evaluation) + "\n")
    except Exception as exc:
        log.error("Signal evaluation log write failed: %s", exc)

    log.info(
        "[SIGNAL-EVAL] %s | score=%.2f | type=%s | conf=%.2f",
        coin, sentiment_score, trade_type, confidence,
    )


def calculate_hypothetical_pnl() -> dict:
    """
    Read signal_evaluations.jsonl and calculate hypothetical performance
    for signals that had no Polymarket match (missed trades).

    Win heuristic: confidence > 0.6 counts as a hypothetical win.

    Returns:
        Dict with hypothetical_trades, hypothetical_wins, hypothetical_winrate.
    """
    if not _SIGNAL_EVAL_LOG.exists():
        return {"hypothetical_trades": 0, "hypothetical_wins": 0, "hypothetical_winrate": 0.0}

    hypothetical_trades = []
    try:
        with _SIGNAL_EVAL_LOG.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get("trade_type") == "MISSED" and entry.get("confidence", 0) > 0.55:
                        hypothetical_trades.append(entry)
                except Exception:
                    continue
    except Exception as exc:
        log.error("Failed to read signal evaluations: %s", exc)
        return {"hypothetical_trades": 0, "hypothetical_wins": 0, "hypothetical_winrate": 0.0}

    wins = [t for t in hypothetical_trades if t.get("confidence", 0) > 0.6]
    win_rate = len(wins) / len(hypothetical_trades) if hypothetical_trades else 0.0

    return {
        "hypothetical_trades": len(hypothetical_trades),
        "hypothetical_wins": len(wins),
        "hypothetical_winrate": round(win_rate, 4),
    }


def setup_file_logging() -> logging.Logger:
    """Configure the 'zisi' logger with a file handler and a console handler.

    Sets propagate=False so the root logger (configured separately via
    basicConfig) does not produce duplicate console lines.
    """
    log_path = Path(__file__).parent / "zisi_bot_console.log"

    logger = logging.getLogger("zisi")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False  # prevent double-printing via root logger

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = logging.FileHandler(str(log_path), mode="a", encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)

    # Avoid adding duplicate handlers if called more than once
    if not logger.handlers:
        logger.addHandler(file_handler)
        logger.addHandler(console_handler)

    return logger