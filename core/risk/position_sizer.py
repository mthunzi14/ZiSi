"""
position_sizer.py - ZiSi Adaptive Kelly Position Sizing (Edge Architecture v2)

Implements Advancement D: Mathematically optimal bet sizing using half-Kelly
criterion with dynamic inputs from all Edge Architecture modules:
  - Regime detector (A) → regime-adjusted win probability
  - Confluence engine (G) → multi-timeframe confidence boost
  - Anti-fragile system (M) → performance-based aggression multiplier
  - Portfolio heat (L) → correlated exposure dampening
  - Volatility surface (E) → sentiment-based confidence modifier
  - Whale tracker (J) → whale activity confidence multiplier

Half-Kelly = f*/2 reduces variance while maintaining most of the edge.
Floor: $0.50 | Ceiling: $5.00 | Never risk >5% of bankroll per trade.
"""
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Dict, Optional
from infrastructure.state.state_manager import GLOBAL_POSITIONS_LOCK

log = logging.getLogger("zisi.position_sizer")

# ── Kelly Formula Constants ─────────────────────────────────────────────────
# Base win rates by signal type (empirically calibrated)
_BASE_WIN_RATES: Dict[str, float] = {
    "TYPE_A_HIGH": 0.72,   # Strong RSI + OFI confluence
    "TYPE_A_LOW":  0.65,   # RSI-only directional
    "TYPE_B_HIGH": 0.58,   # Moderate signal
    "TYPE_B_LOW":  0.52,   # Weak/marginal signal
}

# Average payout ratio on Polymarket UP/DOWN markets
# Typical entry ~40-55c → payout $1.00 → avg ratio ~1.5-2.0
_DEFAULT_PAYOUT_RATIO: float = 1.50

# Position size guards
_MIN_POSITION_USD: float = 0.50
_MAX_POSITION_USD: float = 5.00
_MAX_BANKROLL_FRACTION: float = 0.05  # Never risk >5% per trade

# Kelly multipliers by signal type (backward compat)
_SIGNAL_TYPE_MULT: Dict[str, float] = {
    "TYPE_A_HIGH": 1.5,
    "TYPE_A_LOW":  1.0,
    "TYPE_B_HIGH": 0.8,
    "TYPE_B_LOW":  0.4,
}

# Kelly multipliers by market type
_MARKET_TYPE_MULT: Dict[str, float] = {
    "UP_DOWN":     1.0,
    "PRICE_RANGE": 0.8,
    "HIT_PRICE":   0.5,
    "OTHER":       0.6,
}


class PositionSizer:
    """
    Adaptive Kelly Position Sizer — the central sizing hub.

    Integrates signals from regime detector, confluence engine, anti-fragile
    system, portfolio heat manager, volatility surface, and whale tracker
    to compute mathematically optimal position sizes.

    Usage:
        sizer = PositionSizer(account_balance=100.0)
        sizer.reset_cycle()
        size = sizer.calculate_adaptive(
            signal=signal_dict,
            market=market_dict,
            regime_kelly=1.2,
            confluence_boost=0.10,
            antifragile_mult=1.0,
            heat_mult=1.0,
            sentiment_modifier=0.05,
            whale_mult=1.0,
        )
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

    # ── Primary Adaptive Kelly Calculator ──────────────────────────────────────

    def calculate_adaptive(
        self,
        signal: Dict,
        market: Dict,
        regime_kelly: float = 1.0,
        confluence_boost: float = 0.0,
        antifragile_mult: float = 1.0,
        heat_mult: float = 1.0,
        sentiment_modifier: float = 0.0,
        whale_mult: float = 1.0,
        category_weight: float = 1.0,
    ) -> float:
        """
        Compute position size using half-Kelly with all edge module inputs.

        Args:
            signal: Signal dict with signal_type, score, affected_cryptos
            market: Market dict with market_type, resolutionDate
            regime_kelly: Kelly multiplier from regime detector (A)
            confluence_boost: Win probability boost from confluence engine (G)
            antifragile_mult: Aggression multiplier from anti-fragile system (M)
            heat_mult: Dampening multiplier from portfolio heat manager (L)
            sentiment_modifier: Confidence modifier from volatility surface (E)
            whale_mult: Confidence multiplier from whale tracker (J)
            category_weight: Category weight (existing)

        Returns:
            Dollar amount to bet, or 0.0 if limits reached.
        """
        if self._trades >= self.max_trades_per_cycle:
            log.info("[KELLY] Cycle trade limit reached (%d/%d)", self._trades, self.max_trades_per_cycle)
            return 0.0
        if self._capital_used >= self.max_cycle_capital:
            log.info("[KELLY] Cycle capital limit reached ($%.2f/$%.2f)", self._capital_used, self.max_cycle_capital)
            return 0.0

        # Step 1: Determine base win probability from signal type
        signal_type = signal.get("signal_type", "TYPE_B_LOW")
        base_win_rate = _BASE_WIN_RATES.get(signal_type, 0.52)

        # Step 2: Apply confluence boost (from Multi-TF analysis)
        adjusted_wr = min(0.90, base_win_rate + confluence_boost)

        # Step 3: Apply sentiment modifier (from Volatility Surface)
        adjusted_wr = min(0.90, adjusted_wr + sentiment_modifier)

        # Step 4: Apply rolling win-rate adjustment (existing feature)
        _assets = signal.get("affected_cryptos", [])
        _asset = _assets[0].upper() if _assets else ""
        wr_mult = get_rolling_wr_multiplier(_asset) if _asset else 1.0
        adjusted_wr = min(0.90, adjusted_wr * wr_mult)

        # Step 5: Compute payout ratio from entry price
        entry_price = signal.get("entry_price", 0.45)
        if entry_price > 0 and entry_price < 1:
            payout_ratio = (1.0 - entry_price) / entry_price  # e.g., 45c entry → 55c profit / 45c risk ≈ 1.22
        else:
            payout_ratio = _DEFAULT_PAYOUT_RATIO

        # Step 6: Half-Kelly formula
        # f* = (b*p - q) / b, then take half
        p = adjusted_wr
        q = 1.0 - p
        b = payout_ratio

        if b <= 0:
            return _MIN_POSITION_USD

        full_kelly = (b * p - q) / b
        half_kelly = full_kelly / 2.0

        if half_kelly <= 0:
            log.info("[KELLY] Negative Kelly (%.4f) — edge insufficient, using minimum", full_kelly)
            half_kelly = 0.005  # Minimum fraction

        # Step 7: Apply all multipliers
        # Regime Kelly (A): scales with market volatility state
        # Antifragile (M): scales with recent performance
        # Heat (L): dampens for correlated exposure
        # Whale (J): boosts/dampens based on whale activity
        combined_mult = regime_kelly * antifragile_mult * heat_mult * whale_mult

        # Step 8: Calculate raw USD size
        raw_usd = half_kelly * self.account_balance * combined_mult

        # Step 9: Apply expiry multiplier (existing)
        exp_mult = _expiry_multiplier(market)
        raw_usd *= exp_mult

        # Step 10: Apply category weight (existing)
        raw_usd *= category_weight

        # Step 11: Enforce guards
        # Never exceed max bankroll fraction
        max_from_bankroll = self.account_balance * _MAX_BANKROLL_FRACTION
        raw_usd = min(raw_usd, max_from_bankroll)

        # Enforce floor and ceiling
        size = max(_MIN_POSITION_USD, min(raw_usd, _MAX_POSITION_USD))

        # Respect remaining cycle capital
        remaining = self.max_cycle_capital - self._capital_used
        size = min(size, remaining)
        if size < _MIN_POSITION_USD:
            return 0.0

        self._capital_used += size
        self._trades += 1

        log.info(
            "[KELLY] %s | WR=%.1f%% payout=%.2f half_kelly=%.4f | "
            "regime×%.2f anti×%.2f heat×%.2f whale×%.2f exp×%.2f | "
            "→ $%.2f | cycle=$%.2f/%d trades",
            signal_type, adjusted_wr * 100, payout_ratio, half_kelly,
            regime_kelly, antifragile_mult, heat_mult, whale_mult, exp_mult,
            size, self._capital_used, self._trades,
        )
        return round(size, 2)

    # ── Legacy Calculator (backward compatibility) ─────────────────────────────

    def calculate(
        self,
        signal: Dict,
        market: Dict,
        category_weight: float = 1.0,
    ) -> float:
        """
        Legacy position sizing (backward compatible).
        Delegates to calculate_adaptive with default multipliers.
        """
        return self.calculate_adaptive(
            signal=signal,
            market=market,
            category_weight=category_weight,
        )

    # ── Properties ─────────────────────────────────────────────────────────────

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
        pf = os.path.join(os.path.dirname(__file__), "..", "..", "infrastructure", "exchange", "positions_state.json")
        pf = os.path.normpath(pf)
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
