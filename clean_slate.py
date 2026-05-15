"""
clean_slate.py - Reset ZiSi bot state to a fresh trading baseline.

Resets positions, alerts, and optionally balance without deleting trade history
(which is needed for ML training and PnL verification).

Usage:
    python clean_slate.py                 # interactive mode (confirms before resetting)
    python clean_slate.py --force         # non-interactive reset
    python clean_slate.py --balance 100   # reset with specific starting balance
"""
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).parent


def _backup(fname: str) -> None:
    src = BASE_DIR / fname
    if src.exists():
        dst = BASE_DIR / f"{fname}.bak"
        shutil.copy(src, dst)
        print(f"  [BACKUP] {fname} → {fname}.bak")


def _read_current_balance() -> float:
    """Return current balance from account_state.json, or 100.0 if not found."""
    acc = BASE_DIR / "account_state.json"
    if acc.exists():
        try:
            return float(json.loads(acc.read_text(encoding="utf-8")).get("balance", 100.0))
        except Exception:
            pass
    return 100.0


def clean_slate(force: bool = False, starting_balance: float = None) -> None:
    print("=" * 70)
    print("ZiSi CLEAN SLATE")
    print("=" * 70)
    print()

    current_balance = _read_current_balance()
    if starting_balance is None:
        starting_balance = current_balance

    print(f"  Current balance:  ${current_balance:.2f}")
    print(f"  Reset balance:    ${starting_balance:.2f}")
    print()
    print("  Files that will be RESET:")
    print("    positions_state.json  → empty (no open/closed positions)")
    print("    account_state.json    → clean slate")
    print("    system_alerts.json    → no alerts")
    print()
    print("  Files that will NOT be touched:")
    print("    zisi_local_trades.jsonl        (trade history for ML + PnL)")
    print("    ml_labelled_outcomes.jsonl     (ML training data)")
    print("    balance_history.jsonl          (equity curve)")
    print("    category_win_rates.json        (Kalshi performance data)")
    print()

    if not force:
        confirm = input("Type 'yes' to proceed: ").strip().lower()
        if confirm != "yes":
            print("Cancelled.")
            return

    now_iso = datetime.now(timezone.utc).isoformat()

    # ── 1. positions_state.json ────────────────────────────────────────────────
    _backup("positions_state.json")
    positions_state = {
        "last_updated": now_iso,
        "source": "polymarket+kalshi",
        "summary": {
            "active_count": 0,
            "poly_active": 0,
            "kalshi_active": 0,
            "closed_count": 0,
            "unrealized_pnl": 0,
            "realized_pnl": 0,
            "win_count": 0,
            "loss_count": 0,
        },
        "active": [],
        "closed": [],
    }
    (BASE_DIR / "positions_state.json").write_text(
        json.dumps(positions_state, indent=2), encoding="utf-8"
    )
    print("[RESET] positions_state.json → 0 active, 0 closed")

    # ── 2. account_state.json ──────────────────────────────────────────────────
    _backup("account_state.json")
    account_state = {
        "balance": round(starting_balance, 2),
        "starting_balance": round(starting_balance, 2),
        "last_updated": now_iso,
        "trades_executed": 0,
        "phase": "phase_1",
        "paused": False,
        "status": "running",
        "pnl": 0.00,
        "total_pnl": 0.00,
    }
    (BASE_DIR / "account_state.json").write_text(
        json.dumps(account_state, indent=2), encoding="utf-8"
    )
    print(f"[RESET] account_state.json → ${starting_balance:.2f} balance")

    # ── 3. system_alerts.json ──────────────────────────────────────────────────
    _backup("system_alerts.json")
    system_alerts = {"alerts": [], "last_updated": now_iso}
    (BASE_DIR / "system_alerts.json").write_text(
        json.dumps(system_alerts, indent=2), encoding="utf-8"
    )
    print("[RESET] system_alerts.json → 0 alerts")

    # ── 4. signal_queue.json ───────────────────────────────────────────────────
    sq = BASE_DIR / "signal_queue.json"
    if sq.exists():
        _backup("signal_queue.json")
        sq.write_text(json.dumps({"signals": [], "last_updated": now_iso}, indent=2), encoding="utf-8")
        print("[RESET] signal_queue.json → empty")

    # ── 5. Remove old .bak files from previous resets ─────────────────────────
    for bak in BASE_DIR.glob("*.bak"):
        try:
            bak.unlink()
            print(f"[CLEAN] Removed old backup: {bak.name}")
        except Exception:
            pass

    print()
    print("=" * 70)
    print("[OK] CLEAN SLATE COMPLETE")
    print(f"     Balance: ${starting_balance:.2f} | Mode: paper_trading")
    print("     Restart the bot to begin fresh trading cycle.")
    print("=" * 70)


if __name__ == "__main__":
    force = "--force" in sys.argv
    balance = None
    for i, arg in enumerate(sys.argv):
        if arg == "--balance" and i + 1 < len(sys.argv):
            try:
                balance = float(sys.argv[i + 1])
            except ValueError:
                print(f"Invalid balance: {sys.argv[i + 1]}")
                sys.exit(1)

    clean_slate(force=force, starting_balance=balance)
