"""
Kalshi event matcher.
Maps crypto sentiment signals to macro Kalshi events using keyword overlap.
Independent from Polymarket matcher.
"""
import json
import logging
import re
import requests
from pathlib import Path
from typing import List, Dict, Optional

_SUSPENSIONS_FILE = Path(__file__).parent.parent / "category_suspensions.json"


def _load_suspended_categories() -> set:
    """Read the suspension list written by health_monitor.strategy_drift_check()."""
    try:
        if _SUSPENSIONS_FILE.exists():
            data = json.loads(_SUSPENSIONS_FILE.read_text(encoding="utf-8"))
            return set(data.get("suspended", []))
    except Exception:
        pass
    return set()

log = logging.getLogger("zisi.kalshi.matcher")

# ---------------------------------------------------------------------------
# Keyword gates for crypto signals
# ---------------------------------------------------------------------------

# If ANY of these appear in a Kalshi event title, the event is a sports/entertainment
# market — never eligible for crypto macro signals.
_SPORTS_BLOCKLIST: frozenset = frozenset({
    "ufc", "mma", "fight", "fighter", "bout", "knockout", "tko", "submission",
    "wrestling", "nba", "nfl", "mlb", "nhl", "soccer", "basketball",
    "football", "baseball", "hockey", "tennis", "golf", "boxing",
    "super bowl", "world cup", "championship", "playoff", "tournament",
    "oscar", "grammy", "emmy", "celebrity", "movie", "album", "concert",
    "vs.", "vs ", "parlay",
    # Player prop / stat patterns
    "home run", "strikeout", "batting", "pitcher", "inning", "rbi", "rbis",
    "total hits", "player prop", "assists", "rebounds", "total points",
    "goals", "saves", "shutout", "rushing", "receiving", "passing yards",
    # Kalshi player performance market patterns (e.g. "PlayerName: 1+")
    ": 1+", ": 2+", ": 3+", ": 0+",
})

# Kalshi macro event titles must contain at least ONE of these for a crypto
# signal to match (prevents spurious matches on off-topic markets).
# DELIBERATELY narrow — only clear financial/macro/crypto terms allowed.
# Removed: "market" (too broad — matches "prediction market", "fantasy market"),
#           "trade" (sports use "trade" for player trades),
#           "tech", "technology", "innovation", "adoption" (too generic),
#           "war" (too generic), "stock" (alone too generic),
#           "oil", "energy" (too short/generic)
_MACRO_WHITELIST: frozenset = frozenset({
    "economic", "economy", "gdp", "inflation", "interest rate",
    "fed ", "federal reserve", "federal funds", "funds rate",
    "rate cut", "rate hike", "rate above", "rate below", "rate stay",
    "recession", "unemployment", "cpi", "fomc", "pce",
    "nasdaq", "dow jones", "s&p 500", "s&p", "bond yield", "treasury",
    "crypto", "bitcoin", "ethereum", "btc", "eth", "blockchain", "defi",
    "regulation", "sec ", "congress", "senate", "election", "president",
    "geopoliti", "tariff", "sanction", "trade war", "federal",
    # Price-range markets: "Bitcoin price range on May 20, $103k-$104k?"
    "price range", "bitcoin price", "ethereum price", "btc price", "eth price",
})

# Final hard-gate: a matched Kalshi event title MUST contain one of these
# explicit finance/crypto terms before any trade is placed.
# This is the last line of defence against false-positive keyword matches.
_EXPLICIT_FINANCE_TERMS: frozenset = frozenset({
    "bitcoin", "btc", "ethereum", "eth", "crypto", "blockchain", "defi",
    "inflation", "gdp", "fed", "fomc", "cpi", "interest rate",
    "recession", "nasdaq", "s&p", "dow jones", "treasury", "bond yield",
    "rate cut", "rate hike", "rate above", "rate below",
    "economy", "economic", "tariff", "sanction",
    "federal reserve", "federal funds", "funds rate",
    "unemployment", "pce", "federal",
    # Price-range market titles pass the finance gate
    "price range", "bitcoin price", "ethereum price",
})


def _is_macro_eligible(event: Dict) -> bool:
    """
    Return True when an event is eligible for crypto macro signal matching.

    Fast-path: if the fetcher already classified this event as CRYPTO or
    FINANCE (via _category), trust that and skip the title scan — the
    category detector uses the same title text and word-boundary regexes,
    so re-running here would always agree.

    Full-path: for unlabelled events, scan the combined title+subtitle for
    sports/entertainment blocklist terms (reject) and macro whitelist
    (require at least one match).
    """
    # ── Fast-path: trust the fetcher's category label ────────────────────────
    category = event.get("_category", "")
    if category in ("CRYPTO", "FINANCE"):
        return True
    if category == "SPORTS":
        return False  # Definite sports market

    # ── Full-path: title-based scan for unlabelled events ────────────────────
    title = (event.get("title", "") + " " + event.get("subtitle", "")).lower()
    if not title.strip():
        return False

    # Hard block: sports/entertainment keywords
    for term in _SPORTS_BLOCKLIST:
        if term in title:
            return False

    # Require at least one macro keyword
    for term in _MACRO_WHITELIST:
        if term in title:
            return True

    # No macro keyword found → not eligible
    return False


# Crypto sentiment → macro keyword implications.
# Each phrase is matched as ALL-words-must-appear in the Kalshi event title.
# Use realistic vocabulary that appears in actual Kalshi market titles.
# Single-word implications are fine (score = 1.0 if word appears).
CRYPTO_TO_MACRO = {
    # Bitcoin / BTC — price-range markets listed first so they match before FOMC terms
    "BTC_BULLISH":    [
        "bitcoin price", "btc price", "price range",
        "fomc", "federal funds", "funds rate", "rate cut", "rate cuts",
        "inflation", "cpi", "unemployment", "nasdaq", "economic",
        "gdp", "recession", "tariff", "federal reserve",
        "bitcoin", "btc",
    ],
    "BTC_BEARISH":    [
        "bitcoin price", "btc price", "price range",
        "fomc", "federal funds", "funds rate", "rate hike", "rate above",
        "inflation", "cpi", "unemployment", "recession", "tariff",
        "gdp", "federal reserve", "economic",
        "bitcoin", "btc",
    ],
    "BTC_NEUTRAL":    [
        "bitcoin price", "btc price", "price range",
        "fomc", "federal funds", "funds rate", "rate above", "rate below",
        "inflation", "cpi", "economic", "gdp", "federal reserve",
        "bitcoin", "btc",
    ],
    # Ethereum / ETH
    "ETH_BULLISH":    [
        "ethereum price", "eth price", "price range",
        "fomc", "federal funds", "funds rate", "rate cut",
        "defi", "inflation", "cpi", "nasdaq", "economic", "gdp",
        "ethereum", "eth",
    ],
    "ETH_BEARISH":    [
        "ethereum price", "eth price", "price range",
        "fomc", "federal funds", "funds rate", "rate hike", "rate above",
        "inflation", "cpi", "recession", "regulation", "sec",
        "ethereum", "eth",
    ],
    "ETH_NEUTRAL":    [
        "ethereum price", "eth price", "price range",
        "fomc", "federal funds", "funds rate", "rate above", "rate below",
        "inflation", "cpi", "economic", "gdp", "federal reserve",
        "ethereum", "eth",
    ],
    # Generic crypto signals
    "CRYPTO_BULLISH": [
        "price range", "bitcoin price", "ethereum price",
        "fomc", "federal funds", "funds rate", "rate cut",
        "inflation", "cpi", "nasdaq", "economic", "gdp",
        "crypto", "bitcoin", "ethereum",
    ],
    "CRYPTO_BEARISH": [
        "price range", "bitcoin price", "ethereum price",
        "fomc", "federal funds", "funds rate", "rate hike", "rate above",
        "inflation", "cpi", "recession", "unemployment",
        "crypto", "bitcoin", "ethereum",
    ],
    "CRYPTO_NEUTRAL": [
        "price range", "bitcoin price", "ethereum price",
        "fomc", "federal funds", "funds rate", "rate above", "rate below",
        "inflation", "cpi", "economic", "gdp",
        "crypto", "bitcoin", "ethereum",
    ],
    # SOL, DOGE, XRP — use same macro correlation as CRYPTO
    "SOL_BULLISH":    ["price range", "fomc", "federal funds", "funds rate", "crypto", "bitcoin", "inflation", "cpi", "nasdaq"],
    "SOL_BEARISH":    ["price range", "fomc", "federal funds", "funds rate", "crypto", "recession", "inflation", "cpi"],
    "SOL_NEUTRAL":    ["price range", "fomc", "federal funds", "funds rate", "crypto", "bitcoin", "inflation", "cpi"],
    "DOGE_BULLISH":   ["fomc", "crypto", "bitcoin", "inflation", "nasdaq"],
    "DOGE_BEARISH":   ["fomc", "crypto", "recession", "inflation", "cpi"],
    "DOGE_NEUTRAL":   ["fomc", "crypto", "bitcoin", "inflation"],
    "XRP_BULLISH":    ["fomc", "crypto", "regulation", "bitcoin", "inflation"],
    "XRP_BEARISH":    ["fomc", "crypto", "regulation", "recession", "inflation"],
    "XRP_NEUTRAL":    ["fomc", "crypto", "regulation", "bitcoin", "inflation"],
    "OTHER_BULLISH":  ["price range", "fomc", "federal funds", "funds rate", "rate cut", "inflation", "cpi", "nasdaq", "economic"],
    "OTHER_BEARISH":  ["price range", "fomc", "federal funds", "funds rate", "rate hike", "rate above", "inflation", "recession"],
    "OTHER_NEUTRAL":  ["price range", "fomc", "federal funds", "funds rate", "inflation", "cpi", "economic", "gdp"],
}


class KalshiEventMatcher:
    def __init__(self):
        pass

    def match_signal_to_events(
        self,
        signal: Dict,
        kalshi_events: List[Dict],
        confidence_threshold: float = 0.45,
    ) -> List[Dict]:
        """
        Match a crypto sentiment signal to open Kalshi markets.

        Returns list of dicts: {event, confidence, matched_implication, market: 'KALSHI'}
        """
        if not kalshi_events:
            return []

        sentiment = signal.get("sentiment", "neutral").upper()
        asset = "BTC"
        for crypto in ["BTC", "ETH", "SOL", "DOGE", "XRP"]:
            if crypto in str(signal.get("affected_cryptos", [])).upper() or \
               crypto in str(signal.get("headline", "")).upper():
                asset = crypto
                break

        # Fallback: check coin field
        coin_raw = str(signal.get("coin", "")).upper()
        if "BITCOIN" in coin_raw:
            asset = "BTC"
        elif "ETHEREUM" in coin_raw:
            asset = "ETH"

        signal_key = f"{asset}_{sentiment}"
        implications = CRYPTO_TO_MACRO.get(signal_key) or CRYPTO_TO_MACRO.get(f"CRYPTO_{sentiment}", [])

        if not implications:
            return []

        signal_confidence = float(signal.get("confidence", 0) or signal.get("sentiment_score", 0.5))
        if signal_confidence > 1:
            signal_confidence /= 10.0  # normalize from 10-scale to 0-1

        matches: List[Dict] = []

        for event in kalshi_events:
            title = (event.get("title", "") + " " + event.get("subtitle", "")).lower()
            if not title.strip():
                continue

            best_score = 0.0
            best_impl = None

            for impl in implications:
                impl_lower = impl.lower()
                # Treat each implication as an independent term:
                # - Single word: match if it appears in the title
                # - Multi-word phrase: match if ENTIRE phrase appears as substring
                if impl_lower in title:
                    score = 1.0
                    if score > best_score:
                        best_score = score
                        best_impl = impl

            if best_score < 1.0:
                continue  # no implication matched — skip

            # Final hard gate: matched event title must contain an explicit
            # financial/crypto term.  Guards against any remaining edge-cases
            # where a sports/entertainment event slips through all earlier
            # filters but happens to share vocabulary with an implication.
            title_has_finance = any(ft in title for ft in _EXPLICIT_FINANCE_TERMS)
            if not title_has_finance:
                log.debug(
                    "[KALSHI-GATE] Rejected '%s' — no explicit finance term despite implication match '%s'",
                    event.get("title", "")[:60],
                    best_impl,
                )
                continue

            trade_conf = min(signal_confidence * best_score, 1.0)
            if trade_conf >= confidence_threshold:
                matches.append({
                    "event": event,
                    "confidence": round(trade_conf, 4),
                    "matched_implication": best_impl,
                    "market": "KALSHI",
                })

        if matches:
            log.info(
                "[KALSHI-MATCH] %s (conf %.2f) → %d Kalshi event(s)",
                signal_key, signal_confidence, len(matches),
            )

        # ── Diversity filter: max 1 match per event-title-prefix ─────────────
        # Kalshi often has 50-200 markets for the same theme (e.g. Bitcoin price
        # range at different thresholds for the same date).  Without this filter
        # all 3 capped results would be from the same expired/zero-price batch,
        # leaving no room for a different (valid) event family.
        # We use the first 8 words of the title as a "family key" so that
        # same-date price-range brackets ("$103k-$104k?" vs "$104k-$105k?")
        # count as distinct families rather than collapsing all 100 to 1.
        diverse: List[Dict] = []
        seen_families: set = set()
        for m in matches:
            raw_title = m["event"].get("title", "").lower().strip()
            family = " ".join(raw_title.split()[:8])  # "bitcoin price range on may 20, $103k-$104k?"
            if family not in seen_families:
                seen_families.add(family)
                diverse.append(m)
            if len(diverse) >= 8:
                break

        return diverse

    def _get_current_price(self, coin: str) -> Optional[float]:
        """Fetch current price for BTC or ETH from CoinGecko."""
        _ids = {"BTC": "bitcoin", "ETH": "ethereum"}
        cg_id = _ids.get(coin.upper())
        if not cg_id:
            return None
        try:
            r = requests.get(
                f"https://api.coingecko.com/api/v3/simple/price?ids={cg_id}&vs_currencies=usd",
                timeout=5,
            )
            data = r.json()
            return float(data.get(cg_id, {}).get("usd", 0)) or None
        except Exception:
            return None

    def match_price_range_markets(
        self,
        signal: Dict,
        kalshi_events: List[Dict],
        confidence_threshold: float = 0.45,
    ) -> List[Dict]:
        """
        Directional bracket picker for Kalshi price-range markets.
        'Bitcoin price range on May 18, $95k-$96k?' — 400+ of these exist.
        BEARISH signal → pick brackets BELOW current price (more likely to land there).
        BULLISH signal → pick brackets ABOVE current price.
        NEUTRAL signal → pick brackets closest to current price (±5%).
        Returns top 3 most relevant brackets as match dicts.
        """
        sentiment = signal.get("sentiment", "neutral").upper()
        if sentiment not in ("BULLISH", "BEARISH", "NEUTRAL"):
            return []

        # Detect which coin from signal
        coin = "BTC"
        for crypto in ["BTC", "ETH"]:
            if crypto in str(signal.get("affected_cryptos", [])).upper() or \
               crypto in str(signal.get("headline", "")).upper() or \
               crypto in str(signal.get("coin", "")).upper():
                coin = crypto
                break

        coin_lower = coin.lower()
        coin_name = "bitcoin" if coin == "BTC" else "ethereum"

        # Filter to price-range markets for this coin
        range_events = [
            ev for ev in kalshi_events
            if "price range" in ev.get("title", "").lower()
            and (coin_lower in ev.get("title", "").lower()
                 or coin_name in ev.get("title", "").lower())
        ]

        if not range_events:
            return []

        current_price = self._get_current_price(coin)
        if not current_price:
            # No price data — return top 3 by title order
            log.debug("[PRICE-RANGE] No current price for %s — using unfiltered", coin)
            return [
                {"event": ev, "confidence": 0.55, "matched_implication": "price_range_unfiltered", "market": "KALSHI"}
                for ev in range_events[:3]
            ]

        # Parse "$95k-$96k" or "$95,000-$96,000" style ranges from title
        def _parse_range(title: str):
            # Match patterns like "$95k", "$95,000", "95000", "95k"
            nums = re.findall(r'\$?([\d,]+)k?', title.lower())
            values = []
            for n in nums:
                try:
                    v = float(n.replace(",", ""))
                    if "k" in title[title.lower().find(n): title.lower().find(n) + len(n) + 1].lower():
                        v *= 1000
                    values.append(v)
                except Exception:
                    pass
            return (min(values), max(values)) if len(values) >= 2 else (None, None)

        # Score each bracket: prefer ones whose midpoint is in direction of signal
        scored = []
        for ev in range_events:
            lo, hi = _parse_range(ev.get("title", ""))
            if lo is None:
                continue
            midpoint = (lo + hi) / 2.0
            gap_pct = (midpoint - current_price) / current_price  # positive = above current price

            if sentiment == "BULLISH":
                # Prefer brackets above current price — positive gap_pct is better
                score = min(max(gap_pct * 10 + 0.55, 0.50), 0.85)
            elif sentiment == "BEARISH":
                # Prefer brackets below current price — negative gap_pct is better
                score = min(max(-gap_pct * 10 + 0.55, 0.50), 0.85)
            else:
                # NEUTRAL: prefer brackets closest to current price (within ±5%)
                abs_gap = abs(gap_pct)
                if abs_gap > 0.05:
                    continue  # skip brackets more than 5% away
                score = round(0.55 + (0.05 - abs_gap) * 5, 4)
                score = min(score, 0.75)

            if score >= confidence_threshold:
                scored.append((score, ev))

        scored.sort(key=lambda x: -x[0])
        top3 = scored[:3]

        if top3:
            log.info(
                "[PRICE-RANGE] %s %s: current=$%.0f → %d directional brackets selected",
                coin, sentiment, current_price, len(top3),
            )

        return [
            {"event": ev, "confidence": round(score, 4), "matched_implication": "price_range_directional", "market": "KALSHI"}
            for score, ev in top3
        ]

    def match_with_category_filter(
        self,
        signal: Dict,
        kalshi_events: List[Dict],
        confidence_threshold: float = 0.45,
    ) -> List[Dict]:
        """
        Category-aware matching: skips SPORTS markets for crypto signals and
        logs why each market was accepted or skipped.  Falls through to the
        standard keyword matcher for accepted categories.
        """
        sentiment = signal.get("sentiment", "neutral").upper()
        affected = [c.lower() for c in signal.get("affected_cryptos", [])]
        is_crypto_signal = bool(affected) or sentiment in ("BULLISH", "BEARISH")

        filtered_events: List[Dict] = []
        skipped_by_category = 0

        # Load drift-suspended categories once per call (file read is fast; avoids
        # stale in-memory state if the health monitor updates the file mid-cycle).
        _suspended = _load_suspended_categories()

        for event in kalshi_events:
            cat = event.get("_category", "OTHER")

            # Layer 0: drift enforcement gate — category suspended by health monitor
            if cat in _suspended:
                log.warning(
                    "[DRIFT-GATE] %s suspended (WR<30%%) — skipping: %s",
                    cat, event.get("title", "")[:55],
                )
                skipped_by_category += 1
                continue

            # Layer 1: skip labelled SPORTS markets
            if is_crypto_signal and cat == "SPORTS":
                skipped_by_category += 1
                continue

            # Layer 2: macro-eligibility gate (title-based, catches unlabelled
            # sports/entertainment markets that arrive with category=OTHER)
            if is_crypto_signal and not _is_macro_eligible(event):
                skipped_by_category += 1
                log.debug(
                    "[KALSHI-GATE] Blocked non-macro event: '%s'",
                    event.get("title", "")[:60],
                )
                continue

            filtered_events.append(event)

        log.info(
            "[KALSHI-CAT-FILTER] Signal=%s | Blocked=%d | Remaining=%d/%d",
            sentiment, skipped_by_category, len(filtered_events), len(kalshi_events),
        )

        matches = self.match_signal_to_events(signal, filtered_events, confidence_threshold)

        # Price-range directional bonus: add directional bracket picks on top of keyword matches
        price_range_matches = self.match_price_range_markets(signal, kalshi_events, confidence_threshold)
        if price_range_matches:
            # Merge without duplicating the same event id
            existing_ids = {m["event"].get("id") for m in matches}
            for pm in price_range_matches:
                if pm["event"].get("id") not in existing_ids:
                    matches.append(pm)

        log.info(
            "[KALSHI-CAT-MATCH] Signal=%s | Matches=%d from %d filtered events",
            sentiment, len(matches), len(filtered_events),
        )
        return matches

