"""Calibration gate: validate the price model against ZiSi's real closed trades."""
import json
import os
from dataclasses import dataclass
from typing import List, Optional

_POSITIONS = os.path.join(os.path.dirname(__file__), "..", "..",
                          "infrastructure", "exchange", "positions_state.json")

MAX_ENTRY_ERROR = 0.07
MIN_WL_AGREEMENT = 0.80


@dataclass
class CalibrationReport:
    passed: bool
    reason: str
    mean_entry_error: float
    wl_agreement: float
    xrp_reproduced: bool


def evaluate(mean_entry_error: float, wl_agreement: float,
             xrp_reproduced: bool) -> CalibrationReport:
    """Pure gate decision so it can be unit-tested without a full replay."""
    reasons = []
    if mean_entry_error >= MAX_ENTRY_ERROR:
        reasons.append(f"mean entry-price error {mean_entry_error:.3f} >= {MAX_ENTRY_ERROR}")
    if wl_agreement < MIN_WL_AGREEMENT:
        reasons.append(f"W/L agreement {wl_agreement:.2f} < {MIN_WL_AGREEMENT}")
    if not xrp_reproduced:
        reasons.append("XRP reversal-snipe (0.06 entry) not reproduced")
    passed = not reasons
    return CalibrationReport(
        passed=passed,
        reason="calibration passed" if passed else "; ".join(reasons),
        mean_entry_error=mean_entry_error, wl_agreement=wl_agreement,
        xrp_reproduced=xrp_reproduced)


def load_real_trades(path: str = _POSITIONS) -> List[dict]:
    """Read live closed trades (never hardcode counts)."""
    with open(os.path.normpath(path), encoding="utf-8-sig") as fh:
        return json.load(fh).get("closed", [])


def real_win_loss(trades: List[dict]) -> List[bool]:
    return [float(t.get("realized_pnl", 0)) > 0 for t in trades]


def match_trades(real: list, sim: list) -> list:
    """Match each real closed trade to the nearest simulated trade of the SAME asset
    and timeframe by entry-time proximity.  Returns a list of (real_trade, sim_trade)
    pairs (only matched pairs; unmatched real trades are skipped).

    real  — list of dicts from positions_state.json "closed" list; expected fields:
            asset (str), timeframe (str), entry_time (int/float ms),
            entry_price (float), realized_pnl (float).
    sim   — list of SimTrade dataclass instances; expected fields:
            asset, timeframe, entry_time (int ms), entry_price, realized_pnl.
    """
    from dataclasses import fields as dc_fields

    def _is_dataclass(obj) -> bool:
        try:
            dc_fields(obj)
            return True
        except TypeError:
            return False

    def _get(obj, key, default=None):
        """Unified attribute access for both dicts and dataclass instances."""
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    # Group sim trades by (asset, timeframe) for fast lookup
    sim_by_key: dict = {}
    for s in sim:
        key = (_get(s, "asset", ""), _get(s, "timeframe", ""))
        sim_by_key.setdefault(key, []).append(s)

    pairs = []
    for r in real:
        key = (_get(r, "asset", ""), _get(r, "timeframe", ""))
        candidates = sim_by_key.get(key)
        if not candidates:
            continue
        r_time = int(_get(r, "entry_time", 0) or 0)
        best = min(candidates, key=lambda s: abs(int(_get(s, "entry_time", 0) or 0) - r_time))
        pairs.append((r, best))
    return pairs
