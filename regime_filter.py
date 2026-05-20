# regime_filter.py - Weekday/weekend regime + UTC time gate
from datetime import datetime, timezone
from typing import Literal


def get_regime_mode() -> Literal["TREND", "MEAN_REVERSION"]:
    """Mon–Fri = TREND (follow RSI). Sat–Sun = MEAN_REVERSION (fade RSI extremes)."""
    return "TREND" if datetime.now(timezone.utc).weekday() < 5 else "MEAN_REVERSION"


def time_gate_open() -> bool:
    """Return True only during UTC 13:00–23:00 (US + EU active sessions)."""
    from config import TIME_GATE_UTC
    hour = datetime.now(timezone.utc).hour
    start, end = TIME_GATE_UTC
    return start <= hour < end


def apply_regime(direction: str, regime: str) -> str:
    """
    Apply regime logic to a raw RSI signal direction.
    TREND: keep the signal as-is (follow momentum).
    MEAN_REVERSION: invert the signal (fade extremes).
    """
    if regime == "MEAN_REVERSION":
        return "DOWN" if direction == "UP" else "UP"
    return direction
