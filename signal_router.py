"""
signal_router.py - Signal Type Classification & Market Routing.

Classifies signals into 4 types based on coin specificity and confidence,
then routes each type to the appropriate market categories.

Confidence is on the 0-10 integer scale used throughout ZiSi:
  TYPE_A_HIGH: coin-specific signal, confidence >= 8  → Kelly 1.5x
  TYPE_A_LOW:  coin-specific signal, confidence 7      → Kelly 1.0x
  TYPE_B_HIGH: no specific coin,     confidence >= 8  → Kelly 0.8x
  TYPE_B_LOW:  no specific coin,     confidence 7     → Kelly 0.4x
"""
import logging
from typing import Dict, List

log = logging.getLogger("zisi.signal_router")


class SignalTypeClassifier:
    """Classify signals and annotate them with routing metadata."""

    def classify(self, signal: Dict) -> Dict:
        """
        Adds signal_type, kelly_multiplier, route_to, is_crypto_specific,
        and is_high_confidence keys to signal dict (mutates in place).
        """
        affected = signal.get("affected_cryptos", [])
        raw_conf = signal.get("confidence", 0) or 0
        # Normalise: confidence is 0-10 int; convert to 0-1 for comparisons
        conf_norm = float(raw_conf) / 10.0 if raw_conf > 1 else float(raw_conf)

        is_crypto = len(affected) > 0
        is_high = conf_norm >= 0.8 if is_crypto else conf_norm >= 0.8

        if is_crypto and is_high:
            signal_type = "TYPE_A_HIGH"
            kelly_mult = 1.5
            route_to = "CRYPTO_ONLY"
        elif is_crypto:
            signal_type = "TYPE_A_LOW"
            kelly_mult = 1.0
            route_to = "CRYPTO_ONLY"
        elif is_high:
            signal_type = "TYPE_B_HIGH"
            kelly_mult = 0.8
            route_to = "ALL_CATEGORIES"
        else:
            signal_type = "TYPE_B_LOW"
            kelly_mult = 0.4
            route_to = "SAFE_ONLY"

        signal["signal_type"] = signal_type
        signal["kelly_multiplier"] = kelly_mult
        signal["route_to"] = route_to
        signal["is_crypto_specific"] = is_crypto
        signal["is_high_confidence"] = is_high
        signal["confidence_norm"] = round(conf_norm, 4)

        log.info(
            "[SIGNAL-CLASSIFY] %s | conf=%.2f | kelly=%.1fx | route=%s | coins=%s",
            signal_type, conf_norm, kelly_mult, route_to,
            affected[:1] if affected else ["(none)"],
        )
        return signal


class RoutingEngine:
    """Filter events/markets to those eligible for a classified signal."""

    def get_eligible_markets(
        self,
        signal: Dict,
        polymarket_events: List[Dict],
        kalshi_events: List[Dict],
    ) -> Dict:
        """
        Returns {'polymarket': [...], 'kalshi': [...]} filtered to eligible markets.

        Args:
            signal:             Classified signal dict (has signal_type / route_to).
            polymarket_events:  Full list from fetch_polymarket_events().
            kalshi_events:      Full list from KalshiEventFetcher.fetch_events().
        """
        signal_type = signal.get("signal_type", "TYPE_B_LOW")
        affected = [c.lower() for c in signal.get("affected_cryptos", [])]

        eligible: Dict = {"polymarket": [], "kalshi": []}

        if signal_type == "TYPE_A_HIGH":
            # Crypto-specific + high confidence → CRYPTO markets only
            eligible["polymarket"] = [
                ev for ev in polymarket_events
                if ev.get("market_category") == "CRYPTO" or _is_crypto_event(ev, affected)
            ]
            eligible["kalshi"] = [
                m for m in kalshi_events
                if m.get("_category", "OTHER") in ("CRYPTO", "FINANCE")
            ]

        elif signal_type == "TYPE_A_LOW":
            # Crypto-specific + moderate confidence → all market types in CRYPTO category.
            # Previously restricted to UP_DOWN/PRICE_RANGE only, which blocked most events
            # because _classify_polymarket_market_type often returns "OTHER" for valid markets.
            eligible["polymarket"] = [
                ev for ev in polymarket_events
                if (ev.get("market_category") == "CRYPTO" or _is_crypto_event(ev, affected))
            ]
            eligible["kalshi"] = [
                m for m in kalshi_events
                if m.get("_category", "OTHER") in ("CRYPTO", "FINANCE")
            ]

        elif signal_type == "TYPE_B_HIGH":
            # General high-confidence → UP_DOWN markets across CRYPTO/FINANCE/POLITICS
            eligible["polymarket"] = [
                ev for ev in polymarket_events
                if ev.get("market_type") == "UP_DOWN"
                and ev.get("market_category", "OTHER") in ("CRYPTO", "FINANCE", "POLITICS", "OTHER")
            ]
            eligible["kalshi"] = [
                m for m in kalshi_events
                if m.get("_category", "OTHER") != "SPORTS"
            ]

        else:  # TYPE_B_LOW / SAFE_ONLY
            # Weak general signal → UP_DOWN only, no Kalshi
            eligible["polymarket"] = [
                ev for ev in polymarket_events
                if ev.get("market_type") == "UP_DOWN"
            ]
            eligible["kalshi"] = []

        log.debug(
            "[ROUTING-ENGINE] %s | poly=%d/%d eligible | kalshi=%d eligible",
            signal_type, len(eligible["polymarket"]), len(polymarket_events),
            len(eligible["kalshi"]),
        )
        return eligible


class CategoryConfidenceWeighter:
    """Return an empirical multiplier for exchange × category combinations."""

    _WEIGHTS: Dict = {
        "Kalshi|SPORTS":     1.00,
        "Polymarket|CRYPTO": 0.95,
        "Kalshi|CRYPTO":     0.95,
        "Polymarket|SPORTS": 0.85,
        "Polymarket|FINANCE":0.80,
        "Kalshi|FINANCE":    0.80,
        "Polymarket|OTHER":  0.75,
        "Kalshi|OTHER":      0.75,
        "Polymarket|POLITICS":0.65,
        "Kalshi|POLITICS":   0.70,
    }

    def get_weight(self, exchange: str, category: str) -> float:
        key = f"{exchange}|{category or 'OTHER'}"
        weight = self._WEIGHTS.get(key, 0.75)
        log.debug("[CATEGORY-WEIGHT] %s → %.2fx", key, weight)
        return weight


# ---------------------------------------------------------------------------
# Dual-platform routing decision
# ---------------------------------------------------------------------------

def routing_decision(
    confidence: float,
    spread: float = 0.05,
    has_polymarket: bool = True,
    has_kalshi: bool = True,
    kalshi_yes_price: float = 0.0,
    polymarket_yes_price: float = 0.0,
) -> dict:
    """
    Determine the platform routing target for a signal.

    Decision matrix (confidence is on 0-10 raw Gemini scale):
      conf >= 7.0 + spread < 0.04 + both available → BOTH
      conf 6.0-6.9 + both available                → KALSHI_ONLY
      conf < 6.0                                   → SKIP
      only Polymarket available                    → POLYMARKET
      only Kalshi available                        → KALSHI
      cross-platform arb detected                  → BOTH_ARBITRAGE

    Arbitrage condition: Kalshi YES price + Polymarket NO price < 0.97
    (i.e. buying both legs costs < $0.97, guaranteed payout $1.00).

    Args:
        confidence:            Raw Gemini confidence score (0-10 scale).
        spread:                Polymarket bid-ask spread as a fraction.
        has_polymarket:        Whether a liquid Polymarket market exists.
        has_kalshi:            Whether a Kalshi market exists.
        kalshi_yes_price:      Kalshi YES ask price (0-1 scale). 0 = unknown.
        polymarket_yes_price:  Polymarket YES mid price (0-1 scale). 0 = unknown.

    Returns:
        Dict with: target (str), reason (str), arbitrage (bool).
    """
    # Cross-platform arbitrage: combined leg cost < $0.97 → guaranteed profit
    arb_detected = False
    if (kalshi_yes_price > 0 and polymarket_yes_price > 0
            and has_kalshi and has_polymarket):
        polymarket_no_price = round(1.0 - polymarket_yes_price, 4)
        combined_cost = kalshi_yes_price + polymarket_no_price
        if combined_cost < 0.97:
            arb_detected = True
            log.info(
                "[ROUTING] ARBITRAGE detected | kalshi_YES=%.3f + poly_NO=%.3f = %.3f < 0.97",
                kalshi_yes_price, polymarket_no_price, combined_cost,
            )

    if arb_detected:
        result = {"target": "BOTH_ARBITRAGE", "reason": "cross_platform_arb", "arbitrage": True}

    elif not has_polymarket and not has_kalshi:
        result = {"target": "SKIP", "reason": "no_markets_available", "arbitrage": False}

    elif not has_polymarket:
        result = {"target": "KALSHI", "reason": "polymarket_unavailable", "arbitrage": False}

    elif not has_kalshi:
        result = {"target": "POLYMARKET", "reason": "kalshi_unavailable", "arbitrage": False}

    elif confidence >= 7.0 and spread < 0.04:
        result = {
            "target": "BOTH",
            "reason": f"high_conf={confidence:.1f}_tight_spread={spread:.3f}",
            "arbitrage": False,
        }

    elif 6.0 <= confidence < 7.0:
        result = {
            "target": "KALSHI_ONLY",
            "reason": f"medium_conf={confidence:.1f}_safer_on_kalshi",
            "arbitrage": False,
        }

    elif confidence < 6.0:
        result = {
            "target": "SKIP",
            "reason": f"low_conf={confidence:.1f}_below_threshold",
            "arbitrage": False,
        }

    else:
        result = {"target": "KALSHI_ONLY", "reason": "fallback", "arbitrage": False}

    log.info(
        "[ROUTING DECISION] %s | conf=%.1f spread=%.3f poly=%s kalshi=%s | %s",
        result["target"], confidence, spread, has_polymarket, has_kalshi, result["reason"],
    )
    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_crypto_event(event: Dict, affected: List[str]) -> bool:
    """Return True if the event title mentions any of the affected coins."""
    if not affected:
        return True  # no coin filter → accept all
    title_lower = (event.get("title", "") + " " + event.get("description", "")).lower()
    return any(coin in title_lower for coin in affected)
