"""engine_status.py — write a lightweight JSON status file each scan cycle."""
import json
import time
from pathlib import Path

_STATUS_PATH = Path(__file__).parent.parent.parent / "engine_status.json"


def write_engine_status(status: str, detail: str, asset_states: dict | None = None) -> None:
    """Write current engine state for dashboard consumption.

    status: SCANNING | LOW_EDGE | CHOPPY | LAT_COOLDOWN | NO_MARKET | PRICE_FLOOR | MACRO_BLOCK
    detail: human-readable string e.g. "next 5m in 2m 14s"
    asset_states: optional {asset: {tf: status_str}}
    """
    try:
        payload = {
            "ts": time.time(),
            "status": status,
            "detail": detail,
            "asset_states": asset_states or {},
        }
        _STATUS_PATH.write_text(json.dumps(payload), encoding="utf-8")
    except Exception:
        pass
