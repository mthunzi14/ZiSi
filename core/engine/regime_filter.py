# regime_filter.py - ATR-based regime + UTC time gate
import json
import logging
import os
from datetime import datetime, timezone
from typing import Literal

log = logging.getLogger("zisi.regime_filter")

_REGIME_STATUS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "regime_status.json"
)

def get_regime_mode(timeframe: str = "5m") -> Literal["TREND", "MEAN_REVERSION"]:
    """
    Read the real-time regime written by RegimeDetector into regime_status.json.

    Canonical regimes emitted by the detector:
        TRENDING, MEAN_REVERTING, VOLATILE_CHAOS, COMPRESSION
    Mapping to trade mode:
        MEAN_REVERTING / COMPRESSION -> MEAN_REVERSION
        TRENDING / VOLATILE_CHAOS    -> TREND
    Legacy labels (RANGE/NORMAL/VOLATILE/SHOCK) are still accepted for
    backward compatibility with any stale regime_status.json on disk.
    """
    # Regimes that imply choppy / range-bound conditions → mean-reversion mode
    _MEAN_REVERSION_REGIMES = {
        "MEAN_REVERTING", "COMPRESSION",   # canonical
        "RANGE", "NORMAL",                 # legacy aliases
    }
    try:
        if os.path.exists(_REGIME_STATUS_PATH):
            with open(_REGIME_STATUS_PATH, "r", encoding="utf-8") as fh:
                data = json.load(fh)
                regime = str(data.get("regime", "COMPRESSION")).upper()
                return "MEAN_REVERSION" if regime in _MEAN_REVERSION_REGIMES else "TREND"
    except Exception as e:
        log.warning("[RegimeFilter] Failed to read regime_status.json, defaulting to TREND: %s", e)

    return "TREND"


def time_gate_open() -> bool:
    """Return True to run 24/7 (Time Gate removed)."""
    return True


def apply_regime(direction: str, regime: str, is_momentum: bool = True) -> str:
    """
    Regime-aware direction (REBUILD 2026-06-09).

    Momentum-following signals (SIG) lose because they chase a finished move into a
    fresh candle that mean-reverts. In a MEAN_REVERSION regime we FADE momentum (flip
    the direction); in TREND we follow it.

    is_momentum=False (fair-value and reversal signals) is returned unchanged — those
    already encode their own directional edge and must not be double-flipped.
    """
    if not is_momentum:
        return direction
    if regime == "MEAN_REVERSION":
        faded = "DOWN" if direction == "UP" else "UP"
        log.info("[REGIME-FADE] mean-reversion regime — fading momentum %s -> %s", direction, faded)
        return faded
    return direction

