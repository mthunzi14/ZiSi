#!/usr/bin/env python
"""Debug script to verify bot is trading and updating balance"""

import json
import os
from datetime import datetime, timezone


def check_account_state():
    """Check if account balance is being updated"""
    state_file = "account_state.json"

    if not os.path.exists(state_file):
        print("x account_state.json doesn't exist")
        return False

    with open(state_file, "r") as f:
        state = json.load(f)

    balance = state["balance"]
    last_updated = state["last_updated"]
    reason = state["last_change_reason"]

    print(f"Account Balance: ${balance:.2f}")
    print(f"Last Updated:    {last_updated}")
    print(f"Reason:          {reason}")

    last_update_time = datetime.fromisoformat(last_updated.replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)
    minutes_ago = (now - last_update_time).total_seconds() / 60

    if minutes_ago < 10:
        print(f"[OK] Recently updated ({minutes_ago:.1f} min ago)")
        return True
    else:
        print(f"[!!] NOT recently updated ({minutes_ago:.1f} min ago)")
        return False


def check_trades():
    """Check if trades are being logged"""
    trade_file = "zisi_local_trades.jsonl"

    if not os.path.exists(trade_file):
        print("x No trades logged yet")
        return 0

    with open(trade_file, "r") as f:
        lines = [l for l in f.readlines() if l.strip()]

    recent_count = 0
    for line in lines[-10:]:
        trade = json.loads(line)
        if trade.get("timestamp"):
            recent_count += 1

    total = len(lines)
    print(f"Total records logged: {total}")
    print(f"Recent records (last 10 checked): {recent_count}")

    if lines:
        last = json.loads(lines[-1])
        print(f"Last record type: {last.get('type', 'trade')} @ {last.get('timestamp', last.get('exit_timestamp', '?'))}")

    if total > 0:
        print("[OK] Bot has logged activity")
    else:
        print("[!!] No activity logged")
    return total


def check_console_log():
    """Check console log for recent errors"""
    log_file = "zisi_bot_console.log"

    if not os.path.exists(log_file):
        print("x Console log doesn't exist")
        return

    with open(log_file, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()

    errors = [l for l in lines[-50:] if "[ERROR]" in l or "[WARNING]" in l]

    if errors:
        print(f"Recent warnings/errors ({len(errors)} in last 50 lines):")
        for err in errors[-5:]:
            print(f"  {err.strip()}")
    else:
        print("[OK] No errors in last 50 log lines")


if __name__ == "__main__":
    print("=" * 60)
    print("ZiSi Bot Debug Check")
    print(f"Run at: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 60)

    print("\n1. Account State:")
    check_account_state()

    print("\n2. Trading Activity:")
    check_trades()

    print("\n3. Console Log:")
    check_console_log()

    print("\n" + "=" * 60)
