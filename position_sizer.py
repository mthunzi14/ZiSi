"""
position_sizer.py - Differentiated Kelly position sizing.

Combines signal type × market type × category weight × expiry multiplier
to produce per-trade dollar sizes that respect cycle capital limits.
"""
import logging
from datetime import datetime, timezone
from typing import Dict

log = logging.getLogger("zisi.position_sizer")

# Kelly multipliers by signal type
_SIGNAL_TYPE_MULT: Dict = {
    "TYPE_A_HIGH": 1.5,
    "TYPE_A_LOW":  1.0,
    "TYPE_B_HIGH": 0.8,
    "TYPE_B_LOW":  0.4,
}

# Kelly multipliers by market type
_MARKET_TYPE_MULT: Dict = {
    "UP_DOWN":     1.0,
    "PRICE_RANGE": 0.8,
    "HIT_PRICE":   0.5,
    "OTHER":        0.6,
}


class PositionSizer:
    """
    Per-cycle position sizing with daily capital and trade-count limits.

    Usage:
        sizer = PositionSizer(account_balance=100.0)
        sizer.reset_cycle()             # call once per cycle
        size = sizer.calculate(signal, market, category_weight=0.95)
    """

    def __init__(
        self,
        account_balance: float = 100.0,
        max_cycle_capital: float = 30.0,
        max_trades_per_cycle: int = 40,
    ):
        self.account_balance = account_balance
        self.max_cycle_capital = max_cycle_capital
        self.max_trades_per_cycle = max_trades_per_cycle
        self._capital_used: float = 0.0
        self._trades: int = 0

    def reset_cycle(self) -> None:
        """Reset counters at the start of each cycle."""
        self._capital_used = 0.0
        self._trades = 0

    def calculate(
        self,
        signal: Dict,
        market: Dict,
        category_weight: float = 1.0,
    ) -> float:
        """
        Compute position size in dollars.

        Returns 0.0 when cycle capital or trade-count limit is reached.
        """
        if self._trades >= self.max_trades_per_cycle:
            return 0.0
        if self._capital_used >= self.max_cycle_capital:
            return 0.0

        signal_type = signal.get("signal_type", "TYPE_B_LOW")
        market_type = market.get("market_type", "OTHER")
        kelly_mult  = float(signal.get("kelly_multiplier", 0.4))

        # Base: 1 % of account per trade
        base = self.account_balance * 0.01

        sig_mult = _SIGNAL_TYPE_MULT.get(signal_type, 0.7)
        mkt_mult = _MARKET_TYPE_MULT.get(market_type, 0.6)
        exp_mult = _expiry_multiplier(market)

        size = base * sig_mult * mkt_mult * kelly_mult * category_weight * exp_mult

        # Clamp: $0.10 – $2.00 per trade
        size = max(0.10, min(size, 2.00))

        # Respect remaining cycle capital
        remaining = self.max_cycle_capital - self._capital_used
        size = min(size, remaining)
        if size < 0.10:
            return 0.0

        self._capital_used += size
        self._trades += 1

        log.debug(
            "[POSITION-SIZER] %s×%s | sig=%.1f mkt=%.1f exp=%.1f cat=%.2f → $%.2f"
            " | cycle_total=$%.2f",
            signal_type, market_type, sig_mult, mkt_mult, exp_mult,
            category_weight, size, self._capital_used,
        )
        return round(size, 2)

    @property
    def capital_used(self) -> float:
        return self._capital_used

    @property
    def trades_this_cycle(self) -> int:
        return self._trades

    def remaining_capital(self) -> float:
        return max(0.0, self.max_cycle_capital - self._capital_used)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _expiry_multiplier(market: Dict) -> float:
    """Scale down positions on markets that expire soon."""
    expires = market.get("resolutionDate") or market.get("expires_at")
    if not expires:
        return 1.0
    try:
        expiry = datetime.fromisoformat(str(expires).replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        hours = (expiry - now).total_seconds() / 3600
        if hours < 1:
            return 0.3
        if hours < 6:
            return 0.7
        if hours < 24:
            return 0.95
        return 1.0
    except Exception:
        return 1.0
