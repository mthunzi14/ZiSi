"""REBUILD validation: A/B the FV directional-drift model vs the old driftless model.

No-lookahead replay of cached BTC 1m candles. Reports overall and ATM-band (44-56c)
win-rate + PnL for FLAT (driftless, old) vs DRIFT (momentum drift + confidence gate, new).
Run:  python -m tools.backtest.run_fv_validation
"""
import glob
import json
import os

from backtest.klines import Candle
from backtest.value_simulator import simulate_value, ValueConfig

_CACHE = os.path.join(os.path.dirname(__file__), "cache")


def load_1m(asset="BTC"):
    files = sorted(glob.glob(os.path.join(_CACHE, f"{asset}_1m_*.json")),
                   key=os.path.getsize, reverse=True)
    if not files:
        raise SystemExit(f"no cached 1m data for {asset}")
    rows = json.load(open(files[0], encoding="utf-8"))
    return [Candle.from_binance(r) for r in rows], os.path.basename(files[0])


def metrics(trades, lo=None, hi=None):
    sel = [t for t in trades if (lo is None or (lo <= t.entry_price <= hi))]
    n = len(sel)
    w = sum(1 for t in sel if t.realized_pnl > 0)
    pnl = sum(t.realized_pnl for t in sel)
    return n, w, (100.0 * w / n if n else 0.0), pnl


def main():
    candles, fname = load_1m("BTC")
    print(f"Loaded {len(candles)} BTC 1m candles (~{len(candles)/1440:.1f} days) from {fname}\n")
    print(f"{'tf':>3} {'mode':<6} {'trades':>7} {'WR%':>6} {'PnL$':>9} | {'ATM n':>6} {'ATM WR%':>8} {'ATM PnL$':>9}")
    print("-" * 72)
    for wm in (5, 15):
        rows = {}
        for use_drift in (False, True):
            cfg = ValueConfig(window_min=wm, lag_min=1, use_drift=use_drift)
            tr = simulate_value({"BTC": candles}, cfg)
            n, w, wr, pnl = metrics(tr)
            an, aw, awr, apnl = metrics(tr, 0.44, 0.56)
            tag = "DRIFT" if use_drift else "FLAT"
            rows[tag] = (n, wr, pnl, an, awr, apnl)
            print(f"{wm:>2}m {tag:<6} {n:>7} {wr:>6.1f} {pnl:>9.2f} | {an:>6} {awr:>8.1f} {apnl:>9.2f}")
        # verdict per timeframe
        f, d = rows["FLAT"], rows["DRIFT"]
        print(f"    -> ATM WR {f[4]:.1f}% -> {d[4]:.1f}%  ({'+' if d[4]>=f[4] else ''}{d[4]-f[4]:.1f}pp), "
              f"overall PnL ${f[2]:.2f} -> ${d[2]:.2f}\n")


if __name__ == "__main__":
    main()
