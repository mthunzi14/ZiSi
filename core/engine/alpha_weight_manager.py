"""
alpha_weight_manager.py — Dynamic Alpha Weighting (Kakushadze & Serur §3.20)

Tracks rolling per-strategy realized returns and computes a Sharpe-based
sizing multiplier. When a strategy is performing well → scale up. When it's
underwater → scale down toward 0 to stop the bleed.

Usage:
    from core.engine.alpha_weight_manager import alpha_weights
    mult = alpha_weights.get_multiplier("FAIR_VAL")  # 0.0 – 1.5
    bet_usd *= mult

The multiplier is [0.0, 1.5]:
    0.0  — strategy is net-negative over the rolling window (reduce to zero)
    1.0  — neutral (default when insufficient data)
    1.5  — strategy has strong positive Sharpe (max boost)
"""
import json
import logging
import os
import time
from collections import deque
from typing import Deque, Dict, Optional

log = logging.getLogger("zisi.alpha_weight_manager")

_WINDOW = 20          # rolling trade window per strategy
_MIN_TRADES = 5       # minimum trades before weighting kicks in
_STATE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "alpha_weight_state.json",
)

_STRATEGY_KEYS = ("FAIR_VAL", "SIG", "CLOSE-SNIPE", "CLOSE-SNIPE-EARLY",
                  "REVERSAL-SNIPE", "REVERSAL-STREAK", "LAT-ARB", "SWEEP")


class AlphaWeightManager:
    def __init__(self) -> None:
        # Per-strategy rolling PnL history
        self._history: Dict[str, Deque[float]] = {k: deque(maxlen=_WINDOW) for k in _STRATEGY_KEYS}
        self._load()

    # ── Public API ────────────────────────────────────────────────────────────

    def record_trade(self, strategy: str, pnl: float) -> None:
        key = self._normalize(strategy)
        if key not in self._history:
            self._history[key] = deque(maxlen=_WINDOW)
        self._history[key].append(pnl)
        self._save()

    def get_multiplier(self, strategy: str) -> float:
        key = self._normalize(strategy)
        if key not in self._history:
            return 1.0
        trades = list(self._history[key])
        if len(trades) < _MIN_TRADES:
            return 1.0  # not enough data — neutral
        total_pnl = sum(trades)
        if total_pnl <= 0:
            # Net-negative rolling window: cut to 30% to stop the bleed
            log.info("[ALPHA-WEIGHT] %s: rolling PnL=%.2f < 0 → mult=0.30", key, total_pnl)
            return 0.30
        # Sharpe proxy: mean / std
        n = len(trades)
        mean = total_pnl / n
        variance = sum((t - mean) ** 2 for t in trades) / max(1, n - 1)
        std = variance ** 0.5
        sharpe = mean / std if std > 0 else mean * 10.0
        # Map sharpe → [1.0, 1.5]: positive Sharpe scales up proportionally
        mult = min(1.5, max(1.0, 1.0 + 0.20 * sharpe))
        log.debug("[ALPHA-WEIGHT] %s: n=%d pnl=%.2f sharpe=%.2f → mult=%.2f", key, n, total_pnl, sharpe, mult)
        return mult

    def get_status(self) -> dict:
        return {
            k: {
                "trades": len(v),
                "pnl": round(sum(v), 2),
                "mult": round(self.get_multiplier(k), 2),
            }
            for k, v in self._history.items()
            if len(v) > 0
        }

    # ── Internal ──────────────────────────────────────────────────────────────

    def _normalize(self, strategy: str) -> str:
        s = strategy.upper().replace("-", "_")
        mapping = {
            "FAIR_VAL": "FAIR_VAL", "SIG": "SIG", "SIGNAL": "SIG",
            "CLOSE_SNIPE": "CLOSE-SNIPE", "CLOSE-SNIPE": "CLOSE-SNIPE",
            "CLOSE_SNIPE_EARLY": "CLOSE-SNIPE-EARLY",
            "REVERSAL_SNIPE": "REVERSAL-SNIPE",
            "REVERSAL_STREAK": "REVERSAL-STREAK",
            "LAT_ARB": "LAT-ARB", "LATENCY_ARB": "LAT-ARB",
            "SWEEP": "SWEEP", "RESOLUTION_SWEEP": "SWEEP",
        }
        return mapping.get(s, s)

    def _save(self) -> None:
        try:
            data = {k: list(v) for k, v in self._history.items()}
            with open(_STATE_PATH, "w", encoding="utf-8") as f:
                json.dump({"ts": time.time(), "history": data}, f)
        except Exception as e:
            log.debug("[ALPHA-WEIGHT] Save failed: %s", e)

    def _load(self) -> None:
        try:
            if os.path.exists(_STATE_PATH):
                with open(_STATE_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for k, v in data.get("history", {}).items():
                    if k not in self._history:
                        self._history[k] = deque(maxlen=_WINDOW)
                    for pnl in v[-_WINDOW:]:
                        self._history[k].append(float(pnl))
        except Exception as e:
            log.debug("[ALPHA-WEIGHT] Load failed: %s", e)


# Singleton
alpha_weights = AlphaWeightManager()
