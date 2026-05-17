"""
Binance Real-Time Order Flow — buyer vs seller initiated trade direction.
Uses REST polling of recent trades (aggtrades) to compute buy/sell pressure.
Upgrade path: WebSocket streaming (ws://stream.binance.com:9443/ws/{symbol}@aggTrade)
No API key needed — public endpoint.
"""
import logging
import time
from typing import Optional

import requests

log = logging.getLogger("zisi.data.binance_flow")

BINANCE_API = "https://api.binance.com/api/v3"

# Per-symbol cache: symbol → {buy_pct, sell_pct, ts}
_flow_cache: dict = {}
_FLOW_TTL = 30  # seconds


def get_order_flow(symbol: str, lookback_trades: int = 100) -> Optional[dict]:
    """
    Compute buyer-initiated vs seller-initiated order flow from last N trades.
    Returns: {buy_pct: float, sell_pct: float, buy_volume: float, sell_volume: float,
              net_flow: float (-1 to +1), symbol: str}
    net_flow > 0.3 = strong buying, < -0.3 = strong selling.
    """
    sym_key = symbol.upper().replace("USDT", "") + "USDT"
    now = time.time()
    cached = _flow_cache.get(sym_key, {})
    if cached.get("ts", 0) > now - _FLOW_TTL:
        return cached

    try:
        r = requests.get(
            f"{BINANCE_API}/aggTrades",
            params={"symbol": sym_key, "limit": lookback_trades},
            timeout=6,
        )
        if r.status_code != 200:
            return None

        trades = r.json()
        if not trades:
            return None

        buy_vol   = 0.0
        sell_vol  = 0.0
        for t in trades:
            qty   = float(t.get("q", 0))
            price = float(t.get("p", 0))
            notional = qty * price
            if t.get("m", False):   # m=True means maker = SELL side (buyer was taker)
                sell_vol += notional
            else:
                buy_vol  += notional

        total_vol = buy_vol + sell_vol
        if total_vol <= 0:
            return None

        buy_pct  = round(buy_vol  / total_vol, 4)
        sell_pct = round(sell_vol / total_vol, 4)
        net_flow = round(buy_pct - sell_pct, 4)   # +1 = all buyers, -1 = all sellers

        result = {
            "symbol":     sym_key,
            "buy_pct":    buy_pct,
            "sell_pct":   sell_pct,
            "buy_volume":  round(buy_vol, 2),
            "sell_volume": round(sell_vol, 2),
            "net_flow":   net_flow,
            "ts":         now,
        }
        _flow_cache[sym_key] = result

        log.debug(
            "[FLOW] %s | buy=%.0f%% sell=%.0f%% net=%.2f",
            sym_key, buy_pct * 100, sell_pct * 100, net_flow,
        )
        return result

    except Exception as exc:
        log.debug("[FLOW] %s fetch failed: %s", symbol, exc)
        return None


def get_flow_signal_boost(symbol: str, direction: str) -> float:
    """
    Return a sizing multiplier based on order flow alignment with signal direction.
    Buyers dominating + UP signal → 1.15× boost (smart money confirmation).
    Sellers dominating + DOWN → 1.15× boost.
    Contradicting flow → 0.90× reduction.
    """
    flow = get_order_flow(symbol)
    if flow is None:
        return 1.0

    net = flow["net_flow"]
    if direction == "UP":
        if net >= 0.30:
            log.info("[FLOW] %s | net_flow=+%.2f confirms UP → 1.15×", symbol, net)
            return 1.15
        if net <= -0.25:
            log.info("[FLOW] %s | net_flow=%.2f contradicts UP → 0.90×", symbol, net)
            return 0.90
    elif direction == "DOWN":
        if net <= -0.30:
            log.info("[FLOW] %s | net_flow=%.2f confirms DOWN → 1.15×", symbol, net)
            return 1.15
        if net >= 0.25:
            log.info("[FLOW] %s | net_flow=+%.2f contradicts DOWN → 0.90×", symbol, net)
            return 0.90
    return 1.0
