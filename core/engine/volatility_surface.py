"""
volatility_surface.py — External Market Sentiment via Crypto Derivatives

Integrates Binance perpetual funding-rate and open-interest data into a
unified sentiment score (-1 … +1) that the engine uses as a confidence
modifier on existing trade signals.

Signals
-------
* **Funding Rate (contrarian)**
  - Overcrowded longs  (funding > +0.01 %) → bearish bias
  - Overcrowded shorts (funding < −0.01 %) → bullish bias
  - Extreme levels (|funding| > 0.05 %) → strong contrarian signal

* **Open Interest**
  - Rising OI + rising price   → trend confirmation
  - Rising OI + falling price  → bearish pressure
  - Falling OI + rising price  → weak rally
  - Falling OI + falling price → capitulation

* **Combined** — weighted merge into a single sentiment score
"""

import json
import logging
import os
import time
from collections import deque
from typing import Optional

import aiohttp

log = logging.getLogger("zisi.volatility_surface")

# ── Persistence ──────────────────────────────────────────────────────────────
_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
_OI_HISTORY_PATH = os.path.join(_PROJECT_ROOT, "oi_history.json")

# ── Binance endpoints ────────────────────────────────────────────────────────
_FUNDING_URL = "https://fapi.binance.com/fapi/v1/fundingRate"
_OI_URL = "https://fapi.binance.com/fapi/v1/openInterest"

# ── Asset → Binance perpetual symbol ─────────────────────────────────────────
_ASSET_MAP: dict[str, str] = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "SOL": "SOLUSDT",
    "XRP": "XRPUSDT",
}

# ── Funding-rate thresholds (expressed as ratios, *not* percent) ─────────────
_FUNDING_MILD   = 0.0001   # 0.01 %
_FUNDING_STRONG = 0.0005   # 0.05 %

# ── Cache TTLs (seconds) ─────────────────────────────────────────────────────
_FUNDING_TTL = 300   # 5 minutes
_OI_TTL      = 30    # 30 seconds

# ── OI history (rolling 1 h at ~30 s cadence → 120 samples) ─────────────────
_OI_MAX_SAMPLES = 120


class VolatilitySurface:
    """Derives a sentiment score from Binance perpetual derivatives data."""

    def __init__(self) -> None:
        # Per-asset caches: asset → (timestamp, value)
        self._funding_cache: dict[str, tuple[float, float]] = {}
        self._oi_cache: dict[str, tuple[float, float]] = {}

        # OI history: asset → deque of (timestamp, oi_value)
        self._oi_history: dict[str, deque] = {}

        # Price snapshot at last OI read: asset → price
        self._last_price: dict[str, float] = {}

        # Latest computed results: asset → dict
        self._results: dict[str, dict] = {}

        self._load_oi_history()
        log.info("[VolSurface] initialised — assets=%s", list(_ASSET_MAP.keys()))

    # ══════════════════════════════════════════════════════════════════════════
    #  PUBLIC API
    # ══════════════════════════════════════════════════════════════════════════

    async def update(
        self,
        session: aiohttp.ClientSession,
        asset: str,
        current_price: float,
    ) -> None:
        """Fetch latest funding rate + OI and recompute sentiment for *asset*."""
        symbol = _ASSET_MAP.get(asset.upper())
        if symbol is None:
            log.debug("[VolSurface] unsupported asset %s — skipping", asset)
            return

        asset_key = asset.upper()

        funding_rate = await self._fetch_funding_rate(session, symbol, asset_key)
        oi_value     = await self._fetch_open_interest(session, symbol, asset_key)

        # Store price for OI direction analysis
        self._last_price[asset_key] = current_price

        # Record OI history
        if oi_value is not None:
            if asset_key not in self._oi_history:
                self._oi_history[asset_key] = deque(maxlen=_OI_MAX_SAMPLES)
            self._oi_history[asset_key].append((time.time(), oi_value))
            self._save_oi_history()

        # Derive signals
        funding_bias, funding_strength = self._analyse_funding(funding_rate)
        oi_signal = self._analyse_oi(asset_key, current_price)
        sentiment_score = self._compute_sentiment(
            funding_bias, funding_strength, oi_signal,
        )
        confidence_modifier = self._sentiment_to_modifier(sentiment_score)

        result = {
            "funding_bias": funding_bias,
            "funding_strength": round(funding_strength, 4),
            "oi_signal": oi_signal,
            "sentiment_score": round(sentiment_score, 4),
            "confidence_modifier": round(confidence_modifier, 4),
            "last_updated": time.time(),
        }
        self._results[asset_key] = result

        log.info(
            "[VolSurface] %s → bias=%s strength=%.3f oi=%s sentiment=%.3f modifier=%.3f",
            asset_key,
            funding_bias,
            funding_strength,
            oi_signal,
            sentiment_score,
            confidence_modifier,
        )

    def get_sentiment(self, asset: str) -> dict:
        """
        Return the latest sentiment snapshot for *asset*.

        Returns a neutral dict if no data has been collected yet.
        """
        return self._results.get(asset.upper(), self._neutral_result())

    def get_status(self) -> dict:
        """Dashboard-friendly status summary."""
        return {
            "module": "volatility_surface",
            "assets_tracked": list(self._results.keys()),
            "sentiments": {
                a: {
                    "bias": r["funding_bias"],
                    "score": r["sentiment_score"],
                    "modifier": r["confidence_modifier"],
                }
                for a, r in self._results.items()
            },
            "oi_history_samples": {
                a: len(h) for a, h in self._oi_history.items()
            },
        }

    # ══════════════════════════════════════════════════════════════════════════
    #  DATA FETCHING (with per-asset TTL caching)
    # ══════════════════════════════════════════════════════════════════════════

    async def _fetch_funding_rate(
        self,
        session: aiohttp.ClientSession,
        symbol: str,
        asset_key: str,
    ) -> Optional[float]:
        """Return the latest funding rate as a raw ratio (e.g. 0.0001 = 0.01 %)."""
        now = time.time()
        if asset_key in self._funding_cache:
            ts, cached = self._funding_cache[asset_key]
            if now - ts < _FUNDING_TTL:
                return cached

        try:
            async with session.get(
                _FUNDING_URL,
                params={"symbol": symbol, "limit": "1"},
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
                if data and isinstance(data, list):
                    rate = float(data[0].get("fundingRate", 0))
                    self._funding_cache[asset_key] = (now, rate)
                    log.debug("[VolSurface] %s funding rate = %.6f", asset_key, rate)
                    return rate
        except Exception as exc:
            log.warning("[VolSurface] funding rate fetch failed for %s: %s", asset_key, exc)

        # Return stale cache if available, else None
        if asset_key in self._funding_cache:
            return self._funding_cache[asset_key][1]
        return None

    async def _fetch_open_interest(
        self,
        session: aiohttp.ClientSession,
        symbol: str,
        asset_key: str,
    ) -> Optional[float]:
        """Return aggregate open interest in contracts."""
        now = time.time()
        if asset_key in self._oi_cache:
            ts, cached = self._oi_cache[asset_key]
            if now - ts < _OI_TTL:
                return cached

        try:
            async with session.get(
                _OI_URL,
                params={"symbol": symbol},
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
                oi = float(data.get("openInterest", 0))
                self._oi_cache[asset_key] = (now, oi)
                log.debug("[VolSurface] %s OI = %.2f", asset_key, oi)
                return oi
        except Exception as exc:
            log.warning("[VolSurface] OI fetch failed for %s: %s", asset_key, exc)

        if asset_key in self._oi_cache:
            return self._oi_cache[asset_key][1]
        return None

    # ══════════════════════════════════════════════════════════════════════════
    #  SIGNAL ANALYSIS
    # ══════════════════════════════════════════════════════════════════════════

    @staticmethod
    def _analyse_funding(
        rate: Optional[float],
    ) -> tuple[str, float]:
        """
        Contrarian interpretation of funding rate.

        Returns (funding_bias, funding_strength) where:
          bias     ∈ {'bullish', 'bearish', 'neutral'}
          strength ∈ [0.0, 1.0]
        """
        if rate is None:
            return "neutral", 0.0

        abs_rate = abs(rate)

        # Determine contrarian bias (positive funding → market is long → bearish)
        if rate > _FUNDING_MILD:
            bias = "bearish"
        elif rate < -_FUNDING_MILD:
            bias = "bullish"
        else:
            return "neutral", 0.0

        # Strength: linear scale from mild threshold to strong threshold, capped at 1
        if abs_rate >= _FUNDING_STRONG:
            strength = 1.0
        else:
            strength = (abs_rate - _FUNDING_MILD) / (_FUNDING_STRONG - _FUNDING_MILD)

        return bias, max(0.0, min(1.0, strength))

    def _analyse_oi(self, asset_key: str, current_price: float) -> str:
        """
        Classify OI dynamics into one of four regimes.

        Requires at least 2 OI history samples to infer direction.
        Uses the price stored at the *earliest* sample vs *current_price*
        to determine the price direction over the same window.
        """
        history = self._oi_history.get(asset_key)
        if not history or len(history) < 2:
            return "neutral"

        # OI direction: compare oldest vs newest in buffer
        oldest_oi = history[0][1]
        newest_oi = history[-1][1]
        oi_rising = newest_oi > oldest_oi

        # Price direction over OI window — use stored price at first OI sample
        # If unavailable, fall back to last two updates
        first_price = self._last_price.get(asset_key, current_price)
        price_rising = current_price >= first_price

        if oi_rising and price_rising:
            return "trend_confirm"
        elif oi_rising and not price_rising:
            return "bearish_pressure"
        elif not oi_rising and price_rising:
            return "weak_rally"
        else:
            return "capitulation"

    @staticmethod
    def _compute_sentiment(
        funding_bias: str,
        funding_strength: float,
        oi_signal: str,
    ) -> float:
        """
        Merge funding + OI into a single score ∈ [-1, +1].

        Funding contributes up to ±0.6 (primary signal).
        OI contributes up to ±0.4 (secondary confirmation/negation).
        """
        # Funding component (±0.6 max)
        if funding_bias == "bullish":
            funding_score = funding_strength * 0.6
        elif funding_bias == "bearish":
            funding_score = -funding_strength * 0.6
        else:
            funding_score = 0.0

        # OI component (±0.4 max)
        oi_scores = {
            "trend_confirm":    0.3,
            "bearish_pressure": -0.4,
            "weak_rally":       -0.1,
            "capitulation":     -0.2,
            "neutral":           0.0,
        }
        oi_score = oi_scores.get(oi_signal, 0.0)

        raw = funding_score + oi_score
        return max(-1.0, min(1.0, raw))

    @staticmethod
    def _sentiment_to_modifier(score: float) -> float:
        """
        Convert sentiment score to a confidence multiplier.

        Strongly bullish sentiment (+0.5 … +1.0) → up to 1.15×
        Mildly bullish            (+0.2 … +0.5) → up to 1.08×
        Neutral                   (−0.2 … +0.2) → 1.00×
        Mildly bearish            (−0.5 … −0.2) → down to 0.92×
        Strongly bearish          (−1.0 … −0.5) → down to 0.85×

        Note: the modifier is *directionally agnostic* — it amplifies
        confidence in the *current* signal direction.  The engine must
        decide whether the sentiment aligns or opposes its trade direction.
        """
        if score >= 0.5:
            return 1.0 + 0.15 * ((score - 0.5) / 0.5)
        elif score >= 0.2:
            return 1.0 + 0.08 * ((score - 0.2) / 0.3)
        elif score >= -0.2:
            return 1.0
        elif score >= -0.5:
            return 1.0 - 0.08 * ((-0.2 - score) / 0.3)
        else:
            return 1.0 - 0.15 * ((-0.5 - score) / 0.5)

    # ══════════════════════════════════════════════════════════════════════════
    #  OI HISTORY PERSISTENCE
    # ══════════════════════════════════════════════════════════════════════════

    def _save_oi_history(self) -> None:
        """Persist OI history deques to disk so they survive restarts."""
        try:
            payload: dict[str, list] = {}
            for asset_key, history in self._oi_history.items():
                payload[asset_key] = list(history)
            with open(_OI_HISTORY_PATH, "w", encoding="utf-8") as fh:
                json.dump(payload, fh)
        except OSError as exc:
            log.debug("[VolSurface] OI history write failed: %s", exc)

    def _load_oi_history(self) -> None:
        """Restore OI history from disk on startup."""
        if not os.path.exists(_OI_HISTORY_PATH):
            return
        try:
            with open(_OI_HISTORY_PATH, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            now = time.time()
            for asset_key, samples in data.items():
                # Only keep samples from the last hour
                recent = [
                    (ts, val) for ts, val in samples
                    if now - ts < 3600
                ]
                if recent:
                    dq: deque = deque(recent, maxlen=_OI_MAX_SAMPLES)
                    self._oi_history[asset_key] = dq
            log.info(
                "[VolSurface] restored OI history for %s",
                list(self._oi_history.keys()),
            )
        except Exception as exc:
            log.warning("[VolSurface] OI history load failed: %s", exc)

    # ══════════════════════════════════════════════════════════════════════════
    #  HELPERS
    # ══════════════════════════════════════════════════════════════════════════

    @staticmethod
    def _neutral_result() -> dict:
        return {
            "funding_bias": "neutral",
            "funding_strength": 0.0,
            "oi_signal": "neutral",
            "sentiment_score": 0.0,
            "confidence_modifier": 1.0,
            "last_updated": 0.0,
        }
