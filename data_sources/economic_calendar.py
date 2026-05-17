"""
Economic Calendar — free scraping of high-impact events from TradingEconomics.
Before Fed meetings, CPI, NFP, PPI — Kalshi gold.
Pre-identifies relevant events 2h+ before release so the bot can prepare positions.
"""
import logging
import time
from datetime import datetime, timezone, timedelta
from typing import List, Optional

import requests

log = logging.getLogger("zisi.data.econ_calendar")

# Public TradingEconomics API (free tier, no key required for basic access)
TE_API = "https://api.tradingeconomics.com"
TE_KEY = ""  # optional — can add TRADINGECONOMICS_API_KEY from .env if available

# High-impact events that matter for Kalshi markets
HIGH_IMPACT_EVENTS = {
    "interest rate decision", "fomc", "fed funds rate",
    "cpi", "consumer price index", "inflation",
    "nfp", "non-farm payroll", "jobs", "unemployment",
    "gdp", "gross domestic product",
    "ppi", "producer price index",
    "pce", "personal consumption",
    "retail sales",
    "jobs report",
}

_event_cache: dict = {}
_CACHE_TTL = 600  # 10-minute cache


def get_upcoming_events(hours_ahead: float = 4.0) -> List[dict]:
    """
    Return high-impact economic events in the next N hours.
    Uses TradingEconomics free calendar endpoint.
    Returns list of {event, country, date, importance, hours_until}
    """
    cache_key = f"events_{int(hours_ahead)}"
    now = time.time()
    cached = _event_cache.get(cache_key, {})
    if cached.get("ts", 0) > now - _CACHE_TTL:
        return cached.get("events", [])

    events_found = []
    try:
        # TE free calendar
        url = "https://api.tradingeconomics.com/calendar"
        params = {"country": "united states", "importance": "3"}  # 3 = high impact
        if TE_KEY:
            params["c"] = TE_KEY

        r = requests.get(url, params=params, timeout=10)
        if r.status_code == 200:
            data = r.json()
            now_dt  = datetime.now(timezone.utc)
            cutoff  = now_dt + timedelta(hours=hours_ahead)

            for item in (data if isinstance(data, list) else []):
                date_str = item.get("Date", item.get("date", ""))
                if not date_str:
                    continue
                try:
                    event_dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                except Exception:
                    continue

                if event_dt < now_dt or event_dt > cutoff:
                    continue

                name = str(item.get("Event", item.get("event", ""))).lower()
                importance = int(item.get("Importance", 0) or 0)

                if importance >= 3 or any(kw in name for kw in HIGH_IMPACT_EVENTS):
                    hours_until = (event_dt - now_dt).total_seconds() / 3600
                    events_found.append({
                        "event":       item.get("Event", ""),
                        "country":     item.get("Country", "US"),
                        "date":        date_str,
                        "importance":  importance,
                        "hours_until": round(hours_until, 2),
                        "actual":      item.get("Actual"),
                        "previous":    item.get("Previous"),
                        "forecast":    item.get("Forecast"),
                    })

    except Exception as exc:
        log.debug("[ECON-CAL] TradingEconomics fetch failed: %s", exc)

    # Fallback: hardcoded known high-impact events check (day-of)
    if not events_found:
        events_found = _get_known_recurring_events(hours_ahead)

    events_found.sort(key=lambda e: e["hours_until"])
    _event_cache[cache_key] = {"events": events_found, "ts": now}

    if events_found:
        log.info("[ECON-CAL] %d upcoming high-impact events in next %.0fh: %s",
                 len(events_found), hours_ahead,
                 [e["event"][:30] for e in events_found[:3]])

    return events_found


def _get_known_recurring_events(hours_ahead: float) -> List[dict]:
    """Fallback: identify if today is a known high-impact day using date patterns."""
    now = datetime.now(timezone.utc)
    events = []

    # FOMC meetings: typically 8 per year, decisions at 2PM ET (18:00 UTC) on day 2
    # CPI: usually 2nd or 3rd Tuesday of month, 8:30AM ET (12:30 UTC)
    # NFP: first Friday of month, 8:30AM ET (12:30 UTC)
    # We can't hardcode exact dates but we can detect "release hour" windows
    hour = now.hour
    weekday = now.weekday()  # 0=Mon, 4=Fri

    # NFP: first Friday of month around 8:30AM ET (13:30 UTC)
    if weekday == 4 and now.day <= 7 and 13 <= hour <= 15:
        events.append({"event": "NFP (est)", "country": "US", "date": now.isoformat(),
                       "importance": 3, "hours_until": 0.5})

    # CPI: check if 12:30 UTC is within hours_ahead window on typical release days
    if 10 <= now.day <= 16 and weekday in (1, 2) and abs(hour - 12) <= hours_ahead:
        events.append({"event": "CPI (est)", "country": "US", "date": now.isoformat(),
                       "importance": 3, "hours_until": max(0, 12.5 - hour)})

    return events


def is_high_impact_window(look_ahead_hours: float = 2.0) -> bool:
    """Return True if a high-impact economic event is coming up within look_ahead_hours."""
    events = get_upcoming_events(hours_ahead=look_ahead_hours)
    return len(events) > 0


def get_kalshi_event_boost(kalshi_title: str) -> float:
    """
    If a Kalshi market title matches an upcoming high-impact event, boost confidence.
    Returns 1.20× if event is within 2 hours, 1.10× if within 4 hours, 1.0 otherwise.
    """
    title_lower = kalshi_title.lower()
    events = get_upcoming_events(hours_ahead=4.0)

    for event in events:
        event_name = event.get("event", "").lower()
        # Check if Kalshi market relates to this economic event
        matched = any(
            kw in title_lower for kw in [event_name[:10]] + list(HIGH_IMPACT_EVENTS)
            if kw and kw in event_name
        )
        if matched:
            h = event.get("hours_until", 99)
            if h <= 2:
                log.info("[ECON-CAL] Event match '%s' within %.1fh → 1.20× boost for: %s",
                         event["event"][:30], h, kalshi_title[:50])
                return 1.20
            elif h <= 4:
                return 1.10

    return 1.0
