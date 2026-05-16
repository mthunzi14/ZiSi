"""
updown_trader.py - ZiSi BTC/ETH/SOL Up/Down Market Trader

Fetches the current active 5-minute and 15-minute Up/Down markets from Polymarket,
generates an independent directional signal using Binance price data + RSI/momentum,
and places paper trades — without relying on news sentiment.

This is ZiSi's high-frequency arm, complementing the shadow mode:
  - Shadow mode = copy PBot trades (passive)
  - UpDown trader = independent signal generation (active)

PBot's insight: queue priority + momentum signal on these markets creates a
repeatable edge. We implement the signal side here.
"""

import logging
import time
import re
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests

log = logging.getLogger("zisi.updown")

POLY_GAMMA_API  = "https://gamma-api.polymarket.com"
POLY_CLOB_API   = "https://clob.polymarket.com"
BINANCE_API     = "https://api.binance.com/api/v3"

# Kelly for independent Up/Down trades
UPDOWN_KELLY_FRACTION = 0.02   # 2% of balance (independent signal, higher confidence)
UPDOWN_MIN_USD = 1.00
UPDOWN_MAX_USD = 8.00

# Min liquidity for Up/Down market to be tradeable
UPDOWN_MIN_LIQUIDITY = 500.0

# Coins we trade
UPDOWN_COINS = ["BTC", "ETH", "SOL"]

# Max windows to trade per coin per cycle (3 windows = 5m, 5m+5m, 15m)
MAX_WINDOWS_PER_COIN = 3


def _fetch_binance_klines(symbol: str, interval: str = "1m", limit: int = 30) -> list:
    """Fetch recent OHLCV candles from Binance (no auth needed)."""
    try:
        r = requests.get(
            f"{BINANCE_API}/klines",
            params={"symbol": f"{symbol}USDT", "interval": interval, "limit": limit},
            timeout=8,
        )
        if r.status_code == 200:
            return r.json()  # [[open_time, open, high, low, close, volume, ...], ...]
    except Exception as exc:
        log.debug("[UPDOWN] Binance klines failed for %s: %s", symbol, exc)
    return []


def _compute_rsi(closes: list, period: int = 14) -> Optional[float]:
    """Compute RSI from a list of closing prices. Returns 0-100 or None."""
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        delta = closes[i] - closes[i - 1]
        gains.append(max(delta, 0))
        losses.append(max(-delta, 0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def _compute_momentum(closes: list, lookback: int = 5) -> float:
    """Return price change % over last `lookback` candles."""
    if len(closes) < lookback + 1:
        return 0.0
    return (closes[-1] - closes[-lookback]) / closes[-lookback] * 100


def _generate_direction_signal(coin: str) -> Optional[dict]:
    """
    Generate UP or DOWN signal for a coin using:
      - RSI (14-period, 1-min candles): >55 = UP bias, <45 = DOWN bias
      - Momentum (5-candle): same direction reinforcement
      - Funding rate: bearish funding → DOWN bias

    Returns {direction: 'UP'|'DOWN', confidence: 0.5-0.9} or None if no edge.
    """
    klines = _fetch_binance_klines(coin, interval="1m", limit=30)
    if len(klines) < 20:
        log.info("[UPDOWN] Insufficient kline data for %s (got %d candles)", coin, len(klines))
        return None

    closes = [float(k[4]) for k in klines]  # index 4 = close price
    rsi = _compute_rsi(closes, period=14)
    momentum = _compute_momentum(closes, lookback=5)
    short_mom = _compute_momentum(closes, lookback=3)

    if rsi is None:
        return None

    log.debug("[UPDOWN] %s RSI=%.1f | mom=%.3f%% | short_mom=%.3f%%", coin, rsi, momentum, short_mom)

    # RSI signals — wider thresholds to trade sideways BTC markets
    if rsi > 55:
        rsi_direction = "UP"
        rsi_strength  = min(0.85, 0.50 + (rsi - 55) / 45 * 0.35)
    elif rsi < 45:
        rsi_direction = "DOWN"
        rsi_strength  = min(0.85, 0.50 + (45 - rsi) / 45 * 0.35)
    else:
        # Deep neutral zone (45-55): fall back to pure momentum if strong enough
        if abs(momentum) > 0.15 and abs(short_mom) > 0.08:
            rsi_direction = "UP" if momentum > 0 else "DOWN"
            rsi_strength  = 0.52  # low-confidence momentum-only signal
        else:
            return None  # no RSI or momentum edge

    # Momentum confirmation
    mom_aligned = (momentum > 0 and rsi_direction == "UP") or \
                  (momentum < 0 and rsi_direction == "DOWN")
    short_mom_aligned = (short_mom > 0 and rsi_direction == "UP") or \
                        (short_mom < 0 and rsi_direction == "DOWN")

    if not mom_aligned:
        return None  # momentum contradicts RSI/direction — no trade

    confidence = rsi_strength
    if short_mom_aligned:
        confidence = min(0.88, confidence + 0.05)

    log.info(
        "[UPDOWN] %s | RSI=%.1f → %s | mom=%.3f%% | conf=%.2f",
        coin, rsi, rsi_direction, momentum, confidence,
    )
    return {
        "coin":       coin,
        "direction":  rsi_direction,
        "confidence": round(confidence, 4),
        "rsi":        rsi,
        "momentum":   round(momentum, 4),
    }


_COIN_FULL_NAMES = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana"}


def _fetch_active_updown_markets(coin: str) -> list:
    """
    Fetch active Up/Down markets for a coin using slug-based direct fetch.
    Slug format: {coin}-updown-{dur}m-{expiry_unix_ts}
    Text-search on Gamma API returns old/expired windows — slug fetch is precise.
    """
    coin_lower = coin.lower()
    now_ts = int(time.time())
    found_events: list = []
    seen_ids: set = set()

    def _parse_market(ev: dict, expiry_ts: int, dur_min: int = 5) -> Optional[dict]:
        liq = float(ev.get("liquidity", 0))
        if liq < UPDOWN_MIN_LIQUIDITY:
            return None
        markets = ev.get("markets", [])
        up_price, dn_price = 0.5, 0.5
        up_market, dn_market = None, None
        for mkt in markets:
            outcomes = mkt.get("outcomes", [])
            outcome_str = str(mkt.get("question", mkt.get("title", ""))).lower()
            price_val = float(mkt.get("lastTradePrice") or mkt.get("price") or 0.5)
            is_up = any(o.lower() in ("up", "yes") for o in outcomes) or \
                    "up" in outcome_str or "yes" in outcome_str
            if is_up and up_market is None:
                up_market = mkt
                up_price = price_val
            elif not is_up and dn_market is None:
                dn_market = mkt
                dn_price = price_val
        if up_market is None and markets:
            up_market = markets[0]
            up_price = float(up_market.get("lastTradePrice") or 0.5)
        if dn_market is None and len(markets) > 1:
            dn_market = markets[1]
            dn_price = float(dn_market.get("lastTradePrice") or 0.5)
        if up_price >= 0.90 or up_price <= 0.10:
            return None
        return {
            "id":          ev.get("id", ""),
            "title":       ev.get("title", ""),
            "slug":        ev.get("slug", ""),
            "expiry_ts":   expiry_ts,
            "duration_min": dur_min,
            "liquidity":   liq,
            "up_price":    up_price,
            "dn_price":    dn_price,
            "up_market":   up_market,
            "dn_market":   dn_market,
            "coin":        coin,
        }

    for dur_min in (5, 15):
        interval = dur_min * 60
        boundary = ((now_ts + interval) // interval) * interval
        for offset in range(4):
            expiry_ts = boundary + offset * interval
            if expiry_ts < now_ts + 30:
                continue
            slug = f"{coin_lower}-updown-{dur_min}m-{expiry_ts}"
            try:
                r = requests.get(
                    f"{POLY_GAMMA_API}/events",
                    params={"slug": slug},
                    timeout=10,
                )
                if r.status_code != 200:
                    continue
                raw = r.json()
                evs: list = []
                if isinstance(raw, dict) and "id" in raw:
                    evs = [raw]
                elif isinstance(raw, list):
                    evs = raw
                else:
                    evs = raw.get("data", raw.get("events", []))
                for ev in evs:
                    ev_id = ev.get("id", "")
                    if not ev_id or ev_id in seen_ids:
                        continue
                    parsed = _parse_market(ev, expiry_ts, dur_min)
                    if parsed:
                        seen_ids.add(ev_id)
                        found_events.append(parsed)
                        log.debug("[UPDOWN] Slug found: %s", ev.get("title", slug))
            except Exception as exc:
                log.debug("[UPDOWN] Slug error %s: %s", slug, exc)

    if not found_events:
        log.info("[UPDOWN] No slug results for %s", coin)

    found_events.sort(key=lambda e: e["expiry_ts"])
    return found_events


def run_updown_cycle(
    place_paper_trade_fn,
    get_balance_fn,
    count_open_trades_fn,
) -> int:
    """
    Run one Up/Down trading cycle across BTC, ETH, SOL.
    Returns number of trades placed.
    """
    trades_placed = 0

    for coin in UPDOWN_COINS:
        try:
            signal = _generate_direction_signal(coin)
            if signal is None:
                log.info("[UPDOWN] No signal for %s (RSI neutral + weak momentum)", coin)
                continue

            markets = _fetch_active_updown_markets(coin)
            if not markets:
                log.info("[UPDOWN] No active Up/Down markets found for %s", coin)
                continue

            direction  = signal["direction"]
            confidence = signal["confidence"]

            # Trade up to MAX_WINDOWS_PER_COIN different expiry windows per coin.
            # This multiplies trades: BTC UP signal → 5m window + 5m+5m window + 15m window.
            windows_traded = 0
            for best in markets[:MAX_WINDOWS_PER_COIN]:
                if count_open_trades_fn() >= 25:
                    log.info("[UPDOWN] Max open trades reached (25) — stopping %s", coin)
                    break

                if direction == "UP":
                    entry_price = best["up_price"]
                    market_obj  = best["up_market"]
                else:
                    entry_price = best["dn_price"]
                    market_obj  = best["dn_market"]

                if entry_price <= 0 or entry_price >= 1:
                    continue
                if market_obj is None:
                    continue

                balance = get_balance_fn()
                size = round(max(UPDOWN_MIN_USD, min(UPDOWN_MAX_USD, balance * UPDOWN_KELLY_FRACTION)), 2)

                market_id = market_obj.get("conditionId") or market_obj.get("id", "")
                event_id  = best["id"]
                secs_left = best["expiry_ts"] - int(time.time())
                dur_label = f"{secs_left // 60}m{secs_left % 60}s"

                order = place_paper_trade_fn(
                    event_id=event_id,
                    market_id=market_id,
                    amount_dollars=size,
                    direction=direction,
                    entry_price=entry_price,
                    event_title=f"[UPDOWN] {best['title']}",
                )

                if order:
                    trades_placed += 1
                    windows_traded += 1
                    log.info(
                        "[UPDOWN] ✅ %s %s @ %.3f | $%.2f | RSI=%.1f | %s remaining | conf=%.0f%% | win %d/%d",
                        direction, coin, entry_price, size,
                        signal["rsi"], dur_label, confidence * 100,
                        windows_traded, min(len(markets), MAX_WINDOWS_PER_COIN),
                    )
                    try:
                        from telegram_bot import send_alert as _tg
                        _tg(
                            f"📈 UpDown Trade — {coin}\n"
                            f"{best['title'][:60]}\n"
                            f"Direction: {direction} | Price: {entry_price:.3f}\n"
                            f"Size: ${size:.2f} | RSI: {signal['rsi']:.1f} | {dur_label} left"
                        )
                    except Exception:
                        pass

            if windows_traded == 0:
                log.info("[UPDOWN] %s signal found but no tradeable windows placed", coin)

        except Exception as exc:
            log.error("[UPDOWN] Error for %s: %s", coin, exc)

    return trades_placed
