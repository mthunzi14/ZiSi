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

_lock = asyncio.Lock()
# candle_bucket_key -> list of {asset, score, timeframe}
_candle_slots: dict[str, list[dict]] = {}
# asset -> last candle bucket key traded (in-memory, same session)
_asset_last_bucket: dict[str, str] = {}
# BTC: one fill per candle bucket across 5m and 15m (unless signals are independent)
_btc_bucket_trades: dict[str, dict] = {}

MAX_TRADES_PER_CANDLE_BUCKET = 2
BTC_ASSET = "BTC"


def candle_bucket_key(interval_minutes: int, now_ts: Optional[float] = None) -> str:
    """Shared bucket for all assets on the same N-minute candle."""
    ts = now_ts if now_ts is not None else time.time()
    interval = interval_minutes * 60
    start = int(ts) // interval * interval
    return f"{interval_minutes}m:{start}"


def _parse_asset_from_title(title: str) -> Optional[str]:
    m = re.search(r"\[(BTC|ETH|SOL|XRP)\]", title or "")
    return m.group(1) if m else None


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


def has_open_btc_exposure(open_positions: list) -> bool:
    return has_open_asset_exposure(open_positions, BTC_ASSET)


async def request_trade_slot(
    asset: str,
    timeframe: str,
    score: float,
    interval_minutes: int,
    open_positions: list,
    is_dual: bool = False,
    direction: str = "",
) -> tuple[bool, str]:
    """
    Returns (allowed, reason). Dual trades use separate bucket rules (always allow if asset clear).
    """
    asset = asset.upper()
    bucket = candle_bucket_key(interval_minutes)

    # ── Read regime limit BEFORE acquiring the lock to prevent blocking lock during I/O ──
    limit = MAX_TRADES_PER_CANDLE_BUCKET
    try:
        import os
        import json
        from pathlib import Path
        regime_path = Path(__file__).parent.parent.parent / "regime_status.json"
        if regime_path.exists():
            data = json.loads(regime_path.read_text(encoding="utf-8"))
            regime = data.get("regime", "NORMAL")
            if regime == "RANGE":
                limit = 4
            elif regime == "NORMAL":
                limit = 3
            elif regime == "VOLATILE":
                limit = 2
            elif regime == "SHOCK":
                limit = 1
    except Exception as e:
        log.warning("[GOVERNOR] Failed to read regime-based trade limit: %s", e)

    async with _lock:
        if has_open_asset_exposure(open_positions, asset):
            return False, f"open_position_{asset}"

        # Count open positions in the same direction to enforce correlation cap (isolated from DUAL hedges)
        if not is_dual:
            same_dir_count = 0
            for p in open_positions:
                p_dir = p.get("direction") or ""
                p_norm = "UP" if p_dir in ("YES", "UP") else ("DOWN" if p_dir in ("NO", "DOWN") else "")
                if p_norm == direction:
                    same_dir_count += 1
            if same_dir_count >= 4:
                log.warning(
                    "[GOVERNOR] Already %d active open %s positions — correlation cap blocking new %s %s trade",
                    same_dir_count, direction, asset, direction
                )
                return False, f"correlation_cap_{direction}"


        if asset == BTC_ASSET and bucket in _btc_bucket_trades:
            # We already have a BTC trade in this candle bucket! Let's check if we can dedup smartly
            existing = _btc_bucket_trades[bucket]
            # Allow concurrent BTC 5m and 15m if directions differ or confidence gap > 0.30
            if existing["timeframe"] != timeframe:
                if existing["direction"] != direction:
                    log.info("[GOVERNOR] Allowing concurrent BTC trade: opposite direction (%s vs %s)", direction, existing["direction"])
                elif abs(existing["score"] - score) > 0.30:
                    log.info("[GOVERNOR] Allowing concurrent BTC trade: high confidence gap (%.2f vs %.2f)", score, existing["score"])
                else:
                    return False, "btc_duplicate_candle"
            else:
                return False, "btc_duplicate_candle"

        if is_dual:
            # Dual arb: only enforce per-asset open check
            return True, "dual_ok"

        entries = _candle_slots.get(bucket, [])
        if len(entries) >= limit:
            assets_in = [e["asset"] for e in entries]
            if asset not in assets_in:
                return False, f"candle_cap_{len(entries)}/{limit}"

        if asset == BTC_ASSET and bucket in _btc_bucket_trades:
            # Recheck standard duplicate boundary
            existing = _btc_bucket_trades[bucket]
            if existing["timeframe"] == timeframe or (existing["direction"] == direction and abs(existing["score"] - score) <= 0.30):
                return False, "btc_duplicate_candle"

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
        if asset == BTC_ASSET:
            _btc_bucket_trades[bucket] = {
                "timeframe": timeframe,
                "direction": direction,
                "score": score,
            }
        _asset_last_bucket[asset] = bucket
        if not is_dual:
            _candle_slots.setdefault(bucket, []).append(
                {"asset": asset, "timeframe": timeframe, "score": score}
            )


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
