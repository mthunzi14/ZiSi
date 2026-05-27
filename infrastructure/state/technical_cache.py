"""
technical_cache.py - In-memory technical data cache with Single-Flight request collapsing
"""
import asyncio
import time
import logging
from typing import Optional, Dict, Any, Callable, Awaitable

log = logging.getLogger("zisi.cache")

class TechnicalDataCache:
    """
    Thread-safe (asyncio event-loop safe) in-memory cache that collapses
    concurrent duplicate requests (single-flight pattern) and caches responses
    with a short Time-To-Live (TTL).
    """
    def __init__(self):
        self._cache: Dict[str, tuple[float, Any]] = {}  # key -> (timestamp, data)
        self._pending: Dict[str, asyncio.Future] = {}  # key -> Future for active request

    async def get(
        self, 
        key: str, 
        ttl_seconds: float, 
        fetch_func: Callable[[], Awaitable[Any]]
    ) -> Any:
        """
        Retrieve a value from the cache, or fetch it if not present or expired.
        If a fetch is already in flight for the same key, awaits that fetch.
        """
        now = time.time()

        # 1. Check cache hit
        if key in self._cache:
            ts, val = self._cache[key]
            if now - ts < ttl_seconds:
                log.debug("[CACHE] Hit for key: %s", key)
                return val

        # 2. Check if a request for this key is already in flight
        if key in self._pending:
            log.debug("[CACHE] Single-Flight collapse for key: %s", key)
            # Await the pending request's completion
            fut = self._pending[key]
            try:
                return await fut
            except Exception as exc:
                log.error("[CACHE] Pending request failed for key %s: %s", key, exc)
                # If the pending request fails, we fall back to try fetching again or raise
                pass

        # 3. No hit and no pending request: start a new fetch
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self._pending[key] = fut

        try:
            val = await fetch_func()
            self._cache[key] = (time.time(), val)
            fut.set_result(val)
            return val
        except Exception as exc:
            log.error("[CACHE] Fetch failed for key %s: %s", key, exc)
            fut.set_exception(exc)
            # Prevent "Future exception was never retrieved" warning when discarded
            try:
                fut.exception()
            except Exception:
                pass
            raise exc
        finally:
            self._pending.pop(key, None)

    def clear(self) -> None:
        """Clear all cached entries."""
        self._cache.clear()
        self._pending.clear()
