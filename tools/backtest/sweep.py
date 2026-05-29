"""Advisory parameter sweep. Computes metrics per cell and ranks them.
NEVER writes config.py — output is for human review only."""
import itertools
from statistics import mean, pstdev
from typing import Dict, List


def cell_metrics(pnls: List[float]) -> Dict[str, float]:
    n = len(pnls)
    if n == 0:
        return {"trades": 0, "wins": 0, "win_rate": 0.0, "total_pnl": 0.0,
                "expectancy": 0.0, "sharpe": 0.0, "max_drawdown": 0.0}
    wins = sum(1 for p in pnls if p > 0)
    total = sum(pnls)
    sd = pstdev(pnls) if n > 1 else 0.0
    # Max drawdown over the cumulative P&L curve
    cum, peak, mdd = 0.0, 0.0, 0.0
    for p in pnls:
        cum += p
        peak = max(peak, cum)
        mdd = min(mdd, cum - peak)
    return {
        "trades": n,
        "wins": wins,
        "win_rate": round(100.0 * wins / n, 1),
        "total_pnl": round(total, 4),
        "expectancy": round(mean(pnls), 4),
        "sharpe": round((mean(pnls) / sd), 4) if sd > 0 else 0.0,
        "max_drawdown": round(mdd, 4),
    }


def build_grid() -> List[dict]:
    """Return a list of signal_params override dicts spanning the sweep.

    Grid dimensions:
        rsi_up             in {58, 60, 62}
        rsi_dn             in {38, 40, 42}
        target_threshold   in {0.85, 0.88, 0.90}

    Each cell is a flat dict like:
        {"rsi_up": 58, "rsi_dn": 40, "target_threshold": 0.88}

    Total cells: 3 * 3 * 3 = 27
    """
    rsi_ups = [58, 60, 62]
    rsi_dns = [38, 40, 42]
    thresholds = [0.85, 0.88, 0.90]
    return [
        {"rsi_up": up, "rsi_dn": dn, "target_threshold": thr}
        for up, dn, thr in itertools.product(rsi_ups, rsi_dns, thresholds)
    ]


def rank_cells(cells: List[dict], baseline_trades: int,
               objective: str = "expectancy") -> List[dict]:
    """Return cells sorted best-first by `objective`; flag volume-reducing cells."""
    for c in cells:
        c["below_baseline_volume"] = c["metrics"]["trades"] < baseline_trades
    return sorted(cells, key=lambda c: (c["metrics"].get(objective, 0.0),
                                        c["metrics"]["total_pnl"]), reverse=True)
