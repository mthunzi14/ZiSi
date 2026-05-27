"""
telegram_bot.py
Lightweight Telegram command bot for ZiSi. Runs as a daemon thread.
Requires TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env.

Commands:
  /status       — balance, P&L, positions, uptime
  /pnl          — P&L breakdown with win rate
  /trades       — last 5 closed trades
  /performance  — full stats: by coin and direction
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

_BOT_ROOT = Path(__file__).parent.parent
_POSITIONS_FILE = _BOT_ROOT / "infrastructure" / "exchange" / "positions_state.json"
_TOKEN    = os.getenv("TELEGRAM_BOT_TOKEN", "")
_CHAT_ID  = os.getenv("TELEGRAM_CHAT_ID", "")
_ENABLED  = bool(_TOKEN and _CHAT_ID)

_bot_running = True

# Track last daily summary date so we send it once at midnight
_last_daily_summary_date = ""

# Dedup set: prevents same Telegram update_id being processed twice (polling jitter)
_processed_ids: set = set()


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
        f = _POSITIONS_FILE
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

    realized   = summary.get("realized_pnl", 0.0)
    balance    = round(100.0 + realized, 2)
    pnl        = round(realized, 2)
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
    """Detailed breakdown: by coin and direction."""
    state   = _get_state()
    pos     = _get_positions()

    realized = pos.get("summary", {}).get("realized_pnl", 0.0)
    balance  = round(100.0 + realized, 2)
    pnl      = round(realized, 2)
    summary  = pos.get("summary", {})

    wins     = summary.get("win_count", 0)
    losses   = summary.get("loss_count", 0)
    total    = wins + losses
    wr_str   = f"{100*wins//max(1,total)}%" if total > 0 else "—"
    realized = summary.get("realized_pnl", 0.0)

    # Coin breakdown + direction breakdown from closed positions
    closed = pos.get("closed", [])
    coin_stats: dict = {}
    dir_stats: dict  = {"YES": [0, 0], "NO": [0, 0]}  # [wins, total]
    for trade in (closed if isinstance(closed, list) else []):
        title  = str(trade.get("event_title", ""))
        profit = float(trade.get("realized_pnl") or trade.get("profit") or 0)
        coin   = "OTHER"
        for c in ("BTC", "ETH", "SOL", "XRP"):
            if c in title.upper():
                coin = c
                break
        s = coin_stats.setdefault(coin, {"w": 0, "l": 0})
        if profit > 0:
            s["w"] += 1
        else:
            s["l"] += 1
        d = str(trade.get("direction", "YES")).upper()
        if d in dir_stats:
            dir_stats[d][1] += 1
            if profit > 0:
                dir_stats[d][0] += 1

    coin_lines = []
    for coin, cs in sorted(coin_stats.items()):
        ct = cs["w"] + cs["l"]
        cwr = f"{100*cs['w']//max(1,ct)}%"
        coin_lines.append(f"  {coin}: W{cs['w']}/L{cs['l']} ({cwr})")

    coin_section = "\n".join(coin_lines) if coin_lines else "  (no data yet)"

    dir_lines = []
    for d, (dw, dt) in dir_stats.items():
        if dt > 0:
            dir_lines.append(f"  {d}: W{dw}/L{dt-dw} ({100*dw//max(1,dt)}%)")
    dir_section = "\n".join(dir_lines) if dir_lines else "  (no data yet)"

    lines = [
        f"*ZiSi Performance*",
        f"```",
        f"Balance:   ${balance:.2f}  (P&L: ${pnl:+.2f})",
        f"Trades:    {total}   Win rate: {wr_str}",
        f"Realized:  ${realized:+.4f}",
        f"",
        f"By Coin:",
        coin_section,
        f"",
        f"By Direction:",
        dir_section,
        f"```",
    ]
    return "\n".join(lines)




def _format_pnl_breakdown() -> str:
    """/pnl — ZiSi P&L by market (Polymarket vs Kalshi) with win rates."""
    try:
        pos_file = _POSITIONS_FILE
        if not pos_file.exists():
            return "No positions data available."
        data = json.loads(pos_file.read_text(encoding="utf-8"))
        closed = data.get("closed", [])
    except Exception:
        return "Error reading positions data."

    _sum      = data.get("summary", {})
    realized  = float(_sum.get("realized_pnl", 0.0))
    balance   = round(100.0 + realized, 2)
    total_pnl = round(realized, 2)

    markets: dict = {}
    for t in (closed if isinstance(closed, list) else []):
        mkt = str(t.get("market", "POLYMARKET"))
        pnl = float(t.get("realized_pnl") or t.get("profit") or 0)
        won = pnl > 0
        if mkt not in markets:
            markets[mkt] = []
        markets[mkt].append((pnl, won))

    lines = []
    for mkt, trades in sorted(markets.items()):
        n    = len(trades)
        wins = sum(1 for _, w in trades if w)
        ep   = sum(p for p, _ in trades)
        wr   = f"{100*wins//max(1,n)}%" if n > 0 else "—"
        lines.append(f"{mkt:<12} {n:>4} trades  {wins}W/{n-wins}L  ({wr})  ${ep:+.2f}")

    summary_str = "\n".join(lines) if lines else "No closed trades yet."
    return (
        f"📈 *P&L Breakdown*\n"
        f"```\n"
        f"Balance:   ${balance:.2f}  (Total P&L: ${total_pnl:+.2f})\n"
        f"─────────────────────────────────────\n"
        f"{summary_str}\n"
        f"```"
    )


def _format_daily_summary() -> str:
    state   = _get_state()
    pos     = _get_positions()
    summary = pos.get("summary", {})
    realized = summary.get("realized_pnl", 0.0)
    balance  = round(100.0 + realized, 2)
    pnl      = round(realized, 2)
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




# ── Transport ─────────────────────────────────────────────────────────────────

def _send_message(text: str) -> None:
    def worker():
        try:
            import requests
            requests.post(
                f"https://api.telegram.org/bot{_TOKEN}/sendMessage",
                json={"chat_id": _CHAT_ID, "text": text, "parse_mode": "Markdown"},
                timeout=10,
            )
        except Exception as exc:
            log.warning("[TELEGRAM] Send failed: %s", exc)

    import threading
    threading.Thread(target=worker, daemon=True).start()



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

    elif cmd == "/pnl":
        _send_message(_format_pnl_breakdown())

    elif cmd == "/pause":
        paused_flag.touch()
        _send_message("⏸ *Bot paused.* Send /resume to restart trading.")

    elif cmd == "/resume":
        try:
            import sys as _sys
            _main = _sys.modules.get("__main__") or _sys.modules.get("main")
            if _main and hasattr(_main, "reset_circuit_breaker_state"):
                _main.reset_circuit_breaker_state()
            else:
                if paused_flag.exists():
                    paused_flag.unlink()
        except Exception:
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

    elif cmd == "/nrscan":
        try:
            import subprocess, sys
            _send_message("🔍 *Near-Resolution Scan* — checking markets resolving in <6h...")
            # Trigger rapid-fire by writing a synthetic queue entry
            _qf = _BOT_ROOT / "rapid_fire_queue.json"
            import json as _json
            existing = []
            try:
                existing = _json.loads(_qf.read_text()) if _qf.exists() else []
            except Exception:
                pass
            existing.append({"coin": "BTC", "direction": "BULLISH", "confidence": 8.0,
                              "title": "Manual /nrscan trigger", "source": "telegram"})
            _qf.write_text(_json.dumps(existing))
            _send_message("✅ NR scan queued — Kalshi cycle will fire on next 30s check.")
        except Exception as _nre:
            _send_message(f"❌ NR scan failed: {_nre}")

    elif cmd == "/latency":
        try:
            import time as _time, requests as _req
            _t0 = _time.time()
            _req.get("https://clob.polymarket.com/markets?limit=1", timeout=5)
            _rtt = (_time.time() - _t0) * 1000
            quality = "🟢 Good" if _rtt < 100 else ("🟡 Fair" if _rtt < 200 else "🔴 High")
            _send_message(
                f"*Network Latency*\n"
                f"```\n"
                f"CLOB RTT: {_rtt:.0f}ms  {quality}\n"
                f"Strategy: {'15-min markets only (5-min excluded)' if _rtt > 150 else 'All durations'}\n"
                f"```"
            )
        except Exception as _le:
            _send_message(f"❌ Latency probe failed: {_le}")

    elif cmd == "/macro":
        try:
            import json as _json
            _mf = _BOT_ROOT / "macro_context.json"
            if _mf.exists():
                _m = _json.loads(_mf.read_text())
                _macro_day = "⚠ YES — Kelly 50%" if _m.get("is_macro_event_day") else "No"
                _send_message(
                    f"*Macro Context*\n"
                    f"```\n"
                    f"Fed Rate:    {_m.get('fed_rate', '—')}\n"
                    f"CPI:         {_m.get('cpi', '—')}\n"
                    f"Unemployment:{_m.get('unemployment', '—')}\n"
                    f"Yield Curve: {_m.get('yield_curve', '—')}\n"
                    f"Macro Day:   {_macro_day}\n"
                    f"BTC Funding: {_m.get('funding_signal', 'NEUTRAL')}\n"
                    f"Balance:     ${_m.get('balance', 100):.2f}\n"
                    f"Progress:    {_m.get('progress_pct', 0):.2f}% → $10k\n"
                    f"```"
                )
            else:
                _send_message("No macro context available yet — wait for first cycle.")
        except Exception as _me:
            _send_message(f"❌ Macro data error: {_me}")

    elif cmd == "/funding":
        try:
            from data_sources.funding_consensus import get_consensus_funding
            _fd = get_consensus_funding("BTC")
            if _fd:
                _send_message(
                    f"*BTC Funding Rates*\n"
                    f"```\n"
                    f"Mean Rate:  {_fd.get('mean_rate', 0):.5f}\n"
                    f"Signal:     {_fd.get('signal', 'NEUTRAL')}\n"
                    f"Strength:   {_fd.get('consensus_strength', 0):.2f}\n"
                    f"Exchanges:  {_fd.get('exchange_count', 0)}/3\n"
                    f"```"
                )
            else:
                _send_message("Funding data unavailable right now.")
        except Exception as _fde:
            _send_message(f"❌ Funding error: {_fde}")

    elif cmd == "/help":
        _send_message(
            "*ZiSi Commands*\n"
            "/status       — Balance, P&L, positions\n"
            "/pnl          — P&L breakdown by market (Poly/Kalshi)\n"
            "/trades       — Last 5 closed trades\n"
            "/performance  — Full stats by coin & direction\n"
            "/circuit      — Circuit breaker status\n"
            "/nrscan       — Trigger near-resolution scan now\n"
            "/latency      — CLOB round-trip latency\n"
            "/macro        — FRED macro context + progress to $10k\n"
            "/funding      — BTC funding rate consensus\n"
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
                uid = update["update_id"]
                offset = uid + 1
                # Dedup: skip updates already processed (handles polling jitter/reconnects)
                if uid in _processed_ids:
                    continue
                _processed_ids.add(uid)
                # Keep set bounded to last 200 IDs
                if len(_processed_ids) > 200:
                    _processed_ids.difference_update(sorted(_processed_ids)[:-200])

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


def notify_trade_executed(
    event_title: str,
    direction: str,
    size: float,
    confidence: float,
    market: str = "POLYMARKET",
    entry_price: float = 0.0,
    target_price: float = 0.0,
    stop_loss: float = 0.0,
    provider: str = "",
    expiry_str: str = "",
) -> None:
    if not _ENABLED:
        return
    icon = "🟢" if direction.upper() in ("UP", "YES", "BULLISH") else "🔴"

    bal_line = ""
    try:
        _pf = str(_POSITIONS_FILE)
        _ps = json.loads(open(_pf).read())
        _pnl = float((_ps.get("summary") or {}).get("realized_pnl", 0))
        _bal = round(100.0 + _pnl, 2)
        bal_line = f"Balance: ${_bal:.2f}  |  P&L: ${_pnl:+.2f}\n"
    except Exception:
        pass

    price_line = f"Entry:   ${entry_price:.3f}\n" if entry_price else ""
    target_line = f"Target:  ${target_price:.3f}  ({(target_price/entry_price - 1)*100:+.0f}%)\n" if target_price and entry_price else ""
    stop_line = f"Stop:    ${stop_loss:.3f}  ({(stop_loss/entry_price - 1)*100:+.0f}%)\n" if stop_loss and entry_price else ""
    sig_line = f"Signal:  {provider.title()}\n" if provider else ""
    exp_line = f"Expires: {expiry_str[:16]}\n" if expiry_str else ""

    _send_message(
        f"{icon} *Trade Opened — {market}*\n"
        f"─────────────────────────\n"
        f"```\n"
        f"Event:   {event_title[:52]}\n"
        f"Side:    {direction}  |  Size: ${size:.2f}  |  Conf: {confidence:.1f}/10\n"
        f"{price_line}"
        f"{target_line}"
        f"{stop_line}"
        f"{sig_line}"
        f"{exp_line}"
        f"{bal_line}"
        f"```"
    )


def notify_balance_milestone(balance: float, milestone: float) -> None:
    """Send a Telegram alert when balance crosses a milestone ($110, $150, $200, etc.)."""
    if not _ENABLED:
        return
    pnl = balance - 100.0
    pct = pnl / 100.0 * 100
    _send_message(
        f"🎯 *MILESTONE REACHED: ${milestone:.0f}*\n\n"
        f"Balance: *${balance:.2f}*\n"
        f"Total P&L: ${pnl:+.2f} ({pct:+.1f}%)\n\n"
        f"Keep it going — next target: ${milestone * 1.5:.0f} 🚀"
    )


def notify_drawdown_warning(balance: float, drawdown_pct: float) -> None:
    """Alert on significant drawdown."""
    if not _ENABLED:
        return
    _send_message(
        f"⚠️ *Drawdown Warning*\n\n"
        f"Balance: ${balance:.2f}\n"
        f"Drawdown: {drawdown_pct:.1f}%\n\n"
        f"Kelly automatically reduced. Monitor closely."
    )


def notify_sentiment_degraded(current_provider: str, providers_tried: int) -> None:
    """Alert when all premium sentiment providers are exhausted."""
    if not _ENABLED:
        return
    _send_message(
        f"⚠️ *Sentiment Quality Degraded*\n\n"
        f"Running on *{current_provider.upper()}* (P{providers_tried} fallback)\n"
        f"Higher-priority providers unavailable (quota/credits).\n\n"
        f"Active: {current_provider.title()} is handling analysis.\n"
        f"Quality: {'Good' if providers_tried <= 6 else 'Reduced'} (tier {providers_tried}/11)"
    )


def notify_trade_closed(
    event_title: str,
    pnl: float,
    pnl_pct: float,
    hold_min: float,
    market: str = "POLYMARKET",
    balance: float = None,
    total_pnl: float = None,
    entry_price: float = 0.0,
    exit_price: float = 0.0,
    direction: str = "",
    exit_reason: str = "",
) -> None:
    if not _ENABLED:
        return
    icon    = "✅" if pnl > 0 else "❌"
    outcome = "WIN" if pnl > 0 else "LOSS"

    _wins = _losses = _total_closed = 0
    _streak = 0
    _streak_str = ""
    try:
        _pf = str(_POSITIONS_FILE)
        _ps = json.loads(open(_pf).read())
        _pnl_sum = float((_ps.get("summary") or {}).get("realized_pnl", 0))
        if balance is None:
            balance = round(100.0 + _pnl_sum, 2)
        if total_pnl is None:
            total_pnl = round(_pnl_sum, 2)
        _sum = _ps.get("summary", {})
        _wins = int(_sum.get("win_count", 0))
        _losses = int(_sum.get("loss_count", 0))
        _total_closed = _wins + _losses
        # Streak detection from recent closed trades
        _closed = _ps.get("closed_trades", [])[-10:]
        _is_win = pnl > 0
        for _t in reversed(_closed):
            if (_t.get("profit", 0) > 0) == _is_win:
                _streak += 1
            else:
                break
        if _streak >= 2:
            _dir = "win" if _is_win else "loss"
            _streak_str = f"  🔥 {_streak}-{_dir} streak"
    except Exception:
        pass

    _wr_pct = round(_wins / _total_closed * 100) if _total_closed > 0 else 0
    win_line = f"Rate:    {_wins}/{_total_closed}  ({_wr_pct}%){_streak_str}\n" if _total_closed > 0 else ""
    bal_line = f"Balance: ${balance:.2f}  |  Total: ${total_pnl:+.2f}\n" if balance is not None else ""
    price_line = f"Price:   ${entry_price:.3f} → ${exit_price:.3f}\n" if entry_price and exit_price else ""
    reason_line = f"Reason:  {exit_reason}\n" if exit_reason else ""

    held_line = f"Held:    {hold_min:.0f} min" + (f"  |  {exit_reason}" if exit_reason else "")

    _send_message(
        f"{icon} *Closed {outcome} — {market}*\n"
        f"─────────────────────────\n"
        f"```\n"
        f"Event:   {event_title[:52]}\n"
        f"Side:    {direction or 'YES'}  |  P&L: ${pnl:+.4f}  ({pnl_pct:+.1f}%)\n"
        f"{price_line}"
        f"{held_line}\n"
        f"{bal_line}"
        f"{win_line}"
        f"```"
    )
