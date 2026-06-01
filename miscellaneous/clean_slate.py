"""
clean_slate.py - Reset ZiSi bot state to a fresh trading baseline.

Usage:
    python clean_slate.py                      # interactive reset
    python clean_slate.py --force              # non-interactive reset
    python clean_slate.py --balance 100        # reset with specific starting balance
    python clean_slate.py --archive            # archive session then reset (interactive)
    python clean_slate.py --archive --force    # archive session then reset
    python clean_slate.py --archive-only       # archive only, no reset
    python clean_slate.py --archive-only --label session5_baseline
"""
import argparse
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
ARCHIVE_ROOT = BASE_DIR / "miscellaneous" / "archive"
POSITIONS_FILE = BASE_DIR / "infrastructure" / "exchange" / "positions_state.json"

# Files/dirs to copy into each session archive (relative to BASE_DIR)
ARCHIVE_PATHS = [
    "infrastructure/exchange/positions_state.json",
    "account_state.json",
    "runtime_tracking.json",
    "diagnostics_state.json",
    "infrastructure/state/diagnostics_state.json",
    "system_alerts.json",
    "signal_queue.json",
    "calibration_state.json",
    "markov_state.json",
    "category_suspensions.json",
    "macro_context.json",
    "zisi_local_trades.jsonl",
    "ml_labelled_outcomes.jsonl",
    "balance_history.jsonl",
]

ARCHIVE_GLOBS = ["metrics_*.json"]


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _summarize_positions(positions_path: Path) -> dict:
    data = _read_json(positions_path)
    summary = data.get("summary") or {}
    closed = data.get("closed") or []
    entries = [float(t.get("entry_price", 0) or 0) for t in closed]
    dual_n = sum(1 for t in closed if "DUAL" in (t.get("event_title") or ""))
    avg_entry = round(sum(entries) / len(entries), 4) if entries else 0.0
    return {
        "trades": len(closed),
        "wins": int(summary.get("win_count", 0)),
        "losses": int(summary.get("loss_count", 0)),
        "win_rate": round(
            int(summary.get("win_count", 0)) / max(1, len(closed)), 4
        ),
        "total_pnl": float(summary.get("realized_pnl", 0) or 0),
        "avg_entry_price": avg_entry,
        "dual_trades": dual_n,
        "dual_pct": round(dual_n / max(1, len(closed)), 4),
    }


def archive_session(label: str | None = None, notes: str = "") -> Path:
    """
    Copy current session artifacts into archive/sessionN_YYYY-MM-DD[_label]/.
    Returns the archive directory path.
    """
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H%M%S")

    if label:
        safe = re.sub(r"[^\w\-]+", "_", label.strip())[:48]
        folder_name = f"session_{date_str}_{safe}"
    else:
        existing = list(ARCHIVE_ROOT.glob("session_*")) if ARCHIVE_ROOT.exists() else []
        n = len(existing) + 1
        folder_name = f"session{n}_{date_str}_archived_{time_str}"

    dest = ARCHIVE_ROOT / folder_name
    dest.mkdir(parents=True, exist_ok=True)

    copied = []
    for rel in ARCHIVE_PATHS:
        src = BASE_DIR / rel
        if src.exists():
            dst = dest / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            copied.append(rel)

    metrics_dir = dest / "metrics"
    metrics_dir.mkdir(exist_ok=True)
    for pattern in ARCHIVE_GLOBS:
        for src in BASE_DIR.glob(pattern):
            shutil.copy2(src, metrics_dir / src.name)
            copied.append(f"metrics/{src.name}")

    pos_stats = _summarize_positions(POSITIONS_FILE)
    acc = _read_json(BASE_DIR / "account_state.json")

    manifest = {
        "session_id": folder_name,
        "label": label or folder_name,
        "archived_at": now.isoformat(),
        "notes": notes,
        "account": {
            "balance": acc.get("balance"),
            "starting_balance": acc.get("starting_balance"),
            "pnl": acc.get("pnl"),
        },
        "performance": pos_stats,
        "files_copied": copied,
    }
    (dest / "session_manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )

    print("=" * 70)
    print("ZiSi SESSION ARCHIVE")
    print("=" * 70)
    print(f"  Destination: {dest}")
    print(f"  Trades:      {pos_stats.get('trades', 0)}")
    print(f"  Win rate:    {pos_stats.get('win_rate', 0) * 100:.1f}%")
    print(f"  Total PnL:   ${pos_stats.get('total_pnl', 0):+.2f}")
    print(f"  Avg entry:   {pos_stats.get('avg_entry_price', 0) * 100:.1f}c")
    print(f"  Files:       {len(copied)} copied")
    print("=" * 70)
    return dest


def _backup_to_archive_folder(dest: Path, rel: str) -> None:
    src = BASE_DIR / rel
    if src.exists():
        dst = dest / f"{Path(rel).name}.pre_reset.bak"
        shutil.copy2(src, dst)


def clean_slate(
    force: bool = False,
    starting_balance: float | None = None,
    nuke: bool = False,
    pre_archive_dir: Path | None = None,
) -> None:
    print("=" * 70)
    print("ZiSi CLEAN SLATE")
    print("=" * 70)
    print()

    acc = _read_json(BASE_DIR / "account_state.json")
    current_balance = float(acc.get("balance", 100.0))
    if starting_balance is None:
        starting_balance = current_balance

    print(f"  Current balance:  ${current_balance:.2f}")
    print(f"  Reset balance:    ${starting_balance:.2f}")
    if pre_archive_dir:
        print(f"  Archived to:      {pre_archive_dir}")
    print()

    if not force:
        confirm = input("Type 'yes' to proceed: ").strip().lower()
        if confirm != "yes":
            print("Cancelled.")
            return

    now_iso = datetime.now(timezone.utc).isoformat()

    if pre_archive_dir:
        for rel in (
            "infrastructure/exchange/positions_state.json",
            "account_state.json",
            "diagnostics_state.json",
        ):
            _backup_to_archive_folder(pre_archive_dir, rel)

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
    POSITIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    POSITIONS_FILE.write_text(json.dumps(positions_state, indent=2), encoding="utf-8")
    print("[RESET] positions_state.json -> 0 active, 0 closed")

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
    print(f"[RESET] account_state.json -> ${starting_balance:.2f} balance")

    system_alerts = {"alerts": [], "last_updated": now_iso}
    (BASE_DIR / "system_alerts.json").write_text(
        json.dumps(system_alerts, indent=2), encoding="utf-8"
    )
    print("[RESET] system_alerts.json -> 0 alerts")

    for diag_rel in ("diagnostics_state.json", "infrastructure/state/diagnostics_state.json"):
        diag_file = BASE_DIR / diag_rel
        diag_file.parent.mkdir(parents=True, exist_ok=True)
        diag_state = {
            "latency_history": [],
            "slippage_history": [],
            "asymmetric_fills": 0,
            "circuit_breaker_active": False,
            "avg_latency_ms": 0.0,
            "avg_slippage_cents": 0.0,
            "last_updated": now_iso,
        }
        diag_file.write_text(json.dumps(diag_state, indent=2), encoding="utf-8")
    print("[RESET] diagnostics_state.json -> clean slate")

    sq = BASE_DIR / "signal_queue.json"
    if sq.exists():
        sq.write_text(json.dumps({"signals": [], "last_updated": now_iso}, indent=2), encoding="utf-8")
        print("[RESET] signal_queue.json -> empty")

    # Always reset ML and edge status to avoid residual session pollution
    ml_progress = {
        "cycles_collected": 0,
        "cycles_needed": 50,
        "progress_percent": 0.0,
        "models": {},
        "last_updated": now_iso
    }
    (BASE_DIR / "ml_progress.json").write_text(json.dumps(ml_progress, indent=2), encoding="utf-8")
    print("[RESET] ml_progress.json -> 0 cycles")

    edge_status = {
        "total_evaluated": 0,
        "total_passed": 0,
        "total_filtered": 0,
        "pass_rate": 0.0,
        "kl_threshold": 0.05,
        "last_updated": now_iso
    }
    (BASE_DIR / "edge_status.json").write_text(json.dumps(edge_status, indent=2), encoding="utf-8")
    print("[RESET] edge_status.json -> clean slate")

    regime_status = {
        "regime": "NORMAL",
        "label": "Normal",
        "atr_pct": 0.0,
        "kelly_multiplier": 1.0,
        "last_updated": now_iso
    }
    (BASE_DIR / "regime_status.json").write_text(json.dumps(regime_status, indent=2), encoding="utf-8")
    print("[RESET] regime_status.json -> NORMAL")

    # Always delete or truncate history/log files so the graph starts completely clean
    history_files = [
        BASE_DIR / "zisi_local_trades.jsonl",
        BASE_DIR / "ml_labelled_outcomes.jsonl",
        BASE_DIR / "balance_history.jsonl",
        BASE_DIR / "infrastructure" / "state" / "balance_history.jsonl"
    ]
    for f in history_files:
        if f.exists():
            try:
                f.unlink()
                print(f"[RESET] Deleted history file: {f.name} ({f.relative_to(BASE_DIR)})")
            except Exception as e:
                print(f"[WARNING] Could not delete {f.name}: {e}")

    print()
    print("=" * 70)
    print("[OK] CLEAN SLATE COMPLETE")
    print(f"     Balance: ${starting_balance:.2f}")
    print("     Restart the bot to begin a fresh measurement session.")
    print("=" * 70)


def main() -> None:
    parser = argparse.ArgumentParser(description="ZiSi clean slate and session archive")
    parser.add_argument("--force", action="store_true", help="Skip confirmation")
    parser.add_argument("--nuke", action="store_true", help="Delete JSONL history files")
    parser.add_argument("--balance", type=float, default=None, help="Starting balance after reset")
    parser.add_argument("--archive", action="store_true", help="Archive current session before reset")
    parser.add_argument("--archive-only", action="store_true", help="Archive only; do not reset")
    parser.add_argument(
        "--label",
        type=str,
        default=None,
        help="Archive folder label (e.g. session5_baseline_pre_optimization)",
    )
    parser.add_argument("--notes", type=str, default="", help="Notes stored in session_manifest.json")
    args = parser.parse_args()

    if args.nuke:
        args.force = True

    archive_dir = None
    if args.archive or args.archive_only:
        label = args.label
        if not label and args.archive_only:
            label = "manual_archive"
        if not label and args.archive:
            label = "pre_clean_slate"
        archive_dir = archive_session(label=label, notes=args.notes)

    if args.archive_only:
        return

    clean_slate(
        force=args.force,
        starting_balance=args.balance,
        nuke=args.nuke,
        pre_archive_dir=archive_dir,
    )


if __name__ == "__main__":
    main()
