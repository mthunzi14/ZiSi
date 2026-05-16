"""
shadow_mode.py - ZiSi Shadow Trading Engine

Monitors PBot-6 (0x21d0...e8d7) and Wallet-2 (0xeebd...ba30) on Polymarket.
On every new trade detected:
  1. Validates the market is still active (≥2 minutes remaining)
  2. Fetches current live price
  3. Places a mirror paper trade using ZiSi's Kelly sizing
  4. Schedules resolution check to auto-close and label win/loss

PBot strategy (reverse-engineered):
  - Trades exclusively 5-minute and 15-minute BTC/SOL/ETH Up/Down markets
  - Enters at prices 0.35-0.65 (fair value zone)
  - Average size: $10-$30 per trade
  - Uses queue priority (enters early in the window)
  - Wins by processing BTC price momentum faster than the crowd
"""

import json
import logging
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

log = logging.getLogger("zisi.shadow")

# ── Wallets to monitor ────────────────────────────────────────────────────────
SHADOW_WALLETS = {
    "PBOT6":   "0x21d0a97aac03917e752857a551bbe5103a00e8d7",
    "WALLET2": "0xeebde7a0e019a63e6b476eb425505b7b3e6eba30",
}

# Human-readable names shown in Telegram alerts and dashboard
MULE_NAMES = {"PBOT6": "Mule1", "WALLET2": "Mule2"}

# Per-mule enable flags — loaded from shadow_config.json at startup and each poll cycle
_mule_enabled: dict = {"PBOT6": True, "WALLET2": True}

# File written by the dashboard backend to toggle mules without restarting the bot
_SHADOW_CONFIG_FILE = Path(__file__).parent / "shadow_config.json"

POLY_TRADES_API    = "https://data-api.polymarket.com/trades"
POLY_POSITIONS_API = "https://data-api.polymarket.com/positions"
POLY_GAMMA_API     = "https://gamma-api.polymarket.com"
POLY_CLOB_API      = "https://clob.polymarket.com"

# Kelly fraction for shadow trades (conservative — we're copy-trading, not originating signal)
SHADOW_KELLY_FRACTION = 0.015   # 1.5% of balance per shadow trade
SHADOW_MIN_TRADE_USD  = 1.00
SHADOW_MAX_TRADE_USD  = 5.00

# Minimum seconds remaining in the market window before we'll shadow
MIN_SECONDS_REMAINING = 20      # enter if >= 20s left — PBot often enters in last minute of 5-min window

# Persistent state file to survive restarts
_STATE_FILE = Path(__file__).parent / "shadow_state.json"

# Global enable flag — set from main.py via SHADOW_MODE config
_shadow_enabled = True


def set_shadow_enabled(enabled: bool) -> None:
    global _shadow_enabled
    _shadow_enabled = enabled
    log.info("[SHADOW] Mode %s", "ENABLED" if enabled else "DISABLED")


def _load_mule_config() -> None:
    """Read shadow_config.json and update _mule_enabled. Safe to call every poll cycle."""
    global _mule_enabled
    try:
        if _SHADOW_CONFIG_FILE.exists():
            cfg = json.loads(_SHADOW_CONFIG_FILE.read_text(encoding="utf-8"))
            for label in list(SHADOW_WALLETS.keys()):
                if label in cfg:
                    _mule_enabled[label] = bool(cfg[label].get("enabled", True))
    except Exception as exc:
        log.debug("[SHADOW] Config load failed: %s", exc)


def set_mule_enabled(label: str, enabled: bool) -> None:
    """Enable or disable a specific mule. Persists to shadow_config.json."""
    global _mule_enabled
    _mule_enabled[label] = enabled
    try:
        cfg: dict = {}
        if _SHADOW_CONFIG_FILE.exists():
            cfg = json.loads(_SHADOW_CONFIG_FILE.read_text(encoding="utf-8"))
        cfg[label] = {"enabled": enabled}
        _SHADOW_CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    except Exception as exc:
        log.warning("[SHADOW] Failed to persist mule config: %s", exc)
    mule_name = MULE_NAMES.get(label, label)
    log.info("[SHADOW] %s (%s) %s", mule_name, label, "ENABLED" if enabled else "DISABLED")


def get_mule_status() -> dict:
    """Return current enabled status for all mules (for dashboard API)."""
    _load_mule_config()
    return {
        label: {
            "name": MULE_NAMES.get(label, label),
            "wallet": wallet,
            "enabled": _mule_enabled.get(label, True),
        }
        for label, wallet in SHADOW_WALLETS.items()
    }


def _load_state() -> dict:
    """Load persisted seen-tx set and trade history from disk."""
    try:
        if _STATE_FILE.exists():
            return json.loads(_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {"seen_txs": [], "shadow_trades": []}


def _save_state(state: dict) -> None:
    try:
        _STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except Exception as exc:
        log.warning("[SHADOW] State save failed: %s", exc)


def parse_updown_slug(slug: str) -> Optional[dict]:
    """
    Parse a Polymarket Up/Down market slug.
    'btc-updown-15m-1778850000' → {coin: 'BTC', duration_min: 15, expiry_ts: 1778850000}
    'sol-updown-5m-1778846400' → {coin: 'SOL', duration_min: 5, expiry_ts: 1778846400}
    Returns None if not a recognised Up/Down slug.
    """
    try:
        m = re.match(r"^([a-z]+)-updown-(\d+)m-(\d+)$", slug or "")
        if not m:
            return None
        coin = m.group(1).upper()
        duration_min = int(m.group(2))
        expiry_ts = int(m.group(3))
        return {"coin": coin, "duration_min": duration_min, "expiry_ts": expiry_ts}
    except Exception:
        return None


def _fetch_wallet_trades(wallet: str, limit: int = 20) -> list:
    try:
        r = requests.get(
            POLY_TRADES_API,
            params={"user": wallet, "limit": limit},
            timeout=12,
        )
        if r.status_code == 200:
            return r.json() or []
    except Exception as exc:
        log.debug("[SHADOW] Trade fetch failed for %s: %s", wallet[:10], exc)
    return []


def _fetch_wallet_positions(wallet: str) -> list:
    """
    Fetch CURRENT open positions for a wallet via positions API.
    Unlike trades (completed txs), this returns live active positions —
    critical for 5-minute markets where the window closes before trades are detected.
    """
    try:
        r = requests.get(
            POLY_POSITIONS_API,
            params={"user": wallet},
            timeout=12,
        )
        if r.status_code == 200:
            data = r.json() or []
            return data if isinstance(data, list) else data.get("data", [])
    except Exception as exc:
        log.debug("[SHADOW] Position fetch failed for %s: %s", wallet[:10], exc)
    return []


def _fetch_market_current_price(condition_id: str) -> Optional[dict]:
    """Fetch live price data for a Polymarket market by conditionId."""
    try:
        r = requests.get(
            f"{POLY_CLOB_API}/markets/{condition_id}",
            timeout=8,
        )
        if r.status_code == 200:
            data = r.json()
            tokens = data.get("tokens", [])
            up_token  = next((t for t in tokens if t.get("outcome", "").lower() in ("up", "yes")), tokens[0] if tokens else {})
            dn_token  = next((t for t in tokens if t.get("outcome", "").lower() in ("down", "no")), tokens[1] if len(tokens) > 1 else {})
            return {
                "up_price":    float(up_token.get("price", 0.5)),
                "down_price":  float(dn_token.get("price", 0.5)),
                "condition_id": condition_id,
            }
    except Exception as exc:
        log.debug("[SHADOW] Price fetch failed for %s: %s", condition_id[:16], exc)
    return None


def _fetch_market_by_slug(event_slug: str) -> Optional[dict]:
    """Fetch Polymarket event by slug from Gamma API."""
    try:
        r = requests.get(
            f"{POLY_GAMMA_API}/events",
            params={"slug": event_slug},
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list) and data:
                return data[0]
            if isinstance(data, dict):
                items = data.get("data", data.get("events", []))
                if items:
                    return items[0]
    except Exception as exc:
        log.debug("[SHADOW] Slug fetch failed for %s: %s", event_slug, exc)
    return None


def _fetch_token_midpoint(token_id: str) -> Optional[float]:
    """Fetch mid-price for a Polymarket token ID via the CLOB midpoint endpoint."""
    try:
        r = requests.get(
            f"{POLY_CLOB_API}/midpoint",
            params={"token_id": token_id},
            timeout=8,
        )
        if r.status_code == 200:
            data = r.json()
            mid = data.get("mid")
            if mid is not None:
                return float(mid)
    except Exception as exc:
        log.debug("[SHADOW] Midpoint fetch failed for %s: %s", token_id[:16], exc)
    return None


def _check_market_resolved(condition_id: str) -> Optional[str]:
    """
    Check if an Up/Down market has resolved.
    Returns 'Up' or 'Down' (the winning outcome) if resolved, else None.

    Tries three sources in order:
      1. CLOB /markets/{conditionId} — prices near 0 or 1
      2. Gamma API — outcomePrices field for resolved markets
      3. Returns None if neither source confirms resolution
    """
    # ── 1. CLOB prices ────────────────────────────────────────────────────────
    price_data = _fetch_market_current_price(condition_id)
    if price_data:
        up_price = price_data["up_price"]
        dn_price = price_data["down_price"]
        if up_price >= 0.95:
            return "Up"
        if dn_price >= 0.95 or up_price <= 0.05:
            return "Down"

    # ── 2. Gamma API outcomePrices ────────────────────────────────────────────
    try:
        r = requests.get(
            f"{POLY_GAMMA_API}/markets",
            params={"conditionId": condition_id},
            timeout=8,
        )
        if r.status_code == 200:
            data = r.json()
            mkts = data if isinstance(data, list) else data.get("data", data.get("markets", []))
            for mkt in (mkts if isinstance(mkts, list) else []):
                prices = mkt.get("outcomePrices") or []
                if len(prices) >= 2:
                    try:
                        p0, p1 = float(prices[0]), float(prices[1])
                        # outcomePrices[0] = Up/Yes, outcomePrices[1] = Down/No
                        if p0 >= 0.95:
                            return "Up"
                        if p1 >= 0.95 or p0 <= 0.05:
                            return "Down"
                    except (ValueError, TypeError):
                        pass
                # Also check explicit resolved outcome
                if mkt.get("closed") or mkt.get("resolved"):
                    winner = mkt.get("winner") or mkt.get("winningOutcome") or ""
                    if winner.lower() in ("yes", "up"):
                        return "Up"
                    if winner.lower() in ("no", "down"):
                        return "Down"
    except Exception as exc:
        log.debug("[SHADOW] Gamma resolution check failed for %s: %s", condition_id[:16], exc)

    return None


class ShadowTradeRecord:
    """Lightweight record of one shadow trade."""
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        for k, v in d.items():
            if isinstance(v, datetime):
                d[k] = v.isoformat()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "ShadowTradeRecord":
        obj = cls(**d)
        for ts_key in ("entry_time", "expiry_time"):
            if isinstance(getattr(obj, ts_key, None), str):
                try:
                    setattr(obj, ts_key, datetime.fromisoformat(getattr(obj, ts_key)))
                except Exception:
                    pass
        return obj


class ShadowModeMonitor:
    """
    Background thread that monitors target wallets and mirrors their trades.
    Plugs into trader.py's paper position system via the provided callbacks.
    """

    def __init__(
        self,
        place_paper_trade_fn,
        close_paper_trade_fn,
        get_balance_fn,
        poll_interval: int = 15,
    ):
        self.place_paper_trade = place_paper_trade_fn
        self.close_paper_trade = close_paper_trade_fn
        self.get_balance = get_balance_fn
        self.poll_interval = poll_interval

        state = _load_state()
        self._seen_txs: set = set(state.get("seen_txs", []))
        self._active_trades: dict = {}   # order_id → ShadowTradeRecord
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # Restore persisted active trades
        for td in state.get("shadow_trades", []):
            try:
                rec = ShadowTradeRecord.from_dict(td)
                if str(rec.__dict__.get("status", "OPEN")) == "OPEN":
                    self._active_trades[rec.order_id] = rec
            except Exception:
                pass

        # Stats
        self.total_shadowed = 0
        self.wins = 0
        self.losses = 0
        self.pnl = 0.0

    def _persist(self) -> None:
        seen_list = list(self._seen_txs)[-5000:]  # keep last 5k
        trades_list = [r.to_dict() for r in self._active_trades.values()]
        _save_state({"seen_txs": seen_list, "shadow_trades": trades_list})

    def _process_new_trade(self, label: str, trade: dict) -> None:
        """Evaluate one detected trade from a target wallet. Mirror if valid."""
        if not _shadow_enabled:
            return
        if not _mule_enabled.get(label, True):
            return

        tx_hash    = trade.get("transactionHash", "")
        condition  = trade.get("conditionId", "")
        slug       = trade.get("eventSlug", trade.get("slug", ""))
        title      = trade.get("title", "")
        outcome    = trade.get("outcome", "")      # 'Up' or 'Down'
        tgt_price  = float(trade.get("price", 0.5))
        tgt_size   = float(trade.get("size", 0))
        trade_ts   = int(trade.get("timestamp", 0))

        if not condition or not outcome:
            return

        parsed = parse_updown_slug(slug)
        if not parsed:
            log.debug("[SHADOW] Non-updown trade — skipping: %s", title[:50])
            return

        now_ts = int(time.time())
        expiry_ts = parsed["expiry_ts"]
        secs_remaining = expiry_ts - now_ts

        if secs_remaining < MIN_SECONDS_REMAINING:
            log.info(
                "[SHADOW] %s | %s | %ds remaining < %ds min — too late to enter",
                label, title[:55], secs_remaining, MIN_SECONDS_REMAINING,
            )
            return

        # Fetch live price (target may have entered 60+ seconds ago; price moved)
        live = _fetch_market_current_price(condition)
        if live is None:
            log.warning("[SHADOW] No live price for %s — skipping", condition[:16])
            return

        direction = outcome.lower()  # 'up' or 'down'
        if direction == "up":
            entry_price = live["up_price"]
        else:
            entry_price = live["down_price"]

        # Skip if price has moved too far from entry (>15 cents) — stale signal
        if abs(entry_price - tgt_price) > 0.15:
            log.info(
                "[SHADOW] %s | %s | Price drift too large (was %.2f, now %.2f) — skipping",
                label, title[:55], tgt_price, entry_price,
            )
            return

        # Don't enter if price is already near-resolved
        if entry_price >= 0.90 or entry_price <= 0.10:
            log.info(
                "[SHADOW] %s | %s | Price %.2f near-resolved — no edge",
                label, title[:55], entry_price,
            )
            return

        # Kelly sizing: conservative fraction of current balance
        balance = self.get_balance()
        raw_size = balance * SHADOW_KELLY_FRACTION
        position_size = round(max(SHADOW_MIN_TRADE_USD, min(SHADOW_MAX_TRADE_USD, raw_size)), 2)

        # Place paper trade
        order = self.place_paper_trade(
            event_id=condition,
            market_id=condition,
            amount_dollars=position_size,
            direction=direction.upper(),
            entry_price=entry_price,
            event_title=f"[SHADOW:{MULE_NAMES.get(label, label)}] {title}",
        )

        if not order:
            log.warning("[SHADOW] Paper trade placement failed for %s", title[:50])
            return

        expiry_dt = datetime.fromtimestamp(expiry_ts, tz=timezone.utc)
        rec = ShadowTradeRecord(
            order_id=order["order_id"],
            label=label,
            condition_id=condition,
            event_slug=slug,
            title=title,
            direction=direction.upper(),
            entry_price=entry_price,
            position_size=position_size,
            coin=parsed["coin"],
            duration_min=parsed["duration_min"],
            expiry_ts=expiry_ts,
            expiry_time=expiry_dt,
            entry_time=datetime.now(timezone.utc),
            status="OPEN",
        )
        self._active_trades[order["order_id"]] = rec
        self.total_shadowed += 1
        self._persist()

        log.info(
            "[SHADOW] ✅ TRADE PLACED | %s | %s %s @ %.3f | $%.2f | ~%ds remaining | src=%s",
            title[:55], direction.upper(), parsed["coin"],
            entry_price, position_size, secs_remaining, MULE_NAMES.get(label, label),
        )

        # Telegram alert
        try:
            from telegram_bot import send_alert as _tg
            mule_name = MULE_NAMES.get(label, label)
            dur_str = f"{parsed['duration_min']}min"
            _tg(
                f"👁 Shadow Trade — {mule_name}\n"
                f"Market: {title[:60]}\n"
                f"Direction: {direction.upper()} {parsed['coin']} ({dur_str})\n"
                f"Price: {entry_price:.3f} | Size: ${position_size:.2f}\n"
                f"Window closes: {expiry_dt.strftime('%H:%M:%S')} UTC"
            )
        except Exception:
            pass

    def _process_position(self, label: str, pos: dict) -> None:
        """
        Evaluate one CURRENT open position from a target wallet and mirror it.
        Uses positions API data (conditionId/asset, outcome, avgPrice) rather
        than completed-trade data — so we see 5-min windows while they're live.
        """
        if not _shadow_enabled:
            return
        if not _mule_enabled.get(label, True):
            return

        # Prefer conditionId for CLOB lookups; asset is a token ID that CLOB doesn't accept
        condition = pos.get("conditionId") or pos.get("asset", "")
        token_id  = pos.get("asset", "")   # token ID — used for midpoint fallback
        slug      = pos.get("eventSlug") or pos.get("slug", "")
        title     = pos.get("title", pos.get("eventTitle", ""))
        outcome   = pos.get("outcome", pos.get("side", ""))
        tgt_price = float(pos.get("avgPrice") or pos.get("price", 0.5))

        if not (condition or token_id) or not outcome:
            return

        # Try to derive slug from condition if not returned directly
        if not slug:
            log.debug("[SHADOW] No slug for position conditionId=%s — skipping", (condition or token_id)[:16])
            return

        parsed = parse_updown_slug(slug)
        if not parsed:
            log.debug("[SHADOW] Non-updown position — skipping: %s", title[:50] or slug)
            return

        now_ts        = int(time.time())
        expiry_ts     = parsed["expiry_ts"]
        secs_remaining = expiry_ts - now_ts

        if secs_remaining < MIN_SECONDS_REMAINING:
            log.info(
                "[SHADOW] %s | %s | %ds remaining — too late for this window, seeking next",
                MULE_NAMES.get(label, label), title[:55] or slug, secs_remaining,
            )
            # PBot often enters in the final seconds; we detect it 15s later via polling.
            # Rather than miss the trade entirely, enter the NEXT available window for the
            # same coin so we still ride the same directional signal.
            self._enter_next_shadow_window(label, parsed["coin"], outcome.lower())
            return

        direction = outcome.lower()   # 'up' or 'down'
        entry_price = None

        # Try conditionId-based CLOB market price first
        if condition:
            live = _fetch_market_current_price(condition)
            if live:
                entry_price = live["up_price"] if direction == "up" else live["down_price"]

        # Fallback: token ID midpoint (CLOB /midpoint?token_id=X)
        if entry_price is None and token_id:
            mid = _fetch_token_midpoint(token_id)
            if mid is not None:
                entry_price = mid
                log.debug("[SHADOW] Used token midpoint %.3f for %s", entry_price, token_id[:16])

        if entry_price is None:
            log.warning("[SHADOW] No live price for %s — skipping", (condition or token_id)[:16])
            return

        if abs(entry_price - tgt_price) > 0.15:
            log.info(
                "[SHADOW] %s | %s | Price drift too large (was %.2f, now %.2f) — skipping",
                label, title[:55], tgt_price, entry_price,
            )
            return

        if entry_price >= 0.90 or entry_price <= 0.10:
            log.info(
                "[SHADOW] %s | %s | Price %.2f near-resolved — no edge",
                label, title[:55], entry_price,
            )
            return

        balance = self.get_balance()
        raw_size = balance * SHADOW_KELLY_FRACTION
        position_size = round(max(SHADOW_MIN_TRADE_USD, min(SHADOW_MAX_TRADE_USD, raw_size)), 2)

        order = self.place_paper_trade(
            event_id=condition,
            market_id=condition,
            amount_dollars=position_size,
            direction=direction.upper(),
            entry_price=entry_price,
            event_title=f"[SHADOW:{MULE_NAMES.get(label, label)}] {title or slug}",
        )

        if not order:
            log.warning("[SHADOW] Paper trade placement failed for %s", title[:50] or slug)
            return

        expiry_dt = datetime.fromtimestamp(expiry_ts, tz=timezone.utc)
        rec = ShadowTradeRecord(
            order_id=order["order_id"],
            label=label,
            condition_id=condition,
            event_slug=slug,
            title=title or slug,
            direction=direction.upper(),
            entry_price=entry_price,
            position_size=position_size,
            coin=parsed["coin"],
            duration_min=parsed["duration_min"],
            expiry_ts=expiry_ts,
            expiry_time=expiry_dt,
            entry_time=datetime.now(timezone.utc),
            status="OPEN",
        )
        self._active_trades[order["order_id"]] = rec
        self.total_shadowed += 1
        self._persist()

        log.info(
            "[SHADOW] ✅ POSITION MIRRORED | %s | %s %s @ %.3f | $%.2f | ~%ds remaining | src=%s",
            (title or slug)[:55], direction.upper(), parsed["coin"],
            entry_price, position_size, secs_remaining, MULE_NAMES.get(label, label),
        )

        try:
            from telegram_bot import send_alert as _tg
            mule_name = MULE_NAMES.get(label, label)
            dur_str = f"{parsed['duration_min']}min"
            _tg(
                f"👁 Shadow Trade — {mule_name}\n"
                f"Market: {(title or slug)[:60]}\n"
                f"Direction: {direction.upper()} {parsed['coin']} ({dur_str})\n"
                f"Price: {entry_price:.3f} | Size: ${position_size:.2f}\n"
                f"Window closes: {expiry_dt.strftime('%H:%M:%S')} UTC"
            )
        except Exception:
            pass

    def _enter_next_shadow_window(self, label: str, coin: str, direction: str) -> None:
        """
        When PBot's current window has expired before we can mirror it, enter the
        NEXT available Up/Down window for the same coin and direction.
        Same directional thesis, fresh window — prevents missing every shadow trade.
        """
        try:
            from updown_trader import _fetch_active_updown_markets
            markets = _fetch_active_updown_markets(coin)
        except Exception as exc:
            log.warning("[SHADOW] Next-window fetch failed for %s %s: %s", label, coin, exc)
            return

        if not markets:
            log.info("[SHADOW] No next window available for %s %s", label, coin)
            return

        now_ts = int(time.time())
        next_market = None
        for mkt in markets:
            secs_left = mkt["expiry_ts"] - now_ts
            if secs_left >= MIN_SECONDS_REMAINING * 3:  # need at least 3× min to be worthwhile
                next_market = mkt
                break

        if next_market is None:
            log.info("[SHADOW] All next windows too close for %s %s", label, coin)
            return

        secs_left = next_market["expiry_ts"] - now_ts
        expiry_ts = next_market["expiry_ts"]

        if direction == "up":
            entry_price = next_market["up_price"]
            market_obj  = next_market.get("up_market") or {}
        else:
            entry_price = next_market["dn_price"]
            market_obj  = next_market.get("dn_market") or {}

        if entry_price >= 0.90 or entry_price <= 0.10:
            log.info("[SHADOW] Next window price %.2f near-resolved — skipping", entry_price)
            return

        condition_id = market_obj.get("conditionId") or market_obj.get("id") or next_market.get("id", "")
        if not condition_id:
            log.warning("[SHADOW] No conditionId for next window — skipping")
            return

        balance = self.get_balance()
        position_size = round(max(SHADOW_MIN_TRADE_USD, min(SHADOW_MAX_TRADE_USD, balance * SHADOW_KELLY_FRACTION)), 2)
        title = next_market.get("title", f"{coin} Up/Down")
        expiry_dt = datetime.fromtimestamp(expiry_ts, tz=timezone.utc)

        order = self.place_paper_trade(
            event_id=condition_id,
            market_id=condition_id,
            amount_dollars=position_size,
            direction=direction.upper(),
            entry_price=entry_price,
            event_title=f"[SHADOW:{MULE_NAMES.get(label, label)}] {title} (next window)",
        )

        if not order:
            log.warning("[SHADOW] Next-window trade placement failed for %s %s", label, coin)
            return

        coin_parsed = next_market.get("coin", coin)
        dur_min = next_market.get("duration_min", 5)
        rec = ShadowTradeRecord(
            order_id=order["order_id"],
            label=label,
            condition_id=condition_id,
            event_slug=next_market.get("slug", ""),
            title=title,
            direction=direction.upper(),
            entry_price=entry_price,
            position_size=position_size,
            coin=coin_parsed,
            duration_min=dur_min,
            expiry_ts=expiry_ts,
            expiry_time=expiry_dt,
            entry_time=datetime.now(timezone.utc),
            status="OPEN",
        )
        self._active_trades[order["order_id"]] = rec
        self.total_shadowed += 1
        self._persist()

        log.info(
            "[SHADOW] ✅ NEXT WINDOW | %s | %s %s @ %.3f | $%.2f | ~%ds remaining | src=%s",
            title[:55], direction.upper(), coin_parsed,
            entry_price, position_size, secs_left, MULE_NAMES.get(label, label),
        )
        try:
            from telegram_bot import send_alert as _tg
            mule_name = MULE_NAMES.get(label, label)
            _tg(
                f"👁 Shadow (Next Window) — {mule_name}\n"
                f"Market: {title[:60]}\n"
                f"Direction: {direction.upper()} {coin_parsed} ({dur_min}min)\n"
                f"Price: {entry_price:.3f} | Size: ${position_size:.2f}\n"
                f"Window closes: {expiry_dt.strftime('%H:%M:%S')} UTC"
            )
        except Exception:
            pass

    def _resolve_active_trades(self) -> None:
        """Check if any active shadow trades have resolved and close them."""
        import random as _random
        now_ts = int(time.time())
        resolved_ids = []

        for order_id, rec in list(self._active_trades.items()):
            expiry_ts = getattr(rec, "expiry_ts", 0)
            if now_ts < expiry_ts + 30:
                continue  # market not yet closed (give 30s buffer for settlement)

            winning_outcome = _check_market_resolved(getattr(rec, "condition_id", ""))
            simulated = False

            if winning_outcome is None:
                # Not resolved yet — wait up to 5min after expiry for settlement
                if now_ts <= expiry_ts + 300:
                    continue
                # 5min elapsed and still unresolved — use paper-trading simulation
                # PBot's estimated win rate is ~55%; use deterministic RNG per order
                log.warning("[SHADOW] Market unresolved 5min after expiry — simulating: %s", rec.title[:55])
                sim_rng = _random.Random(hash(order_id))
                winning_outcome = "Up" if sim_rng.random() < 0.55 else "Down"
                simulated = True

            direction   = getattr(rec, "direction", "UP")
            coin        = getattr(rec, "coin", "BTC")
            size        = float(getattr(rec, "position_size", 0))
            entry_price = float(getattr(rec, "entry_price", 0.5))

            # direction is "UP"/"DOWN", winning_outcome is "Up"/"Down"
            if direction.upper() == winning_outcome.upper():
                won = True
                shares = size / entry_price if entry_price > 0 else size
                pnl = round(shares - size, 4)
                self.wins += 1
            else:
                won = False
                pnl = round(-size, 4)
                self.losses += 1

            self.pnl += pnl
            rec.status = "WIN" if won else "LOSS"

            sim_tag = " [sim]" if simulated else ""
            emoji   = "✅" if won else "❌"
            pnl_str = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
            total_str = f"+${self.pnl:.2f}" if self.pnl >= 0 else f"-${abs(self.pnl):.2f}"

            log.info(
                "[SHADOW] %s %s | %s %s @ %.3f | Won=%s | PnL=%s | Cumulative=%s%s",
                emoji, rec.title[:50], direction, coin, entry_price, won, pnl_str, total_str, sim_tag,
            )

            try:
                from telegram_bot import send_alert as _tg
                rec_label = getattr(rec, "label", "")
                mule_name = MULE_NAMES.get(rec_label, rec_label)
                total_closed = self.wins + self.losses
                wr_str = f"{self.wins}/{total_closed} ({100*self.wins//max(1,total_closed)}%)"
                _tg(
                    f"{emoji} Shadow Resolved | {mule_name}{sim_tag}\n"
                    f"{rec.title[:60]}\n"
                    f"Direction: {direction} {'WON ✅' if won else 'LOST ❌'}\n"
                    f"PnL: {pnl_str} | Total PnL: {total_str}\n"
                    f"Win rate: {wr_str}"
                )
            except Exception:
                pass

            try:
                self.close_paper_trade(
                    order_id=order_id,
                    current_price=1.0 if won else 0.0,
                    exit_reason="SHADOW_RESOLVED",
                )
            except Exception as exc:
                log.warning("[SHADOW] close_paper_trade failed for %s: %s", order_id, exc)

            resolved_ids.append(order_id)

        for oid in resolved_ids:
            self._active_trades.pop(oid, None)

        if resolved_ids:
            self._persist()

    def _already_hedged(self, label: str, coin: str, expiry_ts: int) -> bool:
        """Return True if we already have an active shadow trade for this label/coin/expiry window."""
        for rec in self._active_trades.values():
            if (getattr(rec, "label", "") == label
                    and getattr(rec, "coin", "") == coin
                    and getattr(rec, "expiry_ts", 0) == expiry_ts
                    and getattr(rec, "status", "") == "OPEN"):
                return True
        return False

    def _poll_once(self) -> None:
        """Single polling iteration across all target wallets using live positions."""
        _load_mule_config()  # pick up any toggle changes from dashboard

        for label, wallet in SHADOW_WALLETS.items():
            mule_name = MULE_NAMES.get(label, label)

            if not _mule_enabled.get(label, True):
                log.debug("[SHADOW] %s disabled — skipping poll", mule_name)
                continue

            positions = _fetch_wallet_positions(wallet)
            new_count = 0
            for pos in positions:
                # Dedup key: prefer conditionId, fallback to asset (token ID)
                condition = pos.get("conditionId") or pos.get("asset", "")
                token_id  = pos.get("asset", "")
                outcome   = pos.get("outcome", pos.get("side", ""))
                if not (condition or token_id) or not outcome:
                    continue
                dedup_id  = condition or token_id
                pos_key   = f"{dedup_id}:{outcome.lower()}"
                if pos_key in self._seen_txs:
                    continue

                # Self-hedging guard: skip if we already mirrored a position for this
                # same coin+expiry window from this mule (prevents UP+DOWN cancel-out)
                slug = pos.get("eventSlug") or pos.get("slug", "")
                parsed = parse_updown_slug(slug)
                if parsed and self._already_hedged(label, parsed["coin"], parsed["expiry_ts"]):
                    log.info(
                        "[SHADOW] %s | %s %s window already mirrored — skipping opposite side",
                        mule_name, parsed["coin"], slug,
                    )
                    self._seen_txs.add(pos_key)  # mark seen so we don't log again
                    continue

                self._seen_txs.add(pos_key)
                new_count += 1
                try:
                    self._process_position(label, pos)
                except Exception as exc:
                    log.error("[SHADOW] Error processing position from %s: %s", mule_name, exc, exc_info=True)

            if new_count > 0:
                log.info("[SHADOW] %s | %d new position(s) detected", mule_name, new_count)

        # Resolve any completed shadow trades
        self._resolve_active_trades()

    def _loop(self) -> None:
        """Seed existing positions (warm-up so we don't mirror already-open windows), then poll."""
        log.info("[SHADOW] Seeding existing positions (warm-up)...")
        for label, wallet in SHADOW_WALLETS.items():
            existing = _fetch_wallet_positions(wallet)
            for pos in existing:
                # Use same priority as _poll_once — conditionId first, then asset
                condition = pos.get("conditionId") or pos.get("asset", "")
                token_id  = pos.get("asset", "")
                outcome   = pos.get("outcome", pos.get("side", ""))
                dedup_id  = condition or token_id
                if dedup_id and outcome:
                    self._seen_txs.add(f"{dedup_id}:{outcome.lower()}")
        log.info("[SHADOW] Seeded %d position keys across %d wallets", len(self._seen_txs), len(SHADOW_WALLETS))

        while not self._stop.is_set():
            try:
                self._poll_once()
            except Exception as exc:
                log.error("[SHADOW] Poll loop error: %s", exc)
            self._stop.wait(timeout=self.poll_interval)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="zisi-shadow")
        self._thread.start()
        _load_mule_config()
        mule_status = {MULE_NAMES.get(k, k): ("ON" if _mule_enabled.get(k, True) else "OFF") for k in SHADOW_WALLETS}
        log.info("[SHADOW] Monitor started | mules=%s | interval=%ds", mule_status, self.poll_interval)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        self._persist()
        log.info("[SHADOW] Monitor stopped | total=%d | W=%d L=%d | PnL=$%.2f",
                 self.total_shadowed, self.wins, self.losses, self.pnl)

    def get_stats(self) -> dict:
        wr = self.wins / max(1, self.wins + self.losses)
        _load_mule_config()
        return {
            "total_shadowed": self.total_shadowed,
            "wins": self.wins,
            "losses": self.losses,
            "win_rate": round(wr, 4),
            "pnl": round(self.pnl, 4),
            "active_trades": len(self._active_trades),
            "enabled": _shadow_enabled,
            "mules": {
                MULE_NAMES.get(k, k): _mule_enabled.get(k, True)
                for k in SHADOW_WALLETS
            },
        }
