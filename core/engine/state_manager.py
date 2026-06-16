"""
state_manager.py - ZiSi Bot Account State Persistence
Saves account balance to disk so it survives restarts.
"""

import json
import logging
import threading
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Tuple

log = logging.getLogger("zisi.state")

_STATE_FILE       = Path(__file__).parent.parent.parent / "data" / "account_state.json"
_POSITIONS_FILE   = Path(__file__).parent.parent.parent / "data" / "positions_state.json"
_DEFAULT_BALANCE  = 50.0

# ── Hybrid Sync/Async Lock for Thread-Safe/Async Concurrency ──────────────────
class AsyncThreadSafeLock:
    def __init__(self):
        self._async_lock = asyncio.Lock()
        self._sync_lock = threading.Lock()

    async def acquire(self):
        await self._async_lock.acquire()
        self._sync_lock.acquire()

    def release(self):
        self._sync_lock.release()
        self._async_lock.release()

    async def __aenter__(self):
        await self.acquire()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.release()

    def __enter__(self):
        self._sync_lock.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._sync_lock.release()

# Global locks
_lock = asyncio.Lock()
GLOBAL_POSITIONS_LOCK = AsyncThreadSafeLock()

# ── In-Memory RAM State Cache ────────────────────────────────────────────────
_STATE_RAM = {
    "balance": _DEFAULT_BALANCE,
    "starting_balance": _DEFAULT_BALANCE,
    "pnl": 0.0,
    "trades_executed": 0,
    "paused": False,
    "active_positions": [],
    "closed_positions": [],
    "last_updated": "",
    "last_change_reason": ""
}

# Legacy module-level variables (kept synchronized)
_balance: float          = _DEFAULT_BALANCE
_starting_balance: float = _DEFAULT_BALANCE

# Positions file change tracking
_last_positions_load_time = 0.0
_last_positions_mtime = 0.0

async def _ensure_positions_loaded() -> None:
    """Ensure in-memory position state is fresh compared to disk modifications."""
    global _last_positions_load_time, _last_positions_mtime
    import time
    now = time.time()
    
    # Throttle stat check to twice per second to prevent event loop delay
    if now - _last_positions_load_time < 0.5:
        return
        
    try:
        if _POSITIONS_FILE.exists():
            mtime = _POSITIONS_FILE.stat().st_mtime
            if mtime != _last_positions_mtime:
                import aiofiles
                async with GLOBAL_POSITIONS_LOCK:
                    async with aiofiles.open(_POSITIONS_FILE, mode='r', encoding='utf-8') as f:
                        content = await f.read()
                    pos = json.loads(content)
                _STATE_RAM["active_positions"] = pos.get("active", [])
                _STATE_RAM["closed_positions"] = pos.get("closed", [])
                _last_positions_mtime = mtime
                
                # Derive balance from active/closed realized pnl
                summary = pos.get("summary") or {}
                realized_pnl = float(summary.get("realized_pnl", 0) or 0)
                _STATE_RAM["balance"] = round(_STATE_RAM["starting_balance"] + realized_pnl, 2)
    except Exception as exc:
        log.warning("[STATE] Failed to check/reload positions: %s", exc)
    _last_positions_load_time = now


async def _read_starting_balance() -> float:
    """Read starting_balance from account_state.json, fall back to _DEFAULT_BALANCE."""
    try:
        if _STATE_FILE.exists():
            import aiofiles
            async with aiofiles.open(_STATE_FILE, mode='r', encoding='utf-8') as f:
                content = await f.read()
            data = json.loads(content)
            return float(data.get("starting_balance", _DEFAULT_BALANCE))
    except Exception:
        pass
    return _DEFAULT_BALANCE


async def _balance_from_positions() -> float | None:
    """
    Derive the correct account balance from positions_state.json.
    Returns None if the file is missing or unreadable.
    """
    if not _POSITIONS_FILE.exists():
        return None
    try:
        import aiofiles
        async with GLOBAL_POSITIONS_LOCK:
            async with aiofiles.open(_POSITIONS_FILE, mode='r', encoding='utf-8') as f:
                content = await f.read()
            pos = json.loads(content)
        summary = pos.get("summary") or {}
        realized_pnl = float(summary.get("realized_pnl", 0) or 0)
        starting = await _read_starting_balance()
        return round(starting + realized_pnl, 2)
    except Exception:
        return None


async def initialize_state() -> float:
    """Load account balance from disk, then reconcile with positions_state.json."""
    global _balance, _starting_balance
    async with _lock:
        disk_balance = _DEFAULT_BALANCE
        if _STATE_FILE.exists():
            try:
                import aiofiles
                async with aiofiles.open(_STATE_FILE, mode='r', encoding='utf-8') as f:
                    content = await f.read()
                data = json.loads(content)
                disk_balance = float(data["balance"])
                _starting_balance = float(data.get("starting_balance", _DEFAULT_BALANCE))
                
                # Update RAM state
                _STATE_RAM["balance"] = disk_balance
                _STATE_RAM["starting_balance"] = _starting_balance
                _STATE_RAM["pnl"] = float(data.get("pnl", 0.0))
                _STATE_RAM["trades_executed"] = int(data.get("trades_executed", 0))
                _STATE_RAM["paused"] = bool(data.get("paused", False))
                _STATE_RAM["last_updated"] = data.get("last_updated", "")
                _STATE_RAM["last_change_reason"] = data.get("last_change_reason", "")
            except (KeyError, ValueError, json.JSONDecodeError, OSError) as exc:
                log.warning(
                    "Corrupted state file (%s) — resetting to default $%.2f",
                    exc, _DEFAULT_BALANCE,
                )

        # Load positions
        if _POSITIONS_FILE.exists():
            try:
                import aiofiles
                async with GLOBAL_POSITIONS_LOCK:
                    async with aiofiles.open(_POSITIONS_FILE, mode='r', encoding='utf-8') as f:
                        content = await f.read()
                    pos = json.loads(content)
                _STATE_RAM["active_positions"] = pos.get("active", [])
                _STATE_RAM["closed_positions"] = pos.get("closed", [])
            except Exception as exc:
                log.warning("Failed to load positions on init: %s", exc)

        computed = await _balance_from_positions()
        if computed is not None:
            gap_pct = abs(disk_balance - computed) / max(1.0, abs(computed))
            if gap_pct > 0.05:
                log.warning(
                    "[STATE] Balance mismatch on init: disk=$%.2f vs positions=$%.2f "
                    "(%.1f%% gap) — using positions value",
                    disk_balance, computed, gap_pct * 100,
                )
                _STATE_RAM["balance"] = computed
            else:
                _STATE_RAM["balance"] = disk_balance
        else:
            _STATE_RAM["balance"] = disk_balance

        _balance = _STATE_RAM["balance"]
        _starting_balance = _STATE_RAM["starting_balance"]

        await _write_state("Initialized — reconciled with positions_state")
        log.info("Account state initialized: $%.2f", _STATE_RAM["balance"])
        return _STATE_RAM["balance"]


async def update_balance(new_balance: float, reason: str = "") -> None:
    """Save updated balance to disk and update in-memory value."""
    global _balance
    async with _lock:
        _STATE_RAM["balance"] = round(new_balance, 2)
        _balance = _STATE_RAM["balance"]
        await _write_state(reason)
    log.info(
        "Account balance updated: $%.2f%s",
        _STATE_RAM["balance"], f" ({reason})" if reason else "",
    )


def get_current_balance() -> float:
    """Return the authoritative balance from RAM state instantly."""
    return _STATE_RAM["balance"]


async def reset_account(to_amount: float = 100.0) -> None:
    """Reset account to specified amount (emergency use only)."""
    global _balance
    async with _lock:
        _STATE_RAM["balance"] = round(to_amount, 2)
        _balance = _STATE_RAM["balance"]
        await _write_state("Manual account reset")
    log.warning("ACCOUNT RESET TO $%.2f", _STATE_RAM["balance"])


async def update_heartbeat(trades_executed: int = 0, paused: bool = False, reason: str = "heartbeat") -> None:
    """Write timestamp every bot cycle so the dashboard can detect liveness."""
    global _balance
    async with _lock:
        existing: dict = {}
        import aiofiles
        if _STATE_FILE.exists():
            try:
                async with aiofiles.open(_STATE_FILE, mode='r', encoding='utf-8') as f:
                    existing = json.loads(await f.read())
            except Exception:
                pass
        starting = float(existing.get("starting_balance", _DEFAULT_BALANCE))

        computed = await _balance_from_positions()
        if computed is not None:
            _STATE_RAM["balance"] = computed
            _balance = computed

        _STATE_RAM["trades_executed"] = trades_executed
        _STATE_RAM["paused"] = paused
        _STATE_RAM["last_updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        _STATE_RAM["last_change_reason"] = reason

        existing["balance"]             = _STATE_RAM["balance"]
        existing["pnl"]                 = round(_STATE_RAM["balance"] - starting, 2)
        existing["trades_executed"]     = trades_executed
        existing["paused"]              = paused
        existing["last_updated"]        = _STATE_RAM["last_updated"]
        existing["last_change_reason"]  = reason
        
        tmp_file = _STATE_FILE.with_suffix(".tmp")
        async with aiofiles.open(tmp_file, mode='w', encoding='utf-8') as f:
            await f.write(json.dumps(existing, indent=2))
        import os
        os.replace(tmp_file, _STATE_FILE)
        _record_history(_STATE_RAM["balance"], round(_STATE_RAM["balance"] - starting, 2))


def get_progress_toward_phase2() -> dict:
    """Return trade collection progress from RAM (20 trades = logistic regression upgrade threshold)."""
    trades = _STATE_RAM["trades_executed"]
    return {
        "trades_collected": trades,
        "trades_needed": 20,
        "progress_percent": min(int((trades / 20) * 100), 100),
        "phase": "phase_1",
        "ready_for_phase2": trades >= 20,
    }


def _get_trades_count() -> int:
    try:
        return len(_STATE_RAM["closed_positions"])
    except Exception:
        return 0


def _record_history(balance: float, pnl: float) -> None:
    try:
        import sys
        from pathlib import Path
        root = Path(__file__).parent.parent.parent
        sys.path.insert(0, str(root))
        from data.balance_history import record_balance
        trades = _get_trades_count()
        record_balance(balance, pnl, trades)
    except Exception as e:
        log.warning("[STATE] Failed to record balance history: %s", e)


async def _write_state(reason: str = "") -> None:
    global _balance
    existing: dict = {}
    import aiofiles
    if _STATE_FILE.exists():
        try:
            async with aiofiles.open(_STATE_FILE, mode='r', encoding='utf-8') as f:
                existing = json.loads(await f.read())
        except Exception:
            pass
    starting = float(existing.get("starting_balance", _starting_balance))

    computed = await _balance_from_positions()
    if computed is not None:
        _STATE_RAM["balance"] = computed
        _balance = computed

    existing["balance"] = _STATE_RAM["balance"]
    existing["pnl"]     = round(_STATE_RAM["balance"] - starting, 2)
    existing["last_updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    existing["last_change_reason"] = reason
    
    tmp_file = _STATE_FILE.with_suffix(".tmp")
    async with aiofiles.open(tmp_file, mode='w', encoding='utf-8') as f:
        await f.write(json.dumps(existing, indent=2))
    import os
    os.replace(tmp_file, _STATE_FILE)
    _record_history(_STATE_RAM["balance"], round(_STATE_RAM["balance"] - starting, 2))


# ── Fire-and-forget background synchronization helper ────────────────────────
async def _bg_sync_state_to_disk(reason: str) -> None:
    try:
        await _write_state(reason)
    except Exception as e:
        log.warning("[STATE] Background write state failed: %s", e)


# ── HFT STATE FIREWALL RESYNC ───────────────────────────────────────────────
async def check_and_reserve_capital(
    asset: str,
    timeframe: str,
    size_requested: float,
    max_total_open: int,
    max_open_per_asset: int
) -> Tuple[bool, float]:
    """Atomically validates and reserves margin capital under GLOBAL_POSITIONS_LOCK."""
    # Step 1: Calculate in-flight counts BEFORE acquiring locks to avoid latency bottlenecks
    from core.engine.session_governor import get_in_flight_count_internal
    in_flight_total, in_flight_asset = await get_in_flight_count_internal(asset)

    # Step 2: Acquire locks
    async with GLOBAL_POSITIONS_LOCK:
        async with _lock:
            # Sync cache if changes occurred on disk
            await _ensure_positions_loaded()

            positions = _STATE_RAM["active_positions"]
            total_open = len(positions)

            effective_total = total_open + in_flight_total
            if effective_total >= max_total_open:
                return False, 0.0

            # Count open positions for this specific asset
            import re
            asset_upper = asset.upper()
            asset_open = 0
            for p in positions:
                t = p.get("event_title") or ""
                p_asset = p.get("asset") or ""
                if not p_asset:
                    m = re.search(r"\[(BTC|ETH|SOL|XRP|DOGE|ADA|AVAX|SUI)\]", t, re.IGNORECASE)
                    p_asset = m.group(1).upper() if m else ""
                if p_asset.upper() == asset_upper:
                    asset_open += 1

            effective_asset = asset_open + in_flight_asset
            if effective_asset >= max_open_per_asset:
                return False, 0.0

            balance = _STATE_RAM["balance"]
            if size_requested > balance:
                return False, 0.0

            # Reserve balance instantly in RAM
            _STATE_RAM["balance"] = round(balance - size_requested, 2)
            global _balance
            _balance = _STATE_RAM["balance"]
            
            _STATE_RAM["last_change_reason"] = f"Reserved capital for {asset_upper} {timeframe}"

            # Step 3: Trigger fire-and-forget task to sync state on disk asynchronously
            asyncio.create_task(_bg_sync_state_to_disk(_STATE_RAM["last_change_reason"]))

            return True, size_requested


# ── Runtime tracking ─────────────────────────────────────────────────────────

_RUNTIME_FILE = Path(__file__).parent.parent.parent / "data" / "runtime_tracking.json"
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


# ── Reconciliation helpers (RAM caching reads) ────────────────────────────────

def get_open_positions() -> list:
    """Return all active (open) positions from RAM state cache."""
    return _STATE_RAM["active_positions"]


def get_closed_positions(limit: int | None = None) -> list:
    """Return closed positions from RAM state cache, newest first."""
    closed = _STATE_RAM["closed_positions"]
    return closed[:limit] if limit is not None else closed


def is_confirmed(position_id: str) -> bool:
    """Return True if this position has been confirmed (marked filled)."""
    for pos in _STATE_RAM["active_positions"]:
        if pos.get("id") == position_id or pos.get("order_id") == position_id:
            return bool(pos.get("confirmed", False))
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
            
            # Sync in-memory RAM copy
            _STATE_RAM["active_positions"] = data.get("active", [])
            _STATE_RAM["closed_positions"] = data.get("closed", [])
    except Exception as exc:
        log.warning("[STATE] force_confirm failed: %s", exc)


async def cleanup_expired_positions() -> int:
    """
    Move open positions whose expiry_ts passed more than 90 seconds ago to the
    closed list with an estimated outcome (entry ≥ 0.90 → WIN, else LOSS).
    Returns count of cleaned positions.
    """
    import time as _time
    import aiofiles
    now = _time.time()
    cutoff = now - 90.0
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    async with GLOBAL_POSITIONS_LOCK:
        if not _POSITIONS_FILE.exists():
            return 0
        try:
            async with aiofiles.open(_POSITIONS_FILE, mode='r', encoding='utf-8') as f:
                data = json.loads(await f.read())
        except Exception:
            return 0

        active = data.get("active", [])
        zombies = [p for p in active if 0 < p.get("expiry_ts", float("inf")) < cutoff]
        if not zombies:
            return 0

        survivors = [p for p in active if p not in zombies]
        closed_list = data.get("closed", [])

        for z in zombies:
            ep = float(z.get("entry_price", 0.5) or 0.5)
            size = float(z.get("size", z.get("amount_spent", 1.0)) or 1.0)
            shares = size / ep if ep > 0 else 0.0
            won = ep >= 0.90
            exit_p = 0.99 if won else 0.01
            pnl = round(shares * exit_p - size, 4)
            age_s = int(now - z.get("expiry_ts", now))
            entry_time = z.get("entry_time") or z.get("open_time") or now_iso
            closed_record = {
                "order_id":          z.get("order_id", "?"),
                "market":            z.get("market", "POLYMARKET"),
                "market_id":         z.get("market_id", ""),
                "event_title":       z.get("event_title", ""),
                "direction":         z.get("direction", ""),
                "entry_price":       ep,
                "exit_price":        exit_p,
                "size":              size,
                "realized_pnl":      pnl,
                "realized_pnl_pct":  round((exit_p - ep) / ep * 100, 1) if ep > 0 else 0.0,
                "exit_reason":       "MARKET_EXPIRED",
                "hold_hours":        round((z.get("expiry_ts", now) - _time.mktime(
                                         _time.strptime(entry_time[:19], "%Y-%m-%dT%H:%M:%S")
                                     )) / 3600, 3) if "T" in entry_time else 0.0,
                "entry_time":        entry_time,
                "exit_time":         now_iso,
                "expiry_ts":         z.get("expiry_ts"),
                "entry_type":        z.get("entry_type", ""),
            }
            closed_list.insert(0, closed_record)
            log.info(
                "[ZOMBIE-CLEAN] Moved %s to closed: %s %s @ %.0fc → %s pnl=$%.2f (expiry %ds ago)",
                z.get("order_id", "?")[:12],
                z.get("direction", ""),
                "WIN" if won else "LOSS",
                ep * 100,
                z.get("event_title", "")[:30],
                pnl,
                age_s,
            )

        data["active"] = survivors
        data["closed"] = closed_list[:300]
        summary = data.get("summary", {})
        summary["active_count"] = len(survivors)
        data["summary"] = summary

        try:
            tmp_path = _POSITIONS_FILE.with_suffix(".tmp")
            async with aiofiles.open(tmp_path, mode='w', encoding='utf-8') as f:
                await f.write(json.dumps(data, indent=2, default=str))
            import os as _os
            _os.replace(tmp_path, _POSITIONS_FILE)
        except Exception as exc:
            log.warning("[STATE] Failed to save cleanup state atomically: %s", exc)
            async with aiofiles.open(_POSITIONS_FILE, mode='w', encoding='utf-8') as f:
                await f.write(json.dumps(data, indent=2, default=str))
        
        # Sync RAM
        _STATE_RAM["active_positions"] = survivors
        _STATE_RAM["closed_positions"] = closed_list[:300]
        
        return len(zombies)
