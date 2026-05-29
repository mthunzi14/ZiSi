"""Synthetic Polymarket Up/Down contract pricing for the backtester.

Driftless normal-CDF model for the *chosen outcome* price:
    d2(t) = ((S_t - S_0)/S_0) / (sigma_frac * sqrt((T - t)/T))
    P_t   = clamp(N(d2(t)), 0.01, 0.99)
Exits mirror the paper engine: TARGET_HIT when P_t >= target_threshold (rides
to whatever the 30s grid catches), else MARKET_EXPIRED at expired_mid (~0.50).
"""
from dataclasses import dataclass
from statistics import NormalDist
from typing import List, Tuple

_N = NormalDist().cdf
_EPS = 1e-9


@dataclass
class PricingParams:
    sigma_scale: float = 1.0        # multiplies ATR-derived sigma (calibrated)
    target_threshold: float = 0.88  # short-TF TARGET_HIT trigger (trader.py)
    expired_mid: float = 0.50       # paper-engine MARKET_EXPIRED fallback price
    reversal_steepness: float = 0.06  # discount slope vs RSI extremity (calibrated)
    slippage_base: float = 0.0      # added to ATM entry (calibrated)
    slippage_atr_coef: float = 0.0  # extra slippage per unit ATR fraction (regime-aware)


def _directional(p_up: float, direction: str) -> float:
    """Price of the chosen outcome token. UP tracks N(d2); DOWN tracks 1 - N(d2)."""
    return p_up if direction == "UP" else (1.0 - p_up)


def contract_price(s_t: float, s_0: float, sigma_frac: float, t_min: float,
                   total_min: float) -> float:
    """UP-outcome price N(d2) at minute t_min, clamped to [0.01, 0.99]."""
    remaining = max((total_min - t_min) / total_min, _EPS)
    denom = max(sigma_frac * (remaining ** 0.5), _EPS)
    d2 = ((s_t - s_0) / s_0) / denom
    return max(0.01, min(0.99, _N(d2)))


def entry_price(direction: str, is_reversal: bool, rsi: float, sigma_frac: float,
                params: PricingParams, regime_atr_frac: float = 0.0) -> float:
    """Modeled fill price for the chosen outcome at entry."""
    if is_reversal:
        # Deep-discount reversal snipe: the more extreme RSI, the cheaper the fill.
        if direction == "UP":      # oversold; distance below 20
            extremity = max(0.0, 20.0 - rsi)
        else:                      # overbought; distance above 80
            extremity = max(0.0, rsi - 80.0)
        price = max(0.01, 0.50 - params.reversal_steepness * extremity)
        return min(0.50, price)
    # Momentum entry ~ ATM (0.50) plus ATR-relative slippage
    slip = params.slippage_base + params.slippage_atr_coef * regime_atr_frac
    return max(0.01, min(0.99, 0.50 + slip))


def price_path_exit(direction: str, s_0: float, entry: float, spot_path: List[float],
                    minutes: List[float], sigma_frac: float, total_min: float,
                    params: PricingParams) -> Tuple[float, str]:
    """Walk the candle on the provided grid; return (exit_price, exit_reason)."""
    sigma = max(sigma_frac * params.sigma_scale, _EPS)
    for s_t, t in zip(spot_path, minutes):
        p_up = contract_price(s_t, s_0, sigma, t, total_min)
        p = _directional(p_up, direction)
        if p >= params.target_threshold:
            return round(p, 4), "TARGET_HIT"
    return round(params.expired_mid, 4), "MARKET_EXPIRED"
