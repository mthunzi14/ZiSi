"""
position_sizer.py - Differentiated Kelly position sizing.

Combines signal type × market type × category weight × expiry multiplier
to produce per-trade dollar sizes that respect cycle capital limits.
"""
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Dict
from infrastructure.state.state_manager import GLOBAL_POSITIONS_LOCK

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
        max_cycle_capital: float = 100.0,
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

        # Base: 0.5 % of account per trade (Mitigation Plan)
        base = self.account_balance * 0.005

        sig_mult = _SIGNAL_TYPE_MULT.get(signal_type, 0.7)
        mkt_mult = _MARKET_TYPE_MULT.get(market_type, 0.6)
        exp_mult = _expiry_multiplier(market)

        # Adaptive Kelly: adjust by rolling 10-trade win rate for this asset
        _assets = signal.get("affected_cryptos", [])
        _asset = _assets[0].upper() if _assets else ""
        _wr_mult = get_rolling_wr_multiplier(_asset) if _asset else 1.0

        size = base * sig_mult * mkt_mult * kelly_mult * category_weight * exp_mult * _wr_mult

        # Hard cap of $2.00 for capital protection (Mitigation Plan)
        _size_cap = 2.00
        size = max(0.10, min(size, _size_cap))

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
# Helpers
# ---------------------------------------------------------------------------

# Rolling win-rate cache: {asset_key: (timestamp, multiplier)}
_wr_cache: Dict = {}
_WR_CACHE_TTL = 300  # 5 minutes


def get_rolling_wr_multiplier(asset: str) -> float:
    """
    Compute a Kelly multiplier based on the last 10 trades for this asset.
    Reads closed trades from positions_state.json.
    Returns 1.2x if WR > 70%, 0.5x if WR < 30%, 1.0x if <10 samples.
    """
    key = asset.upper()
    now = time.time()
    cached = _wr_cache.get(key)
    if cached and now - cached[0] < _WR_CACHE_TTL:
        return cached[1]
    try:
        pf = os.path.join(os.path.dirname(__file__), "positions_state.json")
        with GLOBAL_POSITIONS_LOCK:
            data = json.loads(open(pf, encoding="utf-8").read())
        closed = data.get("closed", [])
        asset_trades = [
            t for t in closed
            if key in str(t.get("affected_cryptos", "")).upper()
            or key in str(t.get("market_title", "")).upper()
        ]
        recent = asset_trades[-10:]
        if len(recent) < 5:
            _wr_cache[key] = (now, 1.0)
            return 1.0
        wins = sum(1 for t in recent if float(t.get("realized_pnl", 0)) > 0)
        wr = wins / len(recent)
        if wr > 0.70:
            mult = 1.20
        elif wr < 0.30:
            mult = 0.50
        elif wr < 0.40:
            mult = 0.75
        else:
            mult = 1.0
        log.debug("[ROLLING-WR] %s last%d WR=%.0f%% → Kelly×%.2f", key, len(recent), wr * 100, mult)
        _wr_cache[key] = (now, mult)
        return mult
    except Exception:
        return 1.0


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
