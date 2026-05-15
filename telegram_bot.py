"""
telegram_bot.py
Lightweight Telegram command bot for ZiSi. Runs as a daemon thread.
Requires TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env.

Commands:
  /status   — current balance, P&L, active positions, uptime
  /trades   — last 5 closed trades
  /pause    — pause the bot
  /resume   — resume the bot
  /help     — list commands

To enable, install python-telegram-bot:
  pip install python-telegram-bot==13.15

For now uses the simple polling API (no webhooks needed).
"""
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("zisi.telegram")

_BOT_ROOT = Path(__file__).parent
_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
_ENABLED = bool(_TOKEN and _CHAT_ID)

_bot_running = True
_bot_paused = False


def _get_state() -> dict:
    """Read account_state.json for current bot state."""
    try:
        state_file = _BOT_ROOT / "account_state.json"
        if state_file.exists():
            return json.loads(state_file.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _get_positions() -> dict:
    """Read positions_state.json for position summary."""
    try:
        pos_file = _BOT_ROOT / "positions_state.json"
        if pos_file.exists():
            return json.loads(pos_file.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _get_recent_trades(n: int = 5) -> list:
    """Read last n closed trades from zisi_local_trades.jsonl."""
    trades_file = _BOT_ROOT / "zisi_local_trades.jsonl"
    if not trades_file.exists():
        return []
    try:
        lines = trades_file.read_text(encoding="utf-8").strip().split("\n")
        records = []
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                if r.get("status", "").upper() == "CLOSED" and r.get("order_id"):
                    records.append(r)
                    if len(records) >= n:
                        break
            except Exception:
                pass
        return records
    except Exception:
        return []


def _format_status() -> str:
    state = _get_state()
    pos   = _get_positions()
    summary = pos.get("summary", {})

    balance    = state.get("balance", 100.0)
    pnl        = state.get("pnl", 0.0)
    trades     = state.get("trades_executed", 0)
    last_upd   = state.get("last_updated", "?")
    active_cnt = summary.get("active_count", 0)
    closed_cnt = summary.get("closed_count", 0)
    wins       = summary.get("win_count", 0)
    losses     = summary.get("loss_count", 0)
    realized   = summary.get("realized_pnl", 0.0)
    paused_flag = (_BOT_ROOT / "bot_paused.flag").exists()

    mode = "PAUSED" if paused_flag else "RUNNING"
    pnl_sign = "+" if pnl >= 0 else ""

    return (
        f"*ZiSi Bot Status*\n"
        f"```\n"
        f"Mode:     {mode}\n"
        f"Balance:  ${balance:.2f}\n"
        f"P&L:      {pnl_sign}${pnl:.2f}\n"
        f"Trades:   {trades} executed\n"
        f"\nPositions:\n"
        f"  Open:   {active_cnt}\n"
        f"  Closed: {closed_cnt}  W:{wins}/L:{losses}\n"
        f"  P&L:    ${realized:+.4f}\n"
        f"\nLast sync: {last_upd[:19] if last_upd != '?' else '?'}\n"
        f"```"
    )


def _format_trades() -> str:
    trades = _get_recent_trades(5)
    if not trades:
        return "No closed trades yet."

    lines = ["*Last 5 Closed Trades*\n```"]
    for t in trades:
        market   = (t.get("event_title") or t.get("ticker") or "?")[:28]
        profit   = t.get("profit", 0) or 0
        pct      = t.get("profit_percent", 0) or 0
        outcome  = "WIN" if profit > 0 else "LOSS"
        sign     = "+" if profit >= 0 else ""
        lines.append(f"{outcome} {market}")
        lines.append(f"  {sign}${profit:.4f}  ({sign}{pct:.1f}%)")
        lines.append("")
    lines.append("```")
    return "\n".join(lines)


def _send_message(text: str) -> None:
    """Send a message via Telegram Bot API using requests (no SDK needed)."""
    try:
        import requests
        url = f"https://api.telegram.org/bot{_TOKEN}/sendMessage"
        requests.post(url, json={
            "chat_id": _CHAT_ID,
            "text": text,
            "parse_mode": "Markdown",
        }, timeout=10)
    except Exception as exc:
        log.warning("[TELEGRAM] Send failed: %s", exc)


def _get_updates(offset: int) -> list:
    """Poll Telegram for new messages."""
    try:
        import requests
        url = f"https://api.telegram.org/bot{_TOKEN}/getUpdates"
        r = requests.get(url, params={"offset": offset, "timeout": 20}, timeout=25)
        if r.status_code == 200:
            return r.json().get("result", [])
    except Exception as exc:
        log.debug("[TELEGRAM] getUpdates error: %s", exc)
    return []


def _handle_command(text: str, chat_id: str) -> None:
    """Process a Telegram command and reply."""
    cmd = text.strip().lower().split()[0]
    paused_flag = _BOT_ROOT / "bot_paused.flag"

    if cmd in ("/status", "/start"):
        _send_message(_format_status())

    elif cmd == "/trades":
        _send_message(_format_trades())

    elif cmd == "/pause":
        paused_flag.touch()
        _send_message("⏸ *Bot paused.*\nSend /resume to restart trading.")

    elif cmd == "/resume":
        if paused_flag.exists():
            paused_flag.unlink()
        _send_message("▶ *Bot resumed.* Trading will continue on next cycle.")

    elif cmd == "/circuit":
        state = _get_state()
        pnl = float(state.get("pnl", 0))
        threshold = -5.0
        status = "🔴 TRIPPED" if pnl < threshold else "🟢 OK"
        _send_message(
            f"*Circuit Breaker Status*\n"
            f"```\n"
            f"Status:    {status}\n"
            f"Session P&L: ${pnl:+.2f}\n"
            f"Threshold: ${threshold:.2f}\n"
            f"```\n"
            f"{'Send /resume to override and allow trading again.' if pnl < threshold else 'Trading is allowed.'}"
        )

    elif cmd == "/help":
        _send_message(
            "*ZiSi Bot Commands*\n"
            "/status   — Balance, P&L, positions\n"
            "/trades   — Last 5 closed trades\n"
            "/circuit  — Circuit breaker status\n"
            "/pause    — Pause trading\n"
            "/resume   — Resume trading\n"
            "/help     — This message"
        )
    else:
        _send_message(f"Unknown command: `{cmd}`\nSend /help for a list.")


def _polling_loop() -> None:
    """
    Main Telegram polling loop. Runs forever as a daemon thread.
    Self-healing: backs off on errors, restarts automatically if the inner
    loop crashes — the outer while loop never exits until _bot_running = False.
    """
    log.info("[TELEGRAM] Bot started (polling)")
    _send_message(
        "🤖 *ZiSi Bot online.*\n"
        "Send /status to check balance and positions.\n"
        "Send /help for all commands."
    )

    offset = 0
    consecutive_errors = 0
    MAX_BACKOFF = 60  # seconds

    while _bot_running:
        try:
            updates = _get_updates(offset)
            consecutive_errors = 0  # reset on success

            for update in updates:
                offset = update["update_id"] + 1
                msg = update.get("message") or update.get("edited_message")
                if not msg:
                    continue
                chat_id = str(msg.get("chat", {}).get("id", ""))
                text    = msg.get("text", "")
                if not text or not text.startswith("/"):
                    continue
                # Security: only respond to our configured chat
                if chat_id != str(_CHAT_ID):
                    log.warning("[TELEGRAM] Ignoring unknown chat: %s", chat_id)
                    continue
                log.info("[TELEGRAM] Command: %s", text[:50])
                try:
                    _handle_command(text, chat_id)
                except Exception as cmd_exc:
                    log.warning("[TELEGRAM] Command error for '%s': %s", text[:30], cmd_exc)

        except Exception as exc:
            consecutive_errors += 1
            backoff = min(MAX_BACKOFF, 5 * consecutive_errors)
            log.warning(
                "[TELEGRAM] Loop error #%d: %s — retrying in %ds",
                consecutive_errors, exc, backoff,
            )
            time.sleep(backoff)

    log.info("[TELEGRAM] Polling loop stopped")


def start_telegram_bot() -> threading.Thread | None:
    """
    Start the Telegram bot as a daemon thread.
    Returns the thread or None if not configured.
    """
    if not _ENABLED:
        log.info(
            "[TELEGRAM] Not configured — set TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID in .env to enable"
        )
        return None

    t = threading.Thread(target=_polling_loop, name="TelegramBot", daemon=True)
    t.start()
    log.info("[TELEGRAM] Daemon thread started")
    return t


def stop_telegram_bot() -> None:
    """Signal the polling loop to stop on next iteration."""
    global _bot_running
    _bot_running = False


def send_alert(message: str) -> None:
    """
    Send an ad-hoc alert from any module.
    E.g. call from trader.py after a trade closes.
    """
    if not _ENABLED:
        return
    _send_message(message)


def notify_circuit_break(session_pnl: float, threshold: float) -> None:
    """Send a prominent circuit-breaker alert via Telegram."""
    if not _ENABLED:
        return
    _send_message(
        f"🔴 *CIRCUIT BREAKER TRIPPED*\n\n"
        f"Session P&L has crossed the loss limit.\n"
        f"```\n"
        f"Current P&L: ${session_pnl:+.2f}\n"
        f"Threshold:   ${threshold:.2f}\n"
        f"```\n"
        f"Trading is now *HALTED*.\n"
        f"Send /resume to override, or let it reset at next restart."
    )


def notify_trade_executed(event_title: str, direction: str, size: float,
                           confidence: float, market: str = "POLYMARKET") -> None:
    """Send a trade-executed notification via Telegram."""
    if not _ENABLED:
        return
    icon = "🟢" if direction.upper() in ("YES", "BULLISH") else "🔴"
    _send_message(
        f"{icon} *Trade Executed — {market}*\n"
        f"```\n"
        f"Event:  {event_title[:50]}\n"
        f"Side:   {direction}\n"
        f"Size:   ${size:.2f}\n"
        f"Conf:   {confidence:.2f}\n"
        f"```"
    )


def notify_trade_closed(event_title: str, pnl: float, pnl_pct: float,
                         hold_min: float, market: str = "POLYMARKET") -> None:
    """Send a trade-closed notification via Telegram."""
    if not _ENABLED:
        return
    icon = "✅" if pnl > 0 else "❌"
    outcome = "WIN" if pnl > 0 else "LOSS"
    _send_message(
        f"{icon} *Trade Closed — {market}* ({outcome})\n"
        f"```\n"
        f"Event:  {event_title[:50]}\n"
        f"P&L:    ${pnl:+.4f}  ({pnl_pct:+.1f}%)\n"
        f"Held:   {hold_min:.0f} min\n"
        f"```"
    )
