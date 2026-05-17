"""
CoinGlass Liquidation Level Data — free, no API key.
Liquidation levels = forced buying/selling. When price approaches a large
liquidation cluster, market often accelerates through it (cascade effect).
This is a confirmation signal — large cluster in signal direction = boost.
"""
import logging
import time
from typing import Optional

import requests

log = logging.getLogger("zisi.data.coinglass")

COINGLASS_API = "https://open-api.coinglass.com/public/v2"

_liq_cache: dict = {}
_LIQ_TTL = 120  # 2-minute cache (liquidations update frequently)


def get_liquidation_heatmap(symbol: str = "BTC") -> Optional[dict]:
    """
    Fetch top liquidation levels for a symbol.
    Returns: {symbol, long_liquidations: float, short_liquidations: float,
              net_pressure: str ('LONGS_AT_RISK'/'SHORTS_AT_RISK'/'NEUTRAL'), ts}
    """
    now = time.time()
    cached = _liq_cache.get(symbol, {})
    if cached.get("ts", 0) > now - _LIQ_TTL:
        return cached

    try:
        # CoinGlass liquidation chart data (free endpoint)
        r = requests.get(
            f"https://fapi.coinglass.com/api/futures/liquidation/detail/chart",
            params={"symbol": symbol, "interval": "1h"},
            headers={"accept": "application/json"},
            timeout=8,
        )
        if r.status_code == 200:
            data = r.json().get("data", {})
            # Aggregate short vs long liquidation data
            buy_liq  = float(data.get("buyVolUsd", 0) or 0)   # liquidated longs
            sell_liq = float(data.get("sellVolUsd", 0) or 0)  # liquidated shorts

            if buy_liq + sell_liq == 0:
                return None

            net = "SHORTS_AT_RISK" if buy_liq > sell_liq * 1.5 else \
                  "LONGS_AT_RISK"  if sell_liq > buy_liq * 1.5 else "NEUTRAL"

            result = {
                "symbol":             symbol,
                "long_liquidations":  round(buy_liq, 2),
                "short_liquidations": round(sell_liq, 2),
                "net_pressure":       net,
                "ts":                 now,
            }
            _liq_cache[symbol] = result
            log.debug("[COINGLASS] %s | longs_liq=$%.0f shorts_liq=$%.0f → %s",
                      symbol, buy_liq, sell_liq, net)
            return result
    except Exception as exc:
        log.debug("[COINGLASS] Fetch failed for %s: %s", symbol, exc)
    return None


def get_liquidation_signal_boost(symbol: str, direction: str) -> float:
    """
    If shorts are being liquidated (cascading → price UP) → confirms UP signal.
    If longs are being liquidated (cascading → price DOWN) → confirms DOWN.
    Returns multiplier: 1.10× for confirmation, 1.0 for neutral/unavailable.
    """
    data = get_liquidation_heatmap(symbol)
    if not data:
        return 1.0

    pressure = data.get("net_pressure", "NEUTRAL")
    if direction == "UP" and pressure == "SHORTS_AT_RISK":
        log.info("[COINGLASS] %s SHORT liquidations cascading → confirms UP → 1.10×", symbol)
        return 1.10
    if direction == "DOWN" and pressure == "LONGS_AT_RISK":
        log.info("[COINGLASS] %s LONG liquidations cascading → confirms DOWN → 1.10×", symbol)
        return 1.10
    return 1.0
