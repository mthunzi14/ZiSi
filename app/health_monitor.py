"""
health_monitor.py — ZiSi System Health Monitor

Runs a 90-second background diagnostic loop covering:
  1. API connectivity (Kalshi + Polymarket)
  2. Position reconciliation (orphaned positions)
  3. Bankroll accuracy (local vs API balance)
  4. ML pipeline activity (labelled examples in last 24h)
  5. Stale position detection (positions exceeding max hold)

Also provides:
  - startup_recovery(): reconcile positions on restart
  - strategy_drift_check(): suspend underperforming categories

All alerts are written to system_alerts.json for dashboard display.
"""
import json
import logging
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import requests

log = logging.getLogger("zisi.health")
from infrastructure.state.state_manager import GLOBAL_POSITIONS_LOCK

_BASE_DIR          = Path(__file__).parent.parent
_ALERTS_FILE       = _BASE_DIR / "system_alerts.json"
_SUSPENSIONS_FILE  = _BASE_DIR / "category_suspensions.json"
STATE_FILE         = _BASE_DIR / "account_state.json"
HEALTH_LOG         = _BASE_DIR / "health_monitor.log"
POSITIONS_FILE     = _BASE_DIR / "infrastructure" / "exchange" / "positions_state.json"

# Polymarket health endpoint (CLOB API root)
POLYMARKET_HEALTH_URL = "https://clob.polymarket.com"
# Kalshi health endpoint
KALSHI_HEALTH_URL = "https://api.elections.kalshi.com"

PAPER_MAX_HOLD_HOURS = 4
LIVE_MAX_HOLD_HOURS  = 48

_health_thread: Optional[threading.Thread] = None
_health_stop   = threading.Event()
_alerts: list  = []
_alerts_lock   = threading.Lock()
_paper_mode: bool = True  # set from config on startup


# ---------------------------------------------------------------------------
# Alert management
# ---------------------------------------------------------------------------

def _add_alert(level: str, code: str, message: str) -> None:
    """Add an alert and persist to disk."""
    alert = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": level,    # CRITICAL / WARNING / INFO
        "code":  code,
        "message": message,
    }
    with _alerts_lock:
        _alerts.append(alert)
        # Keep last 100 alerts in memory
        if len(_alerts) > 100:
            _alerts.pop(0)
    _persist_alerts()
    log.warning("[HEALTH-ALERT] [%s] %s: %s", level, code, message)


def _persist_alerts() -> None:
    try:
        with _alerts_lock:
            snapshot = list(_alerts[-50:])  # last 50 to disk
        _ALERTS_FILE.write_text(
            json.dumps({"alerts": snapshot, "last_updated": datetime.now(timezone.utc).isoformat()},
                       indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        log.debug("[HEALTH] Alert persist failed: %s", exc)


def get_active_alerts() -> list:
    """Return current alerts for dashboard consumption."""
    with _alerts_lock:
        return list(_alerts)


# ---------------------------------------------------------------------------
# Individual health checks
# ---------------------------------------------------------------------------

def _check_api_connectivity() -> bool:
    """Verify Polymarket + Kalshi APIs are reachable.

    Any HTTP response (including 4xx auth/not-found) means the server is up.
    Only 5xx or network failure counts as degraded.
    """
    ok = True
    for name, url in (("POLYMARKET", POLYMARKET_HEALTH_URL), ("KALSHI", KALSHI_HEALTH_URL)):
        try:
            resp = requests.get(url, timeout=5)
            # 5xx = server error → alert. 4xx = server up but wrong endpoint/no auth → fine.
            if resp.status_code >= 500:
                _add_alert("WARNING", f"API_DEGRADED_{name}", f"{name} returned HTTP {resp.status_code}")
                ok = False
        except requests.exceptions.Timeout:
            _add_alert("WARNING", f"API_TIMEOUT_{name}", f"{name} API timed out")
            ok = False
        except requests.exceptions.ConnectionError:
            _add_alert("CRITICAL", f"API_DOWN_{name}", f"{name} API unreachable")
            ok = False
        except Exception as exc:
            log.debug("[HEALTH] %s connectivity check error: %s", name, exc)
    return ok


def _check_position_reconciliation() -> bool:
    """Detect positions in state that look orphaned or stale."""
    if not POSITIONS_FILE.exists():
        return True  # nothing to reconcile

    try:
        with GLOBAL_POSITIONS_LOCK:
            data = json.loads(POSITIONS_FILE.read_text(encoding="utf-8"))
        active = data.get("active", [])
        now = datetime.now(timezone.utc)
        max_hold = timedelta(hours=PAPER_MAX_HOLD_HOURS if _paper_mode else LIVE_MAX_HOLD_HOURS)

        orphaned = []
        for pos in active:
            entry_time_str = pos.get("entry_time") or pos.get("open_time", "")
            if not entry_time_str:
                continue
            try:
                entry_dt = datetime.fromisoformat(entry_time_str.replace("Z", "+00:00"))
                age = now - entry_dt
                if age > max_hold * 2:  # 2× max hold = definitely orphaned
                    orphaned.append(pos.get("order_id", "?"))
            except Exception:
                continue

        if orphaned:
            _add_alert(
                "WARNING", "ORPHANED_POSITIONS",
                f"{len(orphaned)} position(s) exceed 2× max hold: {orphaned[:3]}",
            )
            return False
        return True
    except Exception as exc:
        log.debug("[HEALTH] Position reconciliation error: %s", exc)
        return True


def _check_bankroll_accuracy() -> bool:
    """Verify local balance matches positions_state.json realized P&L."""
    if not STATE_FILE.exists():
        return True

    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        local_balance = float(state.get("balance", 100))
        starting_balance = float(state.get("starting_balance", state.get("initial_balance", 100.0)))

        # Use positions_state.json as the single source of truth for realized P&L.
        # This avoids discrepancies between zisi_local_trades.jsonl and what the
        # trader actually booked (Kalshi, partial fills, etc.).
        if not POSITIONS_FILE.exists():
            return True

        with GLOBAL_POSITIONS_LOCK:
            pos = json.loads(POSITIONS_FILE.read_text(encoding="utf-8"))
        realized_pnl = float((pos.get("summary") or {}).get("realized_pnl", 0) or 0)

        expected_balance = round(starting_balance + realized_pnl, 2)
        discrepancy = abs(local_balance - expected_balance)
        discrepancy_pct = discrepancy / max(1.0, abs(expected_balance))

        # 5% tolerance — small rounding gaps are normal with many small trades
        if discrepancy_pct > 0.05:
            _add_alert(
                "WARNING", "BANKROLL_MISMATCH",
                f"Balance mismatch: state=${local_balance:.2f} vs computed=${expected_balance:.2f} "
                f"({discrepancy_pct:.1%} gap)",
            )
            return False
        return True
    except Exception as exc:
        log.debug("[HEALTH] Bankroll check error: %s", exc)
        return True


_ML_MIN_EXAMPLES = 10  # need at least this many total before staleness matters

def _check_ml_pipeline_active() -> bool:
    """Warn only if ML examples exist AND none have been added in 24h (genuine stall).
    Suppresses the alert when fewer than 10 total examples exist (trades are sparse)."""
    labelled_file = _BASE_DIR / "ml_labelled_outcomes.jsonl"
    if not labelled_file.exists():
        return True  # no data yet — not a failure

    try:
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=24)
        total = 0
        recent = 0

        with labelled_file.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                    total += 1
                    ts_str = r.get("timestamp_exit") or r.get("timestamp_entry", "")
                    if ts_str:
                        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                        if ts > cutoff:
                            recent += 1
                except Exception:
                    continue

        # Not enough data to establish a baseline — suppress the warning
        if total < _ML_MIN_EXAMPLES:
            log.debug("[HEALTH] ML pipeline: %d/%d examples (sparse data, no stale check)", total, _ML_MIN_EXAMPLES)
            return True

        if recent == 0 and now.hour not in (1, 2, 3, 4):
            _add_alert(
                "WARNING", "ML_PIPELINE_STALE",
                f"No new labelled examples in last 24h ({total} total) — feedback loop may be broken",
            )
            return False
        return True
    except Exception as exc:
        log.debug("[HEALTH] ML pipeline check error: %s", exc)
        return True


def _check_stale_positions() -> bool:
    """Alert only on positions exceeding 1.5× max hold (e.g. >6h in paper mode).
    Positions between max_hold and 1.5× are being actively monitored for exit
    by the position monitor — no alert needed until they become truly stuck."""
    if not POSITIONS_FILE.exists():
        return True

    try:
        with GLOBAL_POSITIONS_LOCK:
            data = json.loads(POSITIONS_FILE.read_text(encoding="utf-8"))
        active = data.get("active", [])
        now = datetime.now(timezone.utc)
        max_hold_h = PAPER_MAX_HOLD_HOURS if _paper_mode else LIVE_MAX_HOLD_HOURS
        alert_threshold_h = max_hold_h * 1.5  # 6h paper, 72h live
        stale = []

        for pos in active:
            entry_str = pos.get("entry_time") or pos.get("open_time", "")
            if not entry_str:
                continue
            try:
                entry_dt = datetime.fromisoformat(entry_str.replace("Z", "+00:00"))
                age_h = (now - entry_dt).total_seconds() / 3600
                if age_h > alert_threshold_h:
                    stale.append(
                        f"{pos.get('order_id','?')[:12]} ({age_h:.1f}h > {alert_threshold_h:.0f}h)"
                    )
            except Exception:
                continue

        if stale:
            _add_alert(
                "WARNING", "STALE_POSITIONS",
                f"{len(stale)} position(s) exceed {alert_threshold_h:.0f}h: {stale[:2]}",
            )
            return False
        return True
    except Exception as exc:
        log.debug("[HEALTH] Stale position check error: %s", exc)
        return True


# ---------------------------------------------------------------------------
# Main health check (aggregates all checks)
# ---------------------------------------------------------------------------

def health_check() -> dict:
    """
    Run all 5 health checks. Returns a summary dict.
    Call every 90 seconds from background thread.
    """
    checks = {
        "api_connectivity":       _check_api_connectivity(),
        "position_reconciliation": _check_position_reconciliation(),
        "bankroll_accuracy":       _check_bankroll_accuracy(),
        "ml_pipeline_active":      _check_ml_pipeline_active(),
        "no_stale_positions":      _check_stale_positions(),
    }
    all_pass = all(checks.values())
    status   = "HEALTHY" if all_pass else "DEGRADED"

    log.debug(
        "[HEALTH] %s | %s",
        status,
        " | ".join(f"{k}={'OK' if v else 'FAIL'}" for k, v in checks.items()),
    )
    return {"status": status, "checks": checks, "timestamp": datetime.now(timezone.utc).isoformat()}


# ---------------------------------------------------------------------------
# Startup recovery
# ---------------------------------------------------------------------------

def startup_recovery() -> bool:
    """
    Reconcile positions on restart: deduplicate, remove expired, save cleaned state.
    Returns True when recovery is complete and trading can resume.
    """
    import re
    log.info("=== STARTUP RECOVERY ===")

    if not POSITIONS_FILE.exists():
        log.info("[RECOVERY] No positions_state.json — clean start")
        return True

    try:
        with GLOBAL_POSITIONS_LOCK:
            data = json.loads(POSITIONS_FILE.read_text(encoding="utf-8"))
        active = data.get("active", [])
        log.info("[RECOVERY] Found %d open position(s) in state file", len(active))

        if not active:
            return True

        # Step 1: Deduplicate — KALSHI order_ids end in _<unix_ts>; strip to get base key
        _KALSHI_TS = re.compile(r'_\d{9,10}$')
        seen: dict = {}
        for pos in active:
            oid = pos.get("order_id", "")
            if pos.get("market") == "KALSHI":
                key = _KALSHI_TS.sub("", oid)
            else:
                key = oid
            time_str = pos.get("entry_time") or pos.get("open_time", "")
            if key not in seen:
                seen[key] = pos
            else:
                existing_ts = seen[key].get("entry_time") or seen[key].get("open_time", "")
                if time_str and existing_ts and time_str > existing_ts:
                    log.warning("[DEDUP] Replaced older: %s", seen[key].get("order_id", "?")[:30])
                    seen[key] = pos
                else:
                    log.warning("[DEDUP] Removed duplicate: %s", oid[:30])

        deduped = list(seen.values())
        removed_dups = len(active) - len(deduped)
        if removed_dups:
            log.warning("[RECOVERY] Removed %d duplicate position(s)", removed_dups)

        # Step 2: Remove expired positions (>max hold time)
        now = datetime.now(timezone.utc)
        max_hold_h = PAPER_MAX_HOLD_HOURS if _paper_mode else LIVE_MAX_HOLD_HOURS
        cleaned = []
        for pos in deduped:
            time_str = pos.get("entry_time") or pos.get("open_time", "")
            if not time_str:
                cleaned.append(pos)
                continue
            try:
                pos_dt = datetime.fromisoformat(time_str.replace("Z", "+00:00"))
                age_h = (now - pos_dt).total_seconds() / 3600
                if age_h > max_hold_h:
                    log.warning(
                        "[EXPIRED] Removing %s: %.1fh old (>%dh max) — %s",
                        pos.get("order_id", "?")[:28], age_h, max_hold_h,
                        pos.get("market", "?"),
                    )
                else:
                    cleaned.append(pos)
            except Exception:
                cleaned.append(pos)

        removed_expired = len(deduped) - len(cleaned)

        # Step 3: Save cleaned state
        data["active"] = cleaned
        if "summary" in data:
            data["summary"]["active_count"] = len(cleaned)
            data["summary"]["poly_active"] = sum(
                1 for p in cleaned if p.get("market") == "POLYMARKET"
            )
            data["summary"]["kalshi_active"] = sum(
                1 for p in cleaned if p.get("market") == "KALSHI"
            )
        data["last_updated"] = datetime.now(timezone.utc).isoformat()
        with GLOBAL_POSITIONS_LOCK:
            tmp_path = POSITIONS_FILE.with_suffix(".tmp")
            tmp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            import os as _os
            _os.replace(tmp_path, POSITIONS_FILE)

        log.info(
            "[RECOVERY] Done: %d kept | %d duplicates removed | %d expired removed",
            len(cleaned), removed_dups, removed_expired,
        )

    except Exception as exc:
        log.error("[RECOVERY] Recovery scan failed: %s", exc)

    log.info("[RECOVERY] Recovery complete — resuming signal cycle")
    return True


# ---------------------------------------------------------------------------
# Strategy drift check (run every 1h)
# ---------------------------------------------------------------------------

def add_alert(level: str, code: str, message: str) -> None:
    """Public wrapper for _add_alert — allows other modules to post alerts."""
    _add_alert(level, code, message)


def strategy_drift_check() -> dict:
    """
    Check per-category rolling win rates. Warn on categories with WR < 40%.
    Suspend categories with WR < 30% and persist the list for matcher enforcement.
    """
    try:
        from kalshi.fetcher import get_category_win_rates
        rates = get_category_win_rates()
    except Exception as exc:
        log.debug("[DRIFT] Could not fetch category win rates: %s", exc)
        return {}

    suspended = []
    warnings  = []
    for cat, stats in rates.items():
        wr = stats.get("win_rate")
        total = stats.get("total", 0)
        if wr is None or total < 5:
            continue  # not enough data

        if wr < 0.30:
            suspended.append(cat)
            _add_alert(
                "CRITICAL", f"DRIFT_SUSPEND_{cat}",
                f"Category {cat} WR={wr:.0%} ({total} trades) — SUSPENDED (no new trades)",
            )
        elif wr < 0.40:
            warnings.append(cat)
            _add_alert(
                "WARNING", f"DRIFT_WARN_{cat}",
                f"Category {cat} WR={wr:.0%} ({total} trades) — sizing reduced 50%",
            )

    if suspended or warnings:
        log.warning("[DRIFT] Suspended: %s | Warning: %s", suspended, warnings)

    # ── Persist suspension list for enforcement by KalshiEventMatcher ─────────
    try:
        _SUSPENSIONS_FILE.write_text(
            json.dumps({
                "suspended": suspended,
                "warning":   warnings,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        log.debug("[DRIFT] Could not persist suspensions: %s", exc)

    # ── Daily trade journal CSV export ────────────────────────────────────────
    _export_trade_journal()

    return {"suspended_categories": suspended, "warning_categories": warnings}


def _export_trade_journal() -> None:
    """Export zisi_local_trades.jsonl → trade_journal_export.csv (called hourly)."""
    import csv
    trades_file = _BASE_DIR / "zisi_local_trades.jsonl"
    export_file = _BASE_DIR / "trade_journal_export.csv"
    if not trades_file.exists():
        return
    try:
        records = []
        with trades_file.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except Exception:
                        continue
        if not records:
            return
        fieldnames = [
            "order_id", "market", "ticker", "event_title", "direction",
            "entry_price", "exit_price", "position_size", "profit",
            "profit_percent", "open_time", "close_time", "hold_hours",
            "exit_reason", "paper_trade", "status",
        ]
        with export_file.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(records)
        log.info("[EXPORT] Trade journal → %s (%d records)", export_file.name, len(records))
    except Exception as exc:
        log.warning("[EXPORT] Trade journal export failed: %s", exc)


# ---------------------------------------------------------------------------
# Background thread
# ---------------------------------------------------------------------------

def _health_loop() -> None:
    """Background daemon: run health_check() every 90 seconds."""
    log.info("[HEALTH] Background health monitor started (90s interval)")
    _drift_last_check = datetime.now(timezone.utc)

    while not _health_stop.is_set():
        try:
            health_check()

            # Strategy drift check every 1 hour
            if (datetime.now(timezone.utc) - _drift_last_check).total_seconds() > 3600:
                strategy_drift_check()
                _drift_last_check = datetime.now(timezone.utc)

        except Exception as exc:
            log.error("[HEALTH] Unexpected error in health loop: %s", exc)

        _health_stop.wait(timeout=90)

    log.info("[HEALTH] Background health monitor stopped")


def start_health_monitor(paper_mode: bool = True) -> None:
    """Start 90s background health monitor. Idempotent."""
    global _health_thread, _paper_mode
    _paper_mode = paper_mode

    if _health_thread and _health_thread.is_alive():
        return

    _health_stop.clear()
    _health_thread = threading.Thread(
        target=_health_loop,
        name="zisi-health",
        daemon=True,
    )
    _health_thread.start()
    log.info("[HEALTH] Monitor thread started: %s", _health_thread.name)


def stop_health_monitor() -> None:
    """Signal health monitor to stop."""
    _health_stop.set()
    if _health_thread:
        _health_thread.join(timeout=5)
    log.info("[HEALTH] Monitor stopped")


# ---------------------------------------------------------------------------
# Legacy standalone mode (kept for bat file compatibility)
# ---------------------------------------------------------------------------

def log_health(message: str) -> None:
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"{timestamp} {message}\n"
    try:
        with HEALTH_LOG.open("a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass
    print(line, end="")


def check_bot_health():
    try:
        if not STATE_FILE.exists():
            return False, "State file missing"
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        last_updated_str = state.get("last_updated", "")
        if not last_updated_str:
            return False, "No last_updated in state file"
        last_updated = datetime.fromisoformat(last_updated_str.replace("Z", "+00:00"))
        now = datetime.now(last_updated.tzinfo)
        minutes_since = (now - last_updated).total_seconds() / 60
        if minutes_since > 45:
            return False, f"State stale ({minutes_since:.1f} min old)"
        return True, f"Healthy (updated {minutes_since:.1f} min ago, balance=${state.get('balance', 0):.2f})"
    except Exception as e:
        return False, f"Check failed: {e}"


def run_monitoring_daemon():
    log_health("=== ZiSi Health Monitor (standalone) started ===")
    consecutive_failures = 0
    while True:
        try:
            is_healthy, message = check_bot_health()
            status = "OK" if is_healthy else "FAIL"
            log_health(f"[{status}] {message}")
            if not is_healthy:
                consecutive_failures += 1
                if consecutive_failures >= 2:
                    log_health(f"ALERT: Bot has been offline for {consecutive_failures * 30} min+")
            else:
                consecutive_failures = 0
            time.sleep(30 * 60)
        except KeyboardInterrupt:
            log_health("=== Health monitor stopped by user ===")
            break
        except Exception as e:
            log_health(f"Monitor error: {e}")
            time.sleep(60)


if __name__ == "__main__":
    run_monitoring_daemon()
