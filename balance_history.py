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

# Minimum time (seconds) between appends — prevents duplicate points
# when sync_balance_to_state() is called multiple times per cycle.
_MIN_INTERVAL_SECONDS = 600  # 10 minutes

_last_append_ts: float = 0.0


def record_balance(balance: float, pnl: float, trades: int) -> None:
    """
    Append a balance snapshot if enough time has passed since the last one.
    Thread-safe for single-writer use (main loop only).
    """
    global _last_append_ts

    now_ts = datetime.now(timezone.utc).timestamp()
    if now_ts - _last_append_ts < _MIN_INTERVAL_SECONDS:
        return  # Too soon — skip to avoid duplicates

    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "balance":   round(float(balance), 4),
        "pnl":       round(float(pnl), 4),
        "trades":    int(trades),
    }
    try:
        with open(_HISTORY_FILE, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
        _last_append_ts = now_ts
        log.debug("[EQUITY] Snapshot: $%.2f | P&L: $%+.2f | trades: %d", balance, pnl, trades)
    except Exception as exc:
        log.warning("[EQUITY] Failed to write balance snapshot: %s", exc)


def load_history() -> list:
    """Return all historical snapshots as a list of dicts."""
    if not _HISTORY_FILE.exists():
        return []
    records = []
    try:
        with open(_HISTORY_FILE, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except Exception:
                        pass
    except Exception as exc:
        log.warning("[EQUITY] Failed to read history: %s", exc)
    return records


def prune_history(max_days: int = 30) -> None:
    """Remove entries older than max_days to keep the file trim."""
    records = load_history()
    if not records:
        return
    cutoff_ts = datetime.now(timezone.utc).timestamp() - max_days * 86400
    kept = [
        r for r in records
        if _parse_ts(r.get("timestamp", "")) >= cutoff_ts
    ]
    if len(kept) < len(records):
        try:
            with open(_HISTORY_FILE, "w", encoding="utf-8") as fh:
                for r in kept:
                    fh.write(json.dumps(r) + "\n")
            log.info("[EQUITY] Pruned %d old entries (kept %d)", len(records) - len(kept), len(kept))
        except Exception as exc:
            log.warning("[EQUITY] Prune failed: %s", exc)


def _parse_ts(iso: str) -> float:
    try:
        return datetime.fromisoformat(iso).timestamp()
    except Exception:
        return 0.0
