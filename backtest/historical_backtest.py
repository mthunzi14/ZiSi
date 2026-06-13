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

from backtest.calibration import CalibrationReport
from backtest.sweep import rank_cells, build_grid

_RESULTS = os.path.join(os.path.dirname(__file__), "results")


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
    from backtest.calibration import load_real_trades, evaluate, match_trades
    from backtest.klines import fetch_klines
    from backtest.simulator import simulate, SimConfig

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

    # Per-trade calibration: match each real trade to the nearest sim trade by
    # (asset, timeframe, entry_time) proximity, then measure ABS entry-price error
    # and W/L sign agreement on matched pairs.
    pairs = match_trades(real, sim_trades)
    if pairs:
        mean_err = sum(
            abs(float(r.get("entry_price", 0)) - s.entry_price)
            for r, s in pairs
        ) / len(pairs)
        wl_matches = sum(
            1 for r, s in pairs
            if (float(r.get("realized_pnl", 0)) > 0) == (s.realized_pnl > 0)
        )
        wl_agreement = wl_matches / len(pairs)
    else:
        # No matches (e.g. no real trades share assets with sim): fall back gracefully
        sim_entries = [t.entry_price for t in sim_trades] or [0.0]
        real_entries = [float(t.get("entry_price", 0)) for t in real]
        mean_err = abs((sum(sim_entries) / len(sim_entries)) -
                       (sum(real_entries) / len(real_entries)))
        real_wins = sum(1 for t in real if float(t.get("realized_pnl", 0)) > 0) / len(real)
        sim_wins = (sum(1 for t in sim_trades if t.realized_pnl > 0) / len(sim_trades)) if sim_trades else 0.0
        wl_agreement = 1.0 - abs(real_wins - sim_wins)

    xrp_ok = any(t.asset == "XRP" and t.is_reversal and t.entry_price <= 0.15 for t in sim_trades)
    return evaluate(mean_entry_error=mean_err, wl_agreement=wl_agreement, xrp_reproduced=xrp_ok)


def run_sweep(days: int, baseline_trades: int) -> List[dict]:
    """Ingest klines once, then evaluate every grid cell from build_grid().

    Returns a list of {"params": cell_dict, "metrics": metrics_dict}.
    Advisory only — never writes config.py.
    """
    from backtest.klines import fetch_klines
    from backtest.simulator import simulate, SimConfig
    from backtest.pricing import PricingParams
    from backtest.sweep import cell_metrics
    from core.engine.signal_core import DEFAULT_SIGNAL_PARAMS

    assets = ["BTC", "ETH", "SOL", "XRP"]
    timeframes = ("5m", "15m")
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - days * 86_400_000

    # Ingest klines once per (asset, timeframe) combination
    klines: dict = {}
    for tf in timeframes:
        klines[tf] = {a: fetch_klines(a, tf, start_ms, now_ms) for a in assets}

    grid = build_grid()
    cells = []
    for cell in grid:
        # Build signal_params: start from defaults, override rsi_up and rsi_dn
        sig_params = dict(DEFAULT_SIGNAL_PARAMS)
        sig_params["rsi_up"] = cell["rsi_up"]
        sig_params["rsi_dn"] = cell["rsi_dn"]

        # Build pricing with the cell's target_threshold
        pricing = PricingParams(target_threshold=cell["target_threshold"])

        cfg = SimConfig(signal_params=sig_params, pricing=pricing)

        # Simulate across both timeframes and collect all P&Ls
        cell_pnls: List[float] = []
        for tf in timeframes:
            trades = simulate(klines[tf], tf, cfg)
            cell_pnls.extend(t.realized_pnl for t in trades)

        cells.append({"params": cell, "metrics": cell_metrics(cell_pnls)})

    return cells


def main(argv: Optional[list] = None) -> None:
    parser = argparse.ArgumentParser(description="ZiSi historical backtester (advisory)")
    parser.add_argument("--days", type=int, default=7, help="lookback window in days")
    parser.add_argument("--objective", default="expectancy",
                        choices=["expectancy", "total_pnl", "win_rate", "sharpe"])
    args = parser.parse_args(argv)
    calib = run_calibration(args.days)
    baseline = len(__import__("backtest.calibration",
                              fromlist=["load_real_trades"]).load_real_trades())

    # Run parameter sweep only if calibration passed
    if calib.passed:
        print("[BACKTEST] calibration passed — running parameter sweep...")
        sweep_cells = run_sweep(args.days, baseline)
    else:
        sweep_cells = []

    report = build_report(calib, sweep_cells=sweep_cells, baseline_trades=baseline,
                          objective=args.objective)
    path = _write(report)
    print(json.dumps(report["calibration"], indent=2))

    # Print top 3 ranked sweep cells (advisory)
    if report["sweep_results"]:
        top3 = report["sweep_results"][:3]
        print("\n[BACKTEST] Top 3 advisory parameter cells:")
        for i, c in enumerate(top3, 1):
            m = c["metrics"]
            flag = " [low-volume]" if c.get("below_baseline_volume") else ""
            print(
                f"  #{i}  params={c['params']}  "
                f"trades={m['trades']}  win_rate={m['win_rate']}%  "
                f"expectancy={m['expectancy']}  sharpe={m['sharpe']}{flag}"
            )

    print(f"\n[BACKTEST] wrote {path}")


if __name__ == "__main__":
    main()
