"""
cycle_manager.py - Signal-to-trade orchestration for each bot cycle.

Wires together: SignalTypeClassifier → RoutingEngine → PositionSizer →
ConflictDetector → PriorityQueue.

The CycleManager does NOT execute trades.  It returns a structured dict of
classified, sized, and prioritised trade candidates that the main loop (or
markets_orchestrator.py) passes to the actual order executors.

Usage in main.py:
    from cycle_manager import CycleManager
    _cycle_manager = CycleManager(account_balance=cfg["ACCOUNT_BALANCE"])

    # Inside the main cycle:
    result = _cycle_manager.process_signals(signals, all_events, kalshi_events)
    for sig in result["enriched_signals"]:
        _process_signal(sig, result["eligible_events"][sig["signal_type"]], cfg)
"""
import logging
from typing import Dict, List

from signal_router import SignalTypeClassifier, RoutingEngine, CategoryConfidenceWeighter
from position_sizer import PositionSizer
from conflict_detector import ConflictDetector
from trade_priority_queue import PriorityQueue, FeedbackTracker

log = logging.getLogger("zisi.cycle_manager")


class CycleManager:
    """
    Orchestrate signal classification, routing, sizing, conflict detection,
    and prioritisation for a single 15/30-minute cycle.
    """

    def __init__(self, account_balance: float = 100.0) -> None:
        self.account_balance = account_balance
        self.classifier   = SignalTypeClassifier()
        self.router       = RoutingEngine()
        self.weighter     = CategoryConfidenceWeighter()
        self.sizer        = PositionSizer(account_balance)
        self.detector     = ConflictDetector()
        self.queue        = PriorityQueue()
        self.feedback     = FeedbackTracker()

    def process_signals(
        self,
        signals: List[Dict],
        polymarket_events: List[Dict],
        kalshi_events: List[Dict],
    ) -> Dict:
        """
        Run all signals through the full pipeline.

        Returns:
            {
              "enriched_signals":  [...],  # signals with signal_type / kelly_multiplier added
              "polymarket_candidates": [...],  # (event, position_size) tuples
              "kalshi_candidates": [...],      # same for Kalshi
              "capital_deployed":  float,
              "trade_count":       int,
              "conflicts_detected": int,
            }
        """
        self.sizer.reset_cycle()

        enriched:    List[Dict] = []
        poly_cands:  List[Dict] = []
        kalshi_cands:List[Dict] = []

        for signal in signals:
            # 1. Classify
            signal = self.classifier.classify(signal)
            enriched.append(signal)

            # 2. Route
            eligible = self.router.get_eligible_markets(
                signal, polymarket_events, kalshi_events
            )

            # 3. Size + collect polymarket candidates
            for ev in eligible["polymarket"]:
                cat = ev.get("market_category") or ev.get("category") or "OTHER"
                cat_wt = self.weighter.get_weight("Polymarket", cat)
                size = self.sizer.calculate(signal, ev, cat_wt)
                if size > 0:
                    poly_cands.append({
                        "signal": signal,
                        "market": ev,
                        "position_size": size,
                        "category_weight": cat_wt,
                        "exchange": "polymarket",
                    })

            # 4. Size + collect Kalshi candidates
            for ev in eligible["kalshi"]:
                cat = ev.get("_category") or "OTHER"
                cat_wt = self.weighter.get_weight("Kalshi", cat)
                size = self.sizer.calculate(signal, ev, cat_wt)
                if size > 0:
                    kalshi_cands.append({
                        "signal": signal,
                        "event": ev,
                        "position_size": size,
                        "category_weight": cat_wt,
                        "exchange": "kalshi",
                    })

        # 5. Conflict detection (reduce Poly positions where Kalshi overlaps)
        conflicts = self.detector.detect(poly_cands, kalshi_cands)
        poly_cands = self.detector.apply(poly_cands, conflicts)

        # 6. Prioritise
        poly_cands   = self.queue.prioritize(poly_cands)
        kalshi_cands = self.queue.prioritize(kalshi_cands)

        # 7. Cap at 15 poly + 10 kalshi per cycle
        poly_cands   = poly_cands[:15]
        kalshi_cands = kalshi_cands[:10]

        total_trades = len(poly_cands) + len(kalshi_cands)

        log.info(
            "[CYCLE-MANAGER] signals=%d | poly_cands=%d | kalshi_cands=%d"
            " | conflicts=%d | capital=$%.2f",
            len(enriched), len(poly_cands), len(kalshi_cands),
            len(conflicts), self.sizer.capital_used,
        )

        return {
            "enriched_signals":      enriched,
            "polymarket_candidates": poly_cands,
            "kalshi_candidates":     kalshi_cands,
            "capital_deployed":      self.sizer.capital_used,
            "trade_count":           total_trades,
            "conflicts_detected":    len(conflicts),
        }

    def record_outcome(
        self,
        signal_type: str,
        category: str,
        confidence: float,
        result: str,
    ) -> None:
        """Log a resolved trade outcome for win-rate tracking."""
        self.feedback.record(signal_type, category, confidence, result)

    def feedback_summary(self) -> Dict:
        """Return win-rate breakdown by signal_type × category."""
        return self.feedback.summary()
