"""
backtester.py - Auto-Backtester for ZiSi

Replays historical trades against current parameters to detect whether
we're leaving edge on the table. Runs once on startup and logs recommendations.

At 50+ trades it becomes statistically meaningful. With < 10 trades, it's
informational only and recommendations should be treated as directional hints.
"""

import json
import logging
import math
from pathlib import Path
from typing import Dict, List, Optional, Tuple

log = logging.getLogger("zisi.backtester")

_BASE_DIR = Path(__file__).parent
_TRADES_FILE = _BASE_DIR / "zisi_local_trades.jsonl"
_BT_RESULT_FILE = _BASE_DIR / "backtest_result.json"

_MIN_TRADES_FOR_RECOMMENDATION = 10


class AutoBacktester:
    """
    Replays closed trades against different parameter sets to find
    optimal signal_threshold and position sizing.
    """

    def load_trade_history(self, n: int = 100) -> List[dict]:
        """Load last N closed trades from zisi_local_trades.jsonl."""
        if not _TRADES_FILE.exists():
            return []

        trades = []
        try:
            for line in _TRADES_FILE.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or '"order_id"' not in line:
                    continue
                try:
                    t = json.loads(line)
                    if t.get("status", "").upper() == "CLOSED":
                        trades.append(t)
                except Exception:
                    pass
        except Exception as exc:
            log.warning("[BACKTEST] Failed to load trade history: %s", exc)

        return trades[-n:] if len(trades) > n else trades

    def simulate_with_params(
        self,
        trades: List[dict],
        signal_threshold: int,
        position_pct: float,
    ) -> dict:
        """
        Replay trades applying different parameter filters.

        signal_threshold: only include trades where confidence >= threshold
        position_pct:     scale position sizes by this factor relative to 2% base
        Returns: {total_trades, wins, win_rate, total_pnl, sharpe_ratio}
        """
        filtered = [
            t for t in trades
            if float(t.get("confidence", 0) or 0) >= signal_threshold
        ]

        if not filtered:
            return {"total_trades": 0, "wins": 0, "win_rate": 0.0, "total_pnl": 0.0, "sharpe_ratio": 0.0}

        pnl_list = []
        wins = 0
        for t in filtered:
            raw_profit = float(t.get("profit", 0) or 0)
            # Scale profit proportionally to position_pct
            original_size = float(t.get("size") or t.get("position_size") or 0)
            base_size = float(t.get("size") or t.get("position_size") or 0.02)
            if base_size > 0:
                scale = position_pct / max(base_size, 0.001)
                scaled_profit = raw_profit * min(scale, 5.0)  # cap scaling at 5×
            else:
                scaled_profit = raw_profit
            pnl_list.append(scaled_profit)
            if scaled_profit > 0:
                wins += 1

        total_pnl = sum(pnl_list)
        win_rate = wins / len(filtered)
        n = len(filtered)

        # Sharpe approximation (annualized, assuming 96 trades/day at 15-min cycles)
        if n >= 2:
            mean_pnl = total_pnl / n
            variance = sum((p - mean_pnl) ** 2 for p in pnl_list) / (n - 1)
            stdev = math.sqrt(variance) if variance > 0 else 0.001
            sharpe = (mean_pnl / stdev) * math.sqrt(96 * 365)
        else:
            sharpe = 0.0

        return {
            "total_trades": n,
            "wins": wins,
            "win_rate": round(win_rate, 4),
            "total_pnl": round(total_pnl, 4),
            "sharpe_ratio": round(sharpe, 3),
        }

    def find_optimal_params(self, trades: List[dict]) -> dict:
        """
        Grid search over signal_threshold × position_pct to maximize Sharpe.
        Returns the best parameter combination.
        """
        best_sharpe = -999.0
        best_params = {"signal_threshold": 6, "position_pct": 0.02}
        best_result = {}

        for threshold in [5, 6, 7, 8]:
            for pct in [0.01, 0.02, 0.03, 0.04, 0.05]:
                result = self.simulate_with_params(trades, threshold, pct)
                if result["total_trades"] < 3:
                    continue  # not enough trades in this bucket to be meaningful
                if result["sharpe_ratio"] > best_sharpe:
                    best_sharpe = result["sharpe_ratio"]
                    best_params = {"signal_threshold": threshold, "position_pct": pct}
                    best_result = result

        return {
            "best_params": best_params,
            "best_result": best_result,
            "best_sharpe": round(best_sharpe, 3),
        }

    def run_startup_backtest(self) -> dict:
        """
        Run on startup, compare current params vs optimal, log recommendations.
        Persists result to backtest_result.json for dashboard use.
        Returns a result dict with any recommendations.
        """
        trades = self.load_trade_history(n=100)
        n = len(trades)

        if n < _MIN_TRADES_FOR_RECOMMENDATION:
            log.info("[BACKTEST] Only %d trades — skipping (need %d for meaningful analysis)", n, _MIN_TRADES_FOR_RECOMMENDATION)
            return {"status": "insufficient_data", "n_trades": n}

        # Current params simulation (threshold=6, position=2%)
        current = self.simulate_with_params(trades, signal_threshold=6, position_pct=0.02)

        # Find optimal params
        optimal = self.find_optimal_params(trades)
        opt_params = optimal["best_params"]
        opt_result = optimal["best_result"]

        # Detect if we're leaving significant edge on the table
        pnl_gap = opt_result.get("total_pnl", 0) - current.get("total_pnl", 0)
        sharpe_gap = optimal["best_sharpe"] - current.get("sharpe_ratio", 0)
        suboptimal = pnl_gap > (current.get("total_pnl", 0) * 0.20) or sharpe_gap > 0.5

        result = {
            "status": "suboptimal" if suboptimal else "optimal",
            "n_trades": n,
            "current_params": {"signal_threshold": 6, "position_pct": 0.02},
            "current_result": current,
            "optimal_params": opt_params,
            "optimal_result": opt_result,
            "pnl_improvement": round(pnl_gap, 4),
            "sharpe_improvement": round(sharpe_gap, 3),
            "recommendation": (
                f"Consider threshold={opt_params['signal_threshold']}, "
                f"position_pct={opt_params['position_pct']:.2f} "
                f"(+{pnl_gap:+.2f} PnL, +{sharpe_gap:.2f} Sharpe)"
            ) if suboptimal else "Current parameters are near-optimal",
        }

        # Log findings
        log.info(
            "[BACKTEST] %d trades | current: WR=%.1f%% PnL=$%.2f Sharpe=%.2f",
            n, current["win_rate"] * 100, current["total_pnl"], current["sharpe_ratio"],
        )
        if suboptimal:
            log.warning(
                "[BACKTEST] Suboptimal params detected → %s",
                result["recommendation"],
            )
        else:
            log.info("[BACKTEST] Current parameters are near-optimal ✓")

        # Persist for dashboard
        try:
            _BT_RESULT_FILE.write_text(json.dumps(result, indent=2), encoding="utf-8")
        except Exception as exc:
            log.debug("[BACKTEST] Failed to persist result: %s", exc)

        return result
