"""
session_governor.py - Cross-task trade coordination (correlation cap, BTC dedup).

Limits stacked directional exposure on the same candle bucket and prevents
duplicate BTC 5m/15m fills in the same window.
"""
import asyncio
import logging
import re
import time
from typing import Optional

log = logging.getLogger("zisi.governor")

CORRELATED_GROUPS = [frozenset({'BTC', 'ETH'}), frozenset({'SOL', 'XRP'})]

_lock = asyncio.Lock()
# candle_bucket_key -> list of {asset, score, timeframe}
_candle_slots: dict[str, list[dict]] = {}
# asset -> last candle bucket key traded (in-memory, same session)
_asset_last_bucket: dict[str, str] = {}
# BTC: one fill per candle bucket across 5m and 15m (unless signals are independent)
_btc_bucket_trades: dict[str, dict] = {}

# Latency Arb tracking to prevent concurrent double-fills during asynchronous order execution
_lat_arb_in_flight: set[tuple[str, str]] = set()
_lat_arb_cooldowns: dict[tuple[str, str], float] = {}
_recent_requests: list[tuple[float, str]] = []

MAX_TRADES_PER_CANDLE_BUCKET = 5  # Bonereaper-mode: up to 5 simultaneous entries per candle
BTC_ASSET = "BTC"


def candle_bucket_key(interval_minutes: int, now_ts: Optional[float] = None) -> str:
    """Shared bucket for all assets on the same N-minute candle."""
    ts = now_ts if now_ts is not None else time.time()
    interval = interval_minutes * 60
    start = int(ts) // interval * interval
    return f"{interval_minutes}m:{start}"


def _parse_asset_from_title(title: str) -> Optional[str]:
    m = re.search(r"\[(BTC|ETH|SOL|XRP|DOGE|ADA|AVAX|SUI)\]", title or "", re.IGNORECASE)
    return m.group(1).upper() if m else None


def has_open_asset_exposure(open_positions: list, asset: str) -> bool:
    """True if any open position is for this asset (any timeframe)."""
    asset = asset.upper()
    for p in open_positions:
        t = p.get("event_title") or ""
        a = (p.get("asset") or _parse_asset_from_title(t) or "").upper()
        if a == asset:
            return True
        if f"[{asset}]" in t.upper():
            return True
    return False


def has_open_asset_tf_exposure(open_positions: list, asset: str, timeframe: str) -> bool:
    """True if an open position exists for this exact (asset, timeframe) pair."""
    asset = asset.upper()
    tf_tag = f"[{timeframe.upper()}]"
    asset_tag = f"[{asset}]"
    for p in open_positions:
        t = (p.get("event_title") or "").upper()
        p_asset = (p.get("asset") or _parse_asset_from_title(t) or "").upper()
        p_tf = (p.get("timeframe") or "").lower()
        has_asset = (p_asset == asset) or (asset_tag in t)
        has_tf = (p_tf == timeframe.lower()) or (tf_tag in t)
        if has_asset and has_tf:
            return True
    return False


def has_open_btc_exposure(open_positions: list) -> bool:
    return has_open_asset_exposure(open_positions, BTC_ASSET)


def has_opposing_correlated_exposure(open_positions: list, asset: str, direction: str) -> bool:
    """True if a correlated asset has an opposing direction open position.
    BTC+ETH are correlated. SOL+XRP are correlated. Blocks self-hedging.
    """
    asset_upper = asset.upper()
    sig_is_up = direction == "UP"

    # Find the correlation group for this asset
    asset_group = None
    for group in CORRELATED_GROUPS:
        if asset_upper in group:
            asset_group = group
            break

    if not asset_group:
        return False  # No correlation group found, no block

    # Check all open positions for opposing direction in correlated assets
    for p in open_positions:
        t = p.get('event_title') or ''
        p_asset = (p.get('asset') or _parse_asset_from_title(t) or '').upper()

        # Skip if not in the same correlation group, or if it's the same asset
        if p_asset not in asset_group or p_asset == asset_upper:
            continue

        p_dir = p.get('direction') or ''
        p_is_up = p_dir in ('YES', 'UP')

        if p_is_up != sig_is_up:
            return True  # Correlated asset has opposing direction

    return False


async def request_trade_slot(
    asset: str,
    timeframe: str,
    score: float,
    interval_minutes: int,
    open_positions: list,
    is_dual: bool = False,
    direction: str = "",
) -> tuple[bool, str]:
    asset_upper = asset.upper()
    
    # 1. 10s simultaneous entry cap: delay altcoins before checking 10s request list
    is_altcoin = asset_upper in ("SOL", "XRP", "DOGE")
    if is_altcoin:
        await asyncio.sleep(0.5)
        
    async with _lock:
        now = time.time()
        
        # Clean up old requests (> 10s old)
        global _recent_requests
        _recent_requests = [(t, a) for t, a in _recent_requests if now - t <= 10.0]
        
        # Record current request
        _recent_requests.append((now, asset_upper))
        
        # Count distinct assets in the 10s window
        distinct_assets = {a for _, a in _recent_requests}
        if len(distinct_assets) > 3 and is_altcoin:
            log.info("[GOVERNOR] Drop altcoin %s due to excess correlation (%d assets in 10s: %s)",
                     asset_upper, len(distinct_assets), distinct_assets)
            return False, "excess_correlation_cap"

        # 2. Enforce exposure ceilings
        from config import get_config
        max_total_open = get_config("MAX_TOTAL_OPEN", 8)
        max_open_per_asset = get_config("MAX_OPEN_PER_ASSET", 2)
        
        total_open = len(open_positions)
        asset_open = 0
        for p in open_positions:
            t = p.get("event_title") or ""
            p_asset = (p.get("asset") or _parse_asset_from_title(t) or "").upper()
            if p_asset == asset_upper:
                asset_open += 1
                
        if total_open >= max_total_open:
            log.info("[GOVERNOR] Total exposure ceiling reached: %d/%d open positions", total_open, max_total_open)
            return False, f"total_positions_limit_reached_{total_open}"
            
        if asset_open >= max_open_per_asset:
            log.info("[GOVERNOR] Asset exposure ceiling reached for %s: %d/%d open positions", asset_upper, asset_open, max_open_per_asset)
            return False, f"asset_positions_limit_reached_{asset_open}"

        # 3. Latency Arb duplicate & cooldown tracking
        if is_dual:
            key = (asset_upper, timeframe)
            if key in _lat_arb_in_flight:
                return False, f"lat_inflight_{asset_upper}_{timeframe}"
            cooldown_until = _lat_arb_cooldowns.get(key, 0.0)
            if now < cooldown_until:
                return False, f"lat_cooldown_{asset_upper}_{timeframe}"
            _lat_arb_in_flight.add(key)
            return True, "dual_ok"

        # 4. Standard opposing exposure checks
        if has_open_asset_exposure(open_positions, asset_upper):
            # Check for opposing direction
            sig_is_up = direction == "UP"
            for p in open_positions:
                t = p.get("event_title") or ""
                p_asset = (p.get("asset") or _parse_asset_from_title(t) or "").upper()
                if p_asset != asset_upper:
                    continue
                p_dir = p.get("direction") or ""
                p_is_up = p_dir in ("YES", "UP")
                if p_is_up != sig_is_up:
                    log.info("[GOVERNOR] Blocked opposing exposure on same asset %s", asset_upper)
                    return False, f"opposing_exposure_{asset_upper}"

        # 5. Correlated asset checks (blocks self-hedging)
        if has_opposing_correlated_exposure(open_positions, asset_upper, direction):
            log.info("[GOVERNOR] Blocked opposing correlated exposure for %s", asset_upper)
            return False, f"correlated_opposing_{asset_upper}"

        # 5.5 BTC duplicate check
        bucket = candle_bucket_key(interval_minutes)
        if asset_upper == BTC_ASSET and bucket in _btc_bucket_trades:
            existing = _btc_bucket_trades[bucket]
            if existing["timeframe"] == timeframe:
                return False, "btc_duplicate_candle"
            log.info("[GOVERNOR] BTC concurrent %s+%s allowed (Bone Reaper Mode)",
                     existing["timeframe"], timeframe)

        # 6. Pre-commit slot to _candle_slots
        entries = _candle_slots.setdefault(bucket, [])
        entries.append({"asset": asset_upper, "timeframe": timeframe, "score": score, "_pre": True})
        
        return True, "ok"


async def commit_trade_slot(
    asset: str,
    timeframe: str,
    score: float,
    interval_minutes: int,
    is_dual: bool = False,
    direction: str = "",
) -> None:
    """Call after a successful place_order."""
    asset = asset.upper()
    bucket = candle_bucket_key(interval_minutes)

    async with _lock:
        if is_dual:
            key = (asset, timeframe)
            _lat_arb_in_flight.discard(key)
            _lat_arb_cooldowns[key] = time.time() + 30.0

        if asset == BTC_ASSET:
            _btc_bucket_trades[bucket] = {
                "timeframe": timeframe,
                "direction": direction,
                "score": score,
            }
        _asset_last_bucket[asset] = bucket
        if not is_dual:
            # Promote the pre-committed entry (added atomically in request_trade_slot) to committed.
            entries = _candle_slots.setdefault(bucket, [])
            for i, e in enumerate(entries):
                if e.get("asset") == asset and e.get("timeframe") == timeframe and e.get("_pre"):
                    entries[i] = {"asset": asset, "timeframe": timeframe, "score": score}
                    break
            else:
                entries.append({"asset": asset, "timeframe": timeframe, "score": score})


async def prune_old_buckets(max_age_seconds: int = 3600) -> None:
    """Drop in-memory bucket tracking older than max_age."""
    now = time.time()
    async with _lock:
        stale = []
        for key in list(_candle_slots.keys()):
            try:
                start = int(key.split(":")[1])
                if now - start > max_age_seconds:
                    stale.append(key)
            except (IndexError, ValueError):
                stale.append(key)
        for key in stale:
            _candle_slots.pop(key, None)
        old_btc = {k for k in _btc_bucket_trades if now - int(k.split(":")[1]) > max_age_seconds}
        for k in old_btc:
            _btc_bucket_trades.pop(k, None)


async def cancel_trade_slot(asset: str, timeframe: str) -> None:
    """Discard (asset, timeframe) from in-flight tracker and remove pre-committed slot on failure."""
    asset = asset.upper()
    async with _lock:
        _lat_arb_in_flight.discard((asset, timeframe))
        for bkt_key, entries in list(_candle_slots.items()):
            _candle_slots[bkt_key] = [
                e for e in entries
                if not (e.get("asset") == asset and e.get("timeframe") == timeframe and e.get("_pre"))
            ]

