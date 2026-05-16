"""
updown_trader.py - ZiSi BTC/ETH/SOL Up/Down Market Trader

Multi-timeframe RSI confluence (1m + 3m + 5m) with volume confirmation gate.
Requires ≥2/3 timeframes to agree on direction before placing any trade.
Confidence-weighted position sizing: stronger confluence → larger position.
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

# Base Kelly fraction — scaled by confidence (0.5–0.9 → size 0.5×–1.4×)
UPDOWN_KELLY_BASE   = 0.018   # 1.8% baseline
UPDOWN_MIN_USD      = 1.00
UPDOWN_MAX_USD      = 10.00

# Volume gate: current candle volume must be >= this fraction of 20-period avg
VOLUME_GATE_RATIO = 0.65

# Min liquidity for Up/Down market to be tradeable
UPDOWN_MIN_LIQUIDITY = 500.0

# Coins we trade
UPDOWN_COINS = ["BTC", "ETH", "SOL"]

# Max windows to trade per coin per cycle
MAX_WINDOWS_PER_COIN = 3

# Per-coin consecutive loss tracking (resets to 0 on win)
_consecutive_losses: dict = {"BTC": 0, "ETH": 0, "SOL": 0}

# Auto-cooldown: skip a coin for N cycles after MAX_CONSEC_LOSSES consecutive losses
MAX_CONSEC_LOSSES = 5
_coin_cooldown_until: dict = {"BTC": 0, "ETH": 0, "SOL": 0}


def _fetch_binance_klines(symbol: str, interval: str = "1m", limit: int = 30) -> list:
    """Fetch recent OHLCV candles from Binance (no auth needed)."""
    try:
        r = requests.get(
            f"{BINANCE_API}/klines",
            params={"symbol": f"{symbol}USDT", "interval": interval, "limit": limit},
            timeout=8,
        )
        if r.status_code == 200:
            return r.json()
    except Exception as exc:
        log.debug("[UPDOWN] Binance klines failed for %s %s: %s", symbol, interval, exc)
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


def _signal_for_timeframe(klines: list, tf_label: str) -> Optional[dict]:
    """
    Extract a directional signal from one timeframe's klines.
    Returns {direction, strength, rsi, momentum} or None if no edge.
    """
    if len(klines) < 16:
        return None
    closes = [float(k[4]) for k in klines]
    rsi = _compute_rsi(closes, period=14)
    momentum = _compute_momentum(closes, lookback=5)
    if rsi is None:
        return None

    if rsi > 55:
        direction = "UP"
        strength  = min(0.85, 0.50 + (rsi - 55) / 45 * 0.35)
    elif rsi < 45:
        direction = "DOWN"
        strength  = min(0.85, 0.50 + (45 - rsi) / 45 * 0.35)
    else:
        if abs(momentum) > 0.15:
            direction = "UP" if momentum > 0 else "DOWN"
            strength  = 0.52
        else:
            return None  # no edge on this timeframe

    mom_aligned = (momentum > 0 and direction == "UP") or (momentum < 0 and direction == "DOWN")
    if not mom_aligned:
        return None

    return {"direction": direction, "strength": strength, "rsi": rsi, "momentum": momentum, "tf": tf_label}


def _generate_direction_signal(coin: str) -> Optional[dict]:
    """
    Multi-timeframe RSI signal (1m + 3m + 5m) with volume confirmation.
    Requires ≥2/3 timeframes to agree on direction.
    Returns {direction, confidence, rsi, momentum, timeframes_agree} or None.
    """
    klines_1m = _fetch_binance_klines(coin, interval="1m", limit=30)
    klines_3m = _fetch_binance_klines(coin, interval="3m", limit=25)
    klines_5m = _fetch_binance_klines(coin, interval="5m", limit=20)

    if len(klines_1m) < 20:
        log.info("[UPDOWN] Insufficient 1m klines for %s (got %d)", coin, len(klines_1m))
        return None

    # ── Volume gate (1m candles) ────────────────────────────────────────────
    volumes = [float(k[5]) for k in klines_1m]
    avg_vol = sum(volumes[:-1]) / max(1, len(volumes) - 1)
    cur_vol = volumes[-1]
    if avg_vol > 0 and cur_vol < VOLUME_GATE_RATIO * avg_vol:
        log.info(
            "[UPDOWN] %s volume gate failed — current %.0f < %.0f%% of avg %.0f",
            coin, cur_vol, VOLUME_GATE_RATIO * 100, avg_vol,
        )
        return None

    # ── Consecutive-loss cooldown ────────────────────────────────────────────
    now_ts = int(time.time())
    if _coin_cooldown_until.get(coin, 0) > now_ts:
        remaining = (_coin_cooldown_until[coin] - now_ts) // 60
        log.info("[UPDOWN] %s in cooldown for %dm (consecutive losses)", coin, remaining)
        return None

    # ── Per-timeframe signals ────────────────────────────────────────────────
    tf_signals = []
    for klines, label in [(klines_1m, "1m"), (klines_3m, "3m"), (klines_5m, "5m")]:
        sig = _signal_for_timeframe(klines, label)
        if sig:
            tf_signals.append(sig)

    if len(tf_signals) < 2:
        log.info("[UPDOWN] %s MTF confluence failed (%d/3 timeframes signal)", coin, len(tf_signals))
        return None

    # ── Vote ────────────────────────────────────────────────────────────────
    up_sigs = [s for s in tf_signals if s["direction"] == "UP"]
    dn_sigs = [s for s in tf_signals if s["direction"] == "DOWN"]

    if len(up_sigs) == len(dn_sigs):
        # Tie — use 1m as tiebreaker
        tf1 = next((s for s in tf_signals if s["tf"] == "1m"), None)
        if tf1 is None:
            return None
        direction = tf1["direction"]
        agreeing  = [s for s in tf_signals if s["direction"] == direction]
    else:
        direction = "UP" if len(up_sigs) > len(dn_sigs) else "DOWN"
        agreeing  = up_sigs if direction == "UP" else dn_sigs

    if len(agreeing) < 2:
        return None

    # ── Confidence ──────────────────────────────────────────────────────────
    confidence = sum(s["strength"] for s in agreeing) / len(agreeing)
    if len(agreeing) == 3:
        confidence = min(0.92, confidence + 0.04)  # all-3 bonus

    avg_rsi = sum(s["rsi"] for s in agreeing) / len(agreeing)
    avg_mom = sum(s["momentum"] for s in agreeing) / len(agreeing)

    log.info(
        "[UPDOWN] %s | %d/3 agree → %s | avg RSI=%.1f | mom=%.3f%% | vol ✓ | conf=%.2f",
        coin, len(agreeing), direction, avg_rsi, avg_mom, confidence,
    )
    return {
        "coin":             coin,
        "direction":        direction,
        "confidence":       round(confidence, 4),
        "rsi":              round(avg_rsi, 1),
        "momentum":         round(avg_mom, 4),
        "timeframes_agree": len(agreeing),
    }


_COIN_FULL_NAMES = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana"}


def _fetch_active_updown_markets(coin: str) -> list:
    """
    Fetch active Up/Down markets for a coin using slug-based direct fetch.
    Slug format: {coin}-updown-{dur}m-{expiry_unix_ts}
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
            "id":           ev.get("id", ""),
            "title":        ev.get("title", ""),
            "slug":         ev.get("slug", ""),
            "expiry_ts":    expiry_ts,
            "duration_min": dur_min,
            "liquidity":    liq,
            "up_price":     up_price,
            "dn_price":     dn_price,
            "up_market":    up_market,
            "dn_market":    dn_market,
            "coin":         coin,
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
                        log.debug("[UPDOWN] Market found: %s", ev.get("title", slug))
            except Exception as exc:
                log.debug("[UPDOWN] Slug error %s: %s", slug, exc)

    if not found_events:
        log.info("[UPDOWN] No active markets for %s", coin)

    found_events.sort(key=lambda e: e["expiry_ts"])
    return found_events


def record_updown_result(coin: str, won: bool) -> None:
    """
    Call after each UP/DOWN trade resolves to track consecutive losses.
    Triggers cooldown if MAX_CONSEC_LOSSES hit.
    """
    global _consecutive_losses, _coin_cooldown_until
    if won:
        _consecutive_losses[coin] = 0
    else:
        _consecutive_losses[coin] = _consecutive_losses.get(coin, 0) + 1
        if _consecutive_losses[coin] >= MAX_CONSEC_LOSSES:
            # 30-min cooldown
            _coin_cooldown_until[coin] = int(time.time()) + 30 * 60
            log.warning(
                "[UPDOWN] %s hit %d consecutive losses — cooling down 30 min",
                coin, MAX_CONSEC_LOSSES,
            )
            _consecutive_losses[coin] = 0


def run_updown_cycle(
    place_paper_trade_fn,
    get_balance_fn,
    count_open_trades_fn,
) -> int:
    """
    Run one Up/Down trading cycle across BTC, ETH, SOL.
    Uses multi-timeframe RSI with volume gate and confidence-weighted sizing.
    Returns number of trades placed.
    """
    trades_placed = 0

    for coin in UPDOWN_COINS:
        try:
            signal = _generate_direction_signal(coin)
            if signal is None:
                continue

            markets = _fetch_active_updown_markets(coin)
            if not markets:
                log.info("[UPDOWN] No active markets for %s", coin)
                continue

            direction  = signal["direction"]
            confidence = signal["confidence"]
            tf_agree   = signal["timeframes_agree"]

            # Confidence-weighted position sizing
            # 0.52 conf = 0.8× base, 0.70 conf = 1.0× base, 0.90 conf = 1.4× base
            size_multiplier = max(0.6, min(1.5, 0.5 + confidence * 1.1))

            windows_traded = 0
            for best in markets[:MAX_WINDOWS_PER_COIN]:
                if count_open_trades_fn() >= 25:
                    log.info("[UPDOWN] Max open trades (25) — stopping %s", coin)
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
                raw_size = balance * UPDOWN_KELLY_BASE * size_multiplier
                size = round(max(UPDOWN_MIN_USD, min(UPDOWN_MAX_USD, raw_size)), 2)

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
                        "[UPDOWN] ✅ %s %s @ %.3f | $%.2f | RSI=%.1f | %d/3 TF | %s left | conf=%.0f%%",
                        direction, coin, entry_price, size,
                        signal["rsi"], tf_agree, dur_label, confidence * 100,
                    )
                    try:
                        from telegram_bot import send_alert as _tg
                        _tg(
                            f"📈 UpDown — {coin}\n"
                            f"{best['title'][:60]}\n"
                            f"{direction} | RSI {signal['rsi']:.0f} | {tf_agree}/3 TF | {dur_label} left\n"
                            f"${size:.2f} @ {entry_price:.3f} | conf {confidence:.0%}"
                        )
                    except Exception:
                        pass

            if windows_traded == 0:
                log.info("[UPDOWN] %s signal found but no windows placed", coin)

        except Exception as exc:
            log.error("[UPDOWN] Error for %s: %s", coin, exc)

    return trades_placed
