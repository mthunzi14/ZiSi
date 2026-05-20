"""
data_sources/kalshi_bracket.py - Kalshi Price Bracket Skewness Analyzer

Reads Kalshi price bracket markets for BTC/ETH and measures the implied
probability distribution skewness. Left-skewed = more probability mass below
current price = bearish. Right-skewed = probability mass above = bullish.

Uses the Kalshi public REST API (no auth required for market data).
Cache: 3 minutes.
"""

import logging
import time
from typing import Optional

import requests

log = logging.getLogger("zisi.kalshi_bracket")

_TIMEOUT = 8
_CACHE_TTL = 180  # 3 minutes

_cache: dict = {}
_cache_ts: dict = {}

# Kalshi series tickers for price range markets
_SERIES_MAP = {
    "BTC": "KXBTC",
    "ETH": "KXETH",
    "SOL": "KXSOL",
}


def _fetch_kalshi_markets(series: str) -> list:
    """Fetch active markets for a Kalshi series."""
    try:
        url = f"https://api.elections.kalshi.com/trade-api/v2/markets?series_ticker={series}&status=open&limit=50"
        headers = {"Accept": "application/json"}
        resp = requests.get(url, headers=headers, timeout=_TIMEOUT)
        data = resp.json()
        return data.get("markets", [])
    except Exception as exc:
        log.debug("[KALSHI-BRACKET] Fetch error for %s: %s", series, exc)
        return []


def get_bracket_skewness(coin: str = "BTC") -> Optional[dict]:
    """
    Compute skewness of the implied probability distribution from Kalshi bracket markets.

    Skewness > 0 (right-skewed): more probability mass in high-price brackets → bullish
    Skewness < 0 (left-skewed): more probability mass in low-price brackets → bearish
    Near 0: symmetric distribution, no directional bias.

    Returns:
        {coin, skewness, weighted_mean, markets_used, signal, direction}
    """
    key = coin.upper()
    now = time.time()
    if now - _cache_ts.get(key, 0) < _CACHE_TTL and key in _cache:
        return _cache[key]

    series = _SERIES_MAP.get(key)
    if not series:
        return None

    markets = _fetch_kalshi_markets(series)
    if not markets:
        return None

    # Each market has yes_bid (implied probability of "YES")
    # We need the strike (threshold price) and probability
    bracket_data = []
    for m in markets:
        try:
            # yes_ask is the cost to buy YES = implied probability
            yes_ask = float(m.get("yes_ask", 0))
            yes_bid = float(m.get("yes_bid", 0))
            yes_prob = (yes_ask + yes_bid) / 2.0 if yes_ask > 0 and yes_bid > 0 else yes_ask

            # Extract strike from subtitle or title (e.g., "Above $50,000")
            # Use floor_strike and cap_strike if available
            floor = m.get("floor_strike") or m.get("cap_strike")
            if floor is None:
                continue
            floor = float(floor)
            if yes_prob > 0 and floor > 0:
                bracket_data.append({"price": floor, "prob": yes_prob})
        except Exception:
            continue

    if len(bracket_data) < 3:
        return None

    # Sort by price ascending
    bracket_data.sort(key=lambda x: x["price"])
    prices = [b["price"] for b in bracket_data]
    probs = [b["prob"] for b in bracket_data]
    total_prob = sum(probs)
    if total_prob <= 0:
        return None

    # Weighted mean price
    weighted_mean = sum(p * pr for p, pr in zip(prices, probs)) / total_prob

    # Weighted variance and skewness (3rd standardized moment)
    variance = sum(pr * (p - weighted_mean) ** 2 for p, pr in zip(prices, probs)) / total_prob
    std_dev = variance ** 0.5 if variance > 0 else 1.0
    skewness = sum(pr * ((p - weighted_mean) / std_dev) ** 3 for p, pr in zip(prices, probs)) / total_prob

    if skewness > 0.3:
        signal = "BULLISH_SKEW"
        direction = "UP"
    elif skewness < -0.3:
        signal = "BEARISH_SKEW"
        direction = "DOWN"
    else:
        signal = "NEUTRAL_SKEW"
        direction = "NEUTRAL"

    result = {
        "coin":          key,
        "skewness":      round(skewness, 4),
        "weighted_mean": round(weighted_mean, 2),
        "markets_used":  len(bracket_data),
        "signal":        signal,
        "direction":     direction,
    }
    log.info("[KALSHI-BRACKET] %s skewness=%.3f (%d markets) → %s", key, skewness, len(bracket_data), signal)
    _cache[key] = result
    _cache_ts[key] = now
    return result


def get_bracket_confidence_boost(coin: str, signal_direction: str) -> float:
    """
    Returns position size multiplier based on Kalshi bracket skewness alignment.

    Skew aligns with direction → 1.12×
    Skew contradicts → 0.88×
    Neutral → 1.0×
    """
    data = get_bracket_skewness(coin)
    if not data or data.get("direction") == "NEUTRAL":
        return 1.0

    bracket_dir = data["direction"]
    our_dir_up = signal_direction.upper() in ("UP", "YES", "BULLISH")
    bracket_up = bracket_dir == "UP"

    if our_dir_up == bracket_up:
        skew = abs(data.get("skewness", 0))
        if skew > 0.5:
            log.info("[KALSHI-BRACKET] Strong %s skew (%.2f) confirms %s → 1.12×", bracket_dir, skew, signal_direction)
            return 1.12
        return 1.06
    else:
        log.info("[KALSHI-BRACKET] %s skew contradicts %s → 0.88×", bracket_dir, signal_direction)
        return 0.88
