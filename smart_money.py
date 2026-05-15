"""
smart_money.py - Polymarket Smart Money confirmation layer.

Fetches qualified whale wallets (>=60% win rate, >=1.5x profit factor)
from the Polymarket leaderboard and checks whether any of them hold
open positions on a market ZiSi is evaluating.

A Smart Money match adds a confidence boost of up to +0.15 to the
trade signal, acting as a 5th confirmation factor alongside:
  1. News sentiment (Gemini/FinBERT)
  2. Signal type (TYPE_A_HIGH etc.)
  3. Kelly position sizing
  4. Fear & Greed index
  5. Smart Money confirmation  ← this module

Usage:
    from smart_money import SmartMoneyFilter
    smf = SmartMoneyFilter()
    boost = smf.get_confirmation_boost(market_condition_id)
    # boost: 0.0 (no qualified whale) → 0.15 (multiple qualified whales)
"""

import logging
import time
from datetime import datetime, timezone
from typing import Optional

import requests

log = logging.getLogger("zisi.smart_money")

# Polymarket Data API
_DATA_API = "https://data-api.polymarket.com"

# Qualification thresholds (MrFadiAi/Polymarket-bot approach)
_MIN_WIN_RATE    = 0.60   # ≥60% historical win rate
_MIN_PROFIT_FACTOR = 1.5  # ≥1.5x (avg_win / avg_loss)
_MIN_TRADES      = 10     # must have at least 10 resolved trades
_MIN_POSITION_USD = 50    # ignore whale positions smaller than $50

# Cache: leaderboard refreshes at most every 60 minutes
_leaderboard_cache: list = []
_leaderboard_cache_time: Optional[datetime] = None
_LEADERBOARD_TTL_MINUTES = 60

# Per-market position cache (TTL: 5 min)
_position_cache: dict = {}  # market_id → (timestamp, result)
_POSITION_TTL_SECONDS = 300


def _safe_get(url: str, params: dict = None, timeout: int = 8) -> Optional[dict]:
    """Simple GET helper — returns parsed JSON or None on any failure."""
    try:
        resp = requests.get(url, params=params, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        log.debug("[SMART-MONEY] GET %s failed: %s", url, exc)
        return None


class SmartMoneyFilter:
    """
    Identifies qualified Smart Money wallets and checks their positions.
    """

    def __init__(self):
        pass

    # ------------------------------------------------------------------
    # Leaderboard fetch + qualification
    # ------------------------------------------------------------------

    def _fetch_leaderboard(self) -> list:
        """
        Fetch and qualify top Polymarket traders.
        Returns list of qualified wallet addresses (cached 60 min).
        """
        global _leaderboard_cache, _leaderboard_cache_time

        if _leaderboard_cache and _leaderboard_cache_time:
            age_min = (datetime.now(timezone.utc) - _leaderboard_cache_time).total_seconds() / 60
            if age_min < _LEADERBOARD_TTL_MINUTES:
                return _leaderboard_cache

        log.info("[SMART-MONEY] Fetching leaderboard...")
        data = _safe_get(f"{_DATA_API}/leaderboard", params={"limit": 100, "interval": "all"})
        if not data:
            log.warning("[SMART-MONEY] Leaderboard unavailable")
            return _leaderboard_cache  # return stale rather than empty

        entries = data if isinstance(data, list) else data.get("data", [])
        qualified = []

        for entry in entries:
            try:
                address      = entry.get("proxyWallet") or entry.get("address") or ""
                pnl          = float(entry.get("pnl", 0) or 0)
                volume       = float(entry.get("volume", 0) or 0)
                # Some endpoints return winRate directly, others require calculation
                win_rate     = float(entry.get("winRate", entry.get("win_rate", 0)) or 0)
                trade_count  = int(entry.get("tradesCount", entry.get("trades_count", 0)) or 0)

                if not address:
                    continue
                if trade_count < _MIN_TRADES:
                    continue
                if win_rate < _MIN_WIN_RATE:
                    continue

                # Profit factor: approximate from PnL and volume when not explicit
                profit_factor = float(entry.get("profitFactor", 0) or 0)
                if profit_factor == 0 and volume > 0:
                    # rough proxy: (pnl + volume/2) / (volume/2)
                    profit_factor = (pnl + volume * 0.5) / (volume * 0.5) if volume > 0 else 0

                if profit_factor < _MIN_PROFIT_FACTOR:
                    continue

                qualified.append({
                    "address": address,
                    "win_rate": win_rate,
                    "profit_factor": profit_factor,
                    "trade_count": trade_count,
                    "pnl": pnl,
                })
            except Exception:
                continue

        _leaderboard_cache = qualified
        _leaderboard_cache_time = datetime.now(timezone.utc)
        log.info(
            "[SMART-MONEY] Leaderboard: %d wallets total → %d qualified (≥%.0f%% WR, ≥%.1fx PF)",
            len(entries), len(qualified), _MIN_WIN_RATE * 100, _MIN_PROFIT_FACTOR,
        )
        return qualified

    # ------------------------------------------------------------------
    # Position check
    # ------------------------------------------------------------------

    def _get_market_positions(self, condition_id: str) -> list:
        """
        Fetch recent large positions on a specific Polymarket market.
        Returns list of {address, side, size_usd}.
        Cached per market for 5 minutes.
        """
        now_ts = time.time()
        if condition_id in _position_cache:
            cached_ts, cached_result = _position_cache[condition_id]
            if now_ts - cached_ts < _POSITION_TTL_SECONDS:
                return cached_result

        data = _safe_get(
            f"{_DATA_API}/positions",
            params={"market": condition_id, "sizeThreshold": _MIN_POSITION_USD},
        )
        if not data:
            return []

        positions = data if isinstance(data, list) else data.get("positions", data.get("data", []))
        result = []
        for p in positions:
            addr = p.get("proxyWallet") or p.get("userAddress") or p.get("user") or ""
            if not addr:
                continue
            result.append({
                "address": addr.lower(),
                "outcome": p.get("outcome", ""),
                "size_usd": float(p.get("size", p.get("currentValue", 0)) or 0),
            })

        _position_cache[condition_id] = (now_ts, result)
        return result

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_confirmation_boost(self, condition_id: str) -> float:
        """
        Check if qualified Smart Money wallets hold positions on this market.

        Returns:
            float 0.0–0.15:
              0.00  = no qualified wallet found
              0.05  = 1 qualified wallet
              0.10  = 2 qualified wallets
              0.15  = 3+ qualified wallets (max boost)
        """
        if not condition_id:
            return 0.0

        try:
            qualified = self._fetch_leaderboard()
            if not qualified:
                return 0.0

            qualified_addrs = {w["address"].lower() for w in qualified}
            positions = self._get_market_positions(condition_id)

            # Count how many qualified wallets hold positions
            whale_count = sum(
                1 for p in positions
                if p["address"] in qualified_addrs and p["size_usd"] >= _MIN_POSITION_USD
            )

            boost = min(0.15, whale_count * 0.05)

            if whale_count > 0:
                log.info(
                    "[SMART-MONEY] Market %s: %d qualified whale(s) → +%.2f confidence boost",
                    condition_id[:12], whale_count, boost,
                )
            else:
                log.debug("[SMART-MONEY] Market %s: no qualified whale positions", condition_id[:12])

            return boost

        except Exception as exc:
            log.debug("[SMART-MONEY] Confirmation check failed (non-fatal): %s", exc)
            return 0.0

    def get_market_summary(self, condition_id: str) -> dict:
        """
        Full summary for logging/dashboard: qualified wallets, positions, boost.
        """
        boost = self.get_confirmation_boost(condition_id)
        qualified = self._fetch_leaderboard()
        positions = self._get_market_positions(condition_id)
        q_addrs = {w["address"].lower() for w in qualified}

        whale_positions = [
            p for p in positions
            if p["address"] in q_addrs and p["size_usd"] >= _MIN_POSITION_USD
        ]

        return {
            "condition_id": condition_id,
            "qualified_wallets_total": len(qualified),
            "whale_positions_on_market": len(whale_positions),
            "confidence_boost": boost,
            "positions": whale_positions,
        }
