"""
fair_value.py — spot-distance-from-strike fair-value signal (the Type-1 core).

Pure, shared by the live engine and the backtester. Given live spot, strike
(window open), time elapsed, vol, and the live contract prices, decide which side
(if any) is underpriced by at least the entry margin, classify the archetype, and
report a directional CONFIDENCE.

REBUILD (2026-06-09) — directional edge:
The old model was DRIFTLESS — P(up) depended only on how far spot had wandered from
strike vs volatility, so at the money (~50c) it was a coin-flip with no conviction.
That is exactly the band mentor PBot-6 makes most of his money in (23 of 30 wins at
46-54c) — because he has a real-time directional read. We now blend a momentum/flow
DRIFT into the probability (expected remaining return) and emit a CONFIDENCE score
(0-1) from the model's conviction, the edge size, and how many flow signals agree.
Confidence drives the ATM entry guard (app/main.py) and confidence-tiered sizing.

  P(up) = N( ( (S_t - S_0)/S_0 + drift ) / (sigma * sqrt((T - t)/T)) )

where `drift` is the EXPECTED REMAINING return (signed, return-fraction units).
At drift=0 this is identical to the previous driftless model (backtest-safe).
"""
from statistics import NormalDist
from typing import Optional

_N = NormalDist().cdf
_EPS = 1e-9

DEFAULT_VALUE_PARAMS = {
    "edge_margin": 0.10,           # min (fair_prob - price) required to enter (breakeven buffer)
    "edge_target": 0.15,           # preferred edge ("+15c to profit")
    "near_certainty_prob": 0.80,   # fair_prob at/above which an entry is "near certain"
    "near_certainty_t_frac": 0.75, # only near-certainty once >= 75% of the window has elapsed
    "sigma_scale": 1.0,            # multiplies ATR-derived sigma (carried from backtest calibration)
    "min_absolute_prob": 0.70,     # min absolute probability of winning required to enter
}

# Fraction of recent momentum assumed to persist over the remaining window.
# Backtest-calibrated; dampened in mean-reverting regimes by the caller.
DEFAULT_CONTINUATION = 0.30


def fair_prob_up(s_t: float, s_0: float, sigma_frac: float, t_min: float,
                 total_min: float, sigma_scale: float = 1.0, drift: float = 0.0) -> float:
    """Drift-adjusted N(d2) probability the market resolves UP, clamped to [0.01, 0.99].

    s_0 = strike (window open), s_t = live spot, sigma_frac = ATR/price, t = minutes
    elapsed, drift = EXPECTED REMAINING return (signed return-fraction; +up / -down).
    At drift=0 this reduces to the original driftless model."""
    if s_0 <= 0:
        return 0.5
    remaining = max((total_min - t_min) / total_min, _EPS)
    sigma = max(sigma_frac * sigma_scale, _EPS)
    denom = max(sigma * (remaining ** 0.5), _EPS)
    d2 = (((s_t - s_0) / s_0) + drift) / denom
    return max(0.01, min(0.99, _N(d2)))


def directional_drift(pct_move: float, ofi: float = 0.5, obi: float = 0.5,
                      sigma_frac: float = 0.0, continuation: float = DEFAULT_CONTINUATION) -> float:
    """Expected remaining return (signed) from short-window momentum + order flow.

    pct_move : recent return over a short lookback (signed return-fraction).
    ofi/obi  : order-flow imbalance / book imbalance in [0,1] (0.5 neutral, >0.5 = buy pressure).
    sigma_frac: ATR/price, scales the flow tilt to current volatility.
    Positive => expect further UP move. Feeds fair_prob_up(drift=...).
    The caller dampens `continuation` (toward 0) in mean-reverting regimes."""
    drift = pct_move * continuation
    flow_units = (sigma_frac if sigma_frac > 0 else 0.002)
    drift += ((ofi - 0.5) + (obi - 0.5)) * flow_units
    return drift


def signal_agreement(direction: str, pct_move: float = 0.0, ofi: float = 0.5,
                     obi: float = 0.5, cvd: float = 0.0) -> float:
    """[0,1] — fraction of available flow signals that agree with `direction`.

    Neutral/missing signals are ignored. Returns 0.5 when nothing is informative."""
    up = direction in ("UP", "YES")
    votes = []
    if pct_move != 0.0:
        votes.append((pct_move > 0) == up)
    if ofi != 0.5:
        votes.append((ofi > 0.5) == up)
    if obi != 0.5:
        votes.append((obi > 0.5) == up)
    if cvd != 0.0:
        votes.append((cvd > 0) == up)
    if not votes:
        return 0.5
    return sum(1 for v in votes if v) / len(votes)


def decide_value_entry(fp_up: float, up_price: float, dn_price: float,
                       t_min: float, total_min: float,
                       params: Optional[dict] = None,
                       regime: str = "TREND",
                       timeframe: str = "5m",
                       pct_move: float = 0.0, ofi: float = 0.5,
                       obi: float = 0.5, cvd: float = 0.0) -> dict:
    """Return {"direction", "edge", "archetype", "confidence"}.

    Enters the side whose (fair_prob - market_price) clears edge_margin; if both do,
    takes the larger edge. archetype in {"moderate", "near_certainty"}.
    `confidence` (0-1) blends model conviction, edge size, and flow agreement; it
    drives the ATM entry guard and confidence-tiered sizing."""
    p = params or DEFAULT_VALUE_PARAMS
    edge_up = fp_up - up_price
    edge_dn = (1.0 - fp_up) - dn_price
    min_prob = p.get("min_absolute_prob", 0.70)
    _none = {"direction": None, "edge": 0.0, "archetype": None, "confidence": 0.0}

    # ── Regime-Aware Proximity Guard ──
    # In a MEAN_REVERSION regime, require 15c edge in the 47c-53c range.
    required_margin_up = p["edge_margin"]
    required_margin_dn = p["edge_margin"]
    if regime == "MEAN_REVERSION":
        if 0.47 <= up_price <= 0.53:
            required_margin_up = p["edge_margin"] + 0.05
        if 0.47 <= dn_price <= 0.53:
            required_margin_dn = p["edge_margin"] + 0.05

    # Timeframe-specific edge margin tightening (15m/1h have more time to move against us).
    if timeframe == '15m':
        required_margin_up = max(required_margin_up, 0.12)
        required_margin_dn = max(required_margin_dn, 0.12)
    elif timeframe == '1h':
        required_margin_up = max(required_margin_up, 0.10)
        required_margin_dn = max(required_margin_dn, 0.10)

    if edge_up < required_margin_up and edge_dn < required_margin_dn:
        return _none

    if edge_up >= edge_dn:
        if edge_up < required_margin_up or fp_up < min_prob:
            return _none
        direction, edge, fp, price = "UP", edge_up, fp_up, up_price
    else:
        if edge_dn < required_margin_dn or (1.0 - fp_up) < min_prob:
            return _none
        direction, edge, fp, price = "DOWN", edge_dn, (1.0 - fp_up), dn_price

    # Price boundary gate: 10c floor (below = lottery payout), 82c ceiling (above = NCS territory).
    if not (0.10 <= price <= 0.82):
        return _none

    t_frac = (t_min / total_min) if total_min > 0 else 0.0
    archetype = ("near_certainty"
                 if (fp >= p["near_certainty_prob"] and t_frac >= p["near_certainty_t_frac"])
                 else "moderate")

    # ── Directional confidence (0-1) ──
    # 45% model conviction (how far fp is from a coin-flip) + 30% edge size + 25% flow agreement.
    conviction = max(0.0, min(1.0, (fp - 0.5) * 2.0))
    edge_factor = max(0.0, min(1.0, edge / max(p.get("edge_target", 0.15), _EPS)))
    agree = signal_agreement(direction, pct_move, ofi, obi, cvd)
    confidence = round(0.45 * conviction + 0.30 * edge_factor + 0.25 * agree, 4)

    return {"direction": direction, "edge": round(edge, 4),
            "archetype": archetype, "confidence": confidence}
