"""
balance_history.py
Appends a timestamped balance snapshot to balance_history.jsonl after every
balance sync. Used by the dashboard equity chart (/api/equity).

Each line: {"timestamp": ISO, "balance": float, "pnl": float, "trades": int}
"""
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("zisi.balance_history")

_HISTORY_FILE = Path(__file__).parent / "balance_history.jsonl"
_ROOT_HISTORY_FILE = Path(__file__).parent.parent.parent / "balance_history.jsonl"

# Minimum time (seconds) between appends — prevents duplicate points
# when sync_balance_to_state() is called multiple times per cycle.
_MIN_INTERVAL_SECONDS = 60  # 1 minute

_last_append_ts: float = 0.0
_last_trades: int = -1


def record_balance(balance: float, pnl: float, trades: int) -> None:
    """
    Append a balance snapshot if enough time has passed since the last one,
    or immediately if the number of closed trades has changed.
    """
    global _last_append_ts, _last_trades

    now_ts = datetime.now(timezone.utc).timestamp()
    if trades != _last_trades or now_ts - _last_append_ts >= _MIN_INTERVAL_SECONDS:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "balance":   round(float(balance), 4),
            "pnl":       round(float(pnl), 4),
            "trades":    int(trades),
        }
        
        # Write to both state file and root file to prevent dashboard data gaps
        for filepath in (_HISTORY_FILE, _ROOT_HISTORY_FILE):
            try:
                with open(filepath, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(record) + "\n")
            except Exception as exc:
                log.warning("[EQUITY] Failed to write balance snapshot to %s: %s", filepath.name, exc)
                
        _last_append_ts = now_ts
        _last_trades = trades
        log.debug("[EQUITY] Snapshot: $%.2f | P&L: $%+.2f | trades: %d", balance, pnl, trades)


def load_history() -> list:
    """Return all historical snapshots as a list of dicts from whichever file is available."""
    for filepath in (_HISTORY_FILE, _ROOT_HISTORY_FILE):
        if not filepath.exists():
            continue
        records = []
        try:
            with open(filepath, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        try:
                            records.append(json.loads(line))
                        except Exception:
                            pass
            if records:
                return records
        except Exception as exc:
            log.warning("[EQUITY] Failed to read history from %s: %s", filepath.name, exc)
    return []


def prune_history(max_days: int = 30) -> None:
    """Remove entries older than max_days to keep both files trim."""
    records = load_history()
    if not records:
        return
    cutoff_ts = datetime.now(timezone.utc).timestamp() - max_days * 86400
    kept = [
        r for r in records
        if _parse_ts(r.get("timestamp", "")) >= cutoff_ts
    ]
    if len(kept) < len(records):
        for filepath in (_HISTORY_FILE, _ROOT_HISTORY_FILE):
            try:
                with open(filepath, "w", encoding="utf-8") as fh:
                    for r in kept:
                        fh.write(json.dumps(r) + "\n")
                log.info("[EQUITY] Pruned %d old entries in %s (kept %d)", len(records) - len(kept), filepath.name, len(kept))
            except Exception as exc:
                log.warning("[EQUITY] Prune failed for %s: %s", filepath.name, exc)


def _parse_ts(iso: str) -> float:
    try:
        return datetime.fromisoformat(iso).timestamp()
    except Exception:
        return 0.0
