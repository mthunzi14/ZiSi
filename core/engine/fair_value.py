"""
fair_value.py — spot-distance-from-strike fair-value signal (the Type-1 core).

Pure, shared by the live engine and the backtester (mirrors signal_core's
no-drift design). Given live spot, strike (window open), time elapsed, vol, and
the live contract prices, decide which side (if any) is underpriced by at least
the entry margin, and classify the archetype (moderate divergence vs near-certainty).

The probability model is the same driftless normal-CDF used by the backtester's
pricing module:  P(up) = N( ((S_t - S_0)/S_0) / (sigma * sqrt((T - t)/T)) ).
"""
from statistics import NormalDist
from typing import Optional

_N = NormalDist().cdf
_EPS = 1e-9

DEFAULT_VALUE_PARAMS = {
    "edge_margin": 0.10,           # min (fair_prob - price) required to enter (breakeven buffer)
    "edge_target": 0.15,           # preferred edge ("+15c to profit")
    "near_certainty_prob": 0.90,   # fair_prob at/above which an entry is "near certain"
    "near_certainty_t_frac": 0.85, # only near-certainty once >= 85% of the window has elapsed
    "sigma_scale": 1.0,            # multiplies ATR-derived sigma (carried from backtest calibration)
}


def fair_prob_up(s_t: float, s_0: float, sigma_frac: float, t_min: float,
                 total_min: float, sigma_scale: float = 1.0, drift: float = 0.0) -> float:
    """Drift-adjusted N(d2) probability the market resolves UP, clamped to [0.01, 0.99].
    s_0 = strike (window open), s_t = live spot, sigma_frac = ATR/price, t = minutes elapsed, drift = trend multiplier."""
    if s_0 <= 0:
        return 0.5
    remaining = max((total_min - t_min) / total_min, _EPS)
    sigma = max(sigma_frac * sigma_scale, _EPS)
    denom = max(sigma * (remaining ** 0.5), _EPS)
    d2 = (((s_t - s_0) / s_0) - drift * (t_min / total_min)) / denom
    return max(0.01, min(0.99, _N(d2)))


def decide_value_entry(fp_up: float, up_price: float, dn_price: float,
                       t_min: float, total_min: float,
                       params: Optional[dict] = None,
                       regime: str = "TREND") -> dict:
    """Return {"direction": "UP"|"DOWN"|None, "edge": float, "archetype": str|None}.
    Enters the side whose (fair_prob - market_price) clears edge_margin; if both do,
    takes the larger edge. archetype in {"moderate", "near_certainty"}."""
    p = params or DEFAULT_VALUE_PARAMS
    edge_up = fp_up - up_price
    edge_dn = (1.0 - fp_up) - dn_price

    # ── Regime-Aware Proximity Guard ──
    # In a MEAN_REVERSION regime, require 15c edge in the 47c-53c range
    # In a TREND regime, default edge_margin is kept (no proximity guard block)
    required_margin_up = p["edge_margin"]
    required_margin_dn = p["edge_margin"]
    if regime == "MEAN_REVERSION":
        if 0.47 <= up_price <= 0.53:
            required_margin_up = p["edge_margin"] + 0.05
        if 0.47 <= dn_price <= 0.53:
            required_margin_dn = p["edge_margin"] + 0.05

    if edge_up < required_margin_up and edge_dn < required_margin_dn:
        return {"direction": None, "edge": 0.0, "archetype": None}

    if edge_up >= edge_dn:
        if edge_up < required_margin_up:
            return {"direction": None, "edge": 0.0, "archetype": None}
        direction, edge, fp, price = "UP", edge_up, fp_up, up_price
    else:
        if edge_dn < required_margin_dn:
            return {"direction": None, "edge": 0.0, "archetype": None}
        direction, edge, fp, price = "DOWN", edge_dn, (1.0 - fp_up), dn_price

    if price < 0.35:
        return {"direction": None, "edge": 0.0, "archetype": None}

    t_frac = (t_min / total_min) if total_min > 0 else 0.0
    archetype = ("near_certainty"
                 if (fp >= p["near_certainty_prob"] and t_frac >= p["near_certainty_t_frac"])
                 else "moderate")
    return {"direction": direction, "edge": round(edge, 4), "archetype": archetype}
