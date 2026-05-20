"""
near_resolution_scanner.py - Near-Certainty Market Scanner

Scans Kalshi and Polymarket for markets resolving within 1-6 hours
where the current price is at an extreme (>75% or <25%). These markets
have near-predetermined outcomes and should yield 85-95%+ win rates.

Strategy:
  - price > 0.80 and hours < 2  → YES trade, confidence 0.92
  - price > 0.75 and hours < 4  → YES trade, confidence 0.85
  - price < 0.20 and hours < 2  → NO trade,  confidence 0.90
  - price < 0.25 and hours < 4  → NO trade,  confidence 0.84

Position sizing bypasses the expiry_multiplier penalty (which normally
reduces size for near-expiry markets). NR trades WANT to be near expiry.
"""

import logging
import time
from datetime import datetime, timezone
from typing import List, Optional

def _is_off_peak() -> bool:
    """Off-peak: UTC hours where HFT competition is thinner (2–6 UTC, 10–14 UTC)."""
    h = datetime.now(timezone.utc).hour
    return h in (2, 3, 4, 5, 10, 11, 12, 13)

import requests

log = logging.getLogger("zisi.nr_scanner")

# ── Configuration ─────────────────────────────────────────────────────────────
_NR_MAX_HOURS = 6.0         # scan markets resolving within this window
_NR_MIN_HOURS = 0.25        # skip if resolving in < 15 min (no time to exit)
_NR_PRICE_HIGH = 0.75       # price above this → YES near-certainty
_NR_PRICE_LOW  = 0.25       # price below this → NO near-certainty
_NR_STRONG_HIGH = 0.82      # strong conviction threshold (YES)
_NR_STRONG_LOW  = 0.18      # strong conviction threshold (NO)

# Max NR trades per cycle to avoid flooding positions
_NR_MAX_PER_CYCLE = 6

# Cache for Polymarket NR scan (5 min TTL — refreshes quickly enough)
_poly_cache: List[dict] = []
_poly_cache_time: float = 0
_POLY_CACHE_TTL = 300  # seconds

# Track tickers traded this session to avoid duplicates
_nr_traded_this_session: set = set()


def _safe_get(url: str, params: dict = None, timeout: int = 10) -> Optional[dict]:
    try:
        resp = requests.get(url, params=params, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        log.debug("[NR-SCANNER] GET %s failed: %s", url, exc)
        return None


def _parse_hours_remaining(market: dict) -> Optional[float]:
    close_str = (
        market.get("close_time")
        or market.get("expiration_time")
        or market.get("expected_expiration_time")
        or market.get("endDate")
        or market.get("endDateIso")
    )
    if not close_str:
        return None
    try:
        close_dt = datetime.fromisoformat(str(close_str).replace("Z", "+00:00"))
        hours = (close_dt - datetime.now(timezone.utc)).total_seconds() / 3600
        return hours
    except Exception:
        return None


def compute_nr_confidence(price: float, hours: float) -> float:
    """Return 0.0 if not a NR candidate, else 0.78-0.93 confidence."""
    if hours < _NR_MIN_HOURS or hours > _NR_MAX_HOURS:
        return 0.0

    # During off-peak hours HFT competition is thinner — be more aggressive
    # Standard thresholds: HIGH=0.75, LOW=0.25 | Off-peak: HIGH=0.65, LOW=0.35
    eff_high = 0.65 if _is_off_peak() else _NR_PRICE_HIGH
    eff_low  = 0.35 if _is_off_peak() else _NR_PRICE_LOW
    eff_strong_high = 0.78 if _is_off_peak() else _NR_STRONG_HIGH
    eff_strong_low  = 0.22 if _is_off_peak() else _NR_STRONG_LOW

    # YES direction (high prices)
    if price >= eff_strong_high:
        if hours < 1:
            return 0.93
        if hours < 2:
            return 0.90
        if hours < 4:
            return 0.87
        return 0.84

    if price >= eff_high:
        if hours < 2:
            return 0.87
        if hours < 4:
            return 0.85
        return 0.84

    # Approaching-certainty tier: 0.65–0.75 YES / 0.25–0.35 NO with < 1h remaining
    if 0.65 <= price < 0.75 and hours < 1:
        return 0.78
    if 0.25 < price <= 0.35 and hours < 1:
        return 0.78

    # NO direction (low prices)
    if price <= eff_strong_low:
        if hours < 1:
            return 0.93
        if hours < 2:
            return 0.90
        if hours < 4:
            return 0.87
        return 0.84

    if price <= eff_low:
        if hours < 2:
            return 0.87
        if hours < 4:
            return 0.85
        return 0.84

    return 0.0  # price in range → not NR candidate


def _compute_nr_size(account_balance: float, nr_confidence: float, hours: float) -> float:
    """
    Compute position size for NR trades, bypassing expiry_multiplier penalty.
    Higher confidence + shorter time window → larger position.
    Approaching-certainty tier (conf == 0.78) uses 3% — smaller due to lower certainty.
    """
    if nr_confidence == 0.78:
        return round(max(0.50, min(account_balance * 0.03, 5.0)), 2)

    if hours < 1:
        pct = 0.08  # 8% for imminent resolution
    elif hours < 2:
        pct = 0.06
    elif hours < 4:
        pct = 0.05
    else:
        pct = 0.04

    # Scale by confidence
    pct *= nr_confidence  # e.g. 0.90 confidence → 90% of pct
    return round(max(0.50, min(account_balance * pct, 10.0)), 2)


class NearResolutionScanner:
    """Scans prediction markets for near-certainty resolution opportunities."""

    def __init__(self):
        self._cycle_count = 0

    def scan_kalshi_nr(self, auth) -> List[dict]:
        """
        Fetch Kalshi open markets and filter for NR candidates.
        Returns list of market dicts enriched with nr_confidence and hours_remaining.
        """
        if auth is None or not getattr(auth, "is_configured", False):
            return []

        # Use a lightweight endpoint — fetch markets directly
        base_url = getattr(auth, "base_url", "https://api.elections.kalshi.com/trade-api/v2")
        path = "/markets?status=open&limit=200"
        try:
            resp = requests.get(
                f"{base_url}{path}",
                headers=auth.get_headers("GET", path),
                timeout=12,
            )
            if resp.status_code != 200:
                log.debug("[NR-KALSHI] HTTP %d — skipping", resp.status_code)
                return []
            markets = resp.json().get("markets", [])
        except Exception as exc:
            log.debug("[NR-KALSHI] Fetch failed: %s", exc)
            return []

        candidates = []
        for m in markets:
            ticker = m.get("ticker") or m.get("market_ticker", "")
            title  = m.get("title") or m.get("subtitle") or ticker

            if ticker in _nr_traded_this_session:
                continue

            hours = _parse_hours_remaining(m)
            if hours is None or not (_NR_MIN_HOURS <= hours <= _NR_MAX_HOURS):
                continue

            # Parse price (Kalshi returns 0-100 or 0-1 scale)
            raw_price = m.get("yes_ask") or m.get("yes_bid") or m.get("last_price") or 0
            price = float(raw_price) / 100.0 if float(raw_price) > 1 else float(raw_price)
            if price <= 0:
                continue

            nr_conf = compute_nr_confidence(price, hours)
            if nr_conf == 0.0:
                continue

            direction = "YES" if price > 0.5 else "NO"
            candidates.append({
                "platform":       "KALSHI",
                "ticker":         ticker,
                "title":          title,
                "hours_remaining": round(hours, 2),
                "entry_price":    round(price, 4),
                "nr_confidence":  round(nr_conf, 4),
                "direction":      direction,
                "_raw_market":    m,
            })

        # Sort by confidence desc, then by hours asc (closest to resolution first)
        candidates.sort(key=lambda c: (-c["nr_confidence"], c["hours_remaining"]))
        log.info("[NR-KALSHI] %d candidates from %d markets", len(candidates), len(markets))
        return candidates

    def scan_polymarket_nr(self) -> List[dict]:
        """
        Fetch Polymarket open markets and filter for NR candidates.
        Cached for 5 minutes to avoid hammering the API every cycle.
        """
        global _poly_cache, _poly_cache_time
        now_ts = time.time()
        if _poly_cache and (now_ts - _poly_cache_time) < _POLY_CACHE_TTL:
            markets = _poly_cache
        else:
            data = _safe_get(
                "https://gamma-api.polymarket.com/markets",
                params={"active": "true", "closed": "false", "limit": 200},
            )
            if not data:
                return []
            markets = data if isinstance(data, list) else data.get("markets", [])
            _poly_cache = markets
            _poly_cache_time = now_ts

        candidates = []
        for m in markets:
            event_id = m.get("conditionId") or m.get("id", "")
            title = m.get("question") or m.get("title") or event_id[:30]

            if event_id in _nr_traded_this_session:
                continue

            hours = _parse_hours_remaining(m)
            if hours is None or not (_NR_MIN_HOURS <= hours <= _NR_MAX_HOURS):
                continue

            price = float(m.get("price") or m.get("lastTradePrice") or 0)
            if price <= 0:
                continue

            # Minimum liquidity filter: skip thin markets with high slippage
            _liquidity = float(m.get("liquidity") or m.get("volume") or m.get("volume24hr") or 0)
            if _liquidity < 200:
                continue

            nr_conf = compute_nr_confidence(price, hours)
            if nr_conf == 0.0:
                continue

            # Direction: >0.5 → YES, ≤0.5 → NO (handles approaching-certainty 0.65–0.74 correctly)
            direction = "YES" if price > 0.5 else "NO"
            # For NO direction, actual entry price is (1 - price)
            entry_price = price if direction == "YES" else round(1.0 - price, 4)

            candidates.append({
                "platform":       "POLYMARKET",
                "event_id":       event_id,
                "market_id":      m.get("conditionId") or event_id,
                "title":          title,
                "hours_remaining": round(hours, 2),
                "entry_price":    entry_price,
                "raw_price":      price,
                "nr_confidence":  round(nr_conf, 4),
                "direction":      direction,
                "_raw_market":    m,
            })

        candidates.sort(key=lambda c: (-c["nr_confidence"], c["hours_remaining"]))
        log.info("[NR-POLY] %d candidates from %d markets", len(candidates), len(markets))
        return candidates

    def get_nr_trades(self, auth, account_balance: float) -> List[dict]:
        """
        Public API: returns all NR trade candidates across Kalshi + Polymarket,
        enriched with computed position size. Capped at _NR_MAX_PER_CYCLE.
        """
        self._cycle_count += 1
        all_candidates = []

        try:
            all_candidates += self.scan_kalshi_nr(auth)
        except Exception as exc:
            log.warning("[NR-SCANNER] Kalshi scan error: %s", exc)

        try:
            all_candidates += self.scan_polymarket_nr()
        except Exception as exc:
            log.warning("[NR-SCANNER] Polymarket scan error: %s", exc)

        # De-sort by confidence desc
        all_candidates.sort(key=lambda c: (-c["nr_confidence"], c["hours_remaining"]))

        result = []
        for cand in all_candidates[:_NR_MAX_PER_CYCLE]:
            cand["position_size"] = _compute_nr_size(
                account_balance, cand["nr_confidence"], cand["hours_remaining"]
            )
            result.append(cand)

        if result:
            log.info(
                "[NR-SCANNER] Cycle %d: %d NR trade(s) ready | best conf=%.2f (%s, %.1fh)",
                self._cycle_count, len(result),
                result[0]["nr_confidence"], result[0]["platform"], result[0]["hours_remaining"],
            )
        else:
            log.debug("[NR-SCANNER] Cycle %d: no NR candidates this cycle", self._cycle_count)

        return result

    def mark_traded(self, ticker_or_id: str) -> None:
        """Record that we've traded this market this session (avoids re-entry)."""
        _nr_traded_this_session.add(ticker_or_id)
