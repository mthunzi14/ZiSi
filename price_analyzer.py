"""
price_analyzer.py - ZiSi Bot Multi-Timeframe Price Confirmation
Uses CoinGecko hourly market data to confirm signal direction across
short, medium, and longer lookback windows — no Binance key required.
"""

import logging
from typing import Optional

import requests

log = logging.getLogger("zisi.price_analyzer")

# CoinGecko coin-id map (lowercase signal names → CoinGecko ids)
_COIN_IDS = {
    "bitcoin": "bitcoin", "btc": "bitcoin",
    "ethereum": "ethereum", "eth": "ethereum",
    "solana": "solana", "sol": "solana",
    "ripple": "ripple", "xrp": "ripple",
    "dogecoin": "dogecoin", "doge": "dogecoin",
    "cardano": "cardano", "ada": "cardano",
    "polygon": "polygon", "matic": "polygon",
    "avalanche": "avalanche", "avax": "avalanche",
}

# Lookback windows (in hourly data points) that map to "timeframes"
_TIMEFRAMES = {
    "short":  2,   # ~2 h ≈ "5–15m confirmation"
    "medium": 6,   # ~6 h ≈ "1h confirmation"
    "long":   24,  # ~24 h ≈ macro confirmation
}


class MultiTimeframeAnalyzer:
    """
    Provides multi-timeframe directional confirmation using CoinGecko free API.
    Falls back to neutral (0.5) on any API failure — never blocks trade logic.
    """

    def __init__(self):
        self._cache: dict = {}  # coin → (timestamp, prices_list)
        self._cache_ttl: int = 300  # 5-minute cache to respect rate limits

    # ── Public API ────────────────────────────────────────────────────────────

    def get_timeframe_confirmation(
        self,
        symbol: str,
        signal_direction: str,
    ) -> dict:
        """
        Confirm signal_direction across 3 time windows using hourly price data.

        Args:
            symbol:           Crypto symbol, e.g. 'BTC', 'ETH', 'SOL'.
            signal_direction: 'bullish' or 'bearish'.
        Returns:
            Dict with confirmation_score (0–1), timeframes dict, confirmations int,
            alignment string ('PERFECT'|'GOOD'|'WEAK'|'NONE').
        """
        signal_dir = signal_direction.lower()
        prices = self._get_hourly_prices(symbol)

        if not prices or len(prices) < _TIMEFRAMES["long"]:
            log.info("[MTF] Insufficient price data for %s — using neutral confirmation", symbol)
            return self._neutral_result()

        confirmations = 0
        tf_results = {}

        for tf_name, lookback in _TIMEFRAMES.items():
            if len(prices) >= lookback + 1:
                price_start = prices[-(lookback + 1)]
                price_end = prices[-1]
                direction = "bullish" if price_end > price_start else "bearish" if price_end < price_start else "neutral"
                tf_results[tf_name] = direction
                if direction == signal_dir:
                    confirmations += 1
            else:
                tf_results[tf_name] = "neutral"

        confirmation_score = confirmations / len(_TIMEFRAMES)

        alignment = (
            "PERFECT" if confirmations == 3
            else "GOOD" if confirmations == 2
            else "WEAK" if confirmations == 1
            else "NONE"
        )

        log.info(
            "[MTF] %s %s: %d/3 confirmations (%s) | short=%s medium=%s long=%s",
            symbol.upper(), signal_dir.upper(), confirmations, alignment,
            tf_results.get("short", "?"), tf_results.get("medium", "?"), tf_results.get("long", "?"),
        )

        return {
            "confirmation_score": round(confirmation_score, 4),
            "timeframes": tf_results,
            "confirmations": confirmations,
            "alignment": alignment,
        }

    def get_price_confirmation(self, symbol: str, signal_direction: str) -> float:
        """
        Convenience method returning just a -1 to +1 price confirmation value.
        Suitable for passing to calculate_confluence_score() as price_confirmation.
        """
        result = self.get_timeframe_confirmation(symbol, signal_direction)
        # Map confirmation_score (0–1) → (-1 to +1)
        return round((result["confirmation_score"] * 2) - 1, 4)

    # ── Internal price fetching ───────────────────────────────────────────────

    def _get_hourly_prices(self, symbol: str) -> list:
        """
        Fetch last 2 days of hourly prices from CoinGecko for symbol.
        Returns list of USD prices (oldest first) or empty list on failure.
        Uses an in-memory cache with 5-minute TTL.
        """
        import time as _time

        coin_id = _COIN_IDS.get(symbol.lower(), symbol.lower())
        now = _time.time()

        # Return cached prices if fresh
        if coin_id in self._cache:
            cached_time, cached_prices = self._cache[coin_id]
            if now - cached_time < self._cache_ttl:
                return cached_prices

        try:
            resp = requests.get(
                f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart",
                params={"vs_currency": "usd", "days": "2", "interval": "hourly"},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            prices = [p[1] for p in data.get("prices", [])]

            self._cache[coin_id] = (now, prices)
            log.debug("[MTF] Fetched %d hourly prices for %s", len(prices), coin_id)
            return prices

        except Exception as exc:
            log.warning("[MTF] Price fetch failed for %s: %s", symbol, exc)
            return []

    @staticmethod
    def _neutral_result() -> dict:
        return {
            "confirmation_score": 0.5,
            "timeframes": {"short": "neutral", "medium": "neutral", "long": "neutral"},
            "confirmations": 0,
            "alignment": "NONE",
        }
