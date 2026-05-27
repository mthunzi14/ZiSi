"""
conflict_detector.py - Cross-market conflict detection.

Prevents double-leverage on the same asset when a signal
appears on both Polymarket and Kalshi simultaneously.
"""
import logging
from typing import Dict, List, Optional, Tuple

log = logging.getLogger("zisi.conflict_detector")

# Static pairwise correlation estimates
_CORRELATIONS: Dict = {
    ("bitcoin",  "ethereum"): 0.85,
    ("ethereum", "bitcoin"):  0.85,
    ("bitcoin",  "solana"):   0.70,
    ("solana",   "bitcoin"):  0.70,
    ("bitcoin",  "xrp"):     0.65,
    ("xrp",      "bitcoin"):  0.65,
}

_HIGH_CORR_THRESHOLD = 0.75


class ConflictDetector:
    """
    Detect and reduce positions when the same asset is bet on both markets.

    Usage:
        detector = ConflictDetector()
        conflicts = detector.detect(poly_trades, kalshi_trades)
        poly_trades = detector.apply(poly_trades, conflicts)
    """

    def detect(
        self,
        polymarket_trades: List[Dict],
        kalshi_trades: List[Dict],
    ) -> List[Tuple[int, float]]:
        """
        Return list of (poly_trade_index, size_multiplier) for conflicts.
        size_multiplier is 0.5 for same-asset conflicts.
        """
        conflicts: List[Tuple[int, float]] = []

        for poly_idx, poly_trade in enumerate(polymarket_trades):
            poly_asset = _extract_asset(
                poly_trade.get("market", {}).get("title", "")
            )
            poly_dir = _extract_direction(poly_trade.get("signal", {}))

            for kalshi_trade in kalshi_trades:
                kalshi_asset = _extract_asset(
                    kalshi_trade.get("event", {}).get("title", "")
                    + " " + kalshi_trade.get("matched_implication", "")
                )
                kalshi_dir = _extract_direction(kalshi_trade.get("signal", {}))

                if _is_conflict(poly_asset, kalshi_asset, poly_dir, kalshi_dir):
                    conflicts.append((poly_idx, 0.5))
                    log.warning(
                        "[CONFLICT-DETECTED] %s in both markets (%s) — Poly position halved",
                        poly_asset, poly_dir,
                    )
                    break  # one Kalshi conflict is enough to flag this poly trade

        if not conflicts:
            log.debug("[CONFLICT-DETECTOR] No cross-market conflicts found")
        return conflicts

    def apply(
        self,
        polymarket_trades: List[Dict],
        conflicts: List[Tuple[int, float]],
    ) -> List[Dict]:
        """Apply position adjustments to conflicting Polymarket trades."""
        for idx, multiplier in conflicts:
            if idx < len(polymarket_trades):
                orig = polymarket_trades[idx].get("position_size", 1.0)
                polymarket_trades[idx]["position_size"] = round(orig * multiplier, 2)
                polymarket_trades[idx]["conflict_adjusted"] = True
        return polymarket_trades


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_asset(text: str) -> Optional[str]:
    t = text.lower()
    if "bitcoin" in t or " btc" in t:
        return "bitcoin"
    if "ethereum" in t or " eth" in t:
        return "ethereum"
    if "solana" in t or " sol" in t:
        return "solana"
    if "ripple" in t or " xrp" in t:
        return "xrp"
    if "dogecoin" in t or " doge" in t:
        return "dogecoin"
    return None


def _extract_direction(signal: Dict) -> str:
    return (signal.get("sentiment", "neutral") or "neutral").lower()


def _is_conflict(a1: Optional[str], a2: Optional[str], d1: str, d2: str) -> bool:
    if not a1 or not a2:
        return False
    if d1 == "neutral" or d2 == "neutral":
        return False
    if d1 != d2:
        return False  # opposite directions is hedging, not conflict
    same = a1 == a2
    corr = _CORRELATIONS.get((a1, a2), 0.0)
    return same or corr >= _HIGH_CORR_THRESHOLD
