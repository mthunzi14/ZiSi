"""
regime_detector.py – Enhanced multi-signal market regime classifier.

Classifies the market into one of four regimes and outputs trading
parameters calibrated for each:

  TRENDING        – Directional momentum detected; lower entry hurdles,
                    trailing exits, aggressive sizing.
  MEAN_REVERTING  – Price oscillates around a mean; tighter hurdles,
                    fixed-target exits, baseline sizing.
  VOLATILE_CHAOS  – Extreme unpredictable swings; very tight hurdles,
                    tight stops, minimal sizing.
  COMPRESSION     – Low-volatility squeeze; slightly reduced hurdles,
                    hold-through-breakout exits, moderate sizing.

Classification signals:
  1. ATR percentile (50-candle lookback)
  2. Order Book Imbalance ratio (injected via update_context)
  3. Volume ratio: current / 20-period average (injected via update_context)
  4. Bollinger Band Width percentile (50-candle lookback, 20-period BB)
"""

import json
import logging
import math
import os
import time
from collections import deque
from typing import Optional

log = logging.getLogger("zisi.regime_detector")

_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
_REGIME_STATUS_PATH = os.path.join(_PROJECT_ROOT, "regime_status.json")

# ── Regime definitions ───────────────────────────────────────────────────────

REGIMES = {
    "TRENDING": {
        "label": "Trending / Directional",
        "hurdle_multiplier": 0.85,
        "exit_strategy": "trailing",
        "kelly_mult": 1.20,
    },
    "MEAN_REVERTING": {
        "label": "Mean-Reverting",
        "hurdle_multiplier": 1.15,
        "exit_strategy": "fixed_target",
        "kelly_mult": 1.00,
    },
    "VOLATILE_CHAOS": {
        "label": "Volatile / Chaotic",
        "hurdle_multiplier": 1.40,
        "exit_strategy": "tight_stop",
        "kelly_mult": 0.30,
    },
    "COMPRESSION": {
        "label": "Compression / Squeeze",
        "hurdle_multiplier": 0.90,
        "exit_strategy": "breakout_hold",
        "kelly_mult": 1.10,
    },
}

# Percentile thresholds used by the scoring engine
_ATR_HIGH_PCT = 80      # above → high volatility signal
_ATR_LOW_PCT = 20       # below → low volatility signal
_BBW_HIGH_PCT = 80      # wide bands
_BBW_LOW_PCT = 20       # narrow bands (compression)


class RegimeDetector:
    """Multi-signal market regime classifier.

    Feed prices via ``update_price`` / ``update_prices`` and inject
    real-time context (order-book imbalance, volume ratio) via
    ``update_context``.  The regime is re-evaluated automatically.

    Public properties expose all regime-aware parameters that
    downstream signal evaluation and position sizing need.
    """

    # ── Construction ──────────────────────────────────────────────────────

    def __init__(
        self,
        timeframe: str = "5m",
        atr_window: int = 14,
        price_lookback: int = 50,
        bb_period: int = 20,
        bb_std: float = 2.0,
    ) -> None:
        self.timeframe: str = timeframe.lower()
        self.atr_window: int = atr_window
        self.price_lookback: int = price_lookback
        self.bb_period: int = bb_period
        self.bb_std: float = bb_std

        # Price history – enough for ATR + BB + percentile ranking
        self._price_history: deque[tuple[float, float]] = deque(
            maxlen=max(price_lookback + 1, atr_window + 1, bb_period + 1)
        )

        # Current state
        self._current_regime: str = "COMPRESSION"
        self._current_atr: float = 0.0
        self._regime_confidence: float = 0.0

        # Context signals injected externally
        self._obi: float = 0.0          # order-book imbalance  [-1, 1]
        self._volume_ratio: float = 1.0  # current_vol / avg_vol

        # Internal caches for the scoring engine
        self._atr_percentile: float = 50.0
        self._bbw_percentile: float = 50.0

        log.info(
            "[RegimeDetector] initialised — timeframe=%s ATR window=%d "
            "lookback=%d BB(%d, %.1f)",
            timeframe, atr_window, price_lookback, bb_period, bb_std,
        )

    # ── Price ingestion ───────────────────────────────────────────────────

    def update_price(self, price: float, symbol: str = "BTC") -> None:
        """Feed the latest price; regime is recalculated automatically."""
        if price <= 0:
            return
        self._price_history.append((time.time(), price))
        self._recalculate()
        log.debug(
            "[Regime] %s price=%.2f → regime=%s (conf=%.0f%%) ATR=%.4f%% "
            "Kelly×%.2f hurdle×%.2f exit=%s",
            symbol, price, self._current_regime,
            self._regime_confidence * 100, self._current_atr,
            self.kelly_multiplier, self.hurdle_multiplier, self.exit_strategy,
        )

    def update_prices(self, prices: list[float], symbol: str = "BTC") -> None:
        """Feed multiple prices at once; recalculates once at the end."""
        valid = [p for p in prices if p > 0]
        if not valid:
            return
        now = time.time()
        for p in valid:
            self._price_history.append((now, p))
        self._recalculate()
        log.debug(
            "[Regime] %s bulk update (%d) → regime=%s ATR=%.4f%% Kelly×%.2f",
            symbol, len(valid), self._current_regime,
            self._current_atr, self.kelly_multiplier,
        )

    # ── External context injection ────────────────────────────────────────

    def update_context(self, obi: float = 0.0, volume_ratio: float = 1.0) -> None:
        """Inject real-time context signals.

        Args:
            obi: Order Book Imbalance ratio in [-1, 1].
                 Positive = bid-heavy, negative = ask-heavy.
            volume_ratio: current volume / 20-period average volume.
        """
        self._obi = max(-1.0, min(1.0, obi))
        self._volume_ratio = max(0.0, volume_ratio)
        # Re-classify with updated context (only if we have price data)
        if len(self._price_history) >= 2:
            self._recalculate()

    # ── Core classification engine ────────────────────────────────────────

    def _recalculate(self) -> None:
        """Run the full multi-signal classification pipeline."""
        prices = [p for _, p in self._price_history]
        if len(prices) < 2:
            return

        # 1. Compute ATR (mean absolute % change)
        pct_changes = [
            abs(prices[i] - prices[i - 1]) / prices[i - 1] * 100
            for i in range(1, len(prices))
        ]
        atr = sum(pct_changes[-self.atr_window:]) / len(pct_changes[-self.atr_window:])
        self._current_atr = round(atr, 4)

        # 2. ATR percentile over full lookback
        self._atr_percentile = self._percentile_rank(
            pct_changes, atr
        )

        # 3. Bollinger Band Width percentile
        self._bbw_percentile = self._compute_bbw_percentile(prices)

        # 4. Score each regime candidate
        scores = self._score_regimes()

        # 5. Pick winner + compute confidence
        best_regime = max(scores, key=scores.get)
        best_score = scores[best_regime]
        total = sum(scores.values()) or 1.0
        self._regime_confidence = round(best_score / total, 4)
        self._current_regime = best_regime

        self._write_status()

    def _score_regimes(self) -> dict[str, float]:
        """Return a score dict for each candidate regime.

        Scoring heuristic (additive, non-negative):
          * ATR percentile → high favours VOLATILE_CHAOS or TRENDING;
                             low favours COMPRESSION.
          * BBW percentile → mirrors ATR but captures price-range squeeze.
          * OBI magnitude  → high |OBI| → directional pressure (TRENDING);
                             low |OBI| → balanced book (MEAN_REVERTING).
          * Volume ratio   → surge above average → TRENDING or VOLATILE_CHAOS;
                             low → COMPRESSION.
        """
        atr_p = self._atr_percentile
        bbw_p = self._bbw_percentile
        abs_obi = abs(self._obi)
        vr = self._volume_ratio

        scores: dict[str, float] = {
            "TRENDING": 0.0,
            "MEAN_REVERTING": 0.0,
            "VOLATILE_CHAOS": 0.0,
            "COMPRESSION": 0.0,
        }

        # ── ATR percentile contribution ──────────────────────────────────
        if atr_p >= _ATR_HIGH_PCT:
            # Very high volatility — could be chaos or trending
            scores["VOLATILE_CHAOS"] += 2.0
            scores["TRENDING"] += 1.0
        elif atr_p <= _ATR_LOW_PCT:
            scores["COMPRESSION"] += 2.5
            scores["MEAN_REVERTING"] += 0.5
        else:
            # Mid-range ATR
            scores["MEAN_REVERTING"] += 1.5
            scores["TRENDING"] += 1.0

        # ── BBW percentile contribution ──────────────────────────────────
        if bbw_p >= _BBW_HIGH_PCT:
            scores["VOLATILE_CHAOS"] += 1.5
            scores["TRENDING"] += 1.0
        elif bbw_p <= _BBW_LOW_PCT:
            scores["COMPRESSION"] += 2.0
        else:
            scores["MEAN_REVERTING"] += 1.0

        # ── OBI contribution ─────────────────────────────────────────────
        if abs_obi >= 0.5:
            # Strong directional imbalance
            scores["TRENDING"] += 2.5
        elif abs_obi >= 0.2:
            scores["TRENDING"] += 1.0
            scores["MEAN_REVERTING"] += 0.5
        else:
            # Balanced book
            scores["MEAN_REVERTING"] += 1.5
            scores["COMPRESSION"] += 0.5

        # ── Volume ratio contribution ────────────────────────────────────
        if vr >= 2.0:
            # Volume surge
            scores["TRENDING"] += 2.0
            scores["VOLATILE_CHAOS"] += 1.5
        elif vr >= 1.3:
            scores["TRENDING"] += 1.0
        elif vr <= 0.5:
            scores["COMPRESSION"] += 1.5
            scores["MEAN_REVERTING"] += 0.5
        else:
            scores["MEAN_REVERTING"] += 0.5

        # ── Coherence bonus: if ATR high AND OBI high → extra TRENDING ───
        if atr_p >= 60 and abs_obi >= 0.3 and vr >= 1.3:
            scores["TRENDING"] += 1.5

        # ── Chaos signal: ATR high but OBI low → no direction ────────────
        if atr_p >= _ATR_HIGH_PCT and abs_obi < 0.15:
            scores["VOLATILE_CHAOS"] += 2.0

        return scores

    # ── Helper: percentile rank ───────────────────────────────────────────

    @staticmethod
    def _percentile_rank(values: list[float], current: float) -> float:
        """Return the percentile rank (0-100) of *current* within *values*."""
        if not values:
            return 50.0
        below = sum(1 for v in values if v < current)
        return round((below / len(values)) * 100, 2)

    # ── Helper: Bollinger Band Width percentile ───────────────────────────

    def _compute_bbw_percentile(self, prices: list[float]) -> float:
        """Compute the current Bollinger Band Width and its percentile
        rank over the lookback window."""
        n = self.bb_period
        if len(prices) < n:
            return 50.0  # not enough data

        # Compute rolling BBW for every window of size n in the lookback
        bbw_values: list[float] = []
        for end in range(n, len(prices) + 1):
            window = prices[end - n : end]
            mean = sum(window) / n
            if mean == 0:
                continue
            variance = sum((p - mean) ** 2 for p in window) / n
            std = math.sqrt(variance)
            upper = mean + self.bb_std * std
            lower = mean - self.bb_std * std
            bbw = (upper - lower) / mean * 100  # width as % of mean
            bbw_values.append(bbw)

        if not bbw_values:
            return 50.0

        current_bbw = bbw_values[-1]
        return self._percentile_rank(bbw_values, current_bbw)

    # ── Public API – properties ───────────────────────────────────────────

    @property
    def regime(self) -> str:
        """Current regime label."""
        return self._current_regime

    @property
    def atr(self) -> float:
        """Current ATR as a percentage."""
        return self._current_atr

    @property
    def kelly_multiplier(self) -> float:
        """Kelly fraction multiplier for current regime."""
        return REGIMES.get(self._current_regime, REGIMES["COMPRESSION"])["kelly_mult"]

    @property
    def hurdle_multiplier(self) -> float:
        """Entry-hurdle multiplier — scales confidence thresholds."""
        return REGIMES.get(self._current_regime, REGIMES["COMPRESSION"])["hurdle_multiplier"]

    @property
    def exit_strategy(self) -> str:
        """Recommended exit strategy for current regime."""
        return REGIMES.get(self._current_regime, REGIMES["COMPRESSION"])["exit_strategy"]

    @property
    def regime_confidence(self) -> float:
        """Classification confidence (0-1).  Higher = more certain."""
        return self._regime_confidence

    # ── Public API – status dict ──────────────────────────────────────────

    def get_status(self) -> dict:
        """Return a comprehensive status dict for dashboards / logging."""
        cfg = REGIMES.get(self._current_regime, {})
        return {
            "regime": self._current_regime,
            "label": cfg.get("label", self._current_regime),
            "regime_confidence": self._regime_confidence,
            "atr_pct": self._current_atr,
            "atr_percentile": self._atr_percentile,
            "bbw_percentile": self._bbw_percentile,
            "obi": self._obi,
            "volume_ratio": self._volume_ratio,
            "kelly_multiplier": self.kelly_multiplier,
            "hurdle_multiplier": self.hurdle_multiplier,
            "exit_strategy": self.exit_strategy,
            "price_samples": len(self._price_history),
            "atr_window": self.atr_window,
        }

    # ── Public API – regime-aware signal parameters ───────────────────────

    def get_regime_for_signal(self) -> dict:
        """Return a dict with all regime-aware parameters that the
        signal evaluation pipeline needs.

        Returns:
            dict with keys:
              regime, label, confidence, kelly_multiplier,
              hurdle_multiplier, exit_strategy, atr_pct,
              atr_percentile, bbw_percentile, obi, volume_ratio
        """
        cfg = REGIMES.get(self._current_regime, REGIMES["COMPRESSION"])
        return {
            "regime": self._current_regime,
            "label": cfg["label"],
            "confidence": self._regime_confidence,
            "kelly_multiplier": cfg["kelly_mult"],
            "hurdle_multiplier": cfg["hurdle_multiplier"],
            "exit_strategy": cfg["exit_strategy"],
            "atr_pct": self._current_atr,
            "atr_percentile": self._atr_percentile,
            "bbw_percentile": self._bbw_percentile,
            "obi": self._obi,
            "volume_ratio": self._volume_ratio,
        }

    # ── Persistence ───────────────────────────────────────────────────────

    def _write_status(self) -> None:
        """Persist current regime status to regime_status.json."""
        try:
            payload = {**self.get_status(), "last_updated": time.time()}
            with open(_REGIME_STATUS_PATH, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2)
        except OSError as exc:
            log.debug("[RegimeDetector] status write failed: %s", exc)
