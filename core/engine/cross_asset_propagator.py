"""
cross_asset_propagator.py — Cross-Asset Signal Propagation Engine.

Detects when BTC makes a strong directional move and anticipates the
cascade into correlated assets (ETH, SOL, XRP).

Core mechanics:
  1. Lead-Lag Detection — track BTC price velocity; when BTC moves
     > threshold% in < 30 s, flag a 'cascade event'.
  2. Correlation Matrix — maintain a rolling 1-hour Pearson correlation
     between BTC and each alt-coin from synchronized price changes.
  3. Signal Generation — when cascade + correlation > 0.7, emit a
     pre-emptive trading signal for the lagging asset.
  4. Empirical Lag Measurement — track actual delay between BTC move
     and each alt's response for adaptive timing.

Public API:
  update_price(asset, price, timestamp)
  check_cascade() -> list[dict]
  get_correlation_matrix() -> dict
  get_status() -> dict
"""

import json
import logging
import math
import os
import time
from collections import deque
from typing import Optional

log = logging.getLogger("zisi.cross_asset_propagator")

# ── Persistence path ──────────────────────────────────────────────────────────
_STATE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "cascade_state.json",
)

# ── Default configuration ─────────────────────────────────────────────────────
_DEFAULT_LEAD = "BTC"

# Per-pair cascade thresholds: (min_pct_move, max_window_seconds)
_DEFAULT_THRESHOLDS: dict[str, tuple[float, float]] = {
    "ETH": (0.15, 30.0),
    "SOL": (0.15, 30.0),
    "XRP": (0.15, 30.0),
}

# Minimum correlation to generate a pre-emptive signal
_MIN_CORRELATION: float = 0.70

# Rolling price-history window: 1 hour of data (3 600 entries ≈ 1/sec)
_HISTORY_MAXLEN: int = 3_600

# Correlation computation: minimum overlapping samples required
_MIN_CORRELATION_SAMPLES: int = 30

# How long a cascade event stays "active" for signal generation (seconds)
_CASCADE_ACTIVE_WINDOW: float = 60.0

# Lag measurement: how long to wait for alt response (seconds)
_LAG_OBSERVATION_WINDOW: float = 120.0


# ── Helper: Pearson correlation ───────────────────────────────────────────────

def _pearson(xs: list[float], ys: list[float]) -> Optional[float]:
    """
    Compute Pearson correlation coefficient between two equal-length series.

    Returns None if the series is too short or has zero variance.
    """
    n = len(xs)
    if n < _MIN_CORRELATION_SAMPLES or n != len(ys):
        return None

    mean_x = sum(xs) / n
    mean_y = sum(ys) / n

    cov = sum((xs[i] - mean_x) * (ys[i] - mean_y) for i in range(n))
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)

    denom = math.sqrt(var_x * var_y)
    if denom == 0.0:
        return None

    return round(cov / denom, 6)


class CrossAssetPropagator:
    """
    Detects BTC-led cascade events and generates pre-emptive signals
    for correlated lagging assets.
    """

    def __init__(
        self,
        lead_asset: str = _DEFAULT_LEAD,
        thresholds: Optional[dict[str, tuple[float, float]]] = None,
        min_correlation: float = _MIN_CORRELATION,
        history_maxlen: int = _HISTORY_MAXLEN,
    ):
        self._lead = lead_asset.upper()
        self._thresholds = thresholds or dict(_DEFAULT_THRESHOLDS)
        self._min_corr = min_correlation
        self._history_maxlen = history_maxlen

        # Price histories: asset → deque[(timestamp, price)]
        self._prices: dict[str, deque[tuple[float, float]]] = {}
        self._ensure_deque(self._lead)
        for alt in self._thresholds:
            self._ensure_deque(alt.upper())

        # Active cascade events: list of dicts
        self._active_cascades: list[dict] = []

        # Empirical lag measurements: alt → deque[lag_seconds]
        self._lag_history: dict[str, deque[float]] = {
            alt.upper(): deque(maxlen=100) for alt in self._thresholds
        }

        # Pending lag observations: alt → {btc_move_ts, btc_direction, btc_price}
        self._pending_lag_obs: dict[str, dict] = {}

        # Cached correlation matrix
        self._corr_cache: dict[str, Optional[float]] = {}
        self._corr_cache_ts: float = 0.0
        self._corr_cache_ttl: float = 5.0  # recompute at most every 5 s

        # Load persisted state
        self._load_state()

        log.info(
            "[CrossAssetPropagator] initialised — lead=%s alts=%s min_corr=%.2f",
            self._lead,
            list(self._thresholds.keys()),
            self._min_corr,
        )

    # ── Private helpers ───────────────────────────────────────────────────────

    def _ensure_deque(self, asset: str) -> None:
        """Lazily create a price deque for *asset* if it doesn't exist."""
        asset = asset.upper()
        if asset not in self._prices:
            self._prices[asset] = deque(maxlen=self._history_maxlen)

    def _pct_change(self, old: float, new: float) -> float:
        """Percentage change from *old* to *new*."""
        if old == 0:
            return 0.0
        return (new - old) / old * 100.0

    # ── Public API: update_price ──────────────────────────────────────────────

    def update_price(self, asset: str, price: float, timestamp: Optional[float] = None) -> None:
        """
        Ingest a new price tick for *asset*.

        Parameters
        ----------
        asset : str
            Asset symbol, e.g. ``"BTC"``, ``"ETH"``.
        price : float
            Latest price in USD.
        timestamp : float, optional
            Unix epoch; defaults to ``time.time()``.
        """
        if price <= 0:
            return

        asset = asset.upper()
        ts = timestamp or time.time()
        self._ensure_deque(asset)
        self._prices[asset].append((ts, price))

        # If this is the lead asset, check for cascade trigger
        if asset == self._lead:
            self._check_lead_velocity(ts, price)

        # If this is a lagging asset, check pending lag observations
        if asset != self._lead and asset in self._pending_lag_obs:
            self._update_lag_observation(asset, ts, price)

    # ── Lead velocity detection ───────────────────────────────────────────────

    def _check_lead_velocity(self, now: float, current_price: float) -> None:
        """
        Scan the lead-asset price history to detect rapid moves that
        exceed per-alt thresholds.  Creates cascade events when triggered.
        """
        history = self._prices[self._lead]
        if len(history) < 2:
            return

        for alt, (min_pct, max_window) in self._thresholds.items():
            alt = alt.upper()
            # Find the price *max_window* seconds ago (or the oldest within window)
            ref_price: Optional[float] = None
            ref_ts: Optional[float] = None
            for ts, px in history:
                if now - ts <= max_window:
                    ref_price = px
                    ref_ts = ts
                    break

            if ref_price is None or ref_ts is None:
                continue

            pct_move = self._pct_change(ref_price, current_price)
            elapsed = now - ref_ts

            if abs(pct_move) >= min_pct and elapsed <= max_window:
                direction = "UP" if pct_move > 0 else "DOWN"

                # Deduplicate: don't fire another cascade for the same alt
                # if one is still active
                already_active = any(
                    c["alt"] == alt
                    and now - c["timestamp"] < _CASCADE_ACTIVE_WINDOW
                    for c in self._active_cascades
                )
                if already_active:
                    continue

                cascade = {
                    "alt": alt,
                    "direction": direction,
                    "btc_pct_move": round(pct_move, 4),
                    "elapsed_sec": round(elapsed, 2),
                    "timestamp": now,
                    "btc_price": current_price,
                }
                self._active_cascades.append(cascade)
                log.info(
                    "[Cascade] %s moved %.4f%% in %.1fs → flagging cascade for %s (%s)",
                    self._lead, pct_move, elapsed, alt, direction,
                )

                # Set up lag observation for empirical measurement
                self._pending_lag_obs[alt] = {
                    "btc_move_ts": now,
                    "btc_direction": direction,
                    "btc_price": current_price,
                }

        # Prune expired cascades
        self._active_cascades = [
            c for c in self._active_cascades
            if now - c["timestamp"] < _CASCADE_ACTIVE_WINDOW
        ]

    # ── Empirical lag measurement ─────────────────────────────────────────────

    def _update_lag_observation(self, alt: str, ts: float, price: float) -> None:
        """
        When we see the alt move in the same direction as the BTC cascade,
        record the empirical lag.
        """
        obs = self._pending_lag_obs.get(alt)
        if obs is None:
            return

        # Timeout — discard if too old
        if ts - obs["btc_move_ts"] > _LAG_OBSERVATION_WINDOW:
            del self._pending_lag_obs[alt]
            return

        # Check if the alt has started moving in the expected direction
        alt_history = self._prices.get(alt)
        if alt_history is None or len(alt_history) < 2:
            return

        # Compare alt price at BTC event time to current alt price
        # Find the alt price closest to btc_move_ts
        ref_alt_price: Optional[float] = None
        for t, p in alt_history:
            if t <= obs["btc_move_ts"]:
                ref_alt_price = p
            else:
                break

        if ref_alt_price is None or ref_alt_price == 0:
            return

        alt_pct = self._pct_change(ref_alt_price, price)
        threshold = self._thresholds.get(alt, (0.15, 30.0))[0] * 0.5  # lower bar for alt

        moved_same_dir = (
            (obs["btc_direction"] == "UP" and alt_pct >= threshold)
            or (obs["btc_direction"] == "DOWN" and alt_pct <= -threshold)
        )

        if moved_same_dir:
            lag = round(ts - obs["btc_move_ts"], 2)
            self._lag_history[alt].append(lag)
            log.info(
                "[Cascade-Lag] %s responded %.2fs after %s cascade (alt moved %.4f%%)",
                alt, lag, self._lead, alt_pct,
            )
            del self._pending_lag_obs[alt]

    # ── Correlation computation ───────────────────────────────────────────────

    def _compute_correlations(self) -> dict[str, Optional[float]]:
        """
        Compute rolling Pearson correlation between BTC price changes
        and each alt's price changes, using synchronized timestamps.

        Returns dict: alt → correlation (or None if insufficient data).
        """
        now = time.time()
        if now - self._corr_cache_ts < self._corr_cache_ttl and self._corr_cache:
            return self._corr_cache

        lead_history = list(self._prices.get(self._lead, []))
        if len(lead_history) < _MIN_CORRELATION_SAMPLES + 1:
            self._corr_cache = {alt.upper(): None for alt in self._thresholds}
            self._corr_cache_ts = now
            return self._corr_cache

        # Build lead price-change series indexed by ~1s buckets
        # bucket = int(timestamp)
        lead_changes: dict[int, float] = {}
        for i in range(1, len(lead_history)):
            t0, p0 = lead_history[i - 1]
            t1, p1 = lead_history[i]
            if p0 > 0:
                bucket = int(t1)
                lead_changes[bucket] = (p1 - p0) / p0

        result: dict[str, Optional[float]] = {}
        for alt in self._thresholds:
            alt = alt.upper()
            alt_history = list(self._prices.get(alt, []))
            if len(alt_history) < _MIN_CORRELATION_SAMPLES + 1:
                result[alt] = None
                continue

            alt_changes: dict[int, float] = {}
            for i in range(1, len(alt_history)):
                t0, p0 = alt_history[i - 1]
                t1, p1 = alt_history[i]
                if p0 > 0:
                    bucket = int(t1)
                    alt_changes[bucket] = (p1 - p0) / p0

            # Find overlapping buckets
            common = sorted(set(lead_changes) & set(alt_changes))
            if len(common) < _MIN_CORRELATION_SAMPLES:
                result[alt] = None
                continue

            xs = [lead_changes[b] for b in common]
            ys = [alt_changes[b] for b in common]
            result[alt] = _pearson(xs, ys)

        self._corr_cache = result
        self._corr_cache_ts = now
        return result

    # ── Public API: check_cascade ─────────────────────────────────────────────

    def check_cascade(self) -> list[dict]:
        """
        Check for active cascade signals across all lagging assets.

        Returns a list of signal dicts, one per alt with an active cascade
        *and* sufficient correlation::

            {
                "asset": "ETH",
                "direction": "UP",
                "confidence": 0.82,
                "lag_seconds": 4.5,
                "correlation": 0.87,
                "btc_pct_move": 0.23,
                "timestamp": 1716909000.0
            }
        """
        now = time.time()
        correlations = self._compute_correlations()
        signals: list[dict] = []

        for cascade in self._active_cascades:
            alt = cascade["alt"]
            corr = correlations.get(alt)

            if corr is None or corr < self._min_corr:
                continue

            # Estimate lag from empirical history or use a default
            lag_hist = self._lag_history.get(alt)
            if lag_hist and len(lag_hist) > 0:
                avg_lag = round(sum(lag_hist) / len(lag_hist), 2)
            else:
                avg_lag = 5.0  # conservative default

            # Confidence = correlation × velocity strength (capped at 1.0)
            velocity_factor = min(abs(cascade["btc_pct_move"]) / 0.30, 1.0)
            confidence = round(min(corr * (0.5 + 0.5 * velocity_factor), 1.0), 4)

            signals.append({
                "asset": alt,
                "direction": cascade["direction"],
                "confidence": confidence,
                "lag_seconds": avg_lag,
                "correlation": corr,
                "btc_pct_move": cascade["btc_pct_move"],
                "timestamp": cascade["timestamp"],
            })

        if signals:
            log.info("[Cascade] %d signal(s) generated: %s", len(signals),
                     [(s["asset"], s["direction"], s["confidence"]) for s in signals])

        return signals

    # ── Public API: get_correlation_matrix ────────────────────────────────────

    def get_correlation_matrix(self) -> dict[str, Optional[float]]:
        """
        Return the current rolling correlation between BTC and each alt.

        Returns
        -------
        dict
            ``{"ETH": 0.87, "SOL": 0.72, "XRP": None}``
            ``None`` means insufficient data.
        """
        return dict(self._compute_correlations())

    # ── Public API: get_status ────────────────────────────────────────────────

    def get_status(self) -> dict:
        """
        Return a status snapshot for dashboards / diagnostics.
        """
        correlations = self._compute_correlations()
        avg_lags = {}
        for alt, hist in self._lag_history.items():
            if hist and len(hist) > 0:
                avg_lags[alt] = round(sum(hist) / len(hist), 2)
            else:
                avg_lags[alt] = None

        return {
            "lead_asset": self._lead,
            "tracked_alts": list(self._thresholds.keys()),
            "price_samples": {
                asset: len(dq) for asset, dq in self._prices.items()
            },
            "correlations": {k: v for k, v in correlations.items()},
            "avg_lags": avg_lags,
            "active_cascades": len(self._active_cascades),
            "pending_lag_obs": len(self._pending_lag_obs),
            "last_updated": time.time(),
        }

    # ── State persistence ─────────────────────────────────────────────────────

    def save_state(self) -> None:
        """Persist correlation data and lag history to disk."""
        try:
            correlations = self._compute_correlations()
            payload = {
                "correlations": {k: v for k, v in correlations.items()},
                "avg_lags": {
                    alt: (round(sum(hist) / len(hist), 2) if hist else None)
                    for alt, hist in self._lag_history.items()
                },
                "lag_samples": {
                    alt: list(hist) for alt, hist in self._lag_history.items()
                },
                "last_saved": time.time(),
            }
            with open(_STATE_PATH, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2)
            log.debug("[CrossAssetPropagator] state saved to %s", _STATE_PATH)
        except OSError as exc:
            log.warning("[CrossAssetPropagator] state save failed: %s", exc)

    def _load_state(self) -> None:
        """Restore persisted lag history from disk (if available)."""
        try:
            if not os.path.exists(_STATE_PATH):
                return
            with open(_STATE_PATH, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            lag_samples = data.get("lag_samples", {})
            for alt, samples in lag_samples.items():
                alt = alt.upper()
                if alt in self._lag_history and isinstance(samples, list):
                    for s in samples:
                        if isinstance(s, (int, float)):
                            self._lag_history[alt].append(float(s))
            log.info(
                "[CrossAssetPropagator] restored lag history from %s (%d alts)",
                _STATE_PATH, len(lag_samples),
            )
        except (OSError, json.JSONDecodeError, KeyError) as exc:
            log.debug("[CrossAssetPropagator] state load failed (clean start): %s", exc)
