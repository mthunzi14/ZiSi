"""
Kalshi Crypto Up/Down Scanner.
Scans Kalshi for BTC/ETH daily and weekly direction markets.
Applies 1h/4h RSI+momentum signals (same algorithm as Polymarket updown_trader
but adapted for Kalshi's longer timeframes).

Kalshi crypto direction series:
  KXBTC — Bitcoin daily close range markets (e.g. "Will BTC close above $X today?")
  KXETH — Ethereum daily close range markets
  KXBTCM — Bitcoin monthly close
  KXBTCD — Bitcoin daily direction

These are fundamentally different from 5-minute Polymarket Up/Down markets:
  - Kalshi: daily/weekly resolution — use 1h/4h candles, fundamentals matter more
  - Polymarket: 5-minute resolution — use 1m/3m/5m candles, momentum dominates
"""
import logging
import time
from typing import List, Dict, Optional

import requests

log = logging.getLogger("zisi.kalshi.updown")

BINANCE_API = "https://api.binance.com/api/v3"

# Kalshi crypto direction market series tickers
KALSHI_CRYPTO_SERIES = ["KXBTC", "KXETH", "KXBTCM", "KXETHM", "KXBTCD", "KXETHD", "KXBTCW"]

# Min/max hours to expiry for Kalshi Up/Down positions
KALSHI_UPDOWN_MIN_HOURS = 0.5    # 30 min minimum
KALSHI_UPDOWN_MAX_HOURS = 48.0   # Up to 2 days

# RSI thresholds for 1h/4h timeframes (less noisy than 1m/5m)
RSI_UP_THRESHOLD_1H   = 58  # more relaxed — 1h RSI has more predictive value
RSI_DOWN_THRESHOLD_1H = 42
RSI_UP_THRESHOLD_4H   = 55
RSI_DOWN_THRESHOLD_4H = 45

# Price zone filter: don't enter near resolution
KALSHI_SKIP_ZONE_LOW  = 0.12   # skip if YES price < 12% (already bearish)
KALSHI_SKIP_ZONE_HIGH = 0.88   # skip if YES price > 88% (already bullish = near resolved)


def _fetch_klines(symbol: str, interval: str, limit: int = 30) -> list:
    """Fetch OHLCV candles from Binance."""
    try:
        r = requests.get(
            f"{BINANCE_API}/klines",
            params={"symbol": f"{symbol}USDT", "interval": interval, "limit": limit},
            timeout=8,
        )
        if r.status_code == 200:
            return r.json()
    except Exception as exc:
        log.debug("[KALSHI-UPDOWN] Binance klines failed %s %s: %s", symbol, interval, exc)
    return []


def _compute_rsi(closes: list, period: int = 14) -> Optional[float]:
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
    if len(closes) < lookback + 1:
        return 0.0
    return (closes[-1] - closes[-lookback]) / closes[-lookback] * 100


def get_kalshi_crypto_direction_signal(coin: str) -> Optional[dict]:
    """
    Generate a directional signal for Kalshi crypto daily markets.
    Uses 1h (primary) + 4h (confirmation) RSI/momentum.
    Returns {direction, confidence, coin, rsi_1h, rsi_4h, momentum_1h}
    or None if no clear signal.
    """
    klines_1h = _fetch_klines(coin, "1h", limit=30)
    klines_4h = _fetch_klines(coin, "4h", limit=20)

    if len(klines_1h) < 16:
        log.debug("[KALSHI-UPDOWN] Insufficient 1h klines for %s", coin)
        return None

    closes_1h = [float(k[4]) for k in klines_1h]
    closes_4h = [float(k[4]) for k in klines_4h] if len(klines_4h) >= 16 else []

    rsi_1h = _compute_rsi(closes_1h)
    rsi_4h = _compute_rsi(closes_4h) if closes_4h else None
    mom_1h = _compute_momentum(closes_1h, lookback=5)
    mom_4h = _compute_momentum(closes_4h, lookback=3) if closes_4h else 0.0

    if rsi_1h is None:
        return None

    # Primary signal from 1h RSI
    if rsi_1h > RSI_UP_THRESHOLD_1H and mom_1h > 0:
        direction_1h = "UP"
    elif rsi_1h < RSI_DOWN_THRESHOLD_1H and mom_1h < 0:
        direction_1h = "DOWN"
    else:
        return None  # no clear 1h signal

    # 4h confirmation (optional but boosts confidence)
    if rsi_4h is not None:
        if direction_1h == "UP" and rsi_4h < RSI_DOWN_THRESHOLD_4H:
            log.debug("[KALSHI-UPDOWN] %s 4h RSI=%.1f contradicts 1h UP → skip", coin, rsi_4h)
            return None  # contradiction filter
        if direction_1h == "DOWN" and rsi_4h > RSI_UP_THRESHOLD_4H:
            log.debug("[KALSHI-UPDOWN] %s 4h RSI=%.1f contradicts 1h DOWN → skip", coin, rsi_4h)
            return None

    # 4h alignment bonus
    rsi_4h_aligned = False
    if rsi_4h is not None:
        rsi_4h_aligned = (direction_1h == "UP" and rsi_4h > RSI_UP_THRESHOLD_4H) or \
                         (direction_1h == "DOWN" and rsi_4h < RSI_DOWN_THRESHOLD_4H)

    # Confidence: RSI strength + momentum + 4h alignment
    rsi_dist  = abs(rsi_1h - 50) / 40.0   # normalized
    mom_score = min(1.0, abs(mom_1h) / 2.0)  # 2% momentum = max
    confidence = rsi_dist * 0.50 + mom_score * 0.35 + (0.15 if rsi_4h_aligned else 0.0)
    confidence = round(min(0.95, max(0.40, confidence)), 4)

    log.info(
        "[KALSHI-UPDOWN] %s | %s | 1h RSI=%.1f | 4h RSI=%s | mom=%.3f%% | 4h_aligned=%s | conf=%.2f",
        coin, direction_1h, rsi_1h,
        f"{rsi_4h:.1f}" if rsi_4h else "N/A",
        mom_1h, rsi_4h_aligned, confidence,
    )

    return {
        "coin":         coin,
        "direction":    direction_1h,
        "confidence":   confidence,
        "rsi_1h":       rsi_1h,
        "rsi_4h":       rsi_4h,
        "momentum_1h":  round(mom_1h, 4),
        "momentum_4h":  round(mom_4h, 4),
        "timeframe":    "1h+4h",
    }


def scan_kalshi_crypto_markets(auth, fetcher_instance) -> List[dict]:
    """
    Scan all Kalshi crypto direction markets and return tradeable opportunities.
    Returns list of {market, signal, direction, side, entry_price, hours_until, ticker}
    """
    if not auth.is_configured:
        return []

    opportunities = []
    seen_tickers: set = set()

    for series in KALSHI_CRYPTO_SERIES:
        path = f"/markets?status=open&limit=50&series_ticker={series}"
        try:
            resp = requests.get(
                f"{auth.base_url}{path}",
                headers=auth.get_headers("GET", path),
                timeout=8,
            )
            if resp.status_code not in (200,):
                continue

            markets = resp.json().get("markets", [])
            for mkt in markets:
                ticker = mkt.get("ticker", "")
                if not ticker or ticker in seen_tickers:
                    continue

                # Parse close time
                from kalshi.fetcher import _parse_close_time
                _, hours = _parse_close_time(mkt)
                if hours is None or hours < KALSHI_UPDOWN_MIN_HOURS or hours > KALSHI_UPDOWN_MAX_HOURS:
                    continue

                # Skip near-resolved markets
                yes_price_raw = mkt.get("yes_ask") or mkt.get("yes_bid") or 0
                yes_price = float(yes_price_raw) / 100.0 if float(yes_price_raw) > 1 else float(yes_price_raw)
                if yes_price <= KALSHI_SKIP_ZONE_LOW or yes_price >= KALSHI_SKIP_ZONE_HIGH:
                    continue

                seen_tickers.add(ticker)

                # Determine which coin this market is for
                title = mkt.get("title", "").lower()
                if "ethereum" in title or " eth " in title or ticker.startswith("KXETH"):
                    coin = "ETH"
                elif "bitcoin" in title or " btc " in title or ticker.startswith("KXBTC"):
                    coin = "BTC"
                else:
                    continue

                opportunities.append({
                    "ticker":       ticker,
                    "coin":         coin,
                    "title":        mkt.get("title", ""),
                    "yes_price":    yes_price,
                    "no_price":     round(1.0 - yes_price, 4),
                    "hours_until":  round(hours, 2),
                    "series":       series,
                    "_category":    "CRYPTO",
                    "market_data":  mkt,
                })

        except Exception as exc:
            log.debug("[KALSHI-UPDOWN] series=%s error: %s", series, exc)

    if not opportunities:
        log.debug("[KALSHI-UPDOWN] No crypto direction markets found on Kalshi")
        return []

    log.info("[KALSHI-UPDOWN] Found %d crypto direction markets", len(opportunities))
    return opportunities


def run_kalshi_updown_cycle(
    auth,
    fetcher_instance,
    kalshi_trader_instance,
    get_balance_fn,
) -> int:
    """
    Run one Kalshi Up/Down crypto direction cycle.
    Generates 1h/4h signals for BTC+ETH, matches to open Kalshi direction markets,
    and executes trades when signal confidence is sufficient.
    Returns number of trades placed.
    """
    trades_placed = 0

    markets = scan_kalshi_crypto_markets(auth, fetcher_instance)
    if not markets:
        return 0

    # Generate signals for BTC and ETH
    signals: dict = {}
    for coin in ("BTC", "ETH"):
        sig = get_kalshi_crypto_direction_signal(coin)
        if sig and sig["confidence"] >= 0.55:
            signals[coin] = sig

    if not signals:
        log.info("[KALSHI-UPDOWN] No qualifying crypto direction signals")
        return 0

    balance = get_balance_fn()
    traded_tickers: set = set()

    for mkt in markets:
        coin = mkt["coin"]
        sig  = signals.get(coin)
        if sig is None:
            continue

        ticker   = mkt["ticker"]
        if ticker in traded_tickers:
            continue

        direction  = sig["direction"]
        confidence = sig["confidence"]
        hours      = mkt["hours_until"]
        yes_price  = mkt["yes_price"]

        # Direction vs price check: if UP signal, YES contract should not be over-priced
        if direction == "UP" and yes_price > 0.72:
            log.debug("[KALSHI-UPDOWN] %s UP but YES=%.2f already expensive", ticker, yes_price)
            continue
        if direction == "DOWN" and yes_price < 0.28:
            log.debug("[KALSHI-UPDOWN] %s DOWN but YES=%.2f already priced in", ticker, yes_price)
            continue

        # Position sizing: 2% of balance for Kalshi updown (less aggressive than 5m Poly)
        size = round(max(1.0, min(balance * 0.02, 15.0)), 2)

        # Construct a signal-like dict for the trader
        trade_signal = {
            "sentiment":       "BULLISH" if direction == "UP" else "BEARISH",
            "confidence":      confidence,
            "affected_cryptos": [coin],
            "signal_type":     "KALSHI_UPDOWN",
        }

        trade = kalshi_trader_instance.execute_trade(
            event={
                "ticker":    ticker,
                "title":     mkt["title"],
                "yes_ask":   int(yes_price * 100),
                "yes_bid":   int(yes_price * 100),
                "_category": "CRYPTO",
                "_hours_to_close": hours,
            },
            signal=trade_signal,
            position_size=size,
            confidence=confidence,
        )

        if trade:
            traded_tickers.add(ticker)
            trades_placed += 1
            log.info(
                "[KALSHI-UPDOWN] TRADE: %s %s | entry=%.2f | size=$%.2f | %.1fh to close | conf=%.2f",
                direction, coin, yes_price, size, hours, confidence,
            )

    return trades_placed
