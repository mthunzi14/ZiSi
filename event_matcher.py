"""
event_matcher.py - ZiSi Bot Event Matching
Maps crypto sentiment signals to the most relevant Polymarket events.
"""

import logging
import re
from datetime import datetime, timezone
from typing import Optional

from config import load_config

log = logging.getLogger("zisi.event_matcher")

# Politics keywords for market classification
_POLITICS_KEYWORDS: frozenset = frozenset({
    "election", "vote", "congress", "senate", "president", "trump", "biden",
    "harris", "republican", "democrat", "political", "poll", "ballot",
    "campaign", "nominee", "primary", "inauguration", "parliament",
    "legislation", "white house", "governor",
})

# Synonym map so "btc" also matches "bitcoin" events
_CRYPTO_ALIASES: dict[str, list[str]] = {
    "bitcoin": ["bitcoin", "btc"],
    "ethereum": ["ethereum", "eth", "ether"],
    "solana": ["solana", "sol"],
    "ripple": ["ripple", "xrp"],
    "dogecoin": ["dogecoin", "doge"],
    "cardano": ["cardano", "ada"],
    "polkadot": ["polkadot", "dot"],
    "chainlink": ["chainlink", "link"],
    "avalanche": ["avalanche", "avax"],
    "polygon": ["polygon", "matic"],
}


def _resolution_window_multiplier(event: dict) -> float:
    """
    Return a scoring multiplier based on days until market resolution.

    Sweet spot: 1-7 days — news impact is maximum, price not yet fully adjusted.
    The further out the resolution, the less edge our fresh news signal has.

    Multipliers:
      1–7 days   → 1.25x  (prime window — maximum edge)
      7–14 days  → 1.00x  (solid, normal priority)
      14–30 days → 0.80x  (news will be stale before resolution)
      >30 days   → 0.60x  (too far out — market has time to self-correct)
      No date    → 1.00x  (unknown, no penalty)
    """
    res_str = event.get("resolutionDate") or event.get("endDate") or ""
    if not res_str:
        return 1.0
    try:
        clean = res_str.replace("Z", "+00:00")
        res_dt = datetime.fromisoformat(clean)
        if res_dt.tzinfo is None:
            res_dt = res_dt.replace(tzinfo=timezone.utc)
        days = (res_dt - datetime.now(timezone.utc)).days
        if days < 0:
            return 0.0   # already expired — should have been filtered at fetch
        if days <= 7:
            return 1.25
        if days <= 14:
            return 1.00
        if days <= 30:
            return 0.80
        return 0.60
    except Exception:
        return 1.0


def _classify_event_market(event: dict) -> str:
    """
    Classify a Polymarket event into BTC, ETH, POLITICS, or OTHER.
    Used for 70/30 market filtering strategy.
    """
    title = event.get("title", "").lower()
    if "btc" in title or "bitcoin" in title:
        return "BTC"
    if "eth" in title or "ethereum" in title:
        return "ETH"
    if any(kw in title for kw in _POLITICS_KEYWORDS):
        return "POLITICS"
    return "OTHER"


def _expand_keywords(cryptos: list[str]) -> list[str]:
    """Expand a list of crypto names to include all known aliases."""
    expanded = set()
    for name in cryptos:
        name_lower = name.lower()
        expanded.add(name_lower)
        for canonical, aliases in _CRYPTO_ALIASES.items():
            if name_lower in aliases or name_lower == canonical:
                expanded.update(aliases)
                expanded.add(canonical)
    return list(expanded)


def calculate_event_relevance(
    event_title: str,
    news_keywords: list[str],
    sentiment: str,
) -> float:
    """
    Score how relevant a Polymarket event title is to a news signal.

    Args:
        event_title:   e.g. "Will Bitcoin hit $70,000 by May 31?"
        news_keywords: e.g. ["bitcoin", "btc"]
        sentiment:     "bullish" | "bearish" | "neutral"
    Returns:
        float in [0, 1]. >0.6 is considered a good match.
    """
    title_lower = event_title.lower()
    keywords = _expand_keywords(news_keywords)
    score = 0.0

    # Exact word matches score highest
    exact_matches = sum(
        1 for kw in keywords if re.search(r"\b" + re.escape(kw) + r"\b", title_lower)
    )
    if exact_matches >= 2:
        score = 1.0
    elif exact_matches == 1:
        score = 0.85
    else:
        # Substring matches (lower confidence)
        partial_matches = sum(1 for kw in keywords if kw in title_lower)
        score = min(0.5, 0.2 * partial_matches)

    # Bonus: event outcome aligns with sentiment direction
    if sentiment == "bullish" and any(
        phrase in title_lower for phrase in ["above", "reach", "hit", "exceed", "over", "surge"]
    ):
        score = min(1.0, score + 0.05)
    elif sentiment == "bearish" and any(
        phrase in title_lower for phrase in ["below", "drop", "fall", "crash", "under", "decline"]
    ):
        score = min(1.0, score + 0.05)

    return round(score, 4)


def find_matching_events(
    sentiment_data: dict,
    all_polymarket_events: list[dict],
) -> list[dict]:
    """
    Find Polymarket events that match the sentiment signal's affected cryptos.

    Args:
        sentiment_data:        A single sentiment analysis result dict.
        all_polymarket_events: Full list from data_fetcher.fetch_polymarket_events().
    Returns:
        Up to 5 events sorted by relevance, each enriched with relevance_score
        and matching_reason.  Empty list if nothing qualifies.
    """
    cfg = load_config()
    min_liquidity = cfg["MIN_EVENT_LIQUIDITY_USD"]

    # Pre-matched candidate fast-path: when CycleManager passes a single event,
    # accept it directly without running relevance scoring (it's already matched).
    if len(all_polymarket_events) == 1:
        event = all_polymarket_events[0]
        if event.get("active", True):
            market_cat = _classify_event_market(event)
            enriched = dict(event)
            enriched.setdefault("relevance_score", 0.5)
            enriched["market_category"] = market_cat
            enriched["matching_reason"] = "pre-matched candidate (CycleManager)"
            log.info("[POLY-MATCH-OK] Pre-matched candidate: %s", event.get("title", "")[:70])
            return [enriched]
        return []

    affected = sentiment_data.get("affected_cryptos", [])
    sentiment_dir = sentiment_data.get("sentiment", "neutral")

    # Fallback: if no coins specified, match any crypto/finance event
    if not affected:
        affected = ["bitcoin", "ethereum", "crypto", "btc", "eth"]
        log.debug("[POLY-MATCH] No affected_cryptos — using general crypto keywords")

    log.debug(
        "[POLY-MATCH] Signal=%s | Coins=%s | Markets to search=%d",
        sentiment_dir, affected, len(all_polymarket_events),
    )

    scored: list[dict] = []
    for event in all_polymarket_events:
        # Skip inactive events
        if not event.get("active", True):
            continue

        # Skip completely illiquid events (hard floor = 100 USD for paper trading)
        liq_floor = max(100.0, min_liquidity / 100)
        if float(event.get("liquidity", 0)) < liq_floor:
            log.debug(
                "[POLY-MATCH-SKIP] Liquidity $%.0f < $%.0f floor | %s",
                float(event.get("liquidity", 0)), liq_floor,
                event.get("title", "")[:60],
            )
            continue

        relevance = calculate_event_relevance(
            event_title=event.get("title", ""),
            news_keywords=affected,
            sentiment=sentiment_dir,
        )

        # Threshold by category — crypto/other markets get lenient matching,
        # politics/sports need stronger relevance to avoid noise trades
        cat = event.get("market_category", "OTHER")
        if cat in ("CRYPTO", "OTHER", "FINANCE"):
            min_relevance = 0.1
        elif cat == "POLITICS":
            min_relevance = 0.2
        else:
            min_relevance = 0.15
        if relevance < min_relevance:
            log.debug(
                "[POLY-MATCH-SKIP] Relevance %.2f < %.2f min | cat=%s | %s",
                relevance, min_relevance, cat, event.get("title", "")[:60],
            )
            continue

        # Apply resolution-window multiplier — favour markets resolving soon
        res_mult = _resolution_window_multiplier(event)
        if res_mult == 0.0:
            log.debug("[POLY-MATCH-SKIP] Event already expired: %s", event.get("title", "")[:60])
            continue
        final_score = round(relevance * res_mult, 4)

        market_cat = _classify_event_market(event)
        enriched = dict(event)
        enriched["relevance_score"] = final_score
        enriched["resolution_multiplier"] = res_mult
        enriched["market_category"] = market_cat
        enriched["matching_reason"] = (
            f"Keyword match (score={relevance:.2f}×res{res_mult:.2f}={final_score:.2f}, "
            f"market={market_cat}): {', '.join(affected)}"
        )
        scored.append(enriched)

    scored.sort(key=lambda e: e["relevance_score"], reverse=True)
    top5 = scored[:5]

    if not top5:
        log.info(
            "[POLY-MATCH-FAIL] 0 matches | Signal=%s | Coins=%s | Searched %d markets",
            sentiment_dir, affected, len(all_polymarket_events),
        )
    else:
        market_counts: dict = {}
        for e in top5:
            m = e.get("market_category", "OTHER")
            market_counts[m] = market_counts.get(m, 0) + 1
        log.info(
            "[POLY-MATCH-OK] Found %d events (top %d) | Markets: %s | Signal=%s",
            len(scored), len(top5), market_counts, sentiment_dir,
        )

    return top5


def select_best_event(
    matching_events: list[dict],
    sentiment_direction: str,
) -> Optional[dict]:
    """
    Pick the single highest-quality event to trade.

    Args:
        matching_events:    Filtered & scored list from find_matching_events().
        sentiment_direction: "bullish" | "bearish" | "neutral"
    Returns:
        The best event dict, or None if no suitable event exists.
    """
    if not matching_events:
        return None

    if sentiment_direction == "neutral":
        log.info("Neutral sentiment — no trade event selected")
        return None

    # Events are already sorted by relevance; take the first with markets
    for event in matching_events:
        if event.get("markets"):
            log.info("Selected event: %s (relevance=%.2f)", event["title"], event["relevance_score"])
            return event

    log.info("No event with available markets found")
    return None


def check_liquidity(event: dict) -> float:
    """
    Returns liquidity score (0-1).
    0 = no liquidity or spread too wide, 1 = excellent liquidity.
    Falls back to raw liquidity value if bid/ask not present.
    """
    try:
        bid = float(event.get("bid", 0))
        ask = float(event.get("ask", 0))

        # If bid/ask present, use spread scoring
        if ask > 0 and bid > 0:
            spread = (ask - bid) / ask
            if spread > 0.10:
                return 0.0
            elif spread > 0.05:
                return 0.3
            elif spread > 0.02:
                return 0.7
            else:
                return 1.0

        # Fallback: use raw liquidity dollar value
        liquidity = float(event.get("liquidity", 0))
        if liquidity <= 0:
            return 0.0
        elif liquidity < 100_000:
            return 0.2
        elif liquidity < 500_000:
            return 0.5
        elif liquidity < 1_000_000:
            return 0.8
        else:
            return 1.0
    except Exception:
        return 0.0


def find_matching_event_smart(
    signal_data: dict,
    polymarket_events: list[dict],
) -> tuple:
    """
    Smart event matching with 3 tiers: exact → fuzzy → category.

    Args:
        signal_data:        Sentiment signal dict (affected_cryptos or coin key).
        polymarket_events:  Full list from data_fetcher.
    Returns:
        (matched_event, confidence_score) tuple. Returns (None, 0) on no match.
    """
    affected = signal_data.get("affected_cryptos", [])
    coin_raw = signal_data.get("coin", "")
    if coin_raw and not affected:
        affected = [coin_raw]

    sentiment_dir = signal_data.get("sentiment", "neutral")
    raw_confidence = signal_data.get("confidence", 0)
    # Normalize confidence: 7-10 int → 0.7-1.0 float, or pass-through if already float
    if isinstance(raw_confidence, int) and raw_confidence > 1:
        signal_strength = raw_confidence / 10.0
    else:
        signal_strength = float(raw_confidence) if raw_confidence else 0.5

    if not affected or sentiment_dir == "neutral":
        return None, 0

    keywords = _expand_keywords(affected)

    # TIER 1: Exact symbol / word match with adequate liquidity
    for event in polymarket_events:
        if not event.get("active", True):
            continue
        title_lower = event.get("title", "").lower()
        exact = sum(1 for kw in keywords if re.search(r"\b" + re.escape(kw) + r"\b", title_lower))
        if exact >= 1:
            liq = check_liquidity(event)
            if liq > 0.3:
                res_mult = _resolution_window_multiplier(event)
                if res_mult == 0.0:
                    continue  # expired
                confidence = round(1.0 * liq * signal_strength * res_mult, 4)
                enriched = dict(event)
                enriched["market_category"] = _classify_event_market(event)
                enriched["resolution_multiplier"] = res_mult
                log.info("[SMART-MATCH T1] %s | market=%s | conf=%.2f | res_mult=%.2f",
                         event.get("title", "")[:60], enriched["market_category"], confidence, res_mult)
                return enriched, confidence

    # TIER 2: Partial / substring match
    for event in polymarket_events:
        if not event.get("active", True):
            continue
        title_lower = event.get("title", "").lower()
        partial = sum(1 for kw in keywords if kw in title_lower)
        if partial >= 1:
            liq = check_liquidity(event)
            if liq > 0.25:
                res_mult = _resolution_window_multiplier(event)
                if res_mult == 0.0:
                    continue  # expired
                confidence = round(0.8 * liq * signal_strength * res_mult, 4)
                enriched = dict(event)
                enriched["market_category"] = _classify_event_market(event)
                enriched["resolution_multiplier"] = res_mult
                log.info("[SMART-MATCH T2] %s | market=%s | conf=%.2f | res_mult=%.2f",
                         event.get("title", "")[:60], enriched["market_category"], confidence, res_mult)
                return enriched, confidence

    # TIER 3: Category match — only if signal very strong
    if signal_strength >= 0.75:
        crypto_keywords = ["crypto", "bitcoin", "ethereum", "blockchain", "digital asset"]
        for event in polymarket_events:
            if not event.get("active", True):
                continue
            title_lower = event.get("title", "").lower()
            if any(kw in title_lower for kw in crypto_keywords):
                liq = check_liquidity(event)
                if liq > 0.2:
                    confidence = 0.6 * liq * signal_strength
                    enriched = dict(event)
                    enriched["market_category"] = _classify_event_market(event)
                    log.info("[SMART-MATCH T3] %s | market=%s | conf=%.2f",
                             event.get("title", "")[:60], enriched["market_category"], confidence)
                    return enriched, round(confidence, 4)

    return None, 0


def pick_trading_direction(sentiment: str, event_markets: list[dict]) -> str:
    """
    Decide whether to bet YES or NO based on sentiment direction.

    Convention:
        markets[0] → YES outcome
        markets[1] → NO outcome

    Args:
        sentiment:     "bullish" | "bearish" | "neutral"
        event_markets: List of market dicts from the event.
    Returns:
        "YES", "NO", or "SKIP".
    """
    sentiment = (sentiment or "neutral").lower()

    if sentiment == "neutral":
        return "SKIP"

    if not event_markets:
        log.warning("Event has no markets — cannot pick direction")
        return "SKIP"

    # Identify YES/NO markets by label or position
    yes_market = None
    no_market = None
    for mkt in event_markets:
        label = str(mkt.get("outcomeLabel", mkt.get("outcome", ""))).upper()
        if "YES" in label or label == "0":
            yes_market = mkt
        elif "NO" in label or label == "1":
            no_market = mkt

    # Fallback: first market = YES, second = NO
    if yes_market is None and len(event_markets) >= 1:
        yes_market = event_markets[0]
    if no_market is None and len(event_markets) >= 2:
        no_market = event_markets[1]

    if sentiment == "bullish":
        direction = "YES" if yes_market else "NO"
    else:  # bearish
        direction = "NO" if no_market else "YES"

    log.info("Trading direction: %s (sentiment=%s)", direction, sentiment)
    return direction

# ---------------------------------------------------------------------------
# Type-aware Polymarket matcher
# ---------------------------------------------------------------------------

class PolymarketMatcher:
    """
    Routes signals to market-type-specific matching logic.
    UP_DOWN   — full confidence (clearest binary outcome)
    HIT_PRICE — 50% confidence (specific price target, hard to call)
    PRICE_RANGE — 70% confidence (range outcome, medium difficulty)
    """

    TYPE_MULTIPLIERS = {
        "UP_DOWN":     1.00,
        "HIT_PRICE":   0.50,
        "PRICE_RANGE": 0.70,
        "OTHER":        0.80,
    }

    def __init__(self):
        pass

    def match_signal(self, signal: dict, events: list[dict]) -> list[tuple]:
        """
        Return list of (event, confidence_score) tuples with score > 0.4.
        """
        sentiment = signal.get("sentiment", "neutral").upper()
        if sentiment == "NEUTRAL":
            return []

        raw_confidence = float(signal.get("confidence", 0) or 0)
        if raw_confidence > 1:
            raw_confidence /= 10.0

        affected = signal.get("affected_cryptos", [])
        keywords = _expand_keywords(affected) if affected else []

        results: list[tuple] = []

        for event in events:
            if not event.get("active", True):
                continue

            title_lower = event.get("title", "").lower()

            # Require at least a partial keyword match
            kw_hit = any(kw in title_lower for kw in keywords) if keywords else True
            if not kw_hit:
                continue

            market_type = event.get("market_type", "OTHER")
            multiplier = self.TYPE_MULTIPLIERS.get(market_type, 0.80)
            score = round(raw_confidence * multiplier, 4)

            if score >= 0.4:
                results.append((event, score))
                log.debug(
                    "[POLY-TYPE-MATCH] Type=%-12s score=%.2f | %s",
                    market_type, score, event.get("title", "")[:60],
                )

        if results:
            log.info(
                "[POLY-TYPE-MATCH] Signal=%s | %d matches from %d events",
                sentiment, len(results), len(events),
            )
        else:
            log.info(
                "[POLY-TYPE-MATCH] Signal=%s | 0 matches (keywords=%s, events=%d)",
                sentiment, affected, len(events),
            )

        # Sort by score descending, cap at 10
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:10]

