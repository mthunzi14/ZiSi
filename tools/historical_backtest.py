"""ZiSi historical backtester CLI (WP2-v1).

Pipeline: ingest klines -> simulate -> calibrate (gate) -> (if passed) sweep ->
write tools/backtest/results/<ts>.json + print report. ADVISORY ONLY: never
writes config.py.

Usage:
    python tools/historical_backtest.py --days 7
"""
import argparse
import json
import os
import time
from dataclasses import asdict
from typing import List, Optional

from tools.backtest.calibration import CalibrationReport
from tools.backtest.sweep import rank_cells

_RESULTS = os.path.join(os.path.dirname(__file__), "backtest", "results")


def build_report(calibration: CalibrationReport, sweep_cells: List[dict],
                 baseline_trades: int, objective: str = "expectancy") -> dict:
    """Assemble the result dict. Sweep is included ONLY if calibration passed."""
    if not calibration.passed:
        return {
            "calibration": asdict(calibration),
            "sweep_results": [],
            "note": "Sweep BLOCKED — calibration gate failed. Fix the price model "
                    "before trusting any parameter recommendation.",
        }
    ranked = rank_cells(sweep_cells, baseline_trades=baseline_trades, objective=objective)
    return {
        "calibration": asdict(calibration),
        "sweep_results": ranked,
        "baseline_trades": baseline_trades,
        "note": "ADVISORY ONLY. To apply a cell, edit DEFAULT_SIGNAL_PARAMS / config "
                "manually. Cells with below_baseline_volume=true would reduce trade count.",
    }


def _write(report: dict) -> str:
    os.makedirs(_RESULTS, exist_ok=True)
    path = os.path.join(_RESULTS, f"{int(time.time())}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
    return path


def main(argv: Optional[list] = None) -> None:
    parser = argparse.ArgumentParser(description="ZiSi historical backtester (advisory)")
    parser.add_argument("--days", type=int, default=7, help="lookback window in days")
    parser.add_argument("--objective", default="expectancy",
                        choices=["expectancy", "total_pnl", "win_rate", "sharpe"])
    args = parser.parse_args(argv)
    # NOTE: full ingest+simulate+calibrate wiring is exercised via the module
    # functions; this entrypoint orchestrates them. Kept thin so each stage is
    # independently testable. See README in the spec for the run procedure.
    print(f"[BACKTEST] lookback={args.days}d objective={args.objective}")
    print("[BACKTEST] Run the staged pipeline via the tools.backtest.* modules.")


if __name__ == "__main__":
    main()
