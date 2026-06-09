"""
sentiment_daemon.py — Fear & Greed Index macro filter (Kakushadze & Serur §18.3)

Polls alternative.me/fng/ every 4 hours and caches the result. When the market
is at extremes (euphoria > 80 or panic < 20), reduce all trade sizes by 40% to
protect against regime-flip risk.

Usage:
    from core.analytics.sentiment_daemon import sentiment_filter
    mult = sentiment_filter.get_size_multiplier()  # 0.60 at extremes, 1.0 otherwise
    bet_usd *= mult

Run as a background task from app/main.py:
    asyncio.create_task(sentiment_filter.start_poll_loop())
"""
import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone

log = logging.getLogger("zisi.sentiment_daemon")

_STATE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "sentiment_state.json",
)
_POLL_INTERVAL = 4 * 3600   # 4 hours
_EXTREME_HIGH = int(os.getenv("FNG_EXTREME_HIGH", "80"))
_EXTREME_LOW  = int(os.getenv("FNG_EXTREME_LOW",  "20"))
_EXTREME_MULT = float(os.getenv("FNG_EXTREME_MULT", "0.60"))
_FNG_API = "https://api.alternative.me/fng/?limit=1"


class SentimentFilter:
    def __init__(self) -> None:
        self._value: int = 50       # neutral default
        self._label: str = "Neutral"
        self._ts: float = 0.0
        self._load()

    def get_fear_greed_index(self) -> int:
        return self._value

    def get_size_multiplier(self) -> float:
        if self._value >= _EXTREME_HIGH:
            log.debug("[SENTIMENT] F&G=%d (Extreme Greed ≥%d) → size ×%.2f", self._value, _EXTREME_HIGH, _EXTREME_MULT)
            return _EXTREME_MULT
        if self._value <= _EXTREME_LOW:
            log.debug("[SENTIMENT] F&G=%d (Extreme Fear ≤%d) → size ×%.2f", self._value, _EXTREME_LOW, _EXTREME_MULT)
            return _EXTREME_MULT
        return 1.0

    def get_status(self) -> dict:
        return {
            "value": self._value,
            "label": self._label,
            "mult": self.get_size_multiplier(),
            "age_h": round((time.time() - self._ts) / 3600, 1) if self._ts else None,
        }

    async def start_poll_loop(self) -> None:
        log.info("[SENTIMENT] Fear & Greed daemon starting (poll every 4h)")
        while True:
            try:
                await self._fetch()
            except Exception as e:
                log.warning("[SENTIMENT] Poll error: %s", e)
            await asyncio.sleep(_POLL_INTERVAL)

    async def _fetch(self) -> None:
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.get(_FNG_API, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        data = await resp.json(content_type=None)
                        entry = data.get("data", [{}])[0]
                        self._value = int(entry.get("value", 50))
                        self._label = entry.get("value_classification", "Neutral")
                        self._ts = time.time()
                        self._save()
                        log.info(
                            "[SENTIMENT] F&G updated: %d (%s) → size mult=%.2f",
                            self._value, self._label, self.get_size_multiplier(),
                        )
        except Exception as e:
            log.debug("[SENTIMENT] Fetch failed: %s", e)

    def _save(self) -> None:
        try:
            with open(_STATE_PATH, "w", encoding="utf-8") as f:
                json.dump({
                    "value": self._value,
                    "label": self._label,
                    "ts": self._ts,
                    "updated": datetime.now(timezone.utc).isoformat(),
                }, f)
        except Exception:
            pass

    def _load(self) -> None:
        try:
            if os.path.exists(_STATE_PATH):
                with open(_STATE_PATH, "r", encoding="utf-8") as f:
                    d = json.load(f)
                self._value = int(d.get("value", 50))
                self._label = d.get("label", "Neutral")
                self._ts = float(d.get("ts", 0.0))
        except Exception:
            pass


# Singleton
sentiment_filter = SentimentFilter()
