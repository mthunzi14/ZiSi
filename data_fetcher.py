"""
data_fetcher.py - ZiSi Bot Data Retrieval
Fetches news, crypto prices, and Polymarket event data from external APIs.
"""

import re
import time
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Optional

import requests
from requests.adapters import HTTPAdapter

from config import load_config

log = logging.getLogger("zisi.data_fetcher")

# Persistent HTTP session with connection pooling — reuses TLS connections
_http = requests.Session()
_http.headers.update({"Connection": "keep-alive", "User-Agent": "ZiSi-Bot/1.0"})
_http_adapter = HTTPAdapter(pool_connections=20, pool_maxsize=50, max_retries=0)
_http.mount("https://", _http_adapter)
_http.mount("http://", _http_adapter)

# Article age filter: skip articles older than this many minutes
_MAX_ARTICLE_AGE_MINUTES = 120

# Module-level price cache so we can fall back if Coingecko is down
_price_cache: dict = {}
_price_cache_time: Optional[datetime] = None

_config: dict = {}


def _get_config() -> dict:
    global _config
    if not _config:
        _config = load_config()
    return _config


def _retry_request(
    method: str,
    url: str,
    params: dict | None = None,
    json: dict | None = None,
    headers: dict | None = None,
) -> Optional[requests.Response]:
    """
    Execute an HTTP request with retry + exponential backoff.

    Returns the Response on success, None after all retries exhausted.
    """
    cfg = _get_config()
    retries = cfg["API_RETRY_COUNT"]
    backoff = cfg["API_RETRY_BACKOFF_SECONDS"]
    timeout = cfg["API_TIMEOUT_SECONDS"]

    for attempt in range(1, retries + 1):
        try:
            resp = requests.request(
                method,
                url,
                params=params,
                json=json,
                headers=headers,
                timeout=timeout,
            )
            resp.raise_for_status()
            return resp
        except requests.RequestException as exc:
            log.warning("Attempt %d/%d failed for %s: %s", attempt, retries, url, exc)
            if attempt < retries:
                time.sleep(backoff * attempt)

    log.error("All %d attempts exhausted for %s", retries, url)
    return None


# ---------------------------------------------------------------------------
# Fear & Greed Index (Alternative.me — no API key, free forever)
# ---------------------------------------------------------------------------

_fng_cache: dict = {}
_fng_cache_time: Optional[datetime] = None
_FNG_TTL_MINUTES = 30  # refresh at most every 30 min


def fetch_fear_and_greed() -> dict:
    """
    Fetch the current Crypto Fear & Greed Index from Alternative.me.

    Returns:
        Dict with keys: value (0-100), label (str), timestamp (str).
        Falls back to cached value or neutral default on failure.

    Regime guide:
      0-24  Extreme Fear   → market over-sold, bullish signals underpriced
      25-49 Fear           → cautious, reduce position sizes
      50-74 Greed          → normal market, trust signals
      75-100 Extreme Greed → market over-bought, bearish signals underpriced
    """
    global _fng_cache, _fng_cache_time

    # Return cached value if fresh
    if _fng_cache and _fng_cache_time:
        age_minutes = (datetime.now(timezone.utc) - _fng_cache_time).total_seconds() / 60
        if age_minutes < _FNG_TTL_MINUTES:
            return _fng_cache

    resp = _retry_request("GET", "https://api.alternative.me/fng/?limit=1&format=json")
    if resp is None:
        if _fng_cache:
            log.warning("[FNG] API unavailable — using cached F&G: %s", _fng_cache.get("label"))
            return _fng_cache
        log.warning("[FNG] API unavailable and no cache — using neutral default")
        return {"value": 50, "label": "Neutral", "timestamp": ""}

    try:
        data = resp.json().get("data", [{}])[0]
        value = int(data.get("value", 50))
        label = data.get("value_classification", "Neutral")
        timestamp = data.get("timestamp", "")

        _fng_cache = {"value": value, "label": label, "timestamp": timestamp}
        _fng_cache_time = datetime.now(timezone.utc)

        # Map value to a Kelly multiplier for position sizing
        if value <= 24:
            kelly_mult = 0.75   # Extreme Fear — prices already reflect bad news
        elif value <= 49:
            kelly_mult = 0.90   # Fear — slight caution
        elif value <= 74:
            kelly_mult = 1.00   # Greed — normal, full Kelly
        else:
            kelly_mult = 0.80   # Extreme Greed — over-bought, fade momentum

        _fng_cache["kelly_multiplier"] = kelly_mult

        log.info(
            "[FNG] Fear & Greed: %d (%s) → Kelly×%.2f",
            value, label, kelly_mult,
        )
        return _fng_cache

    except Exception as exc:
        log.warning("[FNG] Parse error: %s", exc)
        return {"value": 50, "label": "Neutral", "timestamp": "", "kelly_multiplier": 1.0}


# ---------------------------------------------------------------------------
# Binance perpetual futures funding rate (free — no API key needed)
# ---------------------------------------------------------------------------

_funding_cache: dict = {}
_funding_cache_time: Optional[datetime] = None
_FUNDING_TTL_MINUTES = 15  # Binance publishes new rates every 8h; we cache 15m


def fetch_funding_rate(symbol: str = "BTCUSDT") -> dict:
    """
    Fetch the latest perpetual-futures funding rate from Binance (no key needed).

    Interpretation:
      Positive rate (> +0.01%) → longs paying shorts → BEARISH lean
          Crowd is over-long; market likely to correct downward.
      Negative rate (< -0.01%) → shorts paying longs → BULLISH lean
          Shorts are squeezed; potential upward snap.
      Near-zero              → NEUTRAL — balanced positioning.

    Returns dict with keys: symbol, rate, rate_pct, sentiment, signal_strength,
    description.  Returns a neutral default on any failure.
    """
    global _funding_cache, _funding_cache_time

    cached = _funding_cache.get(symbol)
    if cached and _funding_cache_time:
        age = (datetime.now(timezone.utc) - _funding_cache_time).total_seconds() / 60
        if age < _FUNDING_TTL_MINUTES:
            return cached

    _default = {
        "symbol": symbol, "rate": 0.0, "rate_pct": 0.0,
        "sentiment": "NEUTRAL", "signal_strength": 0.0,
        "description": "Funding rate unavailable",
    }

    try:
        resp = requests.get(
            "https://fapi.binance.com/fapi/v1/fundingRate",
            params={"symbol": symbol, "limit": 1},
            timeout=8,
        )
        resp.raise_for_status()
        data = resp.json()
        if not data:
            return _default

        rate = float(data[0].get("fundingRate", 0))
        rate_pct = round(rate * 100, 4)

        if rate > 0.0001:       # > +0.01 %
            sentiment = "BEARISH"
            # Scale: 0.01% → strength 0.1,  0.1% → strength 1.0
            strength = round(min(1.0, rate / 0.001), 3)
            desc = f"High funding {rate_pct:+.4f}% — longs overleveraged → bearish"
        elif rate < -0.0001:    # < -0.01 %
            sentiment = "BULLISH"
            strength = round(min(1.0, abs(rate) / 0.001), 3)
            desc = f"Negative funding {rate_pct:+.4f}% — short squeeze risk → bullish"
        else:
            sentiment = "NEUTRAL"
            strength = 0.0
            desc = f"Balanced funding {rate_pct:+.4f}% — no positioning edge"

        result = {
            "symbol": symbol,
            "rate": rate,
            "rate_pct": rate_pct,
            "sentiment": sentiment,
            "signal_strength": strength,
            "description": desc,
        }
        _funding_cache[symbol] = result
        _funding_cache_time = datetime.now(timezone.utc)
        log.info("[FUNDING] %s: %+.4f%% → %s (strength=%.2f)", symbol, rate_pct, sentiment, strength)
        return result

    except Exception as exc:
        log.debug("[FUNDING] Rate fetch failed for %s: %s", symbol, exc)
        return _default


# ---------------------------------------------------------------------------
# Article age utilities
# ---------------------------------------------------------------------------

def _parse_article_age_minutes(published_at: str) -> Optional[float]:
    """
    Return how many minutes ago an article was published.
    Returns None if the timestamp can't be parsed.
    """
    if not published_at:
        return None
    try:
        clean = published_at.strip().replace("Z", "+00:00")
        pub_dt = datetime.fromisoformat(clean)
        if pub_dt.tzinfo is None:
            pub_dt = pub_dt.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - pub_dt).total_seconds() / 60
        return max(0.0, age)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# NewsAPI
# ---------------------------------------------------------------------------

def fetch_news_from_newsapi() -> list[dict]:
    """
    Fetch latest crypto news articles from NewsAPI.

    Returns:
        List of article dicts; empty list on failure.
    """
    cfg = _get_config()
    api_key = cfg["NEWSAPI_KEY"]

    if not api_key:
        log.error("NEWSAPI_KEY not configured — skipping news fetch")
        return []

    params = {
        "q": "bitcoin OR ethereum OR cryptocurrency OR crypto",
        "sortBy": "publishedAt",
        "language": "en",
        "pageSize": 30,
        "apiKey": api_key,
    }

    resp = _retry_request("GET", "https://newsapi.org/v2/everything", params=params)
    if resp is None:
        log.error("NewsAPI call failed after retries")
        return []

    data = resp.json()
    articles = data.get("articles", [])
    log.info("NewsAPI returned %d articles", len(articles))

    # Normalise fields so downstream modules always see the same shape
    normalised = []
    for art in articles:
        pub_at = art.get("publishedAt") or ""
        age_min = _parse_article_age_minutes(pub_at)
        normalised.append({
            "source": (art.get("source") or {}).get("name", "Unknown"),
            "author": art.get("author") or "",
            "title": art.get("title") or "",
            "description": art.get("description") or "",
            "url": art.get("url") or "",
            "image": art.get("urlToImage") or "",
            "publishedAt": pub_at,
            "content": art.get("content") or "",
            "article_age_minutes": age_min,
            "article_fresh": age_min is not None and age_min <= _MAX_ARTICLE_AGE_MINUTES,
        })

    return normalised


# ---------------------------------------------------------------------------
# Cointelegraph RSS + hybrid article pipeline
# ---------------------------------------------------------------------------

# Terms that must appear for an article to be considered crypto-relevant
_CRYPTO_TERMS: frozenset = frozenset({
    "bitcoin", "ethereum", "crypto", "blockchain", "defi", "btc", "eth",
    "solana", "xrp", "ripple", "dogecoin", "doge", "cardano", "nft",
    "token", "exchange", "altcoin", "web3", "dao", "stablecoin",
    "coinbase", "binance", "halving", "satoshi", "decentralized",
})

# Terms that indicate clearly non-crypto content (only used when no CRYPTO_TERM present)
_GARBAGE_TERMS: frozenset = frozenset({
    "recipe", "cooking", "restaurant", "movie", "cinema", "celebrity",
    "music", "concert", "album", "novel", "tv show", "television show",
})


def _strip_html(text: str) -> str:
    """Remove HTML tags from a string."""
    return re.sub(r"<[^>]+>", " ", text).strip()


def _is_crypto_article(article: dict) -> bool:
    """Return True when the article is relevant to crypto/blockchain."""
    blob = (
        (article.get("title") or "") + " " +
        (article.get("description") or "") + " " +
        (article.get("content") or "")
    ).lower()
    return any(term in blob for term in _CRYPTO_TERMS)


def _is_garbage(article: dict) -> bool:
    """Remove article if it contains zero crypto keywords."""
    return not _is_crypto_article(article)


def _fetch_rss_feed(url: str, source_name: str, source_weight: str = "HIGH") -> list[dict]:
    """Generic RSS fetcher. Returns [] on any failure."""
    try:
        import feedparser
    except ImportError:
        log.warning("[RSS] feedparser not installed — skipping %s (pip install feedparser)", source_name)
        return []

    try:
        feed = feedparser.parse(url)
        if not feed.entries:
            log.warning("[RSS] Empty response from %s", source_name)
            return []

        articles: list[dict] = []
        for entry in feed.entries:
            summary = _strip_html(entry.get("summary") or "")[:500]
            pub_at = entry.get("published") or ""
            age_min = _parse_article_age_minutes(pub_at)
            articles.append({
                "source": source_name,
                "author": entry.get("author") or "",
                "title": entry.get("title") or "",
                "description": summary,
                "url": entry.get("link") or "",
                "image": "",
                "publishedAt": pub_at,
                "content": summary,
                "source_weight": source_weight,
                "article_age_minutes": age_min,
                "article_fresh": age_min is not None and age_min <= _MAX_ARTICLE_AGE_MINUTES,
            })

        log.info("[%s-RSS] Fetched %d articles", source_name.upper().replace(" ", "-"), len(articles))
        return articles

    except Exception as exc:
        log.warning("[RSS] %s fetch failed: %s", source_name, exc)
        return []


def fetch_cointelegraph_rss() -> list[dict]:
    return _fetch_rss_feed("https://cointelegraph.com/rss", "Cointelegraph", "HIGH")


def fetch_decrypt_rss() -> list[dict]:
    return _fetch_rss_feed("https://decrypt.co/feed", "Decrypt", "HIGH")


def fetch_cryptoslate_rss() -> list[dict]:
    return _fetch_rss_feed("https://cryptoslate.com/feed/", "CryptoSlate", "MEDIUM")


def _deduplicate_articles(primary: list[dict], secondary: list[dict]) -> list[dict]:
    """
    Merge two article lists.  primary entries are always kept; secondary entries
    are added only when their title (first 60 chars, normalised) doesn't match
    any title already in the merged set.
    """
    merged = list(primary)
    seen = {(a.get("title") or "").lower().strip()[:60] for a in primary}

    added = 0
    for art in secondary:
        key = (art.get("title") or "").lower().strip()[:60]
        if key and key not in seen:
            merged.append(art)
            seen.add(key)
            added += 1

    log.debug(
        "[DEDUP] primary=%d secondary=%d new_added=%d final=%d",
        len(primary), len(secondary), added, len(merged),
    )
    return merged


# ── Source quality weights ────────────────────────────────────────────────────
# Higher = more credible, faster market-moving source.
# Used downstream by sentiment_analyzer to scale signal confidence.
# Scale 0.0–1.0.  Default (unrecognised source) = 0.70.
SOURCE_QUALITY_MAP: dict = {
    # Tier 1: Institutional / high-impact
    "reuters":          1.00,
    "bloomberg":        1.00,
    "financial times":  0.98,
    "wall street journal": 0.98,
    "wsj":              0.98,
    "cnbc":             0.95,
    "the block":        0.92,
    "coindesk":         0.90,
    # Tier 2: Major crypto-native
    "cointelegraph":    0.85,
    "decrypt":          0.82,
    "cryptoslate":      0.78,
    "bitcoin magazine": 0.80,
    "cryptonews":       0.75,
    "cryptobriefing":   0.72,
    # Tier 3: General news with crypto coverage
    "yahoo finance":    0.70,
    "marketwatch":      0.70,
    "investing.com":    0.68,
    "newsbtc":          0.65,
    "u.today":          0.60,
    "ambcrypto":        0.58,
    "beincrypto":       0.58,
    "coingape":         0.55,
    "cryptopotato":     0.50,
    # Tier 4: Lower credibility / aggregators
    "zycrypto":         0.45,
    "bitcoinist":       0.45,
    "tronweekly":       0.40,
}
_DEFAULT_SOURCE_QUALITY = 0.70


def _get_source_quality(source_name: str) -> float:
    """Return a quality score 0.0–1.0 for a given source name."""
    if not source_name:
        return _DEFAULT_SOURCE_QUALITY
    key = source_name.strip().lower()
    # Exact match first
    if key in SOURCE_QUALITY_MAP:
        return SOURCE_QUALITY_MAP[key]
    # Partial match (e.g. "CoinDesk News" → "coindesk")
    for k, v in SOURCE_QUALITY_MAP.items():
        if k in key or key in k:
            return v
    return _DEFAULT_SOURCE_QUALITY


def fetch_crypto_articles() -> list[dict]:
    """
    Hybrid news fetch from 4 sources: Cointelegraph + Decrypt + CryptoSlate (RSS)
    + NewsAPI. Fetches all 4 in parallel (saves ~6s vs sequential).
    Deduplicates then removes clearly non-crypto articles.
    Tags every article with a source_quality float (0.0–1.0) used by sentiment_analyzer.
    """
    _source_fns = {
        "ct": fetch_cointelegraph_rss,
        "dc": fetch_decrypt_rss,
        "cs": fetch_cryptoslate_rss,
        "na": fetch_news_from_newsapi,
    }
    _results: dict = {k: [] for k in _source_fns}
    with ThreadPoolExecutor(max_workers=4) as _ex:
        _futs = {_ex.submit(fn): name for name, fn in _source_fns.items()}
        for _fut in as_completed(_futs, timeout=20):
            _name = _futs[_fut]
            try:
                _results[_name] = _fut.result() or []
            except Exception as _e:
                log.debug("[FETCH-SOURCES] %s failed: %s", _name, _e)

    ct_articles = _results["ct"]
    dc_articles = _results["dc"]
    cs_articles = _results["cs"]
    na_articles = _results["na"]

    for art in na_articles:
        art.setdefault("source_weight", "MEDIUM")

    log.info(
        "[FETCH-SOURCES] Cointelegraph=%d | Decrypt=%d | CryptoSlate=%d | NewsAPI=%d (parallel)",
        len(ct_articles), len(dc_articles), len(cs_articles), len(na_articles),
    )

    # Merge: Cointelegraph primary, then Decrypt, CryptoSlate, then NewsAPI
    merged = _deduplicate_articles(ct_articles, dc_articles)
    merged = _deduplicate_articles(merged, cs_articles)
    merged = _deduplicate_articles(merged, na_articles)

    # Tag every article with a source_quality score for downstream confidence scaling
    for art in merged:
        src = art.get("source") or art.get("source_name") or ""
        art["source_quality"] = _get_source_quality(str(src))

    total_before = len(merged)

    # Filter only clear garbage — NOT crypto articles
    filtered = [a for a in merged if not _is_garbage(a)]
    garbage_removed = total_before - len(filtered)

    # ── Article age filter ────────────────────────────────────────────────
    # Skip articles published more than MAX_ARTICLE_AGE_MINUTES ago.
    # Old articles have already moved the market — no edge remains.
    # Articles with no parseable timestamp are kept (can't determine age).
    fresh = []
    stale_removed = 0
    for art in filtered:
        age = art.get("article_age_minutes")
        if age is not None and age > _MAX_ARTICLE_AGE_MINUTES:
            stale_removed += 1
            log.debug(
                "[AGE-FILTER] Dropping stale article (%d min old): %s",
                int(age), (art.get("title") or "")[:60],
            )
        else:
            fresh.append(art)

    log.info(
        "[FETCH-COMPLETE] merged=%d | garbage=%d | stale=%d | final=%d",
        total_before, garbage_removed, stale_removed, len(fresh),
    )
    return fresh


# ---------------------------------------------------------------------------
# CoinGecko prices
# ---------------------------------------------------------------------------

def get_crypto_prices() -> dict:
    """
    Fetch current BTC and ETH prices from CoinGecko (no auth required).

    Falls back to cached prices if the API is unavailable.

    Returns:
        Dict keyed by coin id with price, market cap, volume, 24h change.
    """
    global _price_cache, _price_cache_time

    params = {
        "ids": "bitcoin,ethereum",
        "vs_currencies": "usd",
        "include_market_cap": "true",
        "include_24hr_vol": "true",
        "include_24hr_change": "true",
    }

    resp = _retry_request(
        "GET",
        "https://api.coingecko.com/api/v3/simple/price",
        params=params,
    )

    if resp is None:
        if _price_cache:
            log.warning("Using cached prices from %s", _price_cache_time)
            return _price_cache
        log.error("CoinGecko unavailable and no cached prices exist")
        return {}

    _price_cache = resp.json()
    _price_cache_time = datetime.now(timezone.utc)
    log.info(
        "Prices — BTC: $%.0f | ETH: $%.0f",
        _price_cache.get("bitcoin", {}).get("usd", 0),
        _price_cache.get("ethereum", {}).get("usd", 0),
    )
    return _price_cache


# ---------------------------------------------------------------------------
# Polymarket events
# ---------------------------------------------------------------------------

def _classify_polymarket_market_type(title: str) -> str:
    """Classify a Polymarket event title into UP_DOWN, HIT_PRICE, PRICE_RANGE, or OTHER."""
    t = title.lower()
    if any(w in t for w in ('up or down', 'bullish or bearish', 'direction', 'go up', 'go down')):
        return 'UP_DOWN'
    has_price = '$' in t or any(c.isdigit() for c in t)
    if has_price and any(w in t for w in ('above', 'below', 'will', 'hit', 'reach', 'exceed')):
        return 'HIT_PRICE'
    if has_price and any(w in t for w in ('between', 'range', 'to $', '-$')):
        return 'PRICE_RANGE'
    return 'OTHER'


def _detect_polymarket_category(title: str, description: str = "") -> str:
    """
    Detect market category from title + description.
    Returns CRYPTO, SPORTS, POLITICS, FINANCE, or OTHER.
    """
    text = (title + " " + description).lower()

    crypto_kw = ("bitcoin", "ethereum", "crypto", "btc", "eth", "blockchain",
                 "defi", "altcoin", "xrp", "solana", "sol ", "doge", "dogecoin", "nft",
                 "coinbase", "binance", "stablecoin", "up or down", "updown")
    if any(kw in text for kw in crypto_kw):
        return "CRYPTO"

    sports_kw = ("nba", "nfl", "mlb", "nhl", "soccer", "basketball", "football",
                 "baseball", "hockey", "championship", "super bowl", "world cup",
                 "premier league", "la liga", "tennis", "golf", "mma", "ufc")
    if any(kw in text for kw in sports_kw):
        return "SPORTS"

    politics_kw = ("trump", "biden", "harris", "election", "congress", "senate",
                   "president", "vote", "parliament", "political", "impeach", "gop")
    if any(kw in text for kw in politics_kw):
        return "POLITICS"

    finance_kw = ("fed", "inflation", "gdp", "interest rate", "stock", "dow",
                  "nasdaq", "s&p", "earnings", "unemployment", "cpi", "fomc")
    if any(kw in text for kw in finance_kw):
        return "FINANCE"

    return "OTHER"


def fetch_polymarket_events(search_term: str) -> list[dict]:
    """
    Fetch Polymarket active events using multi-query strategy.
    Searches bitcoin, ethereum, and the provided term separately, then
    deduplicates so callers get maximum coverage without repeated events.
    """
    cfg = _get_config()
    base_url = cfg["POLYMARKET_GAMMA_API_URL"].rstrip("/")

    # Broader query set: standard macro + Up/Down short-duration markets (PBot pattern).
    base_queries = [
        "bitcoin", "ethereum", "crypto", "solana", "btc",
        "will bitcoin", "will ethereum", "bitcoin price",
        "xrp", "dogecoin", "chainlink",
        # Up/Down short-duration markets — PBot's primary trading arena
        "bitcoin up or down", "ethereum up or down", "solana up or down",
        "btc up", "eth up",
        # Macro/regulatory crypto events
        "sec crypto", "crypto regulation", "bitcoin etf", "stablecoin",
    ]
    queries = list(dict.fromkeys([search_term] + base_queries))
    all_events: list[dict] = []
    seen_ids: set = set()

    # ── Slug-based Up/Down window fetch ───────────────────────────────────────
    # Polymarket Up/Down markets have predictable slugs: {coin}-updown-{dur}m-{expiry_unix_ts}
    # Text search buries these short-duration markets under high-volume sports events.
    # Instead, compute the current + next active window expiry timestamps and fetch by slug.
    _now_ts = int(time.time())
    _now_utc_direct = datetime.now(timezone.utc)
    _ud_fetched = 0

    def _try_slug_fetch(slug: str) -> None:
        nonlocal _ud_fetched
        try:
            _r = _retry_request("GET", f"{base_url}/events", params={"slug": slug})
            if _r is None:
                return
            _raw = _r.json()
            evs = _raw if isinstance(_raw, list) else _raw.get("data", _raw.get("events", []))
            if isinstance(_raw, dict) and "id" in _raw:
                evs = [_raw]
            for _ev in (evs if isinstance(evs, list) else []):
                _eid = _ev.get("id", "")
                if not _eid or _eid in seen_ids:
                    return
                _end = _ev.get("endDate", _ev.get("resolutionDate", ""))
                if _end:
                    try:
                        from datetime import timedelta as _tdx
                        _edt = datetime.fromisoformat(_end.replace("Z", "+00:00"))
                        if _edt.tzinfo is None:
                            _edt = _edt.replace(tzinfo=timezone.utc)
                        if _edt < (_now_utc_direct + _tdx(seconds=90)):
                            return
                    except Exception:
                        pass
                if float(_ev.get("liquidity", 0) or 0) < 50:
                    return
                seen_ids.add(_eid)
                _ev["_updown_direct"] = True
                all_events.append(_ev)
                _ud_fetched += 1
                log.info("[POLYMARKET-UPDOWN] Slug fetch found: %s", _ev.get("title", "")[:60])
        except Exception as _e:
            log.debug("[POLYMARKET-UPDOWN] Slug fetch error %s: %s", slug, _e)

    # Generate candidate slugs — 15-min only (5-min dominated by sub-10ms colocated HFT;
    # from high-latency regions like Africa, 5-min fills are gone before we arrive)
    for _dur_min in (15,):
        _interval = _dur_min * 60
        _boundary = ((_now_ts + _interval) // _interval) * _interval  # next boundary
        for _offset in range(4):  # next 4 windows
            _expiry_ts = _boundary + _offset * _interval
            if _expiry_ts < _now_ts + 90:
                continue
            for _coin in ("btc", "eth", "sol"):
                _try_slug_fetch(f"{_coin}-updown-{_dur_min}m-{_expiry_ts}")

    if _ud_fetched > 0:
        log.info("[POLYMARKET-UPDOWN] Slug fetch added %d active Up/Down markets", _ud_fetched)
    else:
        # Fallback: fetch all active events sorted by soonest endDate, filter client-side
        log.debug("[POLYMARKET-UPDOWN] Slug fetch returned 0 — trying broad active fetch")
        try:
            _broad = _retry_request(
                "GET", f"{base_url}/events",
                params={"active": "true", "limit": 100, "order": "endDate", "ascending": "true"},
            )
            if _broad:
                _broad_raw = _broad.json()
                _broad_evs = _broad_raw if isinstance(_broad_raw, list) else _broad_raw.get("data", [])
                for _ev in _broad_evs:
                    _eid = _ev.get("id", "")
                    if not _eid or _eid in seen_ids:
                        continue
                    _title = _ev.get("title", "").lower()
                    if "up or down" not in _title and "updown" not in _title:
                        continue
                    _end = _ev.get("endDate", _ev.get("resolutionDate", ""))
                    if _end:
                        try:
                            from datetime import timedelta as _tdx
                            _edt = datetime.fromisoformat(_end.replace("Z", "+00:00"))
                            if _edt.tzinfo is None:
                                _edt = _edt.replace(tzinfo=timezone.utc)
                            if _edt < (_now_utc_direct + _tdx(seconds=90)):
                                continue
                        except Exception:
                            pass
                    if float(_ev.get("liquidity", 0) or 0) < 50:
                        continue
                    seen_ids.add(_eid)
                    _ev["_updown_direct"] = True
                    all_events.append(_ev)
                    _ud_fetched += 1
            if _ud_fetched > 0:
                log.info("[POLYMARKET-UPDOWN] Broad fallback fetch added %d Up/Down markets", _ud_fetched)
        except Exception as _be:
            log.debug("[POLYMARKET-UPDOWN] Broad fetch error: %s", _be)

    _now_utc = datetime.now(timezone.utc)

    for query in queries:
        params = {
            "search": query,
            "limit": 25,
            "order": "volume24hr",
            "ascending": "false",
            "active": "true",
        }

        resp = _retry_request("GET", f"{base_url}/events", params=params)
        if resp is None:
            log.warning("[POLYMARKET] Query '%s' failed after retries", query)
            continue

        try:
            raw = resp.json()
        except MemoryError:
            log.warning("[POLYMARKET] MemoryError parsing response for query '%s' — skipping", query)
            continue
        if isinstance(raw, dict):
            raw = raw.get("data", raw.get("events", []))

        added = 0
        for ev in raw:
            ev_id = ev.get("id", "")
            if not ev_id or ev_id in seen_ids:
                continue

            # ── Stale-market guard ─────────────────────────────────────────
            # Up/Down (5/15-min) markets expire within minutes — never filter them.
            # Standard macro events: skip if expiring within 24 hours.
            ev_title_lower = ev.get("title", "").lower()
            _is_updown = "up or down" in ev_title_lower or "updown" in ev_title_lower
            end_date_str = ev.get("endDate", ev.get("resolutionDate", ""))
            if end_date_str and not _is_updown:
                try:
                    from datetime import timedelta as _td
                    _clean = end_date_str.replace("Z", "+00:00")
                    _end_dt = datetime.fromisoformat(_clean)
                    if _end_dt.tzinfo is None:
                        from datetime import timezone as _tz
                        _end_dt = _end_dt.replace(tzinfo=_tz.utc)
                    if _end_dt < (_now_utc + _td(hours=24)):
                        log.debug("[POLYMARKET] Skipping near-expiry event '%s' (ends %s)",
                                  ev.get("title", "")[:50], end_date_str[:10])
                        continue
                except Exception:
                    pass  # unparseable date — keep the event
            elif end_date_str and _is_updown:
                # For Up/Down: skip only if already expired (< 90s remaining)
                try:
                    from datetime import timedelta as _td
                    _clean = end_date_str.replace("Z", "+00:00")
                    _end_dt = datetime.fromisoformat(_clean)
                    if _end_dt.tzinfo is None:
                        from datetime import timezone as _tz
                        _end_dt = _end_dt.replace(tzinfo=_tz.utc)
                    if _end_dt < (_now_utc + _td(seconds=90)):
                        log.debug("[POLYMARKET] Skipping expired Up/Down '%s'",
                                  ev.get("title", "")[:50])
                        continue
                except Exception:
                    pass

            # Skip zero-liquidity or dead markets
            ev_liquidity = float(ev.get("liquidity", 0) or 0)
            ev_volume24h = float(ev.get("volume24hr", ev.get("volume24h", 0)) or 0)
            if ev_liquidity == 0:
                log.debug("[POLYMARKET] Skipping zero-liquidity event '%s'",
                          ev.get("title", "")[:50])
                continue
            # Skip markets with no recent trading activity (stale / dead)
            if ev_liquidity < 50 and ev_volume24h < 100:
                log.debug("[POLYMARKET] Skipping low-activity event '%s' (liq=$%.0f vol=$%.0f)",
                          ev.get("title", "")[:50], ev_liquidity, ev_volume24h)
                continue

            seen_ids.add(ev_id)
            added += 1

            markets = []
            _raw_prices: list[float] = []   # un-sanitized prices for near-resolved check

            for _mkt_idx, mkt in enumerate(ev.get("markets", [])):
                # Outcome label: use outcomes[] array if available, else position-based
                _outcomes_list = mkt.get("outcomes") or []
                if _outcomes_list and _mkt_idx < len(_outcomes_list):
                    _outcome_label = str(_outcomes_list[_mkt_idx])
                else:
                    # Position-based: first = YES, second = NO (Polymarket convention)
                    _outcome_label = "Yes" if _mkt_idx == 0 else "No"

                # ── Raw price (before sanitization) ───────────────────────
                # Used ONLY for the near-resolved event filter below.
                # We derive it from outcomePrices[0] → lastTradePrice → price field.
                _raw_from_outcome = None
                _outcome_prices = mkt.get("outcomePrices") or []
                if _outcome_prices:
                    try:
                        _raw_from_outcome = float(_outcome_prices[0])
                    except (ValueError, TypeError):
                        _raw_from_outcome = None

                _raw_from_last = None
                try:
                    _lt = mkt.get("lastTradePrice")
                    _raw_from_last = float(_lt) if _lt is not None else None
                except (ValueError, TypeError):
                    _raw_from_last = None

                _raw_price_for_filter = (
                    _raw_from_last
                    if _raw_from_last is not None
                    else _raw_from_outcome
                    if _raw_from_outcome is not None
                    else float(mkt.get("price") or 0.5)
                )
                _raw_prices.append(_raw_price_for_filter)

                # ── Sanitized price for trading ───────────────────────────
                # Sanitize AFTER recording raw price.  Clamp near-resolved
                # prices to 0.5 so downstream code never sees 0.9999 / 0.0001.
                _yes_price = _raw_from_outcome
                if _yes_price is not None and (_yes_price >= 0.97 or _yes_price <= 0.03):
                    _yes_price = None  # only reject fully-resolved prices, not lopsided active ones

                _last = _raw_from_last
                if _last is not None and (_last >= 0.97 or _last <= 0.03):
                    _last = None  # only reject fully-resolved lastTradePrice

                _mkt_price = _last or _yes_price or float(mkt.get("price") or 0.5)
                if _mkt_price >= 0.97 or _mkt_price <= 0.03:
                    _mkt_price = 0.5  # final safety cap — only truly resolved markets

                markets.append({
                    "id": mkt.get("id", ""),
                    "conditionId": mkt.get("conditionId", ""),
                    "clobTokenIds": mkt.get("clobTokenIds") or [],  # YES/NO token IDs for CLOB price fetch
                    "title": mkt.get("question", mkt.get("title", "")),
                    "outcomeLabel": _outcome_label,
                    "price": _mkt_price,
                    "liquidity": float(mkt.get("liquidity") or 0),
                })

            ev_title = ev.get("title", "")
            ev_desc  = ev.get("description", "")

            # Skip events where ALL sub-markets are near-resolved (no tradeable edge).
            # Threshold 0.10/0.90 instead of 0.05/0.95 — more aggressive early filter.
            # IMPORTANT: use _raw_prices (before sanitization) — sanitized prices are
            # all 0.5, so the check would never trigger without this.
            if markets and _raw_prices:
                if all(p >= 0.90 or p <= 0.10 for p in _raw_prices):
                    log.debug(
                        "[POLYMARKET] Skipping near-resolved event '%s' (raw prices: %s)",
                        ev_title[:50], [f"{p:.4f}" for p in _raw_prices],
                    )
                    seen_ids.discard(ev_id)  # allow re-fetch if prices change
                    added -= 1
                    continue

            all_events.append({
                "id": ev_id,
                "title": ev_title,
                "description": ev_desc,
                "category": ev.get("category", ""),
                "market_category": _detect_polymarket_category(ev_title, ev_desc),
                "slug": ev.get("slug", ""),
                "resolutionDate": ev.get("endDate", ev.get("resolutionDate", "")),
                "liquidity": float(ev.get("liquidity", 0)),
                "volume24h": float(ev.get("volume24hr", ev.get("volume24h", 0))),
                "active": ev.get("active", True),
                "markets": markets,
                "market_type": _classify_polymarket_market_type(ev_title),
            })

        log.debug("[POLYMARKET] query='%s' → %d new events", query, added)

    log.info("[POLYMARKET] %d unique events across %d queries", len(all_events), len(queries))

    # Category breakdown summary
    if all_events:
        cat_counts: dict = {}
        for ev in all_events:
            c = ev.get("market_category", "OTHER")
            cat_counts[c] = cat_counts.get(c, 0) + 1
        log.info("[POLYMARKET-CATEGORIES] %s | total=%d", cat_counts, len(all_events))

    # Diagnostic: log first 5 events in full detail every cycle
    if all_events:
        log.info("[POLYMARKET-DIAGNOSTIC] First %d of %d events:", min(5, len(all_events)), len(all_events))
        for idx, ev in enumerate(all_events[:5], 1):
            desc = (ev.get("description") or "")[:80]
            log.info(
                "  [%d] Type=%-12s | Cat=%-8s | Liq=$%-10.0f | Vol=$%-10.0f | Mkt#=%d",
                idx, ev.get("market_type", "N/A"), ev.get("market_category", "OTHER"),
                ev.get("liquidity", 0), ev.get("volume24h", 0),
                len(ev.get("markets", [])),
            )
            log.info("      Title: %s", ev["title"][:90])
            if desc:
                log.info("      Desc:  %s", desc)
    else:
        log.warning("[POLYMARKET-DIAGNOSTIC] Zero events returned for queries: %s", queries)

    return all_events


# ---------------------------------------------------------------------------
# Real-time market price
# ---------------------------------------------------------------------------

def fetch_polymarket_book_imbalance(token_id: str) -> dict:
    """
    Fetch the Polymarket CLOB order book and compute YES/NO bid depth imbalance.

    Returns:
        {imbalance_ratio, yes_depth, no_depth, signal: "YES_HEAVY"|"NO_HEAVY"|"BALANCED"}
    A 3:1+ imbalance in our direction means retail crowd is heavily one-sided — the
    contrarian edge (or momentum confirmation) is exploitable.
    """
    try:
        resp = _retry_request("GET", "https://clob.polymarket.com/book", params={"token_id": token_id})
        if resp is None:
            return {}
        data = resp.json()
        bids = data.get("bids", [])
        asks = data.get("asks", [])

        # Sum USD depth from bids (YES buyers) and asks (NO sellers / YES sellers)
        yes_depth = sum(float(b.get("size", 0)) * float(b.get("price", 0)) for b in bids[:10])
        no_depth  = sum(float(a.get("size", 0)) * (1 - float(a.get("price", 0))) for a in asks[:10])

        total = yes_depth + no_depth
        if total < 10:
            return {}

        ratio = yes_depth / max(no_depth, 0.01)
        if ratio >= 3.0:
            signal = "YES_HEAVY"
        elif ratio <= 0.33:
            signal = "NO_HEAVY"
        else:
            signal = "BALANCED"

        log.info(
            "[BOOK-IMBALANCE] token=%s | YES_depth=$%.0f NO_depth=$%.0f ratio=%.2f signal=%s",
            token_id[:16], yes_depth, no_depth, ratio, signal,
        )
        return {
            "imbalance_ratio": round(ratio, 3),
            "yes_depth":       round(yes_depth, 2),
            "no_depth":        round(no_depth, 2),
            "signal":          signal,
        }
    except Exception as exc:
        log.debug("[BOOK-IMBALANCE] Failed: %s", exc)
        return {}


def get_event_current_price(market_id: str) -> Optional[dict]:
    """
    Fetch real-time orderbook price for a specific Polymarket market.

    Args:
        market_id: The CLOB market identifier.
    Returns:
        Price dict or None if the market is not found.
    """
    cfg = _get_config()
    clob_url = cfg["POLYMARKET_CLOB_API_URL"].rstrip("/")

    resp = _retry_request("GET", f"{clob_url}/markets/{market_id}")
    if resp is None:
        log.error("Could not fetch price for market %s", market_id)
        return None

    data = resp.json()
    if not data:
        return None

    # Normalise the CLOB market response
    tokens = data.get("tokens", [])
    yes_token = next((t for t in tokens if t.get("outcome", "").upper() == "YES"), tokens[0] if tokens else {})

    price = float(yes_token.get("price", data.get("lastTradePrice", 0.5)))
    bid = float(data.get("bestBid", price - 0.01))
    ask = float(data.get("bestAsk", price + 0.01))

    return {
        "market_id": market_id,
        "event_title": data.get("question", ""),
        "outcome": "Yes",
        "price": price,
        "bid": bid,
        "ask": ask,
        "liquidity": float(data.get("liquidityClob", data.get("liquidity", 0))),
        "volume24h": float(data.get("volume24hr", 0)),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def fetch_market_resolution(market_id: str) -> Optional[str]:
    """
    Determine the final resolution outcome for a Polymarket market.

    Checks CLOB token winner flags first, then falls back to Gamma API
    outcomePrices / closed / winner fields.

    Returns:
        "YES" | "NO" | "UP" | "DOWN" — or None if still unresolved.
    """
    if not market_id:
        return None

    cfg = _get_config()

    # ── Layer 1: CLOB token winner field ──────────────────────────────────────
    try:
        clob_url = cfg["POLYMARKET_CLOB_API_URL"].rstrip("/")
        resp = _retry_request("GET", f"{clob_url}/markets/{market_id}", timeout=6)
        if resp is not None:
            data = resp.json() or {}
            tokens = data.get("tokens", [])
            for tok in tokens:
                if tok.get("winner") is True:
                    outcome = tok.get("outcome", "").upper()
                    if outcome in ("YES", "NO", "UP", "DOWN"):
                        log.debug("[RESOLUTION] CLOB winner=%s for %s", outcome, market_id)
                        return outcome
            # Price-based heuristic when explicit winner flag absent
            for tok in tokens:
                p = float(tok.get("price", 0.5))
                outcome = tok.get("outcome", "").upper()
                if p >= 0.97 and outcome in ("YES", "UP"):
                    log.debug("[RESOLUTION] CLOB price heuristic YES/UP for %s", market_id)
                    return outcome
                if p <= 0.03 and outcome in ("NO", "DOWN"):
                    log.debug("[RESOLUTION] CLOB price heuristic NO/DOWN for %s", market_id)
                    return outcome
    except Exception as exc:
        log.debug("[RESOLUTION] CLOB check failed for %s: %s", market_id, exc)

    # ── Layer 2: Gamma API outcomePrices / closed / winner ───────────────────
    try:
        gamma_url = cfg["POLYMARKET_GAMMA_API_URL"].rstrip("/")
        resp = _retry_request("GET", f"{gamma_url}/markets", params={"conditionId": market_id}, timeout=6)
        if resp is not None:
            items = resp.json()
            mkt = items[0] if isinstance(items, list) and items else (items or {})
            # Explicit winner field
            winner = (mkt.get("winner") or mkt.get("winningOutcome") or "").upper()
            if winner in ("YES", "NO", "UP", "DOWN"):
                log.debug("[RESOLUTION] Gamma winner=%s for %s", winner, market_id)
                return winner
            # outcomePrices array
            op = mkt.get("outcomePrices")
            if isinstance(op, list) and len(op) >= 2:
                if float(op[0]) >= 0.97:
                    return "YES"
                if float(op[1]) >= 0.97:
                    return "NO"
            # closed flag with price check
            if mkt.get("closed") or mkt.get("resolved"):
                ltp = float(mkt.get("lastTradePrice", 0.5))
                if ltp >= 0.97:
                    return "YES"
                if ltp <= 0.03:
                    return "NO"
    except Exception as exc:
        log.debug("[RESOLUTION] Gamma check failed for %s: %s", market_id, exc)

    return None  # still open / unresolvable


# ── Polymarket CLOB Whale Trade Detection ──────────────────────────────────────

_whale_cache: dict = {}
_WHALE_CACHE_TTL = 120  # 2 minutes


def fetch_polymarket_whale_trades(
    market_id: str,
    min_size_usd: float = 500.0,
    lookback_seconds: int = 600,
) -> list[dict]:
    """
    Scan the last `lookback_seconds` of Polymarket CLOB trades for a market
    and return orders with notional value >= min_size_usd.

    A cluster of large buys on YES = whale accumulation → bullish signal.
    A cluster of large sells = smart money exiting → bearish signal.

    Returns list of dicts: {side, price, size, usd_value, timestamp}
    """
    global _whale_cache
    cache_key = f"{market_id}:{int(time.time() // _WHALE_CACHE_TTL)}"
    if cache_key in _whale_cache:
        return _whale_cache[cache_key]

    cfg = load_config()
    clob_url = cfg.get("POLYMARKET_CLOB_API_URL", "https://clob.polymarket.com").rstrip("/")
    cutoff_ts = time.time() - lookback_seconds

    try:
        resp = _retry_request(
            "GET",
            f"{clob_url}/trades",
            params={"market": market_id, "limit": 100},
            timeout=8,
        )
        if resp is None:
            return []

        trades_raw = resp if isinstance(resp, list) else resp.get("data", [])
        whale_trades = []

        for t in trades_raw:
            ts = float(t.get("timestamp") or t.get("created_at") or 0)
            # Convert millis to seconds if needed
            if ts > 1e12:
                ts /= 1000
            if ts < cutoff_ts:
                continue

            price = float(t.get("price", 0))
            size  = float(t.get("size", 0))
            if price <= 0 or size <= 0:
                continue

            usd_value = round(price * size, 2)
            if usd_value < min_size_usd:
                continue

            whale_trades.append({
                "side":      (t.get("side") or t.get("maker_side") or "UNKNOWN").upper(),
                "price":     round(price, 4),
                "size":      round(size, 2),
                "usd_value": usd_value,
                "timestamp": ts,
            })

        if whale_trades:
            log.info(
                "[WHALE] %d whale trades found for %s (min $%.0f, last %ds)",
                len(whale_trades), market_id, min_size_usd, lookback_seconds,
            )

        _whale_cache[cache_key] = whale_trades
        return whale_trades

    except Exception as exc:
        log.debug("[WHALE] Trade fetch failed for %s: %s", market_id, exc)
        return []


def get_whale_signal(market_id: str, min_usd: float = 500.0) -> Optional[str]:
    """
    Aggregate whale trades into a directional signal.

    Returns "BULLISH" if whales are net buying YES,
            "BEARISH" if whales are net buying NO,
            None if insufficient data or balanced.
    """
    trades = fetch_polymarket_whale_trades(market_id, min_size_usd=min_usd)
    if not trades:
        return None

    yes_usd = sum(t["usd_value"] for t in trades if t["side"] in ("BUY", "YES"))
    no_usd  = sum(t["usd_value"] for t in trades if t["side"] in ("SELL", "NO"))
    total   = yes_usd + no_usd

    if total < min_usd * 2:
        return None

    ratio = yes_usd / total
    if ratio >= 0.65:
        log.info("[WHALE] BULLISH signal for %s (YES $%.0f vs NO $%.0f, ratio=%.2f)",
                 market_id, yes_usd, no_usd, ratio)
        return "BULLISH"
    if ratio <= 0.35:
        log.info("[WHALE] BEARISH signal for %s (YES $%.0f vs NO $%.0f, ratio=%.2f)",
                 market_id, yes_usd, no_usd, ratio)
        return "BEARISH"
    return None


# ── Binance aggTrades buy/sell pressure ───────────────────────────────────────
_AGGTRADES_CACHE: dict = {}
_AGGTRADES_TTL = 30   # 30-second cache (high-frequency signal)


def get_binance_buysell_pressure(symbol: str, lookback_ms: int = 60000) -> Optional[dict]:
    """
    Compute buy vs. sell pressure from Binance aggTrades over the last `lookback_ms` milliseconds.

    Taker buy trades = market orders lifting the ask = bullish aggression.
    Taker sell trades = market orders hitting the bid = bearish aggression.

    Args:
        symbol:      Coin ticker, e.g. "BTC" → "BTCUSDT"
        lookback_ms: Lookback window in ms (default: last 60 seconds)
    Returns:
        Dict with:
            buy_ratio:  fraction of volume that was taker-buy (0–1)
            direction:  "BULLISH" | "BEARISH" | "NEUTRAL"
            buy_vol:    total buy volume (base asset)
            sell_vol:   total sell volume (base asset)
        Or None on failure.
    """
    import time as _t
    binance_sym = symbol.upper().replace("BITCOIN", "BTC").replace("ETHEREUM", "ETH") + "USDT"
    if binance_sym.endswith("USDTUSDT"):
        binance_sym = binance_sym[:-4]

    cache_key = (binance_sym, lookback_ms // 1000)
    now = _t.time()
    if cache_key in _AGGTRADES_CACHE:
        cached_time, cached_data = _AGGTRADES_CACHE[cache_key]
        if now - cached_time < _AGGTRADES_TTL:
            return cached_data

    try:
        import requests as _req
        end_ms = int(now * 1000)
        start_ms = end_ms - lookback_ms
        resp = _req.get(
            "https://api.binance.com/api/v3/aggTrades",
            params={"symbol": binance_sym, "startTime": start_ms, "endTime": end_ms, "limit": 1000},
            timeout=5,
        )
        if resp.status_code != 200:
            return None
        trades = resp.json()
        if not trades:
            return None

        buy_vol = sell_vol = 0.0
        for t in trades:
            qty = float(t.get("q", 0))
            is_buyer_maker = bool(t.get("m", False))
            # m=True means buyer is maker → taker is seller → SELL aggression
            # m=False means buyer is taker → BUY aggression
            if is_buyer_maker:
                sell_vol += qty
            else:
                buy_vol += qty

        total = buy_vol + sell_vol
        if total == 0:
            return None

        buy_ratio = buy_vol / total
        if buy_ratio >= 0.60:
            direction = "BULLISH"
        elif buy_ratio <= 0.40:
            direction = "BEARISH"
        else:
            direction = "NEUTRAL"

        result = {
            "symbol":    binance_sym,
            "buy_ratio": round(buy_ratio, 4),
            "direction": direction,
            "buy_vol":   round(buy_vol, 4),
            "sell_vol":  round(sell_vol, 4),
        }
        _AGGTRADES_CACHE[cache_key] = (now, result)
        log.debug("[AGGTRADES] %s: buy=%.1f%% %s", binance_sym, buy_ratio * 100, direction)
        return result

    except Exception as exc:
        log.debug("[AGGTRADES] Fetch failed for %s: %s", symbol, exc)
        return None
