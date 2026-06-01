"""Lag-sensitivity validation of the fair-value signal (honest replacement for a
single GO/NO-GO, which the kline data cannot truthfully produce).

The captured edge depends entirely on how far the market lags true spot. So we sweep
`lag_min` over a range and report, per lag: trades, win-rate, avg entry, net expectancy,
total P&L. Interpretation: lag=0 should show ~no edge (efficient market); the curve tells
us how many minutes of *lead* the strategy needs to be profitable. The REAL go/no-go is
the Ireland VPS demo, which measures our actual lead against live Polymarket quotes.

Run:  python -m tools.validate_fairvalue --days 14
"""
import argparse
import json
import os
import time
from dataclasses import replace
from statistics import mean
from typing import List

_RESULTS = os.path.join(os.path.dirname(__file__), "backtest", "results")


def summarize(trades: List) -> dict:
    if not trades:
        return {"trades": 0, "win_rate": 0.0, "avg_entry": 0.0,
                "net_expectancy": 0.0, "total_pnl": 0.0}
    wins = sum(1 for t in trades if t.realized_pnl > 0)
    return {
        "trades": len(trades),
        "win_rate": round(wins / len(trades), 4),
        "avg_entry": round(mean(t.entry_price for t in trades), 4),
        "net_expectancy": round(mean(t.realized_pnl for t in trades), 4),
        "total_pnl": round(sum(t.realized_pnl for t in trades), 2),
    }


def lag_sensitivity(candles_1m_by_asset: dict, lags: List[int], base_cfg) -> List[dict]:
    """Run the value simulator once per lag value; return one summary row per lag."""
    from tools.backtest.value_simulator import simulate_value
    rows = []
    for lag in lags:
        cfg = replace(base_cfg, lag_min=lag)
        trades = simulate_value(candles_1m_by_asset, cfg)
        rows.append({"lag_min": lag, **summarize(trades)})
    return rows


def interpret(rows: List[dict]) -> str:
    """Plain-English read of the lag curve."""
    profitable = [r for r in rows if r["net_expectancy"] > 0 and r["trades"] > 0]
    if not profitable:
        return ("NO-GO (in range): no lag setting yields positive net expectancy. "
                "The edge is not present in this data/horizon.")
    min_lag = min(r["lag_min"] for r in profitable)
    if min_lag == 0:
        return ("CAUTION: positive expectancy even at lag=0 — re-check the model for "
                "residual lookahead; a truly efficient market should show ~no edge at lag 0.")
    return (f"GO (conditional): profitable once the market lags by >= {min_lag} min, i.e. ZiSi "
            f"needs ~{min_lag} min of lead. The VPS demo must confirm we actually have that lead.")


def run(days: int = 14, lags: List[int] = None) -> dict:
    from tools.backtest.klines import fetch_klines
    from tools.backtest.value_simulator import ValueConfig
    lags = lags or [0, 1, 2, 3]
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - days * 86_400_000
    candles = {a: fetch_klines(a, "1m", start_ms, now_ms) for a in ("BTC", "ETH")}
    rows = lag_sensitivity(candles, lags, ValueConfig())
    report = {"days": days, "lags": lags, "sensitivity": rows, "verdict": interpret(rows)}
    os.makedirs(_RESULTS, exist_ok=True)
    path = os.path.join(_RESULTS, f"fairvalue_lag_{int(time.time())}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
    report["path"] = path
    return report


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="ZiSi fair-value lag-sensitivity validation")
    ap.add_argument("--days", type=int, default=14)
    args = ap.parse_args(argv)
    rep = run(args.days)
    print(f"{'lag_min':>7} {'trades':>7} {'win_rate':>9} {'avg_entry':>10} "
          f"{'net_exp':>9} {'total_pnl':>10}")
    for r in rep["sensitivity"]:
        print(f"{r['lag_min']:>7} {r['trades']:>7} {r['win_rate']:>9.3f} "
              f"{r['avg_entry']:>10.3f} {r['net_expectancy']:>9.4f} {r['total_pnl']:>10.2f}")
    print(f"\nVERDICT: {rep['verdict']}")
    print(f"[VALIDATE] wrote {rep['path']}")


if __name__ == "__main__":
    main()
