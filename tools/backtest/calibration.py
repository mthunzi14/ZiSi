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
