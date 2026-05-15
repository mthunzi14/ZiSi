"""
regime_detector.py - ATR-based market regime classifier.

Detects whether the market is in a SHOCK, VOLATILE, NORMAL, or RANGE-BOUND
state and provides a Kelly multiplier so position sizing scales with actual
market conditions rather than assuming constant volatility.

ATR (Average True Range) is approximated from recent price deltas.

Regimes and Kelly multipliers:
  SHOCK    ATR > 5%  → 0.20  (extreme volatility — tiny positions)
  VOLATILE ATR 3-5%  → 0.60  (elevated vol — cautious)
  NORMAL   ATR 1-3%  → 1.00  (baseline — full Kelly)
  RANGE    ATR < 1%  → 1.30  (low vol — slightly larger positions OK)
"""

import json
import logging
import os
import time
from collections import deque
from typing import Optional

log = logging.getLogger("zisi.regime_detector")

_REGIME_STATUS_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "regime_status.json"
)

REGIMES = {
    "SHOCK":    {"min_atr": 5.0,  "max_atr": float("inf"), "kelly_mult": 0.20, "label": "Extreme Volatility"},
    "VOLATILE": {"min_atr": 3.0,  "max_atr": 5.0,          "kelly_mult": 0.60, "label": "Elevated Volatility"},
    "NORMAL":   {"min_atr": 1.0,  "max_atr": 3.0,          "kelly_mult": 1.00, "label": "Normal"},
    "RANGE":    {"min_atr": 0.0,  "max_atr": 1.0,          "kelly_mult": 1.30, "label": "Range-Bound"},
}


class RegimeDetector:
    def __init__(self, atr_window: int = 14):
        self.atr_window = atr_window
        # Store (timestamp, price) tuples
        self._price_history: deque = deque(maxlen=atr_window + 1)
        self._current_regime: str = "NORMAL"
        self._current_atr: float = 0.0
        self.kelly_multiplier: float = 1.00
        log.info("[RegimeDetector] initialised — ATR window=%d", atr_window)

    # ── Price ingestion ────────────────────────────────────────────────────────

    def update_price(self, price: float, symbol: str = "BTC") -> None:
        """Feed the latest price; regime is recalculated automatically."""
        if price <= 0:
            return
        self._price_history.append((time.time(), price))
        self._recalculate()
        log.debug(
            "[Regime] %s price=%.2f → regime=%s ATR=%.2f%% Kelly×%.2f",
            symbol, price, self._current_regime, self._current_atr, self.kelly_multiplier,
        )

    # ── ATR + regime logic ─────────────────────────────────────────────────────

    def _recalculate(self) -> None:
        prices = [p for _, p in self._price_history]
        if len(prices) < 2:
            return

        # Approximate ATR as mean absolute % change between consecutive prices
        pct_changes = [
            abs(prices[i] - prices[i - 1]) / prices[i - 1] * 100
            for i in range(1, len(prices))
        ]
        atr = sum(pct_changes) / len(pct_changes)
        self._current_atr = round(atr, 4)

        # Classify
        for name, cfg in REGIMES.items():
            if cfg["min_atr"] <= atr < cfg["max_atr"]:
                self._current_regime = name
                self.kelly_multiplier = cfg["kelly_mult"]
                break
        # SHOCK has no upper bound — catches anything above 5%
        if atr >= 5.0:
            self._current_regime = "SHOCK"
            self.kelly_multiplier = REGIMES["SHOCK"]["kelly_mult"]

        self._write_status()

    # ── Public API ─────────────────────────────────────────────────────────────

    @property
    def regime(self) -> str:
        return self._current_regime

    @property
    def atr(self) -> float:
        return self._current_atr

    def get_status(self) -> dict:
        regime_cfg = REGIMES.get(self._current_regime, {})
        return {
            "regime": self._current_regime,
            "label": regime_cfg.get("label", self._current_regime),
            "atr_pct": self._current_atr,
            "kelly_multiplier": self.kelly_multiplier,
            "price_samples": len(self._price_history),
            "atr_window": self.atr_window,
        }

    def _write_status(self) -> None:
        try:
            payload = {**self.get_status(), "last_updated": time.time()}
            with open(_REGIME_STATUS_PATH, "w", encoding="utf-8") as fh:
                json.dump(payload, fh)
        except OSError as exc:
            log.debug("[RegimeDetector] status write failed: %s", exc)
