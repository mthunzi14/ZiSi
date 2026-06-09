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
    # Regimes that imply genuine mean-reversion (→ fade momentum). REBUILD: COMPRESSION
    # (low-vol squeeze precedes a breakout, not reversion) and NORMAL/unknown (the
    # post-reset default) now map to TREND (follow), so the SIG fade only fires on a
    # real mean-reverting label — the detector is OBI-starved and over-labels MR.
    _MEAN_REVERSION_REGIMES = {
        "MEAN_REVERTING",   # canonical
        "RANGE",            # legacy alias for genuine range/mean-reversion
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


def apply_regime(direction: str, regime: str, is_momentum: bool = True, mom: float = None) -> str:
    """
    Regime-aware direction (REBUILD 2026-06-09).

    Momentum-following signals (SIG) lose when they chase a finished move into a fresh
    candle that mean-reverts. In a MEAN_REVERSION regime we FADE momentum (flip), but
    ONLY when momentum is WEAK (genuine chop). In a strong directional move (large |mom|)
    we FOLLOW even under a MEAN_REVERSION label — the regime detector is OBI-starved and
    over-labels mean-reversion, so fading a real trend would put us on the wrong side.

    is_momentum=False (fair-value / reversal signals) is returned unchanged — those
    already encode their own directional edge and must not be double-flipped.
    """
    if not is_momentum:
        return direction
    if regime == "MEAN_REVERSION":
        fade_max_mom = float(os.getenv("SIG_FADE_MAX_MOM", "0.0015"))
        if mom is not None and abs(mom) >= fade_max_mom:
            return direction  # strong trend — follow, do not fade
        faded = "DOWN" if direction == "UP" else "UP"
        log.info("[REGIME-FADE] mean-reversion + weak momentum (mom=%.4f) — fading %s -> %s",
                 (mom if mom is not None else 0.0), direction, faded)
        return faded
    return direction

