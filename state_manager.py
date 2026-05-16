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

_STATE_FILE = Path(__file__).parent / "account_state.json"
_DEFAULT_BALANCE = 100.0
_lock = threading.Lock()
_balance: float = _DEFAULT_BALANCE


def initialize_state() -> float:
    """Load account balance from disk or create file with default $100."""
    global _balance
    with _lock:
        if _STATE_FILE.exists():
            try:
                data = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
                _balance = float(data["balance"])
                log.info(
                    "Account state initialized: $%.2f (loaded from %s)",
                    _balance, _STATE_FILE.name,
                )
                return _balance
            except (KeyError, ValueError, json.JSONDecodeError, OSError) as exc:
                log.warning(
                    "Corrupted state file (%s) — resetting to default $%.2f",
                    exc, _DEFAULT_BALANCE,
                )

        _balance = _DEFAULT_BALANCE
        _write_state("Initialized with default balance")
        log.info("Account state initialized: $%.2f (new file created)", _balance)
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
    """Return the current in-memory account balance."""
    return _balance


def reset_account(to_amount: float = 100.0) -> None:
    """Reset account to specified amount (emergency use only)."""
    global _balance
    with _lock:
        _balance = round(to_amount, 2)
        _write_state("Manual account reset")
    log.warning("ACCOUNT RESET TO $%.2f", _balance)


def update_heartbeat(trades_executed: int = 0, paused: bool = False, reason: str = "heartbeat") -> None:
    """Write timestamp every bot cycle so the dashboard can detect liveness.

    Call this at the end of every main-loop cycle, not just on trades.
    """
    with _lock:
        # Read existing state so we don't overwrite fields we don't own
        existing: dict = {}
        if _STATE_FILE.exists():
            try:
                existing = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass
        starting = float(existing.get("starting_balance", _DEFAULT_BALANCE))
        existing["balance"] = _balance
        existing["pnl"] = round(_balance - starting, 2)   # always correct
        existing["trades_executed"] = trades_executed
        existing["paused"] = paused
        existing["last_updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        existing["last_change_reason"] = reason
        _STATE_FILE.write_text(json.dumps(existing, indent=2), encoding="utf-8")


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


def _write_state(reason: str = "") -> None:
    # Merge with existing file so we never lose fields written by other functions
    existing: dict = {}
    if _STATE_FILE.exists():
        try:
            existing = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    starting = float(existing.get("starting_balance", _DEFAULT_BALANCE))
    existing["balance"] = _balance
    existing["pnl"] = round(_balance - starting, 2)   # always correct — never accumulated
    existing["last_updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    existing["last_change_reason"] = reason
    _STATE_FILE.write_text(json.dumps(existing, indent=2), encoding="utf-8")


# ── Runtime tracking ─────────────────────────────────────────────────────────

_RUNTIME_FILE = Path(__file__).parent / "runtime_tracking.json"
_PHASE1_GOAL_HOURS = 336  # 14 days × 24 hours


def initialize_runtime_tracking() -> bool:
    """
    Create runtime_tracking.json on bot start if it doesn't exist.
    Returns True if a new file was created, False if it already existed.
    """
    if _RUNTIME_FILE.exists():
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
