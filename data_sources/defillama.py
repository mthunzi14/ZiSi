"""
DeFiLlama TVL (Total Value Locked) Monitor — free REST API, no key needed.
TVL changes signal macro crypto health. Rising TVL = capital flowing into DeFi = bullish.
Falling TVL = capital flight = bearish. Used as a macro trend filter.
"""
import logging
import time
from typing import Optional

import requests

log = logging.getLogger("zisi.data.defillama")

DEFILLAMA_API = "https://api.llama.fi"

_tvl_cache: dict = {}
_TVL_TTL = 300  # 5-minute cache (TVL updates slowly)


def get_total_tvl() -> Optional[dict]:
    """
    Fetch total DeFi TVL across all protocols.
    Returns: {tvl_usd: float, tvl_24h_change_pct: float, trend: 'UP'/'DOWN'/'FLAT', ts}
    """
    now = time.time()
    cached = _tvl_cache.get("total", {})
    if cached.get("ts", 0) > now - _TVL_TTL:
        return cached

    try:
        r = requests.get(f"{DEFILLAMA_API}/charts", timeout=10)
        if r.status_code != 200:
            return None

        data = r.json()
        if not data or len(data) < 2:
            return None

        # Last two data points for 24h change
        current = float(data[-1].get("totalLiquidityUSD", 0))
        prev    = float(data[-2].get("totalLiquidityUSD", 1))
        change_pct = round((current - prev) / prev * 100, 3) if prev else 0

        trend = "UP" if change_pct > 1.0 else ("DOWN" if change_pct < -1.0 else "FLAT")

        result = {
            "tvl_usd":            round(current, 2),
            "tvl_24h_change_pct": change_pct,
            "trend":              trend,
            "ts":                 now,
        }
        _tvl_cache["total"] = result
        log.info("[DEFILLAMA] TVL=$%.2fB | 24h=%.2f%% | trend=%s",
                 current / 1e9, change_pct, trend)
        return result

    except Exception as exc:
        log.debug("[DEFILLAMA] TVL fetch failed: %s", exc)
    return None


def get_tvl_macro_multiplier(direction: str) -> float:
    """
    Macro TVL trend as a signal alignment multiplier.
    Rising TVL + UP trade = 1.08× (capital flowing in = bullish).
    Falling TVL + DOWN trade = 1.08×.
    Contradicting = 0.95×.
    """
    data = get_total_tvl()
    if not data:
        return 1.0

    trend = data.get("trend", "FLAT")
    if trend == "UP" and direction == "UP":
        log.debug("[DEFILLAMA] Rising TVL confirms UP → 1.08×")
        return 1.08
    if trend == "DOWN" and direction == "DOWN":
        log.debug("[DEFILLAMA] Falling TVL confirms DOWN → 1.08×")
        return 1.08
    if trend == "UP" and direction == "DOWN":
        return 0.95
    if trend == "DOWN" and direction == "UP":
        return 0.95
    return 1.0
