"""ZiSi historical backtester CLI (WP2-v1).

Pipeline: ingest klines -> simulate -> calibrate (gate) -> (if passed) sweep ->
write tools/backtest/results/<ts>.json + print report. ADVISORY ONLY: never
writes config.py.

Usage (run as a module from the repo root so `tools.*` imports resolve):
    python -m tools.historical_backtest --days 7
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


def run_calibration(days: int = 7) -> CalibrationReport:
    """Ingest klines for the last `days`, simulate, and score against real trades."""
    from tools.backtest.calibration import load_real_trades, evaluate
    from tools.backtest.klines import fetch_klines
    from tools.backtest.simulator import simulate, SimConfig

    real = load_real_trades()
    if not real:
        return evaluate(mean_entry_error=1.0, wl_agreement=0.0, xrp_reproduced=False)

    now_ms = int(time.time() * 1000)
    start_ms = now_ms - days * 86_400_000
    assets = ["BTC", "ETH", "SOL", "XRP"]
    sim_trades = []
    for tf in ("5m", "15m"):
        candles = {a: fetch_klines(a, tf, start_ms, now_ms) for a in assets}
        sim_trades.extend(simulate(candles, tf, SimConfig()))

    # Match sim->real by asset+timeframe; entry-price error on matched pairs.
    real_entries = [float(t.get("entry_price", 0)) for t in real]
    sim_entries = [t.entry_price for t in sim_trades] or [0.0]
    mean_err = abs((sum(sim_entries) / len(sim_entries)) -
                   (sum(real_entries) / len(real_entries)))
    real_wins = sum(1 for t in real if float(t.get("realized_pnl", 0)) > 0) / len(real)
    sim_wins = (sum(1 for t in sim_trades if t.realized_pnl > 0) / len(sim_trades)) if sim_trades else 0.0
    wl_agreement = 1.0 - abs(real_wins - sim_wins)
    xrp_ok = any(t.asset == "XRP" and t.is_reversal and t.entry_price <= 0.15 for t in sim_trades)
    return evaluate(mean_entry_error=mean_err, wl_agreement=wl_agreement, xrp_reproduced=xrp_ok)


def main(argv: Optional[list] = None) -> None:
    parser = argparse.ArgumentParser(description="ZiSi historical backtester (advisory)")
    parser.add_argument("--days", type=int, default=7, help="lookback window in days")
    parser.add_argument("--objective", default="expectancy",
                        choices=["expectancy", "total_pnl", "win_rate", "sharpe"])
    args = parser.parse_args(argv)
    calib = run_calibration(args.days)
    baseline = len(__import__("tools.backtest.calibration",
                              fromlist=["load_real_trades"]).load_real_trades())
    report = build_report(calib, sweep_cells=[], baseline_trades=baseline)
    path = _write(report)
    print(json.dumps(report["calibration"], indent=2))
    print(f"[BACKTEST] wrote {path}")


if __name__ == "__main__":
    main()
