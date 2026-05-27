"""
trade_priority_queue.py - Priority ordering and outcome tracking.

Ranks prepared trades so the highest-conviction ones execute first,
and records outcomes for feedback-driven adjustment over time.
"""
import logging
from enum import IntEnum
from typing import Dict, List

log = logging.getLogger("zisi.priority_queue")


class TradePriority(IntEnum):
    """Lower value = execute first."""
    CRITICAL  = 1   # TYPE_A_HIGH + CRYPTO
    EXCELLENT = 2   # TYPE_B_HIGH + SPORTS (proven)
    GOOD      = 3   # TYPE_A_LOW  + CRYPTO
    FAIR      = 4   # TYPE_B_HIGH + OTHER
    LOW       = 5   # TYPE_B_LOW  (any)


def _get_priority(trade: Dict) -> TradePriority:
    sig_type = trade.get("signal", {}).get("signal_type", "")
    category = (
        trade.get("market", {}).get("market_category") or
        trade.get("market", {}).get("_category") or
        trade.get("event", {}).get("_category") or
        "OTHER"
    )

    if sig_type == "TYPE_A_HIGH" and category == "CRYPTO":
        return TradePriority.CRITICAL
    if sig_type == "TYPE_B_HIGH" and category == "SPORTS":
        return TradePriority.EXCELLENT
    if sig_type == "TYPE_A_LOW" and category == "CRYPTO":
        return TradePriority.GOOD
    if sig_type == "TYPE_B_HIGH":
        return TradePriority.FAIR
    return TradePriority.LOW


class PriorityQueue:
    """Sort a list of trade dicts by execution priority."""

    def prioritize(self, trades: List[Dict]) -> List[Dict]:
        """Return trades sorted highest-priority first."""
        sorted_trades = sorted(trades, key=_get_priority)

        counts = {p: 0 for p in TradePriority}
        for t in sorted_trades:
            counts[_get_priority(t)] += 1

        log.info(
            "[PRIORITY-QUEUE] %d trades | CRITICAL=%d EXCELLENT=%d GOOD=%d FAIR=%d LOW=%d",
            len(sorted_trades),
            counts[TradePriority.CRITICAL],
            counts[TradePriority.EXCELLENT],
            counts[TradePriority.GOOD],
            counts[TradePriority.FAIR],
            counts[TradePriority.LOW],
        )
        return sorted_trades


class FeedbackTracker:
    """
    Record trade outcomes and report win rates by signal_type × category.

    Usage:
        tracker = FeedbackTracker()
        tracker.record("TYPE_A_HIGH", "CRYPTO", 0.9, "WIN")
        tracker.win_rate("TYPE_A_HIGH", "CRYPTO")  # → 1.0
    """

    def __init__(self) -> None:
        self._outcomes: Dict = {}

    def record(
        self,
        signal_type: str,
        category: str,
        confidence: float,
        result: str,
    ) -> None:
        key = f"{signal_type}|{category}"
        if key not in self._outcomes:
            self._outcomes[key] = {"wins": 0, "losses": 0}
        if result.upper() == "WIN":
            self._outcomes[key]["wins"] += 1
        else:
            self._outcomes[key]["losses"] += 1

        data = self._outcomes[key]
        total = data["wins"] + data["losses"]
        wr = data["wins"] / total if total else 0.0
        log.debug(
            "[FEEDBACK] %s | result=%s conf=%.2f | win_rate=%.0f%% (N=%d)",
            key, result, confidence, wr * 100, total,
        )

    def win_rate(self, signal_type: str, category: str) -> float:
        key = f"{signal_type}|{category}"
        d = self._outcomes.get(key, {})
        total = d.get("wins", 0) + d.get("losses", 0)
        return d.get("wins", 0) / total if total else 0.0

    def summary(self) -> Dict:
        result = {}
        for key, d in self._outcomes.items():
            total = d["wins"] + d["losses"]
            result[key] = {
                "wins": d["wins"],
                "losses": d["losses"],
                "win_rate": round(d["wins"] / total, 4) if total else 0.0,
                "total": total,
            }
        return result
