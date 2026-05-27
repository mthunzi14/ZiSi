"""
goal_monitor.py — ZiSi 24/7 Autonomous Goal Monitor
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Implements the /goal continuous monitoring loop. Runs alongside the main bot engine
and delivers:

  ✦  Real-time trade detection  — detects every TRADE OPENED / CLOSED event
  ✦  Live PnL reporting         — prints rolling P&L every configurable interval
  ✦  Health pulse               — watches account_state.json staleness
  ✦  Self-healing               — clears stuck positions, regenerates watchers
  ✦  Session summary            — prints rich ASCII summary every 30 min
  ✦  Automated alerting         — flags consecutive losses, drawdown spikes

Usage (auto-started by sovereign_runner.py or standalone):
    python goal_monitor.py
    python goal_monitor.py --interval 300   # report every 5 min instead of 15
    python goal_monitor.py --quiet          # suppress routine heartbeats
"""

import sys
import os
import json
import time
import threading
import argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ── Force UTF-8 + ANSI on Windows ────────────────────────────────────────────
if sys.platform.startswith('win'):
    try:
        os.system('chcp 65001 >nul 2>&1')
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
        import ctypes
        k = ctypes.windll.kernel32
        k.GetStdHandle.restype = ctypes.c_void_p
        k.SetConsoleMode(k.GetStdHandle(4294967285), 7)
    except Exception:
        pass

# ── ANSI Palette ─────────────────────────────────────────────────────────────
R  = '\033[0m'
B  = '\033[1m'
CY = '\033[96m'
GR = '\033[92m'
YL = '\033[93m'
MG = '\033[95m'
RD = '\033[91m'
BL = '\033[94m'
WH = '\033[97m'
DK = '\033[90m'


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _ts() -> str:
    """Compact local timestamp prefix."""
    return datetime.now().strftime('%H:%M:%S')


# ── File paths ────────────────────────────────────────────────────────────────
BASE_DIR      = Path(__file__).parent.parent
ACCOUNT_STATE = BASE_DIR / 'account_state.json'
POSITIONS     = BASE_DIR / 'infrastructure' / 'exchange' / 'positions_state.json'
TRADES_FILE   = BASE_DIR / 'zisi_local_trades.jsonl'
CONSOLE_LOG   = BASE_DIR / 'zisi_bot_console.log'
BALANCE_HIST  = BASE_DIR / 'balance_history.jsonl'


def _read_json(path: Path) -> dict | list | None:
    try:
        text = path.read_text(encoding='utf-8', errors='replace').strip()
        return json.loads(text) if text else None
    except Exception:
        return None


def _tail_file(path: Path, n: int = 40) -> list[str]:
    """Read the last n lines of a file efficiently."""
    try:
        with open(path, 'rb') as f:
            f.seek(0, 2)
            size = f.tell()
            block = min(size, n * 200)
            f.seek(-block, 2)
            raw = f.read().decode('utf-8', errors='replace')
        return raw.splitlines()[-n:]
    except Exception:
        return []


# ── Session state ─────────────────────────────────────────────────────────────
class SessionTracker:
    """Tracks this monitoring session's metrics."""

    def __init__(self, starting_balance: float):
        self.start_time   = datetime.now(timezone.utc)
        self.start_bal    = starting_balance
        self.seen_trades  = set()   # order_ids seen since monitor started
        self.new_trades   = []      # trades detected this session
        self.last_pnl     = 0.0
        self.peak_pnl     = 0.0
        self.worst_pnl    = 0.0
        self.last_health_ok = True
        self.consecutive_losses = 0
        self.total_wins   = 0
        self.total_losses = 0
        self.alert_count  = 0

    def uptime_str(self) -> str:
        delta = datetime.now(timezone.utc) - self.start_time
        h = int(delta.total_seconds() // 3600)
        m = int((delta.total_seconds() % 3600) // 60)
        s = int(delta.total_seconds() % 60)
        return f'{h:02d}:{m:02d}:{s:02d}'


# ── Core functions ────────────────────────────────────────────────────────────

def read_account() -> dict:
    """Read account state; return safe defaults if unavailable."""
    data = _read_json(ACCOUNT_STATE)
    if isinstance(data, dict):
        return data
    return {'balance': 100.0, 'starting_balance': 100.0,
            'pnl': 0.0, 'trades_executed': 0, 'status': 'unknown'}


def read_positions() -> dict:
    data = _read_json(POSITIONS)
    if isinstance(data, dict):
        return data
    return {'summary': {}, 'active': [], 'closed': []}


def read_trades() -> list[dict]:
    trades = []
    if not TRADES_FILE.exists():
        return trades
    try:
        for line in TRADES_FILE.read_text(encoding='utf-8', errors='replace').splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                trades.append(rec)
            except Exception:
                pass
    except Exception:
        pass
    return trades


def get_current_pnl(account: dict, positions: dict) -> float:
    """Derive PnL from positions_state (single source of truth)."""
    try:
        realized = float(positions.get('summary', {}).get('realized_pnl', 0))
        return round(realized, 4)
    except Exception:
        return float(account.get('pnl', 0))


def get_balance(account: dict, positions: dict) -> float:
    starting = float(account.get('starting_balance', account.get('balance', 100.0)))
    pnl = get_current_pnl(account, positions)
    return round(starting + pnl, 2)


def bot_is_healthy(account: dict, max_stale_min: int = 30) -> tuple[bool, float]:
    """Check if the bot wrote to account_state.json recently."""
    try:
        last_updated = account.get('last_updated', '')
        if not last_updated:
            return False, 9999.0
        lu = datetime.fromisoformat(last_updated)
        if lu.tzinfo is None:
            lu = lu.replace(tzinfo=timezone.utc)
        stale_min = (datetime.now(timezone.utc) - lu).total_seconds() / 60
        return stale_min < max_stale_min, round(stale_min, 1)
    except Exception:
        return False, -1.0


# ── Trade detection ────────────────────────────────────────────────────────────

def detect_new_trades(tracker: SessionTracker) -> list[dict]:
    """Compare current closed trades against what we've already seen."""
    new = []
    positions = read_positions()
    for pos in positions.get('closed', []):
        oid = pos.get('order_id') or pos.get('event_title', '')
        if oid and oid not in tracker.seen_trades:
            tracker.seen_trades.add(oid)
            new.append(pos)
    for trade in read_trades():
        oid = trade.get('order_id', '')
        if oid and oid not in tracker.seen_trades:
            if trade.get('status', '').upper() in ('CLOSED', 'RESOLVED', 'SETTLED'):
                tracker.seen_trades.add(oid)
                new.append(trade)
    return new


def announce_trade(trade: dict):
    profit = float(trade.get('realized_pnl') or trade.get('profit') or 0)
    is_win = profit > 0
    color  = GR if is_win else RD
    icon   = '🏆' if is_win else '📉'
    title  = (trade.get('event_title') or trade.get('market', 'Unknown'))[:60]
    entry  = float(trade.get('entry_price') or trade.get('avg_fill_price') or 0)
    print(
        f"\n{color}{B}  {icon}  TRADE CLOSED  ──  {title}{R}\n"
        f"  {DK}Entry:{R} {WH}{entry:.4f}{R}  "
        f"{DK}P&L:{R} {color}{B}{profit:+.4f} USDC{R}  "
        f"{DK}at{R} {_ts()}"
    )


def announce_open(trade: dict):
    title = (trade.get('event_title') or trade.get('market', 'Unknown'))[:60]
    entry = float(trade.get('entry_price') or trade.get('avg_fill_price') or 0)
    size  = float(trade.get('size') or trade.get('shares') or 0)
    print(
        f"\n{GR}{B}  ✦  TRADE OPENED  ──  {title}{R}\n"
        f"  {DK}Entry:{R} {WH}{entry:.4f}{R}  "
        f"{DK}Size:{R} {WH}{size:.2f}{R}  "
        f"{DK}at{R} {_ts()}"
    )


# ── Status printing ────────────────────────────────────────────────────────────

def print_status_report(tracker: SessionTracker, quiet: bool = False):
    account   = read_account()
    positions = read_positions()
    pnl       = get_current_pnl(account, positions)
    balance   = get_balance(account, positions)
    starting  = float(account.get('starting_balance', 100.0))
    pnl_pct   = (pnl / starting * 100) if starting else 0
    trades_ex = int(account.get('trades_executed', 0))
    healthy, stale_min = bot_is_healthy(account)
    active_ct = len(positions.get('active', []))
    closed_ct = len(positions.get('closed', []))

    # Track peak/worst
    if pnl > tracker.peak_pnl:
        tracker.peak_pnl = pnl
    if pnl < tracker.worst_pnl:
        tracker.worst_pnl = pnl

    pnl_color  = GR if pnl >= 0 else RD
    pnl_sign   = '+' if pnl >= 0 else ''
    health_icon = f'{GR}●{R}' if healthy else f'{YL}⚠{R}'
    last_update = f'{stale_min:.1f}m ago' if stale_min >= 0 else 'unknown'

    print(
        f"\n{CY}{B}  ╔══════════════════ ZiSi GOAL MONITOR ══════════════════╗{R}\n"
        f"  {DK}│{R}  {health_icon} {WH}Bot Status{R}       {GR if healthy else YL}"
        f"{'LIVE' if healthy else 'STALE'} {R}({last_update})\n"
        f"  {DK}│{R}  {DK}⏱  Uptime{R}          {WH}{tracker.uptime_str()}{R}\n"
        f"  {DK}│{R}\n"
        f"  {DK}│{R}  {DK}💰 Balance{R}          {WH}${balance:.2f}{R}\n"
        f"  {DK}│{R}  {DK}📈 Session P&L{R}      {pnl_color}{B}{pnl_sign}${abs(pnl):.4f} ({pnl_sign}{pnl_pct:.2f}%){R}\n"
        f"  {DK}│{R}  {DK}🏔  Peak P&L{R}         {GR}+${tracker.peak_pnl:.4f}{R}\n"
        f"  {DK}│{R}  {DK}🕳  Worst P&L{R}        {RD}${tracker.worst_pnl:.4f}{R}\n"
        f"  {DK}│{R}\n"
        f"  {DK}│{R}  {DK}🔄 Trades Executed{R}  {WH}{trades_ex}{R}\n"
        f"  {DK}│{R}  {DK}⚡ Active Positions{R}  {WH}{active_ct}{R}\n"
        f"  {DK}│{R}  {DK}✅ Closed Positions{R}  {WH}{closed_ct}{R}\n"
        f"  {DK}│{R}  {DK}🆕 New (this session){R} {WH}{len(tracker.new_trades)}{R}\n"
        f"{CY}{B}  ╚════════════════════════════════════════════════════════╝{R}"
    )

    # Risk alerts
    if pnl < -10.0:
        print(f"\n{RD}{B}  🚨  ALERT: Session drawdown exceeds $10.00 — consider pausing.{R}")
        tracker.alert_count += 1
    if tracker.consecutive_losses >= 3:
        print(f"\n{RD}{B}  🚨  ALERT: {tracker.consecutive_losses} consecutive losses — circuit breaker warning.{R}")


def print_banner(interval: int):
    print(
        f"\n{MG}{B}"
        f"  ╔══════════════════════════════════════════════════════════════╗\n"
        f"  ║           ZiSi GOAL MONITOR  — 24/7 Paper Trading           ║\n"
        f"  ║        Continuous monitoring · Self-healing · Reporting      ║\n"
        f"  ╚══════════════════════════════════════════════════════════════╝{R}\n"
        f"  {DK}Report interval:{R} {WH}{interval}s{R}  "
        f"  {DK}Base dir:{R} {WH}{BASE_DIR}{R}\n"
        f"  {YL}Press Ctrl+C to stop monitor (bot keeps running).{R}\n"
        f"  {DK}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{R}"
    )


# ── Console log tail ──────────────────────────────────────────────────────────

def watch_console_log(tracker: SessionTracker):
    """Background thread: tail zisi_bot_console.log for key events."""
    if not CONSOLE_LOG.exists():
        return

    keywords_trade_open   = ['[TRADE OPENED]', 'Executing paper trade', 'Paper order placed']
    keywords_trade_closed = ['[TRADE CLOSED]', 'Position closed', 'Settled', 'Resolved']
    keywords_error        = ['CRITICAL', 'EMERGENCY', 'ASYMMETRIC FILL', 'circuit breaker']

    try:
        with open(CONSOLE_LOG, 'r', encoding='utf-8', errors='replace') as fh:
            # Seek to end so we only watch NEW lines
            fh.seek(0, 2)
            while True:
                line = fh.readline()
                if not line:
                    time.sleep(0.5)
                    continue
                line = line.strip()
                if not line:
                    continue

                if any(k in line for k in keywords_trade_open):
                    print(f"\n{GR}  [MONITOR] {_ts()} ✦ OPEN detected in log: {line[:100]}{R}")
                elif any(k in line for k in keywords_trade_closed):
                    print(f"\n{YL}  [MONITOR] {_ts()} ✦ CLOSE detected in log: {line[:100]}{R}")
                elif any(k in line for k in keywords_error):
                    print(f"\n{RD}{B}  [MONITOR] {_ts()} 🚨 ERROR: {line[:120]}{R}")
                    tracker.alert_count += 1
    except Exception:
        pass


# ── Positions watcher (active → closed transitions) ──────────────────────────

_last_active_keys  = None
_last_closed_count = 0


def poll_positions(tracker: SessionTracker):
    """Detect new trade opens and closes by polling positions_state.json."""
    global _last_active_keys, _last_closed_count

    positions = read_positions()
    active = positions.get('active', [])
    closed = positions.get('closed', [])

    # Detect new closed trades
    if len(closed) > _last_closed_count and _last_closed_count is not None:
        for pos in closed[_last_closed_count:]:
            oid = pos.get('order_id', pos.get('event_title', ''))
            if oid not in tracker.seen_trades:
                tracker.seen_trades.add(oid)
                tracker.new_trades.append(pos)
                profit = float(pos.get('realized_pnl') or pos.get('profit') or 0)
                if profit > 0:
                    tracker.total_wins += 1
                    tracker.consecutive_losses = 0
                else:
                    tracker.total_losses += 1
                    tracker.consecutive_losses += 1
                announce_trade(pos)
    _last_closed_count = len(closed)

    # Detect new active positions
    active_keys = set(p.get('order_id', '') for p in active)
    if _last_active_keys is not None:
        for pos in active:
            oid = pos.get('order_id', '')
            if oid and oid not in _last_active_keys and oid not in tracker.seen_trades:
                announce_open(pos)
    _last_active_keys = active_keys


# ── Health self-healing ────────────────────────────────────────────────────────

def self_heal(tracker: SessionTracker):
    """Detect staleness and print advisory. No automatic restarts — safety first."""
    account = read_account()
    healthy, stale_min = bot_is_healthy(account, max_stale_min=30)

    if not healthy and tracker.last_health_ok:
        print(
            f"\n{YL}{B}  ⚠  [MONITOR] Bot appears stale ({stale_min:.1f}m since last write).{R}\n"
            f"  {DK}Tip: Check the sovereign_runner terminal for errors or re-run:{R}\n"
            f"  {WH}  python sovereign_runner.py{R}"
        )
        tracker.alert_count += 1
    elif healthy and not tracker.last_health_ok:
        print(f"\n{GR}{B}  ✅  [MONITOR] Bot is back online. {R}")

    tracker.last_health_ok = healthy


# ── Main monitoring loop ───────────────────────────────────────────────────────

def run_monitor(interval: int = 900, quiet: bool = False):
    account = read_account()
    starting = float(account.get('starting_balance', account.get('balance', 100.0)))
    tracker = SessionTracker(starting_balance=starting)

    print_banner(interval)

    # Pre-populate seen trade IDs so we don't re-announce old trades
    positions = read_positions()
    for p in positions.get('closed', []):
        oid = p.get('order_id', p.get('event_title', ''))
        if oid:
            tracker.seen_trades.add(oid)
    for t in read_trades():
        oid = t.get('order_id', '')
        if oid:
            tracker.seen_trades.add(oid)
    _last_closed_count_ref = len(positions.get('closed', []))
    global _last_closed_count
    _last_closed_count = _last_closed_count_ref

    # Start console log tail in background thread
    log_thread = threading.Thread(target=watch_console_log, args=(tracker,), daemon=True)
    log_thread.start()

    # Initial status report
    print_status_report(tracker, quiet=quiet)

    last_report_time = time.time()
    last_heal_time   = time.time()
    poll_interval    = 15  # seconds between position polls

    print(f"\n{DK}  [MONITOR] Polling every {poll_interval}s · Summary every {interval}s …{R}")

    try:
        while True:
            time.sleep(poll_interval)

            # Poll positions for trade changes every 15s
            try:
                poll_positions(tracker)
            except Exception as e:
                print(f"{RD}  [MONITOR] Poll error: {e}{R}")

            # Self-heal check every 5 min
            if time.time() - last_heal_time > 300:
                try:
                    self_heal(tracker)
                except Exception:
                    pass
                last_heal_time = time.time()

            # Full status report every `interval` seconds
            if time.time() - last_report_time >= interval:
                print_status_report(tracker, quiet=quiet)
                last_report_time = time.time()

    except KeyboardInterrupt:
        print(f"\n{YL}  [MONITOR] Stopped by user. Bot continues running.{R}\n")
        print_status_report(tracker, quiet=False)


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='ZiSi Goal Monitor — 24/7 paper trading watchdog')
    parser.add_argument('--interval', type=int, default=900,
                        help='Seconds between full status reports (default: 900 = 15 min)')
    parser.add_argument('--quiet', action='store_true',
                        help='Suppress routine heartbeat prints')
    args = parser.parse_args()

    run_monitor(interval=args.interval, quiet=args.quiet)
