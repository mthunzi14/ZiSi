"""
Kalshi event fetcher.
Fetches macro events (politics, economics, sports, financials) from Kalshi REST API v2.
Independent from Polymarket fetcher — safe to fail without affecting Polymarket.
"""
import json
import logging
import re
import requests
import time
from pathlib import Path
from typing import List, Dict, Optional

# ── API rate limit tracking ───────────────────────────────────────────────────
_KALSHI_HOURLY_LIMIT = 1_000
_api_calls_this_hour: int = 0
_api_hour_start: float = time.time()


def _track_api_call() -> int:
    """Increment hourly API call counter, reset on new hour. Warn at 80% capacity."""
    global _api_calls_this_hour, _api_hour_start
    now = time.time()
    if now - _api_hour_start >= 3600:
        _api_calls_this_hour = 0
        _api_hour_start = now
    _api_calls_this_hour += 1
    threshold = int(_KALSHI_HOURLY_LIMIT * 0.80)
    if _api_calls_this_hour >= threshold:
        log.warning(
            "[API-RATE] %d/%d Kalshi calls used this hour (%.0f%%)",
            _api_calls_this_hour, _KALSHI_HOURLY_LIMIT,
            _api_calls_this_hour / _KALSHI_HOURLY_LIMIT * 100,
        )
    return _api_calls_this_hour

_CATEGORY_WIN_RATES_FILE = Path(__file__).parent.parent / "category_win_rates.json"

log = logging.getLogger("zisi.kalshi.fetcher")


# 12-category taxonomy covering all Kalshi event types.
# SHORT terms (≤4 chars) use WORD-BOUNDARY matching to avoid false positives.
# Longer terms use plain substring matching.
_CATEGORY_KEYWORDS: dict = {
    # 1. Crypto price movement
    "CRYPTO":      ("bitcoin", "ethereum", "crypto", "blockchain", "coinbase",
                    "defi", "nft", "solana", "ripple", "dogecoin", "altcoin"),
    # 2. Political outcomes
    "POLITICS":    ("election", "vote", "trump", "biden", "congress", "senate",
                    "president", "political", "campaign", "democrat", "republican",
                    "ballot", "polling", "approval rating", "impeach"),
    # 3. Economic data releases
    "ECONOMICS":   ("inflation", "unemployment", "interest rate", "fomc",
                    "federal reserve", "treasury", "recession", "jobs report",
                    "payroll", "consumer price", "producer price", "housing"),
    # 4. Climate / weather events
    "CLIMATE":     ("hurricane", "tornado", "earthquake", "flood", "wildfire",
                    "drought", "storm", "blizzard", "climate", "weather", "temperature",
                    "el nino", "la nina", "cyclone"),
    # 5. Tech / AI developments
    "TECH":        ("artificial intelligence", "chatgpt", "openai", "apple", "google",
                    "microsoft", "meta ", "amazon", "nvidia", "semiconductor",
                    "self-driving", "autonomous", "chip", "ipo", "startup"),
    # 6. Sports outcomes
    "SPORTS":      ("nfl", "nba", "mlb", "nhl", "soccer", "football", "basketball",
                    "baseball", "hockey", "championship", "golf", "pga", "tennis",
                    "mma", "ufc", "wrestling", "boxing", "playoff", "tournament",
                    "superbowl", "world cup", "olympic"),
    # 7. Regulatory decisions
    "REGULATORY":  ("sec", "cftc", "fda", "epa", "antitrust", "regulation",
                    "sanction", "ban", "tariff", "compliance", "enforcement",
                    "ruling", "lawsuit", "fine", "penalty"),
    # 8. Corporate earnings
    "EARNINGS":    ("earnings", "revenue", "profit", "quarterly", "fiscal",
                    "eps", "guidance", "forecast", "beat", "miss", "annual report"),
    # 9. Commodity prices
    "COMMODITIES": ("oil", "gold", "silver", "copper", "wheat", "corn", "soybean",
                    "natural gas", "lumber", "cotton", "coffee", "sugar", "cattle"),
    # 10. Energy / oil price
    "ENERGY":      ("crude oil", "brent", "wti", "opec", "gasoline", "petroleum",
                    "energy price", "power grid", "solar", "wind energy", "pipeline"),
    # 11. Geopolitical events
    "GEOPOLITICAL": ("war", "conflict", "nato", "russia", "ukraine", "china",
                     "taiwan", "iran", "north korea", "nuclear", "treaty",
                     "sanctions", "invasion", "ceasefire", "diplomatic"),
    # 12. Finance / markets (catch-all for stocks, indices)
    "FINANCE":     ("nasdaq", "dow jones", "s&p", "stock market", "fed funds",
                    "bond yield", "currency", "forex", "dollar index", "yen",
                    "euro", "bank", "credit", "debt"),
}

# Short tokens requiring whole-word match to prevent substring false positives.
_WORD_BOUNDARY_CRYPTO   = re.compile(r'\b(btc|eth|sol|xrp|ada|bnb)\b')
_WORD_BOUNDARY_FINANCE  = re.compile(r'\b(fed|gdp|cpi|pce|pmi|rate|rates)\b')
_WORD_BOUNDARY_POLITICS = re.compile(r'\b(gop|dnc|rnc)\b')
_WORD_BOUNDARY_ENERGY   = re.compile(r'\b(lng|wti)\b')

# Per-category rolling win rate tracker (persisted across cycles in memory).
# Format: {category: {"wins": int, "total": int}}
_category_win_rates: dict = {cat: {"wins": 0, "total": 0} for cat in _CATEGORY_KEYWORDS}
_category_win_rates["OTHER"] = {"wins": 0, "total": 0}


def _detect_kalshi_category(title: str) -> str:
    """
    Return the best-matching category for a Kalshi market title.
    Uses word-boundary regex for short tokens to prevent false matches.
    Checks all 12 categories in priority order.
    """
    t = title.lower()

    if any(kw in t for kw in _CATEGORY_KEYWORDS["CRYPTO"]) or _WORD_BOUNDARY_CRYPTO.search(t):
        return "CRYPTO"
    if any(kw in t for kw in _CATEGORY_KEYWORDS["POLITICS"]) or _WORD_BOUNDARY_POLITICS.search(t):
        return "POLITICS"
    if any(kw in t for kw in _CATEGORY_KEYWORDS["ECONOMICS"]) or _WORD_BOUNDARY_FINANCE.search(t):
        return "ECONOMICS"
    if any(kw in t for kw in _CATEGORY_KEYWORDS["EARNINGS"]):
        return "EARNINGS"
    if any(kw in t for kw in _CATEGORY_KEYWORDS["REGULATORY"]):
        return "REGULATORY"
    if any(kw in t for kw in _CATEGORY_KEYWORDS["TECH"]):
        return "TECH"
    if any(kw in t for kw in _CATEGORY_KEYWORDS["ENERGY"]) or _WORD_BOUNDARY_ENERGY.search(t):
        return "ENERGY"
    if any(kw in t for kw in _CATEGORY_KEYWORDS["COMMODITIES"]):
        return "COMMODITIES"
    if any(kw in t for kw in _CATEGORY_KEYWORDS["GEOPOLITICAL"]):
        return "GEOPOLITICAL"
    if any(kw in t for kw in _CATEGORY_KEYWORDS["CLIMATE"]):
        return "CLIMATE"
    if any(kw in t for kw in _CATEGORY_KEYWORDS["SPORTS"]):
        return "SPORTS"
    if any(kw in t for kw in _CATEGORY_KEYWORDS["FINANCE"]):
        return "FINANCE"
    return "OTHER"


# Maximum hours to expiry — sweet spot is 2–24h for optimal edge.
MAX_MARKET_HOURS = 24.0
# Minimum hours to expiry — skip markets about to close.
MIN_MARKET_HOURS = 0.5   # 30 minutes (was 15 min — more exit room)

# Liquidity minimum raised for session 2
MIN_MARKET_LIQUIDITY_USD = 2000  # was implicitly lower — thin markets eat edge via spread


def _parse_close_time(market: dict):
    """Return (close_dt, hours_remaining) or (None, None) if unparseable."""
    from datetime import datetime, timezone
    close_str = (
        market.get("close_time")
        or market.get("expiration_time")
        or market.get("expected_expiration_time")
    )
    if not close_str:
        return None, None
    try:
        close_dt = datetime.fromisoformat(close_str.replace("Z", "+00:00"))
        hours = (close_dt - datetime.now(timezone.utc)).total_seconds() / 3600
        return close_dt, hours
    except Exception:
        return None, None


def is_same_day_market(market: dict) -> bool:
    """
    Return True only if market closes within [MIN_MARKET_HOURS, MAX_MARKET_HOURS].
    Rejects long-dated events (Fed meetings months away, political elections, etc.)
    and already-closing markets with insufficient time to exit.
    """
    _, hours = _parse_close_time(market)
    if hours is None:
        return False  # no expiry info — skip (conservative)
    return MIN_MARKET_HOURS <= hours <= MAX_MARKET_HOURS


def market_freshness_score(market: dict) -> float:
    """
    Return a 0-1 freshness score for a Kalshi market.
    Markets near resolution (>70% through duration) with extreme prices
    have near-zero edge — the outcome is essentially predetermined.

    Score:
      0.0 = stale (skip this market)
      1.0 = fully fresh (just opened, ideal entry window)

    Logic:
      - If price > 0.90 or price < 0.10 AND market is >70% through TTR → 0.0
      - Otherwise → score based on time remaining (more time = fresher)
    """
    import math
    from datetime import datetime, timezone

    yes_price_raw = market.get("yes_ask") or market.get("yes_bid") or 0
    yes_price = float(yes_price_raw) / 100.0 if yes_price_raw > 1 else float(yes_price_raw)

    # Price near resolution?
    near_resolved = yes_price < 0.10 or yes_price > 0.90

    # Parse close_time to compute TTR fraction
    close_str = market.get("close_time") or market.get("expiration_time", "")
    open_str = market.get("open_time", "")
    try:
        now = datetime.now(timezone.utc)
        if close_str:
            close_dt = datetime.fromisoformat(close_str.replace("Z", "+00:00"))
            hours_remaining = max(0, (close_dt - now).total_seconds() / 3600)
            # Estimate total duration if open_time available
            if open_str:
                open_dt = datetime.fromisoformat(open_str.replace("Z", "+00:00"))
                total_hours = max(1, (close_dt - open_dt).total_seconds() / 3600)
                pct_elapsed = min(1.0, (now - open_dt).total_seconds() / (total_hours * 3600))
            else:
                pct_elapsed = 0.5  # unknown — assume halfway
        else:
            hours_remaining = 24.0  # unknown — assume 1 day
            pct_elapsed = 0.5
    except Exception:
        hours_remaining = 24.0
        pct_elapsed = 0.5

    # Stale: predetermined outcome (near resolved + >70% elapsed)
    if near_resolved and pct_elapsed > 0.70:
        return 0.0

    # Freshness decays logarithmically with elapsed fraction
    freshness = 1.0 - (pct_elapsed ** 0.5)
    return round(max(0.0, min(1.0, freshness)), 4)


def update_category_win_rate(category: str, won: bool) -> None:
    """Record a trade outcome for per-category win rate tracking and persist to disk."""
    cat = category if category in _category_win_rates else "OTHER"
    _category_win_rates[cat]["total"] += 1
    if won:
        _category_win_rates[cat]["wins"] += 1
    try:
        _CATEGORY_WIN_RATES_FILE.write_text(
            json.dumps(_category_win_rates, indent=2), encoding="utf-8"
        )
    except Exception as exc:
        log.warning("[KALSHI-CATEGORY] Failed to persist win rates: %s", exc)


def load_category_win_rates() -> None:
    """Load persisted category win rates from disk on startup."""
    if not _CATEGORY_WIN_RATES_FILE.exists():
        return
    try:
        data = json.loads(_CATEGORY_WIN_RATES_FILE.read_text(encoding="utf-8"))
        for cat, stats in data.items():
            if cat in _category_win_rates:
                _category_win_rates[cat]["wins"] = int(stats.get("wins", 0))
                _category_win_rates[cat]["total"] = int(stats.get("total", 0))
        log.info("[KALSHI-CATEGORY] Loaded win rates for %d categories", len(data))
    except Exception as exc:
        log.warning("[KALSHI-CATEGORY] Failed to load win rates: %s", exc)


def get_category_win_rates(window: int = 20) -> dict:
    """
    Return rolling win rate per category.
    Uses all recorded trades (not windowed — use when volume is low).
    """
    result = {}
    for cat, data in _category_win_rates.items():
        total = data["total"]
        wins = data["wins"]
        wr = round(wins / total, 4) if total > 0 else None
        result[cat] = {"win_rate": wr, "wins": wins, "total": total}
    return result


def fetch_kalshi_markets(auth=None, retry_count: int = 0, max_retries: int = 2) -> list:
    """
    Standalone market fetch with explicit per-status-code error handling.
    Returns [] on any failure rather than raising, so callers are never blocked.
    Uses KalshiEventFetcher.fetch_events() under the hood when auth is available.
    """
    if retry_count > max_retries:
        log.error("[KALSHI-FAIL] Max retries (%d) exceeded", max_retries)
        return []

    if auth is None or not getattr(auth, "is_configured", False):
        log.debug("[KALSHI] fetch_kalshi_markets: auth not configured — skipping")
        return []

    base_url = getattr(auth, "base_url", "https://api.elections.kalshi.com/trade-api/v2")
    path = "/markets?status=open&limit=100"
    try:
        _track_api_call()
        resp = requests.get(
            f"{base_url}{path}",
            headers=auth.get_headers("GET", path),
            timeout=10,
        )

        if resp.status_code == 404:
            log.error("[KALSHI-404] Markets endpoint not found — API structure may have changed")
            return []
        elif resp.status_code == 429:
            log.warning("[KALSHI-RATE-LIMITED] Hit rate limit, backing off 60s")
            time.sleep(60)
            return fetch_kalshi_markets(auth, retry_count + 1, max_retries)
        elif resp.status_code == 503:
            log.warning("[KALSHI-UNAVAILABLE] API temporarily down, backing off 30s")
            time.sleep(30)
            return fetch_kalshi_markets(auth, retry_count + 1, max_retries)
        elif resp.status_code == 401:
            log.error("[KALSHI-UNAUTHORIZED] Check RSA key / key ID in .env")
            return []
        elif resp.status_code != 200:
            log.warning("[KALSHI-ERROR] HTTP %d: %s", resp.status_code, resp.text[:100])
            return []

        markets = resp.json().get("markets", [])
        log.debug("[KALSHI-OK] fetch_kalshi_markets: %d markets", len(markets))
        return markets

    except requests.Timeout:
        log.warning("[KALSHI-TIMEOUT] Request exceeded 10s")
        return []
    except requests.ConnectionError:
        log.warning("[KALSHI-CONNECTION] Network error")
        return []
    except json.JSONDecodeError:
        log.error("[KALSHI-BAD-JSON] Response was not valid JSON")
        return []
    except Exception as exc:
        log.error("[KALSHI-UNEXPECTED] %s: %s", type(exc).__name__, str(exc)[:100])
        return []


class KalshiEventFetcher:
    def __init__(self, auth):
        self.auth = auth
        self.base_url = auth.base_url
        self.timeout = 10

    def fetch_events(self, categories: List[str] = None) -> List[Dict]:
        """
        Fetch open Kalshi markets with targeted series queries + generic fallback.

        Strategy:
          1. Query specific crypto/macro series tickers first (most relevant).
          2. Fall back to generic /markets?status=open with increased limit.
          3. Deduplicate by ticker and categorize everything.
        Returns list of market dicts (deduplicated), or [] on any failure.
        """
        if not self.auth.is_configured:
            return []

        all_markets: List[Dict] = []
        seen_tickers: set = set()

        # ── Priority 1: Targeted series across all 12 categories ─────────────
        # Each series_ticker corresponds to a real Kalshi event family.
        target_series = [
            # ── PRIORITY 1: Crypto intraday (hourly resolution markets) ────────
            # These resolve TODAY — perfect for our same-day filter.
            "KXBTC",    # Bitcoin hourly/daily spot price range markets
            "KXETH",    # Ethereum hourly/daily price markets
            "KXBTCM",   # Bitcoin daily close price
            "KXETHM",   # Ethereum daily close price
            "KXSOLANA", # Solana daily price
            "KXCRYPTO", # General same-day crypto markets
            "KXBTCD",   # Bitcoin daily (if series exists)
            "KXETHD",   # Ethereum daily
            # ── PRIORITY 2: Intraday macro (same-day economic data) ─────────
            "KXCPI",    # CPI release day markets (if they have hourly sub-markets)
            "KXGOLD",   # Gold intraday price
            "KXOIL",    # Oil intraday price
            "KXENERGY", # Energy intraday
            "KXECON",   # General econ intraday
            "KXJOBS",   # Jobs data release (same-day only after filter)
            # ── PRIORITY 3: Longer-dated (will be filtered by expiry) ─────────
            "KXFED",    # Fed decision (filtered out — resolves in months)
            "KXGDP",    # GDP (filtered out)
            "KXINFL",   # Inflation (filtered out if monthly)
            "KXPCE",    # PCE (filtered out if monthly)
            "KXAI",     # AI developments
            "KXTECH",   # Tech events
            "KXGEO",    # Geopolitical
            "KXREG",    # Regulatory decisions
            # Skip sports entirely — almost always long-dated with poor edge
            # "KXNFL", "KXNBA" — excluded
            "KXWEATHER", # Weather (some are same-day hurricane/storm tracks)
        ]

        for series in target_series:
            path = f"/markets?status=open&limit=100&series_ticker={series}"
            try:
                _track_api_call()
                resp = requests.get(
                    f"{self.base_url}{path}",
                    headers=self.auth.get_headers("GET", path),
                    timeout=self.timeout,
                )
                if resp.status_code == 200:
                    batch = resp.json().get("markets", [])
                    added = 0
                    for m in batch:
                        ticker = m.get("ticker", "")
                        if ticker and ticker not in seen_tickers:
                            seen_tickers.add(ticker)
                            m["_category"] = _detect_kalshi_category(
                                m.get("title", "") + " " + m.get("subtitle", "")
                            )
                            all_markets.append(m)
                            added += 1
                    if added:
                        log.debug("[KALSHI] series=%s → %d markets", series, added)
                elif resp.status_code not in (404, 400):
                    # 404 = series doesn't exist (normal), 400 = bad param — both are silent
                    log.debug("[KALSHI] series=%s HTTP %s", series, resp.status_code)
            except Exception as exc:
                log.debug("[KALSHI] series=%s error: %s", series, exc)

        # ── Priority 2: Generic open markets (larger limit) ───────────────────
        # Fetches up to 200 general markets as a fallback / supplement.
        for cursor_offset in [0, 100]:
            path = f"/markets?status=open&limit=100"
            if cursor_offset:
                path += f"&cursor={cursor_offset}"
            try:
                _track_api_call()
                resp = requests.get(
                    f"{self.base_url}{path}",
                    headers=self.auth.get_headers("GET", path),
                    timeout=self.timeout,
                )
            except requests.exceptions.Timeout:
                log.warning("[KALSHI] API timeout (generic fetch)")
                break
            except requests.exceptions.ConnectionError:
                log.warning("[KALSHI] Connection refused")
                return all_markets or []
            except Exception as exc:
                log.warning("[KALSHI] Fetch error: %s", exc)
                break

            if resp.status_code == 401:
                log.warning("[KALSHI] 401 Unauthorized — check RSA key / key ID in .env")
                return []
            if resp.status_code != 200:
                log.warning("[KALSHI] HTTP %s (generic fetch)", resp.status_code)
                break

            batch = resp.json().get("markets", [])
            if not batch:
                break

            added = 0
            for m in batch:
                ticker = m.get("ticker", "")
                if ticker and ticker not in seen_tickers:
                    seen_tickers.add(ticker)
                    m["_category"] = _detect_kalshi_category(
                        m.get("title", "") + " " + m.get("subtitle", "")
                    )
                    all_markets.append(m)
                    added += 1

            log.debug("[KALSHI] generic offset=%d → %d new markets", cursor_offset, added)

        if not all_markets:
            log.warning("[KALSHI-DIAGNOSTIC] Zero markets returned from all queries")
            return []

        # ── Same-day expiry filter: ONLY trade markets closing TODAY ──────────
        # This is the most critical filter — eliminates KXFED, KXCPI monthly,
        # political events, and anything that won't resolve within 24 hours.
        same_day: list = []
        long_dated_count = 0
        for m in all_markets:
            _, hours = _parse_close_time(m)
            if hours is None:
                long_dated_count += 1
                log.debug("[KALSHI-EXPIRY] No close_time for: %s — skipped", m.get("title", "?")[:50])
                continue
            if hours < MIN_MARKET_HOURS:
                log.debug("[KALSHI-EXPIRY] Too close to expiry (%.1fh): %s — skipped", hours, m.get("title", "?")[:50])
                continue
            if hours > MAX_MARKET_HOURS:
                long_dated_count += 1
                log.debug("[KALSHI-EXPIRY] Long-dated (%.1fh): %s — skipped", hours, m.get("title", "?")[:50])
                continue
            m["_hours_to_close"] = round(hours, 2)
            same_day.append(m)

        log.info(
            "[KALSHI-EXPIRY] Same-day filter: %d kept / %d long-dated removed",
            len(same_day), long_dated_count,
        )
        all_markets = same_day

        if not all_markets:
            log.warning("[KALSHI] No same-day markets found — nothing to trade today")
            return []

        # ── Freshness filter: skip near-resolved stale markets ────────────────
        fresh_markets = []
        stale_count = 0
        for m in all_markets:
            score = market_freshness_score(m)
            m["_freshness"] = score
            if score == 0.0:
                stale_count += 1
                log.debug(
                    "[KALSHI-FRESHNESS] Stale market skipped: %s (score=0)",
                    m.get("title", "?")[:50],
                )
            else:
                fresh_markets.append(m)
        if stale_count:
            log.info("[KALSHI-FRESHNESS] Filtered %d stale markets (near-resolved)", stale_count)

        all_markets = fresh_markets
        log.info("[KALSHI] %d fresh same-day markets ready", len(all_markets))

        # ── Category win-rate enforcement ────────────────────────────────────
        # After 10 trades in a category: WR < 45% → reduce liquidity threshold (soft gate)
        # After 20 trades: WR < 40% → suspend (will be caught by matcher's drift gate)
        _cwr = get_category_win_rates()
        _wl_filtered: list = []
        _wl_removed = 0
        for m in all_markets:
            cat = m.get("_category", "OTHER")
            cat_stats = _cwr.get(cat, {})
            cat_total = cat_stats.get("total", 0)
            cat_wr    = cat_stats.get("win_rate")
            if cat_total >= 20 and cat_wr is not None and cat_wr < 0.40:
                # Suspended category — drop from fetch output
                _wl_removed += 1
                log.debug("[KALSHI-CAT-GATE] Category %s WR=%.0f%% after %d trades → dropped",
                          cat, cat_wr * 100, cat_total)
                continue
            # Liquidity check for min threshold
            liq_raw = float(m.get("open_interest", 0) or 0)
            if liq_raw > 0 and liq_raw < MIN_MARKET_LIQUIDITY_USD:
                _wl_removed += 1
                continue
            _wl_filtered.append(m)

        if _wl_removed:
            log.info("[KALSHI-FILTER] Removed %d markets (low WR/liquidity) → %d remain",
                     _wl_removed, len(_wl_filtered))
        all_markets = _wl_filtered

        # Diagnostic: 12-category breakdown
        cats: dict = {}
        for m in all_markets:
            cats[m["_category"]] = cats.get(m["_category"], 0) + 1
        log.info("[KALSHI-DIAGNOSTIC] 12-category breakdown: %s", cats)

        # Show top 3 markets per high-value category
        for cat in ("CRYPTO", "ECONOMICS", "POLITICS", "TECH", "ENERGY"):
            cat_markets = [m for m in all_markets if m["_category"] == cat]
            if cat_markets:
                log.info("[KALSHI-CAT] %s: %d markets", cat, len(cat_markets))
                for idx, m in enumerate(cat_markets[:3], 1):
                    title = m.get("title", "N/A")[:55]
                    yes_price = m.get("yes_ask", m.get("yes_bid", 50))
                    freshness = m.get("_freshness", 0)
                    log.info("  [%d] fresh=%.2f YES~%s%% | %s", idx, freshness, yes_price, title)

        return all_markets

    def get_market_details(self, ticker: str) -> Optional[Dict]:
        """Get detailed info for a specific market ticker."""
        if not self.auth.is_configured:
            return None
        path = f"/markets/{ticker}"
        try:
            resp = requests.get(
                f"{self.base_url}{path}",
                headers=self.auth.get_headers("GET", path),
                timeout=self.timeout,
            )
            return resp.json() if resp.status_code == 200 else None
        except Exception as exc:
            log.warning("[KALSHI] Market detail error (%s): %s", ticker, exc)
            return None
