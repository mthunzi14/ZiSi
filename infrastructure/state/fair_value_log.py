"""Append-only JSONL log of fair-value entries for HONEST win-rate measurement.
Records the real quote we filled at + the fair-value edge we believed we had, so
realized WR can be compared against the backtest's lag-conditional expectation."""
import json
import os
from typing import Optional

_DEFAULT = os.path.join(os.path.dirname(__file__), "fair_value_trades.jsonl")


def log_fair_value_entry(row: dict, path: Optional[str] = None) -> None:
    """Append one fair-value entry record as a JSON line. Never raises into the engine."""
    target = path or _DEFAULT
    try:
        with open(target, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    except Exception:
        pass
