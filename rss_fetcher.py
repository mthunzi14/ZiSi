"""
rss_fetcher.py — Free multi-source news harvester for ZiSi.

Pulls crypto/macro headlines from 6+ zero-cost sources that require no API keys:
  1. CryptoPanic (free public endpoint)
  2. Reddit r/CryptoCurrency (JSON API, no auth)
  3. Reddit r/Bitcoin (JSON API, no auth)
  4. CoinTelegraph RSS
  5. CoinDesk RSS
  6. Google News RSS (crypto + BTC + ETH queries)
  7. Decrypt.co RSS

Each source returns a list of article dicts:
  {title, url, published_at, source, coin_hint}

Call get_all_headlines(max_age_minutes=30) to get deduplicated headlines.
Then pass them to the sentiment pipeline as additional signal candidates.
"""

import logging
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import List, Optional, Dict
from xml.etree import ElementTree

import requests

log = logging.getLogger("zisi.rss")

_HEADERS = {
    "User-Agent": "ZiSiBot/2.0 (crypto trading research; contact@zisi.bot)",
    "Accept": "application/json, application/rss+xml, text/xml, */*",
}
_TIMEOUT = 8  # seconds per request

# ── Source registry ───────────────────────────────────────────────────────────

_RSS_SOURCES = [
    {
        "name": "CoinTelegraph",
        "url":  "https://cointelegraph.com/rss",
        "type": "rss",
    },
    {
        "name": "CoinDesk",
        "url":  "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "type": "rss",
    },
    {
        "name": "Decrypt",
        "url":  "https://decrypt.co/feed",
        "type": "rss",
    },
    {
        "name": "GoogleNews-Bitcoin",
        "url":  "https://news.google.com/rss/search?q=bitcoin+price+crypto&hl=en-US&gl=US&ceid=US:en",
        "type": "rss",
    },
    {
        "name": "GoogleNews-Ethereum",
        "url":  "https://news.google.com/rss/search?q=ethereum+crypto+market&hl=en-US&gl=US&ceid=US:en",
        "type": "rss",
    },
    {
        "name": "GoogleNews-Macro",
        "url":  "https://news.google.com/rss/search?q=federal+reserve+inflation+interest+rates&hl=en-US&gl=US&ceid=US:en",
        "type": "rss",
    },
]

_REDDIT_SOURCES = [
    {"name": "Reddit/CryptoCurrency", "subreddit": "CryptoCurrency"},
    {"name": "Reddit/Bitcoin",        "subreddit": "Bitcoin"},
    {"name": "Reddit/ethereum",       "subreddit": "ethereum"},
    {"name": "Reddit/ethfinance",     "subreddit": "ethfinance"},
]

# CryptoPanic free public API (no key needed — returns last 20 posts)
_CRYPTOPANIC_URL = "https://cryptopanic.com/api/free/v1/posts/?auth_token=free&currencies=BTC,ETH,SOL,XRP&kind=news"

# Coin hint patterns for tagging articles by coin
_COIN_PATTERNS: List[tuple] = [
    ("BTC",  re.compile(r'\b(bitcoin|btc)\b', re.I)),
    ("ETH",  re.compile(r'\b(ethereum|eth|ether)\b', re.I)),
    ("SOL",  re.compile(r'\b(solana|sol)\b', re.I)),
    ("XRP",  re.compile(r'\b(xrp|ripple)\b', re.I)),
    ("BNB",  re.compile(r'\b(bnb|binance)\b', re.I)),
    ("DOGE", re.compile(r'\b(dogecoin|doge)\b', re.I)),
]

# ── Result cache ──────────────────────────────────────────────────────────────
_cache: Dict = {"ts": 0.0, "data": []}
_CACHE_TTL = 90  # seconds


def _coin_hint(text: str) -> str:
    for coin, pat in _COIN_PATTERNS:
        if pat.search(text):
            return coin
    return "CRYPTO"


def _parse_rss_date(date_str: str) -> Optional[datetime]:
    if not date_str:
        return None
    try:
        return parsedate_to_datetime(date_str).astimezone(timezone.utc)
    except Exception:
        pass
    try:
        return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
    except Exception:
        return None


def _fetch_rss(source: dict, max_age_minutes: int) -> List[Dict]:
    """Parse an RSS feed and return headline dicts."""
    results = []
    cutoff = time.time() - max_age_minutes * 60
    try:
        resp = requests.get(source["url"], headers=_HEADERS, timeout=_TIMEOUT)
        resp.raise_for_status()
        # Strip namespaces to simplify parsing
        text = re.sub(r' xmlns[^=]*="[^"]*"', '', resp.text)
        root = ElementTree.fromstring(text.encode("utf-8"))

        for item in root.iter("item"):
            title_el = item.find("title")
            link_el  = item.find("link")
            date_el  = item.find("pubDate") or item.find("published")
            title = (title_el.text or "").strip() if title_el is not None else ""
            url   = (link_el.text or "").strip()  if link_el  is not None else ""
            pub_raw = (date_el.text or "").strip() if date_el  is not None else ""
            if not title:
                continue
            pub_dt = _parse_rss_date(pub_raw)
            if pub_dt and pub_dt.timestamp() < cutoff:
                continue
            results.append({
                "title":        title,
                "url":          url,
                "published_at": pub_dt.isoformat() if pub_dt else "",
                "source":       source["name"],
                "coin_hint":    _coin_hint(title),
            })
    except Exception as exc:
        log.debug("[RSS] %s fetch failed: %s", source["name"], exc)
    return results


def _fetch_reddit(source: dict, max_age_minutes: int) -> List[Dict]:
    """Fetch newest posts from a subreddit using the free JSON API."""
    results = []
    cutoff = time.time() - max_age_minutes * 60
    url = f"https://www.reddit.com/r/{source['subreddit']}/new.json?limit=15&sort=new"
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
        resp.raise_for_status()
        posts = resp.json().get("data", {}).get("children", [])
        for p in posts:
            d = p.get("data", {})
            title     = (d.get("title") or "").strip()
            url_link  = d.get("url") or d.get("permalink") or ""
            created   = float(d.get("created_utc", 0))
            score     = int(d.get("score", 0))
            if not title or created < cutoff:
                continue
            if score < 10:  # skip very low-engagement posts
                continue
            pub_dt = datetime.fromtimestamp(created, tz=timezone.utc)
            results.append({
                "title":        title,
                "url":          url_link,
                "published_at": pub_dt.isoformat(),
                "source":       source["name"],
                "coin_hint":    _coin_hint(title),
                "score":        score,
            })
    except Exception as exc:
        log.debug("[RSS] %s fetch failed: %s", source["name"], exc)
    return results


def _fetch_cryptopanic(max_age_minutes: int) -> List[Dict]:
    """Fetch from CryptoPanic free public API."""
    results = []
    cutoff = time.time() - max_age_minutes * 60
    try:
        resp = requests.get(_CRYPTOPANIC_URL, headers=_HEADERS, timeout=_TIMEOUT)
        resp.raise_for_status()
        posts = resp.json().get("results", [])
        for p in posts:
            title    = (p.get("title") or "").strip()
            url_link = p.get("url") or p.get("source", {}).get("url", "")
            created  = p.get("created_at", "")
            if not title:
                continue
            pub_dt = None
            try:
                pub_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                if pub_dt.timestamp() < cutoff:
                    continue
            except Exception:
                pass
            # Extract currencies mentioned
            currencies = [c.get("code", "") for c in (p.get("currencies") or [])]
            coin = currencies[0] if currencies else _coin_hint(title)
            results.append({
                "title":        title,
                "url":          url_link,
                "published_at": pub_dt.isoformat() if pub_dt else created,
                "source":       "CryptoPanic",
                "coin_hint":    coin,
                "votes":        p.get("votes", {}),
            })
    except Exception as exc:
        log.debug("[RSS] CryptoPanic fetch failed: %s", exc)
    return results


def get_all_headlines(max_age_minutes: int = 30) -> List[Dict]:
    """
    Fetch fresh headlines from all free sources.
    Results are deduplicated by title (fuzzy: first 60 chars) and cached for
    _CACHE_TTL seconds to avoid hammering sources on back-to-back calls.

    Returns list of {title, url, published_at, source, coin_hint} dicts,
    sorted newest first.
    """
    now = time.time()
    if now - _cache["ts"] < _CACHE_TTL and _cache["data"]:
        return _cache["data"]

    all_results: List[Dict] = []

    # RSS sources
    for src in _RSS_SOURCES:
        try:
            items = _fetch_rss(src, max_age_minutes)
            all_results.extend(items)
            if items:
                log.debug("[RSS] %s: %d items", src["name"], len(items))
        except Exception:
            pass

    # Reddit sources
    for src in _REDDIT_SOURCES:
        try:
            items = _fetch_reddit(src, max_age_minutes)
            all_results.extend(items)
            if items:
                log.debug("[RSS] %s: %d items", src["name"], len(items))
        except Exception:
            pass

    # CryptoPanic
    try:
        items = _fetch_cryptopanic(max_age_minutes)
        all_results.extend(items)
        if items:
            log.debug("[RSS] CryptoPanic: %d items", len(items))
    except Exception:
        pass

    # Deduplicate by title prefix (first 60 chars, lowercased)
    seen_titles: set = set()
    deduped = []
    for item in all_results:
        key = item["title"][:60].lower().strip()
        if key not in seen_titles:
            seen_titles.add(key)
            deduped.append(item)

    # Sort by published_at descending (newest first)
    def _sort_key(x):
        try:
            return datetime.fromisoformat(x["published_at"]).timestamp()
        except Exception:
            return 0.0

    deduped.sort(key=_sort_key, reverse=True)

    _cache["ts"]   = now
    _cache["data"] = deduped

    log.info(
        "[RSS] Harvested %d headlines from %d sources (%d after dedup)",
        len(all_results),
        len(_RSS_SOURCES) + len(_REDDIT_SOURCES) + 1,
        len(deduped),
    )
    return deduped


def headlines_to_text(headlines: List[Dict], coin: Optional[str] = None, max_count: int = 20) -> str:
    """
    Convert headlines to a plain text block for the sentiment pipeline.
    Optionally filter by coin_hint.
    """
    if coin:
        filtered = [h for h in headlines if h.get("coin_hint", "").upper() == coin.upper() or h.get("coin_hint", "").upper() == "CRYPTO"]
    else:
        filtered = headlines

    filtered = filtered[:max_count]
    lines = []
    for h in filtered:
        src  = h.get("source", "")
        title = h.get("title", "")
        lines.append(f"[{src}] {title}")
    return "\n".join(lines)
