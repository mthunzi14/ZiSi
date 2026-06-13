"""
Measure ZiSi's potential LEAD: how long after a Binance spot move does the
Polymarket book reprice? Positive lag = the market is slow = we have a window to
act. Negative = the book moved first = we have no lead (Type-2 territory).
"""
from typing import Optional


def reprice_lag_seconds(binance_move_ts: Optional[float],
                        poly_reprice_ts: Optional[float]) -> Optional[float]:
    """Seconds between a Binance spot move and the Polymarket book repricing past it.
    Positive = our potential lead; negative = we are behind; None if either ts missing."""
    if binance_move_ts is None or poly_reprice_ts is None:
        return None
    return poly_reprice_ts - binance_move_ts
