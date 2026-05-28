"""
whale_tracker.py — On-Chain Whale Tracking via Binance Large Trades

Monitors recent Binance spot trades and isolates "whale" trades (those
exceeding 10× the median trade size).  The ratio of whale buy volume to
whale sell volume produces a directional pressure metric and a confidence
multiplier the engine can apply to its signals.

Binance trade fields
--------------------
``isBuyerMaker``
  True  → the buyer was the maker (limit buy was sitting on the book);
           the taker *sold* into it → **sell-side aggression**.
  False → the seller was the maker; the taker *bought* into the ask
           → **buy-side aggression**.

Confidence Multiplier
---------------------
* whale_pressure > +0.3 and direction matches signal → 1.10×
* whale_pressure < −0.3 and direction opposes signal → 0.85×
* otherwise → 1.00× (neutral)
"""

import logging
import time
from collections import deque
from typing import Optional

import aiohttp

log = logging.getLogger("zisi.whale_tracker")

# ── Binance endpoint ─────────────────────────────────────────────────────────
_TRADES_URL = "https://api.binance.com/api/v3/trades"

# ── Asset → Binance spot symbol ──────────────────────────────────────────────
_ASSET_MAP: dict[str, str] = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "SOL": "SOLUSDT",
    "XRP": "XRPUSDT",
}

# ── Constants ─────────────────────────────────────────────────────────────────
_WHALE_MULTIPLE    = 10     # trade must be ≥ 10× median to count as whale
_TRADES_LIMIT      = 50     # number of recent trades to fetch
_CACHE_TTL         = 60     # seconds

# ── Confidence multiplier thresholds ─────────────────────────────────────────
_PRESSURE_THRESHOLD = 0.3
_CONF_BOOST         = 1.10
_CONF_PENALTY       = 0.85


class WhaleTracker:
    """Detects whale-sized trades and produces a directional pressure signal."""

    def __init__(self) -> None:
        # Per-asset trade cache: asset → (timestamp, trades_list)
        self._cache: dict[str, tuple[float, list[dict]]] = {}

        # Latest computed results: asset → dict
        self._results: dict[str, dict] = {}

        # Rolling whale events for trend analysis: asset → deque of dicts
        self._whale_events: dict[str, deque] = {}

        log.info("[WhaleTracker] initialised — assets=%s", list(_ASSET_MAP.keys()))

    # ══════════════════════════════════════════════════════════════════════════
    #  PUBLIC API
    # ══════════════════════════════════════════════════════════════════════════

    async def update(
        self,
        session: aiohttp.ClientSession,
        asset: str,
    ) -> None:
        """Fetch latest trades for *asset*, detect whales, recompute signal."""
        symbol = _ASSET_MAP.get(asset.upper())
        if symbol is None:
            log.debug("[WhaleTracker] unsupported asset %s — skipping", asset)
            return

        asset_key = asset.upper()
        trades = await self._fetch_trades(session, symbol, asset_key)

        if not trades:
            log.debug("[WhaleTracker] no trades returned for %s", asset_key)
            self._results[asset_key] = self._neutral_result()
            return

        # Compute median trade size (in quote volume = price × qty)
        volumes = [float(t["price"]) * float(t["qty"]) for t in trades]
        sorted_vols = sorted(volumes)
        n = len(sorted_vols)
        if n % 2 == 1:
            median_vol = sorted_vols[n // 2]
        else:
            median_vol = (sorted_vols[n // 2 - 1] + sorted_vols[n // 2]) / 2.0

        whale_threshold = median_vol * _WHALE_MULTIPLE

        # Separate whale trades into buy/sell
        whale_buy_vol  = 0.0
        whale_sell_vol = 0.0
        whale_count    = 0

        for trade, vol in zip(trades, volumes):
            if vol < whale_threshold:
                continue
            whale_count += 1
            # isBuyerMaker=True → taker sold → sell pressure
            # isBuyerMaker=False → taker bought → buy pressure
            if trade.get("isBuyerMaker", False):
                whale_sell_vol += vol
            else:
                whale_buy_vol += vol

        # Compute pressure ratio
        total_whale_vol = whale_buy_vol + whale_sell_vol
        if total_whale_vol > 0:
            whale_pressure = (whale_buy_vol - whale_sell_vol) / total_whale_vol
        else:
            whale_pressure = 0.0

        # Determine direction label
        if whale_pressure > _PRESSURE_THRESHOLD:
            direction = "bullish"
        elif whale_pressure < -_PRESSURE_THRESHOLD:
            direction = "bearish"
        else:
            direction = "neutral"

        # Compute confidence multiplier
        confidence_multiplier = self._compute_multiplier(whale_pressure, direction)

        result = {
            "whale_pressure": round(whale_pressure, 4),
            "direction": direction,
            "confidence_multiplier": round(confidence_multiplier, 4),
            "whale_trade_count": whale_count,
            "whale_buy_volume": round(whale_buy_vol, 2),
            "whale_sell_volume": round(whale_sell_vol, 2),
            "median_trade_size": round(median_vol, 2),
            "whale_threshold": round(whale_threshold, 2),
            "total_trades_sampled": len(trades),
            "last_updated": time.time(),
        }
        self._results[asset_key] = result

        # Store whale event for historical tracking
        if whale_count > 0:
            if asset_key not in self._whale_events:
                self._whale_events[asset_key] = deque(maxlen=100)
            self._whale_events[asset_key].append({
                "ts": time.time(),
                "pressure": whale_pressure,
                "direction": direction,
                "count": whale_count,
            })

        log.info(
            "[WhaleTracker] %s → pressure=%.3f dir=%s whales=%d "
            "buy_vol=%.0f sell_vol=%.0f multiplier=%.3f",
            asset_key,
            whale_pressure,
            direction,
            whale_count,
            whale_buy_vol,
            whale_sell_vol,
            confidence_multiplier,
        )

    def get_whale_signal(self, asset: str) -> dict:
        """
        Return the latest whale signal for *asset*.

        Returns a neutral dict if no data has been collected yet.
        """
        return self._results.get(asset.upper(), self._neutral_result())

    def get_status(self) -> dict:
        """Dashboard-friendly status summary."""
        return {
            "module": "whale_tracker",
            "assets_tracked": list(self._results.keys()),
            "signals": {
                a: {
                    "pressure": r["whale_pressure"],
                    "direction": r["direction"],
                    "whale_count": r["whale_trade_count"],
                    "multiplier": r["confidence_multiplier"],
                }
                for a, r in self._results.items()
            },
            "whale_events_stored": {
                a: len(dq) for a, dq in self._whale_events.items()
            },
        }

    # ══════════════════════════════════════════════════════════════════════════
    #  DATA FETCHING
    # ══════════════════════════════════════════════════════════════════════════

    async def _fetch_trades(
        self,
        session: aiohttp.ClientSession,
        symbol: str,
        asset_key: str,
    ) -> list[dict]:
        """Fetch recent trades from Binance spot, with 60-second caching."""
        now = time.time()
        if asset_key in self._cache:
            ts, cached = self._cache[asset_key]
            if now - ts < _CACHE_TTL:
                return cached

        try:
            async with session.get(
                _TRADES_URL,
                params={"symbol": symbol, "limit": str(_TRADES_LIMIT)},
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                resp.raise_for_status()
                trades = await resp.json()
                if isinstance(trades, list):
                    self._cache[asset_key] = (now, trades)
                    log.debug(
                        "[WhaleTracker] %s fetched %d trades", asset_key, len(trades),
                    )
                    return trades
        except Exception as exc:
            log.warning(
                "[WhaleTracker] trade fetch failed for %s: %s", asset_key, exc,
            )

        # Fall back to stale cache
        if asset_key in self._cache:
            return self._cache[asset_key][1]
        return []

    # ══════════════════════════════════════════════════════════════════════════
    #  SIGNAL LOGIC
    # ══════════════════════════════════════════════════════════════════════════

    @staticmethod
    def _compute_multiplier(whale_pressure: float, direction: str) -> float:
        """
        Convert whale pressure into a confidence multiplier.

        * Strong bullish whale pressure (> +0.3) → 1.10×
        * Strong bearish whale pressure (< −0.3) → 0.85×
        * Neutral / mixed                        → 1.00×

        The engine is responsible for checking whether the whale direction
        *agrees* or *opposes* the trade signal and applying the multiplier
        accordingly.
        """
        if direction == "bullish":
            return _CONF_BOOST
        elif direction == "bearish":
            return _CONF_PENALTY
        return 1.0

    # ══════════════════════════════════════════════════════════════════════════
    #  HELPERS
    # ══════════════════════════════════════════════════════════════════════════

    @staticmethod
    def _neutral_result() -> dict:
        return {
            "whale_pressure": 0.0,
            "direction": "neutral",
            "confidence_multiplier": 1.0,
            "whale_trade_count": 0,
            "whale_buy_volume": 0.0,
            "whale_sell_volume": 0.0,
            "median_trade_size": 0.0,
            "whale_threshold": 0.0,
            "total_trades_sampled": 0,
            "last_updated": 0.0,
        }
