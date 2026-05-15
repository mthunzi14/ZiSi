"""
analyze_trades.py - ZiSi Bot Trade Analysis
Reads the local JSONL trade log and produces a full metrics report using
metrics_engine.  Run standalone: python analyze_trades.py
"""

import json
import sys
from datetime import datetime
from pathlib import Path

from metrics_engine import (
    calculate_daily_metrics,
    calculate_hourly_metrics,
    calculate_coin_metrics,
    calculate_signal_metrics,
    format_pretty_report,
    save_metrics_to_file,
)

_LOG_FILE = Path(__file__).parent / "zisi_local_trades.jsonl"


def load_trades(filepath: Path = _LOG_FILE) -> list[dict]:
    """Read all JSON lines from the trade log; skip non-trade entries."""
    if not filepath.exists():
        print(f"No trade log found at {filepath}")
        return []

    trades = []
    with filepath.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                # Only include actual trade records (not skip/signal/error rows)
                if record.get("type") in ("skip", "signal", "error"):
                    continue
                trades.append(record)
            except json.JSONDecodeError:
                continue

    return trades


def analyze_trades(filepath: Path = _LOG_FILE) -> dict | None:
    """
    Generate and print a full daily metrics report from the local trade log.

    Returns:
        The metrics dict, or None if no trades are found.
    """
    trades = load_trades(filepath)

    if not trades:
        print("No trades logged yet.")
        return None

    daily  = calculate_daily_metrics(trades)
    hourly = calculate_hourly_metrics(trades)
    coin   = calculate_coin_metrics(trades)
    signal = calculate_signal_metrics(trades)

    report = format_pretty_report(daily, hourly, coin, signal)
    print(report)

    date_str = datetime.utcnow().strftime("%Y-%m-%d")
    save_metrics_to_file(
        {"daily": daily, "hourly": hourly, "coin": coin, "signal": signal},
        date_str,
    )
    print(f"\nMetrics saved → metrics_{date_str}.json")

    return {"daily": daily, "hourly": hourly, "coin": coin, "signal": signal}


if __name__ == "__main__":
    log_path = Path(sys.argv[1]) if len(sys.argv) > 1 else _LOG_FILE
    analyze_trades(log_path)
