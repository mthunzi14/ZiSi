"""
state_manager.py - ZiSi Bot Account State Persistence
Saves account balance to disk so it survives restarts.
"""

import json
import logging
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("zisi.state")

_STATE_FILE       = Path(__file__).parent.parent.parent / "account_state.json"
_POSITIONS_FILE   = Path(__file__).parent.parent / "exchange" / "positions_state.json"
_DEFAULT_BALANCE  = 100.0
_lock             = threading.Lock()
GLOBAL_POSITIONS_LOCK = threading.Lock()
_balance: float          = _DEFAULT_BALANCE
_starting_balance: float = _DEFAULT_BALANCE


def _read_starting_balance() -> float:
    """Read starting_balance from account_state.json, fall back to _DEFAULT_BALANCE."""
    try:
        if _STATE_FILE.exists():
            data = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
            return float(data.get("starting_balance", _DEFAULT_BALANCE))
    except Exception:
        pass
    return _DEFAULT_BALANCE


def _balance_from_positions() -> float | None:
    """
    Derive the correct account balance from positions_state.json.
    Returns None if the file is missing or unreadable.
    Uses starting_balance from account_state.json so clean_slate resets are respected.
    """
    if not _POSITIONS_FILE.exists():
        return None
    try:
        with GLOBAL_POSITIONS_LOCK:
            pos     = json.loads(_POSITIONS_FILE.read_text(encoding="utf-8"))
        summary = pos.get("summary") or {}
        realized_pnl = float(summary.get("realized_pnl", 0) or 0)
        return round(_starting_balance + realized_pnl, 2)
    except Exception:
        return None



def initialize_state() -> float:
    """Load account balance from disk, then reconcile with positions_state.json."""
    global _balance, _starting_balance
    with _lock:
        disk_balance = _DEFAULT_BALANCE
        if _STATE_FILE.exists():
            try:
                data = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
                disk_balance = float(data["balance"])
                _starting_balance = float(data.get("starting_balance", _DEFAULT_BALANCE))
            except (KeyError, ValueError, json.JSONDecodeError, OSError) as exc:
                log.warning(
                    "Corrupted state file (%s) — resetting to default $%.2f",
                    exc, _DEFAULT_BALANCE,
                )

        # Always reconcile against positions_state.json — it is the authoritative
        # source of truth. If the disk value diverges by more than 5%, use the
        # positions-computed value to prevent drift from removed markets (e.g. Kalshi).
        computed = _balance_from_positions()
        if computed is not None:
            gap_pct = abs(disk_balance - computed) / max(1.0, abs(computed))
            if gap_pct > 0.05:
                log.warning(
                    "[STATE] Balance mismatch on init: disk=$%.2f vs positions=$%.2f "
                    "(%.1f%% gap) — using positions value",
                    disk_balance, computed, gap_pct * 100,
                )
                _balance = computed
            else:
                _balance = disk_balance
        else:
            _balance = disk_balance

        _write_state("Initialized — reconciled with positions_state")
        log.info("Account state initialized: $%.2f", _balance)
        return _balance


def update_balance(new_balance: float, reason: str = "") -> None:
    """Save updated balance to disk and update in-memory value."""
    global _balance
    with _lock:
        _balance = round(new_balance, 2)
        _write_state(reason)
    log.info(
        "Account balance updated: $%.2f%s",
        _balance, f" ({reason})" if reason else "",
    )


def get_current_balance() -> float:
    """Return the authoritative balance derived from positions_state.json.

    Falls back to the in-memory value only if positions_state.json is unavailable.
    This prevents any caller from seeing the stale accumulated value.
    """
    computed = _balance_from_positions()
    return computed if computed is not None else _balance


def reset_account(to_amount: float = 100.0) -> None:
    """Reset account to specified amount (emergency use only)."""
    global _balance
    with _lock:
        _balance = round(to_amount, 2)
        _write_state("Manual account reset")
    log.warning("ACCOUNT RESET TO $%.2f", _balance)


def update_heartbeat(trades_executed: int = 0, paused: bool = False, reason: str = "heartbeat") -> None:
    """Write timestamp every bot cycle so the dashboard can detect liveness."""
    global _balance
    with _lock:
        existing: dict = {}
        if _STATE_FILE.exists():
            try:
                existing = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass
        starting = float(existing.get("starting_balance", _DEFAULT_BALANCE))

        # Always derive from positions_state.json — prevents stale in-memory drift
        computed = _balance_from_positions()
        if computed is not None:
            _balance = computed

        existing["balance"]             = _balance
        existing["pnl"]                 = round(_balance - starting, 2)
        existing["trades_executed"]     = trades_executed
        existing["paused"]              = paused
        existing["last_updated"]        = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        existing["last_change_reason"]  = reason
        _STATE_FILE.write_text(json.dumps(existing, indent=2), encoding="utf-8")
        _record_history(_balance, round(_balance - starting, 2))


def get_progress_toward_phase2() -> dict:
    """Return trade collection progress (20 trades = logistic regression upgrade threshold)."""
    if _STATE_FILE.exists():
        try:
            data = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
            trades = int(data.get("trades_executed", 0))
        except Exception:
            trades = 0
    else:
        trades = 0

    return {
        "trades_collected": trades,
        "trades_needed": 20,
        "progress_percent": min(int((trades / 20) * 100), 100),
        "phase": "phase_1",
        "ready_for_phase2": trades >= 20,
    }


def _get_trades_count() -> int:
    try:
        if not _POSITIONS_FILE.exists():
            return 0
        with GLOBAL_POSITIONS_LOCK:
            pos = json.loads(_POSITIONS_FILE.read_text(encoding="utf-8"))
        summary = pos.get("summary") or {}
        return int(summary.get("closed_count", 0))
    except Exception:
        return 0


def _record_history(balance: float, pnl: float) -> None:
    try:
        import sys
        import os
        from pathlib import Path
        root = Path(__file__).parent.parent.parent
        sys.path.insert(0, str(root))
        from infrastructure.state.balance_history import record_balance
        trades = _get_trades_count()
        record_balance(balance, pnl, trades)
    except Exception as e:
        log.warning("[STATE] Failed to record balance history: %s", e)


def _write_state(reason: str = "") -> None:
    global _balance
    # Merge with existing file so we never lose fields written by other functions
    existing: dict = {}
    if _STATE_FILE.exists():
        try:
            existing = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    starting = float(existing.get("starting_balance", _starting_balance))

    # Derive balance from positions_state.json (single source of truth).
    # Keeps disk in sync even if _balance drifted due to a removed market.
    computed = _balance_from_positions()
    if computed is not None:
        _balance = computed   # keep in-memory value authoritative too

    existing["balance"] = _balance
    existing["pnl"]     = round(_balance - starting, 2)
    existing["last_updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    existing["last_change_reason"] = reason
    _STATE_FILE.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    _record_history(_balance, round(_balance - starting, 2))


# ── Runtime tracking ─────────────────────────────────────────────────────────

_RUNTIME_FILE = Path(__file__).parent.parent.parent / "runtime_tracking.json"
_PHASE1_GOAL_HOURS = 336  # 14 days × 24 hours


def initialize_runtime_tracking() -> bool:
    """
    Create runtime_tracking.json on bot start if it doesn't exist.
    If it exists but is missing 'start_time', auto-repair/populate it.
    Returns True if a new file was created, False if it already existed.
    """
    if _RUNTIME_FILE.exists():
        try:
            data = json.loads(_RUNTIME_FILE.read_text(encoding="utf-8"))
            if "start_time" not in data or not data["start_time"]:
                data["start_time"] = datetime.now(timezone.utc).isoformat()
                _RUNTIME_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
                log.info("[RUNTIME] Repaired missing start_time in tracking file")
        except Exception as e:
            log.warning("[RUNTIME] Failed to repair start_time: %s", e)
        log.info("[RUNTIME] Tracking file found — resuming runtime timer")
        return False

    now = datetime.now(timezone.utc)
    data = {
        "start_time": now.isoformat(),
        "phase": "phase_1",
        "goal_hours": _PHASE1_GOAL_HOURS,
        "target_completion": (now + timedelta(hours=_PHASE1_GOAL_HOURS)).isoformat(),
        "runtime_hours": 0.0,
        "progress_percent": 0.0,
        "status": "tracking",
    }
    _RUNTIME_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    log.info("[RUNTIME] Runtime tracking initialized (%d hour window)", _PHASE1_GOAL_HOURS)
    return True


def update_runtime_tracking() -> dict | None:
    """
    Recalculate elapsed hours from start_time and write back to file.
    Called at the end of every main loop cycle.
    """
    try:
        data = json.loads(_RUNTIME_FILE.read_text(encoding="utf-8"))
        start = datetime.fromisoformat(data["start_time"])
        elapsed = datetime.now(timezone.utc) - start
        hours = elapsed.total_seconds() / 3600
        goal = data.get("goal_hours", _PHASE1_GOAL_HOURS)

        data["runtime_hours"] = round(hours, 2)
        data["progress_percent"] = round((hours / goal) * 100, 1)
        data["last_update"] = datetime.now(timezone.utc).isoformat()

        _RUNTIME_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return data
    except Exception as exc:
        log.warning("[RUNTIME] Update failed: %s", exc)
        return None


def get_runtime_summary() -> dict | None:
    """Return a human-readable runtime summary dict for the dashboard."""
    try:
        data = json.loads(_RUNTIME_FILE.read_text(encoding="utf-8"))
        hours = data.get("runtime_hours", 0.0)
        return {
            "total_hours": round(hours, 2),
            "days": int(hours // 24),
            "hours": int(hours % 24),
            "progress_percent": data.get("progress_percent", 0.0),
            "goal_hours": data.get("goal_hours", _PHASE1_GOAL_HOURS),
            "phase": data.get("phase", "phase_1"),
            "status": "complete" if hours >= data.get("goal_hours", _PHASE1_GOAL_HOURS) else "tracking",
        }
    except Exception as exc:
        log.warning("[RUNTIME] Summary failed: %s", exc)
        return None


# ── Reconciliation helpers ────────────────────────────────────────────────────

def get_open_positions() -> list:
    """Return all active (open) positions from positions_state.json."""
    if not _POSITIONS_FILE.exists():
        return []
    try:
        with GLOBAL_POSITIONS_LOCK:
            data = json.loads(_POSITIONS_FILE.read_text(encoding="utf-8"))
        return data.get("active", [])
    except Exception:
        return []


def get_closed_positions(limit: int | None = None) -> list:
    """Return closed positions from positions_state.json, newest first."""
    if not _POSITIONS_FILE.exists():
        return []
    try:
        with GLOBAL_POSITIONS_LOCK:
            data = json.loads(_POSITIONS_FILE.read_text(encoding="utf-8"))
        closed = data.get("closed", [])
        return closed[:limit] if limit is not None else closed
    except Exception:
        return []


def is_confirmed(position_id: str) -> bool:
    """Return True if this position has been confirmed (marked filled)."""
    if not _POSITIONS_FILE.exists():
        return False
    try:
        with GLOBAL_POSITIONS_LOCK:
            data = json.loads(_POSITIONS_FILE.read_text(encoding="utf-8"))
        for pos in data.get("active", []):
            if pos.get("id") == position_id or pos.get("order_id") == position_id:
                return bool(pos.get("confirmed", False))
    except Exception:
        pass
    return False


def force_confirm(position: dict) -> None:
    """Mark a position as confirmed (ghost fill correction)."""
    if not _POSITIONS_FILE.exists():
        return
    try:
        with GLOBAL_POSITIONS_LOCK:
            data = json.loads(_POSITIONS_FILE.read_text(encoding="utf-8"))
            for pos in data.get("active", []):
                if pos.get("order_id") == position.get("order_id"):
                    pos["confirmed"] = True
                    break
            tmp_path = _POSITIONS_FILE.with_suffix(".tmp")
            tmp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            import os as _os
            _os.replace(tmp_path, _POSITIONS_FILE)
    except Exception as exc:
        log.warning("[STATE] force_confirm failed: %s", exc)
