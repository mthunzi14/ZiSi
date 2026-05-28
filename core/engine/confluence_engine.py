"""
confluence_engine.py — Multi-Timeframe Confluence Engine.

Analyses RSI and Momentum across multiple timeframes (1m, 5m, 15m, 1h)
simultaneously and generates a Confluence Score that quantifies how many
timeframes agree on a given direction.

Core mechanics:
  1. Per-Timeframe Signal — each timeframe independently evaluates RSI and
     Momentum to signal UP, DOWN, or NEUTRAL.
  2. Confluence Score (0–4) — count of agreeing timeframes.
  3. Confidence Mapping — score → win_prob_boost for position sizing.
  4. Async Kline Fetching — aiohttp against Binance public API with
     per-timeframe TTL caching to avoid rate-limit issues.

Public API:
  async get_confluence(session, asset, direction) -> dict
  get_status() -> dict
"""

import logging
import time
from typing import Optional

import aiohttp

log = logging.getLogger("zisi.confluence_engine")

# ── Binance kline endpoint ────────────────────────────────────────────────────
_BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"

# ── Timeframes to analyse ────────────────────────────────────────────────────
_TIMEFRAMES: list[str] = ["1m", "5m", "15m", "1h"]

# ── Cache TTLs per timeframe (seconds) ────────────────────────────────────────
_CACHE_TTLS: dict[str, float] = {
    "1m":  15.0,
    "5m":  30.0,
    "15m": 60.0,
    "1h":  120.0,
}

# ── Confluence score → win_prob_boost mapping ─────────────────────────────────
_BOOST_MAP: dict[int, float] = {
    4:  +0.15,
    3:  +0.10,
    2:  +0.05,
    1:   0.00,
    0:  -0.10,
}

# ── Indicator thresholds ──────────────────────────────────────────────────────
_RSI_OVERBOUGHT: float = 60.0
_RSI_OVERSOLD: float = 40.0
_MOMENTUM_THRESHOLD: float = 0.10  # ±0.10% considered neutral

# ── Binance symbol map ────────────────────────────────────────────────────────
_SYMBOL_MAP: dict[str, str] = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "SOL": "SOLUSDT",
    "XRP": "XRPUSDT",
    "DOGE": "DOGEUSDT",
    "ADA": "ADAUSDT",
    "AVAX": "AVAXUSDT",
    "LINK": "LINKUSDT",
    "SUI": "SUIUSDT",
}

# Number of kline candles to fetch per request
_KLINE_LIMIT: int = 30


# ── Technical indicators ──────────────────────────────────────────────────────

def _compute_rsi(closes: list[float], period: int = 14) -> Optional[float]:
    """
    Compute RSI (Relative Strength Index) from a list of close prices.

    Returns None if there are insufficient data points.
    """
    if len(closes) < period + 1:
        return None
    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period
    if al == 0:
        return 100.0
    return round(100 - (100 / (1 + ag / al)), 2)


def _compute_momentum(closes: list[float], lookback: int = 5) -> float:
    """
    Compute momentum as the percentage change over *lookback* candles.

    Returns 0.0 if there are insufficient data points.
    """
    if len(closes) < lookback + 1:
        return 0.0
    return (closes[-1] - closes[-lookback]) / closes[-lookback] * 100


def _evaluate_direction(rsi: Optional[float], momentum: float) -> str:
    """
    Combine RSI and Momentum into a single directional signal.

    Returns ``"UP"``, ``"DOWN"``, or ``"NEUTRAL"``.

    Logic:
      - RSI > 60 *and* momentum positive → UP
      - RSI < 40 *and* momentum negative → DOWN
      - Otherwise → NEUTRAL
      - If RSI is unavailable, fall back to momentum alone.
    """
    if rsi is None:
        # Momentum-only fallback
        if momentum > _MOMENTUM_THRESHOLD:
            return "UP"
        elif momentum < -_MOMENTUM_THRESHOLD:
            return "DOWN"
        return "NEUTRAL"

    if rsi > _RSI_OVERBOUGHT and momentum > _MOMENTUM_THRESHOLD:
        return "UP"
    elif rsi < _RSI_OVERSOLD and momentum < -_MOMENTUM_THRESHOLD:
        return "DOWN"
    return "NEUTRAL"


# ── Main engine class ─────────────────────────────────────────────────────────

class ConfluenceEngine:
    """
    Evaluates multi-timeframe confluence for a given asset and direction.

    Usage::

        engine = ConfluenceEngine()
        async with aiohttp.ClientSession() as session:
            result = await engine.get_confluence(session, "BTC", "UP")
            print(result["score"], result["win_prob_boost"])
    """

    def __init__(self) -> None:
        # Cache: (symbol, timeframe) → (timestamp, closes_list)
        self._cache: dict[tuple[str, str], tuple[float, list[float]]] = {}
        # Stats
        self._queries: int = 0
        self._cache_hits: int = 0
        self._last_result: Optional[dict] = None

    # ── Async kline fetching ──────────────────────────────────────────────────

    async def _fetch_klines(
        self,
        session: aiohttp.ClientSession,
        symbol: str,
        interval: str,
    ) -> list[float]:
        """
        Fetch kline close prices from Binance with TTL-based caching.

        Parameters
        ----------
        session : aiohttp.ClientSession
            Shared HTTP session.
        symbol : str
            Binance trading pair, e.g. ``"BTCUSDT"``.
        interval : str
            Kline interval, e.g. ``"1m"``, ``"5m"``, ``"15m"``, ``"1h"``.

        Returns
        -------
        list[float]
            Close prices (oldest → newest), or ``[]`` on failure.
        """
        cache_key = (symbol, interval)
        now = time.time()
        ttl = _CACHE_TTLS.get(interval, 30.0)

        # Check cache freshness
        if cache_key in self._cache:
            cached_ts, cached_closes = self._cache[cache_key]
            if now - cached_ts < ttl:
                self._cache_hits += 1
                return cached_closes

        try:
            params = {
                "symbol": symbol,
                "interval": interval,
                "limit": _KLINE_LIMIT,
            }
            async with session.get(
                _BINANCE_KLINES_URL, params=params, timeout=aiohttp.ClientTimeout(total=8)
            ) as resp:
                if resp.status != 200:
                    log.warning(
                        "[Confluence] Binance kline error: %s %s → HTTP %d",
                        symbol, interval, resp.status,
                    )
                    return self._cache.get(cache_key, (0.0, []))[1]  # stale-fallback

                data = await resp.json()
                # Each kline: [open_time, open, high, low, close, volume, ...]
                closes = [float(k[4]) for k in data]
                self._cache[cache_key] = (now, closes)
                log.debug(
                    "[Confluence] fetched %d closes for %s/%s",
                    len(closes), symbol, interval,
                )
                return closes

        except Exception as exc:
            log.warning("[Confluence] kline fetch failed %s/%s: %s", symbol, interval, exc)
            # Return stale data if available
            return self._cache.get(cache_key, (0.0, []))[1]

    # ── Public API: get_confluence ────────────────────────────────────────────

    async def get_confluence(
        self,
        session: aiohttp.ClientSession,
        asset: str,
        direction: str,
    ) -> dict:
        """
        Compute multi-timeframe confluence for *asset* in *direction*.

        Parameters
        ----------
        session : aiohttp.ClientSession
            Shared HTTP session for Binance API calls.
        asset : str
            Asset symbol, e.g. ``"BTC"``, ``"ETH"``.
        direction : str
            Expected direction: ``"UP"`` or ``"DOWN"``.

        Returns
        -------
        dict
            ``{score, timeframes, win_prob_boost, alignment}``
        """
        self._queries += 1
        asset_upper = asset.upper()
        direction_upper = direction.upper()
        binance_symbol = _SYMBOL_MAP.get(asset_upper, asset_upper + "USDT")

        tf_results: dict[str, dict] = {}
        agreeing: int = 0

        for tf in _TIMEFRAMES:
            closes = await self._fetch_klines(session, binance_symbol, tf)

            rsi = _compute_rsi(closes)
            momentum = _compute_momentum(closes)
            signal = _evaluate_direction(rsi, momentum)

            tf_results[tf] = {
                "signal": signal,
                "rsi": rsi,
                "momentum": round(momentum, 4),
            }

            if signal == direction_upper:
                agreeing += 1

        score = agreeing
        win_prob_boost = _BOOST_MAP.get(score, 0.0)

        alignment = (
            "MAXIMUM" if score == 4
            else "STRONG" if score == 3
            else "MODERATE" if score == 2
            else "WEAK" if score == 1
            else "CONFLICT"
        )

        result = {
            "score": score,
            "timeframes": tf_results,
            "win_prob_boost": win_prob_boost,
            "alignment": alignment,
            "asset": asset_upper,
            "direction": direction_upper,
            "timestamp": time.time(),
        }

        self._last_result = result

        log.info(
            "[Confluence] %s %s: score=%d/4 (%s) boost=%.2f | %s",
            asset_upper,
            direction_upper,
            score,
            alignment,
            win_prob_boost,
            " | ".join(
                f"{tf}={d['signal']}(RSI={d['rsi']}, Mom={d['momentum']:.2f}%)"
                for tf, d in tf_results.items()
            ),
        )

        return result

    # ── Public API: get_status ────────────────────────────────────────────────

    def get_status(self) -> dict:
        """
        Return engine diagnostics for dashboards / health checks.
        """
        now = time.time()
        cache_ages = {}
        for (symbol, tf), (ts, closes) in self._cache.items():
            key = f"{symbol}/{tf}"
            cache_ages[key] = {
                "age_sec": round(now - ts, 1),
                "ttl_sec": _CACHE_TTLS.get(tf, 30.0),
                "candles": len(closes),
                "fresh": (now - ts) < _CACHE_TTLS.get(tf, 30.0),
            }

        return {
            "total_queries": self._queries,
            "cache_hits": self._cache_hits,
            "cache_hit_rate": (
                round(self._cache_hits / max(self._queries, 1), 4)
            ),
            "cached_pairs": len(self._cache),
            "cache_detail": cache_ages,
            "last_result": self._last_result,
            "timeframes": _TIMEFRAMES,
            "boost_map": _BOOST_MAP,
        }
