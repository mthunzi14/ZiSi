"""
signal_router.py - Signal classification, routing, and weighting engine.
"""
import logging

log = logging.getLogger("zisi.signal_router")

class SignalTypeClassifier:
    """
    Classifies raw signals into signal types and maps appropriate base Kelly scale multipliers.
    """
    MULTIPLIERS = {
        "UP_DOWN": 1.0,
        "HIT_PRICE": 0.5,
        "PRICE_RANGE": 0.7,
        "OTHER": 0.8
    }

    def classify(self, signal: dict) -> dict:
        if not signal:
            return signal
        
        # If signal_type is not already present, determine it
        if "signal_type" not in signal:
            # Check direction or fields to classify
            direction = str(signal.get("direction", "")).upper()
            if direction in ["UP", "DOWN"]:
                signal["signal_type"] = "UP_DOWN"
            else:
                signal["signal_type"] = "OTHER"
                
        # Inject kelly_multiplier based on signal_type
        if "kelly_multiplier" not in signal:
            sig_type = signal["signal_type"].upper()
            signal["kelly_multiplier"] = self.MULTIPLIERS.get(sig_type, 0.8)
            
        return signal


class RoutingEngine:
    """
    Routes enriched signals to matching Polymarket or Kalshi events.
    """
    def get_eligible_markets(
        self,
        signal: dict,
        polymarket_events: list,
        kalshi_events: list
    ) -> dict:
        asset = str(signal.get("coin") or signal.get("asset") or "").upper()
        
        eligible_poly = []
        for ev in polymarket_events:
            title = str(ev.get("title") or ev.get("event_title") or "").upper()
            coins_mentioned = str(ev.get("coins_mentioned") or "").upper()
            if asset and (asset in title or asset in coins_mentioned):
                eligible_poly.append(ev)
                
        eligible_kalshi = []
        for ev in kalshi_events:
            title = str(ev.get("title") or ev.get("event_title") or "").upper()
            if asset and asset in title:
                eligible_kalshi.append(ev)
                
        return {
            "polymarket": eligible_poly,
            "kalshi": eligible_kalshi
        }


class CategoryConfidenceWeighter:
    """
    Returns confidence weights for different category-exchange combinations.
    """
    def __init__(self) -> None:
        self.weights = {
            "polymarket": {
                "CRYPTO": 1.2,
                "MACRO": 1.0,
                "SPORTS": 0.5,
                "OTHER": 0.8,
            },
            "kalshi": {
                "CRYPTO": 1.2,
                "MACRO": 1.0,
                "OTHER": 0.8,
            }
        }

    def get_weight(self, exchange: str, category: str) -> float:
        ex = str(exchange).lower()
        cat = str(category).upper()
        if ex not in self.weights:
            return 1.0
        return self.weights[ex].get(cat, self.weights[ex].get("OTHER", 1.0))
