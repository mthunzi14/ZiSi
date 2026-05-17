"""
CryptoCompare News Aggregator — free tier, aggregates crypto news with sentiment scores.
Used as an additional signal layer for Polymarket + Kalshi matching.
API: https://min-api.cryptocompare.com/data/v2/news/
No key needed for basic access (100 req/hour free).
"""
import logging
import os
import time
from typing import List, Optional

import requests

log = logging.getLogger("zisi.data.cryptocompare")

CC_API   = "https://min-api.cryptocompare.com/data/v2"
CC_KEY   = os.getenv("CRYPTOCOMPARE_API_KEY", "")  # optional — free tier works without key

_news_cache: dict = {}
_NEWS_TTL = 300  # 5-minute cache


def get_latest_news(coins: List[str] = None, limit: int = 20) -> List[dict]:
    """
    Fetch latest crypto news from CryptoCompare.
    Returns list of articles with title, body, sentiment, source.
    """
    cache_key = ",".join(sorted(coins or [])) + f":{limit}"
    now = time.time()
    cached = _news_cache.get(cache_key, {})
    if cached.get("ts", 0) > now - _NEWS_TTL:
        return cached.get("articles", [])

    params: dict = {"limit": limit, "lang": "EN"}
    if coins:
        params["categories"] = ",".join(coins)
    if CC_KEY:
        params["api_key"] = CC_KEY

    try:
        r = requests.get(f"{CC_API}/news/", params=params, timeout=8)
        if r.status_code != 200:
            return []
        raw = r.json().get("Data", [])
        articles = []
        for item in raw:
            categories = str(item.get("categories", "")).lower()
            sentiment_str = str(item.get("imageurl", "")).lower()   # CryptoCompare doesn't score sentiment directly

            # Compute simple keyword-based sentiment
            body = (item.get("title", "") + " " + item.get("body", "")).lower()
            bullish_kw = ["surge", "rally", "breakout", "ath", "adoption", "approval", "bullish", "growth"]
            bearish_kw = ["crash", "collapse", "ban", "hack", "lawsuit", "bearish", "dump", "fear"]
            bull_hits = sum(1 for kw in bullish_kw if kw in body)
            bear_hits = sum(1 for kw in bearish_kw if kw in body)
            if bull_hits > bear_hits:
                sentiment = "BULLISH"
                score = min(0.9, 0.5 + bull_hits * 0.08)
            elif bear_hits > bull_hits:
                sentiment = "BEARISH"
                score = min(0.9, 0.5 + bear_hits * 0.08)
            else:
                sentiment = "NEUTRAL"
                score = 0.5

            articles.append({
                "id":         item.get("id", ""),
                "title":      item.get("title", ""),
                "body":       item.get("body", "")[:500],
                "source":     item.get("source", ""),
                "url":        item.get("url", ""),
                "published":  item.get("published_on", 0),
                "categories": categories,
                "sentiment":  sentiment,
                "score":      score,
            })

        _news_cache[cache_key] = {"articles": articles, "ts": now}
        log.info("[CRYPTOCOMPARE] Fetched %d articles | bull=%d bear=%d neutral=%d",
                 len(articles),
                 sum(1 for a in articles if a["sentiment"] == "BULLISH"),
                 sum(1 for a in articles if a["sentiment"] == "BEARISH"),
                 sum(1 for a in articles if a["sentiment"] == "NEUTRAL"))
        return articles

    except Exception as exc:
        log.debug("[CRYPTOCOMPARE] News fetch failed: %s", exc)
        return []


def get_news_sentiment_score(coins: List[str] = None) -> Optional[dict]:
    """
    Aggregate news sentiment over the last 20 articles.
    Returns: {sentiment: 'BULLISH'/'BEARISH'/'NEUTRAL', score: 0-1, article_count: int}
    """
    articles = get_latest_news(coins=coins, limit=20)
    if not articles:
        return None

    bull = sum(1 for a in articles if a["sentiment"] == "BULLISH")
    bear = sum(1 for a in articles if a["sentiment"] == "BEARISH")
    total = len(articles)

    if bull > bear * 1.3:
        sentiment = "BULLISH"
        score = round(bull / total, 3)
    elif bear > bull * 1.3:
        sentiment = "BEARISH"
        score = round(bear / total, 3)
    else:
        sentiment = "NEUTRAL"
        score = 0.5

    return {"sentiment": sentiment, "score": score, "article_count": total,
            "bullish_count": bull, "bearish_count": bear}
