"""
telegram_bot.py
Lightweight Telegram command bot for ZiSi. Runs as a daemon thread.
Requires TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env.

Commands:
  /status       — balance, P&L, positions, uptime
  /trades       — last 5 closed trades
  /performance  — full stats: by coin, by mule, by direction
  /mule         — show mule status
  /mule on 1    — enable Mule1
  /mule off 2   — disable Mule2
  /pause        — pause the bot
  /resume       — resume / override circuit breaker
  /circuit      — circuit breaker status
  /help         — all commands
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
_TOKEN    = os.getenv("TELEGRAM_BOT_TOKEN", "")
_CHAT_ID  = os.getenv("TELEGRAM_CHAT_ID", "")
_ENABLED  = bool(_TOKEN and _CHAT_ID)

_bot_running = True

# Track last daily summary date so we send it once at midnight
_last_daily_summary_date = ""


# ── Data helpers ─────────────────────────────────────────────────────────────

def _get_state() -> dict:
    try:
        f = _BOT_ROOT / "account_state.json"
        if f.exists():
            return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _get_positions() -> dict:
    try:
        f = _BOT_ROOT / "positions_state.json"
        if f.exists():
            return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _get_shadow_state() -> dict:
    try:
        f = _BOT_ROOT / "shadow_state.json"
        if f.exists():
            return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _get_shadow_config() -> dict:
    try:
        f = _BOT_ROOT / "shadow_config.json"
        if f.exists():
            return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _get_recent_trades(n: int = 5) -> list:
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


# ── Formatters ───────────────────────────────────────────────────────────────

def _format_status() -> str:
    state   = _get_state()
    pos     = _get_positions()
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
    paused     = (_BOT_ROOT / "bot_paused.flag").exists()

    mode     = "PAUSED" if paused else "RUNNING"
    pnl_sign = "+" if pnl >= 0 else ""

    return (
        f"*ZiSi Status*\n"
        f"```\n"
        f"Mode:      {mode}\n"
        f"Balance:   ${balance:.2f}\n"
        f"P&L:       {pnl_sign}${pnl:.2f}\n"
        f"Trades:    {trades} executed\n"
        f"\nPositions:\n"
        f"  Open:    {active_cnt}\n"
        f"  Closed:  {closed_cnt}  W:{wins} L:{losses}\n"
        f"  Realized:${realized:+.4f}\n"
        f"\nLast sync: {last_upd[:19] if last_upd != '?' else '?'}\n"
        f"```"
    )


def _format_trades() -> str:
    trades = _get_recent_trades(5)
    if not trades:
        return "No closed trades yet."
    lines = ["*Last 5 Closed Trades*\n```"]
    for t in trades:
        market  = (t.get("event_title") or t.get("ticker") or "?")[:28]
        profit  = t.get("profit", 0) or 0
        pct     = t.get("profit_percent", 0) or 0
        outcome = "WIN" if profit > 0 else "LOSS"
        sign    = "+" if profit >= 0 else ""
        lines.append(f"{outcome} {market}")
        lines.append(f"  {sign}${profit:.4f}  ({sign}{pct:.1f}%)")
        lines.append("")
    lines.append("```")
    return "\n".join(lines)


def _format_performance() -> str:
    """Detailed breakdown: own trades + shadow mules."""
    state   = _get_state()
    pos     = _get_positions()
    shadow  = _get_shadow_state()

    balance  = state.get("balance", 100.0)
    pnl      = state.get("pnl", 0.0)
    summary  = pos.get("summary", {})

    # Own trades
    wins     = summary.get("win_count", 0)
    losses   = summary.get("loss_count", 0)
    total    = wins + losses
    wr_str   = f"{100*wins//max(1,total)}%" if total > 0 else "—"
    realized = summary.get("realized_pnl", 0.0)

    # Shadow trades
    shadow_trades = shadow.get("shadow_trades", [])
    s_wins   = sum(1 for t in shadow_trades if t.get("status") == "WIN")
    s_losses = sum(1 for t in shadow_trades if t.get("status") == "LOSS")
    s_total  = s_wins + s_losses
    s_wr     = f"{100*s_wins//max(1,s_total)}%" if s_total > 0 else "—"

    # Coin breakdown from closed positions
    closed   = pos.get("closed", {})
    coin_stats: dict = {}
    for trade in closed.values():
        title  = str(trade.get("event_title", ""))
        profit = float(trade.get("profit", 0) or 0)
        coin   = "OTHER"
        for c in ("BTC", "ETH", "SOL"):
            if c in title.upper():
                coin = c
                break
        s = coin_stats.setdefault(coin, {"w": 0, "l": 0})
        if profit > 0:
            s["w"] += 1
        else:
            s["l"] += 1

    coin_lines = []
    for coin, cs in sorted(coin_stats.items()):
        ct = cs["w"] + cs["l"]
        cwr = f"{100*cs['w']//max(1,ct)}%"
        coin_lines.append(f"  {coin}: W{cs['w']}/L{cs['l']} ({cwr})")

    coin_section = "\n".join(coin_lines) if coin_lines else "  (no data yet)"

    lines = [
        f"*ZiSi Performance*",
        f"```",
        f"Balance:   ${balance:.2f}  (P&L: ${pnl:+.2f})",
        f"",
        f"Own Trades:",
        f"  W/L:     {wins}/{losses}  Win rate: {wr_str}",
        f"  P&L:     ${realized:+.4f}",
        f"",
        f"By Coin:",
        coin_section,
        f"",
        f"Shadow Mules:",
        f"  W/L:     {s_wins}/{s_losses}  Win rate: {s_wr}",
        f"  Total:   {s_total} trades",
        f"```",
    ]
    return "\n".join(lines)


def _format_mule_status() -> str:
    cfg = _get_shadow_config()
    m1  = cfg.get("PBOT6",   {}).get("enabled", True)
    m2  = cfg.get("WALLET2", {}).get("enabled", True)

    shadow = _get_shadow_state()
    trades = shadow.get("shadow_trades", [])

    def _mule_stats(tag: str) -> str:
        mts = [t for t in trades if t.get("label") == tag]
        w   = sum(1 for t in mts if t.get("status") == "WIN")
        l   = sum(1 for t in mts if t.get("status") == "LOSS")
        n   = w + l
        wr  = f"{100*w//max(1,n)}%" if n > 0 else "—"
        return f"W{w}/L{l} ({wr})"

    return (
        f"*Shadow Mule Status*\n"
        f"```\n"
        f"Mule1 (PBot6):   {'ON ✅' if m1 else 'OFF ❌'}  {_mule_stats('PBOT6')}\n"
        f"Mule2 (Wallet2): {'ON ✅' if m2 else 'OFF ❌'}  {_mule_stats('WALLET2')}\n"
        f"```\n"
        f"Toggle:\n"
        f"/mule on 1 or /mule off 1  — Mule1\n"
        f"/mule on 2 or /mule off 2  — Mule2"
    )


def _format_daily_summary() -> str:
    state   = _get_state()
    pos     = _get_positions()
    summary = pos.get("summary", {})
    balance  = state.get("balance", 100.0)
    pnl      = state.get("pnl", 0.0)
    wins     = summary.get("win_count", 0)
    losses   = summary.get("loss_count", 0)
    total    = wins + losses
    wr_str   = f"{100*wins//max(1,total)}%" if total > 0 else "—"
    realized = summary.get("realized_pnl", 0.0)
    today    = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    sign     = "+" if pnl >= 0 else ""
    return (
        f"📊 *ZiSi Daily Summary — {today}*\n"
        f"```\n"
        f"Balance:   ${balance:.2f}\n"
        f"Session P&L: {sign}${pnl:.2f}\n"
        f"Win rate:  {wr_str}  ({wins}W / {losses}L)\n"
        f"Realized:  ${realized:+.4f}\n"
        f"```"
    )


# ── Toggle helpers ────────────────────────────────────────────────────────────

def _set_mule(idx: str, enabled: bool) -> str:
    """Write shadow_config.json to enable/disable a mule."""
    label_map = {"1": "PBOT6", "2": "WALLET2"}
    name_map  = {"1": "Mule1", "2": "Mule2"}
    label = label_map.get(idx)
    if not label:
        return f"Unknown mule index: {idx}. Use 1 or 2."
    try:
        config_file = _BOT_ROOT / "shadow_config.json"
        cfg: dict = {}
        if config_file.exists():
            cfg = json.loads(config_file.read_text(encoding="utf-8"))
        cfg[label] = {"enabled": enabled}
        config_file.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        action = "enabled ✅" if enabled else "disabled ❌"
        return f"{name_map[idx]} ({label}) {action}."
    except Exception as exc:
        return f"Error updating mule config: {exc}"


# ── Transport ─────────────────────────────────────────────────────────────────

def _send_message(text: str) -> None:
    try:
        import requests
        requests.post(
            f"https://api.telegram.org/bot{_TOKEN}/sendMessage",
            json={"chat_id": _CHAT_ID, "text": text, "parse_mode": "Markdown"},
            timeout=10,
        )
    except Exception as exc:
        log.warning("[TELEGRAM] Send failed: %s", exc)


def _get_updates(offset: int) -> list:
    try:
        import requests
        r = requests.get(
            f"https://api.telegram.org/bot{_TOKEN}/getUpdates",
            params={"offset": offset, "timeout": 20},
            timeout=25,
        )
        if r.status_code == 200:
            return r.json().get("result", [])
    except Exception as exc:
        log.debug("[TELEGRAM] getUpdates error: %s", exc)
    return []


# ── Command handler ───────────────────────────────────────────────────────────

def _handle_command(text: str, chat_id: str) -> None:
    parts = text.strip().lower().split()
    cmd   = parts[0]
    paused_flag = _BOT_ROOT / "bot_paused.flag"

    if cmd in ("/status", "/start"):
        _send_message(_format_status())

    elif cmd == "/trades":
        _send_message(_format_trades())

    elif cmd == "/performance":
        _send_message(_format_performance())

    elif cmd == "/mule":
        if len(parts) == 1:
            _send_message(_format_mule_status())
        elif len(parts) == 3 and parts[1] in ("on", "off") and parts[2] in ("1", "2"):
            enabled = parts[1] == "on"
            result  = _set_mule(parts[2], enabled)
            _send_message(result)
        else:
            _send_message(
                "Usage:\n"
                "/mule          — show status\n"
                "/mule on 1     — enable Mule1\n"
                "/mule off 1    — disable Mule1\n"
                "/mule on 2     — enable Mule2\n"
                "/mule off 2    — disable Mule2"
            )

    elif cmd == "/pause":
        paused_flag.touch()
        _send_message("⏸ *Bot paused.* Send /resume to restart trading.")

    elif cmd == "/resume":
        if paused_flag.exists():
            paused_flag.unlink()
        _send_message("▶ *Bot resumed.* Trading continues on next cycle.")

    elif cmd == "/circuit":
        state = _get_state()
        pnl   = float(state.get("pnl", 0))
        threshold = -5.0
        status = "🔴 TRIPPED" if pnl < threshold else "🟢 OK"
        _send_message(
            f"*Circuit Breaker*\n"
            f"```\n"
            f"Status:    {status}\n"
            f"P&L:       ${pnl:+.2f}\n"
            f"Threshold: ${threshold:.2f}\n"
            f"```\n"
            f"{'Send /resume to override.' if pnl < threshold else 'Trading allowed.'}"
        )

    elif cmd == "/help":
        _send_message(
            "*ZiSi Commands*\n"
            "/status       — Balance, P&L, positions\n"
            "/trades       — Last 5 closed trades\n"
            "/performance  — Full stats by coin & mule\n"
            "/mule         — Shadow mule status + toggles\n"
            "/circuit      — Circuit breaker status\n"
            "/pause        — Pause trading\n"
            "/resume       — Resume trading\n"
            "/help         — This message"
        )

    else:
        _send_message(f"Unknown command: `{cmd}`\nSend /help for a list.")


# ── Daily summary check ───────────────────────────────────────────────────────

def _maybe_send_daily_summary() -> None:
    global _last_daily_summary_date
    now  = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    if now.hour == 0 and today != _last_daily_summary_date:
        _send_message(_format_daily_summary())
        _last_daily_summary_date = today
        log.info("[TELEGRAM] Daily summary sent")


# ── Polling loop ──────────────────────────────────────────────────────────────

def _polling_loop() -> None:
    log.info("[TELEGRAM] Bot started (polling)")
    _send_message(
        "🤖 *ZiSi Bot online.*\n"
        "Send /status for balance and positions.\n"
        "Send /help for all commands."
    )

    offset = 0
    consecutive_errors = 0

    while _bot_running:
        try:
            _maybe_send_daily_summary()

            updates = _get_updates(offset)
            consecutive_errors = 0

            for update in updates:
                offset = update["update_id"] + 1
                msg = update.get("message") or update.get("edited_message")
                if not msg:
                    continue
                chat_id = str(msg.get("chat", {}).get("id", ""))
                text    = msg.get("text", "")
                if not text or not text.startswith("/"):
                    continue
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
            backoff = min(60, 5 * consecutive_errors)
            log.warning("[TELEGRAM] Loop error #%d: %s — retrying in %ds", consecutive_errors, exc, backoff)
            time.sleep(backoff)

    log.info("[TELEGRAM] Polling loop stopped")


# ── Public API ────────────────────────────────────────────────────────────────

def start_telegram_bot() -> threading.Thread | None:
    if not _ENABLED:
        log.info("[TELEGRAM] Not configured — set TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID to enable")
        return None
    t = threading.Thread(target=_polling_loop, name="TelegramBot", daemon=True)
    t.start()
    log.info("[TELEGRAM] Daemon thread started")
    return t


def stop_telegram_bot() -> None:
    global _bot_running
    _bot_running = False


def send_alert(message: str) -> None:
    if not _ENABLED:
        return
    _send_message(message)


def notify_circuit_break(session_pnl: float, threshold: float) -> None:
    if not _ENABLED:
        return
    _send_message(
        f"🔴 *CIRCUIT BREAKER TRIPPED*\n\n"
        f"Session P&L: ${session_pnl:+.2f}\n"
        f"Threshold:   ${threshold:.2f}\n\n"
        f"Trading *HALTED*. Send /resume to override."
    )


def notify_trade_executed(event_title: str, direction: str, size: float,
                           confidence: float, market: str = "POLYMARKET") -> None:
    if not _ENABLED:
        return
    icon = "🟢" if direction.upper() in ("UP", "YES", "BULLISH") else "🔴"
    _send_message(
        f"{icon} *Trade — {market}*\n"
        f"```\n"
        f"Event: {event_title[:50]}\n"
        f"Side:  {direction}\n"
        f"Size:  ${size:.2f}  Conf: {confidence:.2f}\n"
        f"```"
    )


def notify_trade_closed(event_title: str, pnl: float, pnl_pct: float,
                         hold_min: float, market: str = "POLYMARKET") -> None:
    if not _ENABLED:
        return
    icon    = "✅" if pnl > 0 else "❌"
    outcome = "WIN" if pnl > 0 else "LOSS"
    _send_message(
        f"{icon} *Closed — {market}* ({outcome})\n"
        f"```\n"
        f"Event: {event_title[:50]}\n"
        f"P&L:   ${pnl:+.4f}  ({pnl_pct:+.1f}%)\n"
        f"Held:  {hold_min:.0f} min\n"
        f"```"
    )
