"""
signal_core.py — the pure ZiSi entry-signal cascade.

Single source of truth for the RSI/momentum/OFI direction decision, shared by
the live engine (core/engine/updown_engine.py) and the historical backtester
(tools/backtest/simulator.py) so the two can never drift apart.

This captures ONLY the raw cascade: direction + base score + an OFI-divergence
`blocked` flag. The mom/OFI score boosts, dual-entry path, AI predictor, and
edge-orchestrator layers remain in generate_signal (they depend on live market
prices / external systems and are applied after this function returns).
"""
from typing import Optional

# Defaults == the constants currently hardcoded in generate_signal.
# Overriding these is how the backtester sweeps parameters.
DEFAULT_SIGNAL_PARAMS = {
    "rsi_up": 60.0, "mom_up": 0.02,
    "rsi_up_soft": 54.0, "mom_up_soft": 0.01, "ofi_confirm_up": 0.45,
    "rsi_dn": 40.0, "mom_dn": -0.02,
    "rsi_dn_soft": 46.0, "mom_dn_soft": -0.01, "ofi_confirm_dn": -0.45,
    "reversal_lo": 20.0, "reversal_hi": 80.0, "reversal_score": 0.70,
    # OFI-divergence block magnitudes (sign applied per-direction)
    "ofi_block_neutral": 0.35,  # used when 45 <= rsi <= 55
    "ofi_block_5m": 0.28,
    "ofi_block_15m": 0.20,
}


def _block_magnitude(rsi: float, timeframe: str, p: dict) -> float:
    if 45.0 <= rsi <= 55.0:
        return p["ofi_block_neutral"]
    return p["ofi_block_5m"] if timeframe == "5m" else p["ofi_block_15m"]


def decide_signal(rsi, mom: float, ofi: float, timeframe: str, params: Optional[dict] = None) -> dict:
    """Return {"direction": "UP"|"DOWN"|None, "score": float, "is_reversal": bool, "blocked": bool}."""
    p = params or DEFAULT_SIGNAL_PARAMS
    res = {"direction": None, "score": 0.0, "is_reversal": False, "blocked": False}
    if rsi is None:
        return res

    up_trigger = (
        (rsi > p["rsi_up"] and mom >= p["mom_up"])
        or (rsi > p["rsi_up_soft"] and mom >= p["mom_up_soft"] and ofi > p["ofi_confirm_up"])
    )
    dn_trigger = (
        (rsi < p["rsi_dn"] and mom <= p["mom_dn"])
        or (rsi < p["rsi_dn_soft"] and mom <= p["mom_dn_soft"] and ofi < p["ofi_confirm_dn"])
    )

    if up_trigger:
        if ofi < -_block_magnitude(rsi, timeframe, p):
            res["blocked"] = True
            return res
        rsi_eff = max(rsi, 60.0)
        res["direction"] = "UP"
        res["score"] = min(0.85, 0.50 + (rsi_eff - 60.0) / 40.0 * 0.35)
        return res

    if dn_trigger:
        if ofi > _block_magnitude(rsi, timeframe, p):
            res["blocked"] = True
            return res
        rsi_eff = min(rsi, 40.0)
        res["direction"] = "DOWN"
        res["score"] = min(0.85, 0.50 + (40.0 - rsi_eff) / 40.0 * 0.35)
        return res

    # Pre-momentum reversal sniping
    if rsi < p["reversal_lo"]:
        res.update(direction="UP", score=p["reversal_score"], is_reversal=True)
    elif rsi > p["reversal_hi"]:
        res.update(direction="DOWN", score=p["reversal_score"], is_reversal=True)
    return res
