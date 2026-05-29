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
    "hype": "hyperliquid", "hyperliquid": "hyperliquid",
    "bnb": "binancecoin", "binancecoin": "binancecoin",
    "cardano": "cardano", "ada": "cardano",
    "polygon": "polygon", "matic": "polygon",
    "avalanche": "avalanche", "avax": "avalanche",
}

# Binance spot symbol map for UP/DOWN short-term confirmation
_BINANCE_SYMBOLS = {
    "bitcoin": "BTCUSDT", "btc": "BTCUSDT",
    "ethereum": "ETHUSDT", "eth": "ETHUSDT",
    "solana": "SOLUSDT", "sol": "SOLUSDT",
    "ripple": "XRPUSDT", "xrp": "XRPUSDT",
    "dogecoin": "DOGEUSDT", "doge": "DOGEUSDT",
    "hype": "HYPEUSDT", "hyperliquid": "HYPEUSDT",
    "bnb": "BNBUSDT", "binancecoin": "BNBUSDT",
    "cardano": "ADAUSDT", "ada": "ADAUSDT",
    "polygon": "MATICUSDT", "matic": "MATICUSDT",
    "avalanche": "AVAXUSDT", "avax": "AVAXUSDT",
}

# Timeframes for UP/DOWN markets (short-term binary prediction — 5–15 min resolution)
_UPDOWN_TIMEFRAMES = {
    "1m":  5,   # 5 × 1m candles = last 5 minutes
    "3m":  3,   # 3 × 3m candles = last 9 minutes
    "5m":  3,   # 3 × 5m candles = last 15 minutes
}

# Lookback windows (in hourly data points) that map to "timeframes"
_TIMEFRAMES = {
    "short":  2,   # ~2 h — used only for multi-day markets, NOT for UP/DOWN
    "medium": 6,   # ~6 h
    "long":   24,  # ~24 h ≈ macro confirmation
}


class MultiTimeframeAnalyzer:
    """
    Provides multi-timeframe directional confirmation.
    - UP/DOWN markets (5–15 min): uses Binance 1m/3m/5m klines (short-term micro momentum).
    - Multi-day/macro markets:    uses CoinGecko hourly data (unchanged).
    Falls back to neutral (0.5) on any API failure — never blocks trade logic.
    """

    def __init__(self):
        self._cache: dict = {}           # CoinGecko: coin → (timestamp, prices_list)
        self._binance_cache: dict = {}   # Binance: (symbol, interval) → (timestamp, closes_list)
        self._cache_ttl: int = 300       # 5-min TTL for CoinGecko
        self._binance_ttl: int = 30      # 30-sec TTL for Binance minute-level data

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

    def get_updown_confirmation(self, symbol: str, signal_direction: str) -> dict:
        """
        Short-term MTF confirmation for 5–15 min UP/DOWN binary markets.
        Uses Binance klines at 1m, 3m, 5m — each compared against N candles back.

        This replaces CoinGecko hourly data for UP/DOWN markets; hourly 2h/6h/24h
        has zero predictive value for a 5-minute binary outcome.

        Returns same dict shape as get_timeframe_confirmation().
        """
        signal_dir = signal_direction.lower()
        confirmations = 0
        tf_results = {}
        total_tfs = 0

        for interval, lookback in _UPDOWN_TIMEFRAMES.items():
            closes = self._get_binance_klines(symbol, interval, lookback + 1)
            if len(closes) < 2:
                tf_results[interval] = "neutral"
                continue
            total_tfs += 1
            price_start = closes[0]
            price_end = closes[-1]
            if price_end > price_start * 1.0001:
                direction = "bullish"
            elif price_end < price_start * 0.9999:
                direction = "bearish"
            else:
                direction = "neutral"
            tf_results[interval] = direction
            if direction == signal_dir:
                confirmations += 1

        if total_tfs == 0:
            log.info("[MTF-UPDOWN] No Binance data for %s — using neutral", symbol)
            return self._neutral_result_updown()

        confirmation_score = confirmations / total_tfs
        alignment = (
            "PERFECT" if confirmations == 3
            else "GOOD" if confirmations == 2
            else "WEAK" if confirmations == 1
            else "NONE"
        )

        log.info(
            "[MTF-UPDOWN] %s %s: %d/%d confirmations (%s) | 1m=%s 3m=%s 5m=%s",
            symbol.upper(), signal_dir.upper(), confirmations, total_tfs, alignment,
            tf_results.get("1m", "?"), tf_results.get("3m", "?"), tf_results.get("5m", "?"),
        )
        return {
            "confirmation_score": round(confirmation_score, 4),
            "timeframes": tf_results,
            "confirmations": confirmations,
            "alignment": alignment,
            "source": "binance_klines",
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

    def _get_binance_klines(self, symbol: str, interval: str, limit: int) -> list:
        """
        Fetch recent kline close prices from Binance (no auth required).
        Returns list of floats (close prices, oldest first) or [] on failure.
        Caches for 30 seconds to avoid hammering on every UP/DOWN check.
        """
        import time as _time

        binance_sym = _BINANCE_SYMBOLS.get(symbol.lower(), symbol.upper() + "USDT")
        cache_key = (binance_sym, interval)
        now = _time.time()

        if cache_key in self._binance_cache:
            cached_time, cached_closes = self._binance_cache[cache_key]
            if now - cached_time < self._binance_ttl:
                return cached_closes[-limit:]

        try:
            resp = requests.get(
                "https://api.binance.com/api/v3/klines",
                params={"symbol": binance_sym, "interval": interval, "limit": limit},
                timeout=5,
            )
            resp.raise_for_status()
            # Each kline: [open_time, open, high, low, close, volume, ...]
            closes = [float(k[4]) for k in resp.json()]
            self._binance_cache[cache_key] = (now, closes)
            log.debug("[MTF-BINANCE] %s %s: %d closes fetched", binance_sym, interval, len(closes))
            return closes
        except Exception as exc:
            log.debug("[MTF-BINANCE] Kline fetch failed for %s %s: %s", symbol, interval, exc)
            return []

    @staticmethod
    def _neutral_result_updown() -> dict:
        return {
            "confirmation_score": 0.5,
            "timeframes": {"1m": "neutral", "3m": "neutral", "5m": "neutral"},
            "confirmations": 0,
            "alignment": "NONE",
            "source": "binance_klines",
        }

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
