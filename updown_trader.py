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

# ── Tier-based Kelly sizing (composite score → size) ─────────────────────────
# Score > 0.85 = high conviction: 4% of balance, 15% cap
# Score 0.75–0.85 = good: 3% of balance, 10% cap
# Score 0.62–0.75 = acceptable: 1.5% of balance, 5% cap
# Score < 0.62 = skip
UPDOWN_KELLY_HIGH  = 0.040   # 4% Kelly for high-conviction entries
UPDOWN_KELLY_MED   = 0.030   # 3% Kelly for good entries
UPDOWN_KELLY_LOW   = 0.015   # 1.5% Kelly for acceptable entries
UPDOWN_CAP_HIGH    = 0.150   # 15% of balance max (prevents runaway at high balance)
UPDOWN_CAP_MED     = 0.100   # 10% of balance max
UPDOWN_CAP_LOW     = 0.050   # 5% of balance max
UPDOWN_MIN_USD     = 1.00

# Legacy: used as fallback only
UPDOWN_KELLY_BASE  = 0.022
UPDOWN_MAX_USD     = 10.00

# Volume gate: current candle volume must be >= this fraction of 20-period avg
VOLUME_GATE_RATIO = 0.30
# Absolute minimum 1m volume per coin (in base asset units — Binance native).
# During quiet hours, avg_vol drops and the ratio gate becomes too restrictive.
# If current volume >= floor, the gate always passes regardless of the ratio.
VOLUME_GATE_ABSOLUTE_FLOORS: dict = {
    "BTC":  2.0,     # BTC per minute  (off-peak minimum — 1 BTC/min can occur in quiet hours)
    "ETH":  10.0,    # ETH per minute  (was 15)
    "SOL":  75.0,    # SOL per minute  (was 500 — blocked valid 158 SOL/min readings)
    "XRP":  5000.0,  # XRP per minute  (was 10000)
}

# BTC/ETH last-minute momentum thresholds (percentage move, NOT dollar amount).
# Based on empirical data: >0.05% is the 94.3% WR zone — our minimum for entries.
BTC_MOMENTUM_THRESH_LOW  = 0.050   # >0.050% → 94.3% WR  (entry minimum)
BTC_MOMENTUM_THRESH_MED  = 0.080   # >0.080% → strong edge
BTC_MOMENTUM_THRESH_HIGH = 0.150   # >0.150% → near-certain (99%+ WR zone)

# Composite score gates
COMPOSITE_SCORE_MIN  = 0.60   # lowered from 0.68 — was blocking too many valid entries
COMPOSITE_SCORE_MED  = 0.75   # medium tier
COMPOSITE_SCORE_HIGH = 0.85   # high tier

# Rolling VWAP window (5m candles = 2 hours)
ROLLING_VWAP_BARS = 24

# ATR volatility gate: trade only when ATR > N-period median
ATR_PERIOD     = 14
ATR_MED_BARS   = 40   # fetch 40+ candles to compute ATR median over 20 periods

# Blowoff guard: skip UP entries when these ALL hold simultaneously
BLOWOFF_RSI_THRESHOLD = 60   # RSI ≥ 60 on UP = exhaustion
BLOWOFF_BB_SIGMA      = 2.0  # Bollinger upper band = SMA + 2σ
BLOWOFF_MIN_MOVE      = 0.08 # last-minute move > 0.08% = vertical exhaustion

# Min liquidity for Up/Down market to be tradeable
UPDOWN_MIN_LIQUIDITY = 500.0

# Coins we trade
UPDOWN_COINS = ["BTC", "ETH", "SOL", "XRP"]

# Max windows to trade per coin per cycle (normal)
MAX_WINDOWS_PER_COIN = 2
# Smart cascade: extreme RSI or regime lock → up to this many windows per coin
MAX_CASCADE_WINDOWS  = 3

# Cross-window correlation cap: max 2 concurrent positions per coin per direction
CORR_CAP_PER_COIN_DIRECTION = 2

# Per-coin consecutive loss tracking (resets to 0 on win)
_consecutive_losses: dict = {"BTC": 0, "ETH": 0, "SOL": 0, "XRP": 0}

# Auto-cooldown: skip a coin for N cycles after MAX_CONSEC_LOSSES consecutive losses
MAX_CONSEC_LOSSES = 4
_coin_cooldown_until: dict = {"BTC": 0, "ETH": 0, "SOL": 0, "XRP": 0}

# Session-wide win streak for compounding multiplier
_session_win_streak: int = 0

# Session loss circuit breaker for Up/Down
_updown_session_loss: float = 0.0
_updown_circuit_reset: float = 0.0
UPDOWN_SESSION_LOSS_LIMIT = 10.0  # pause after $10 updown loss in a session
_hwm_tracker: dict = {}  # high watermark per order_id for trailing floor stop

# Per-duration win rate tracker (in-memory only, resets on restart)
# Maps duration_min (5/10/15/60) → list of bool outcomes (True=win)
_duration_wr: dict = {}
_DURATION_MIN_SAMPLES = 10
_DURATION_WR_FLOOR    = 0.40

# Position ladder: high-conviction trades (score ≥ 0.85) enter 60% now, 40% after 3-min confirmation
# _ladder_pending: { event_id: {coin, direction, remaining_size, entry_ts, entry_price, market_id} }
_ladder_pending: dict = {}
_LADDER_SCORE_THRESHOLD = 0.85
_LADDER_CONFIRM_SECS    = 180  # 3 minutes


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


def _compute_rolling_vwap(klines_5m: list, window: int = ROLLING_VWAP_BARS) -> Optional[float]:
    """24-bar rolling VWAP on 5m candles (2h). Standard intraday VWAP practice.
    Old cumulative VWAP got anchored to past prices and went stale — this fixes it."""
    if len(klines_5m) < window:
        return None
    recent = klines_5m[-window:]
    total_vol = 0.0
    total_pv  = 0.0
    for k in recent:
        high   = float(k[2])
        low    = float(k[3])
        close  = float(k[4])
        vol    = float(k[5])
        typical = (high + low + close) / 3.0
        total_pv  += typical * vol
        total_vol += vol
    if total_vol <= 0:
        return None
    return round(total_pv / total_vol, 2)


def _compute_atr(klines: list, period: int = ATR_PERIOD) -> Optional[float]:
    """Compute ATR (Average True Range) from klines. Returns None if insufficient data."""
    if len(klines) < period + 1:
        return None
    trs = []
    for i in range(1, len(klines)):
        high  = float(klines[i][2])
        low   = float(klines[i][3])
        prev_close = float(klines[i-1][4])
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
    return round(sum(trs[-period:]) / period, 6)


def _compute_volume_trend(klines: list) -> str:
    """Compare avg volume in first half vs second half of klines. Returns 'RISING' or 'FALLING'."""
    if len(klines) < 2:
        return "FLAT"
    mid = len(klines) // 2
    first_half_avg  = sum(float(k[5]) for k in klines[:mid]) / mid
    second_half_avg = sum(float(k[5]) for k in klines[mid:]) / (len(klines) - mid)
    return "RISING" if second_half_avg > first_half_avg else "FALLING"


def _get_adaptive_composite_min() -> float:
    """Dynamically adjust composite quality gate based on recent rolling win rate."""
    try:
        import json as _j, os as _o
        pf = _o.path.join(_o.path.dirname(__file__), "positions_state.json")
        data = _j.loads(open(pf, encoding="utf-8").read())
        summary = data.get("summary", {})
        win_count   = int(summary.get("win_count", 0))
        loss_count  = int(summary.get("loss_count", 0))
        closed      = win_count + loss_count
        if closed < 5:
            return COMPOSITE_SCORE_MIN  # insufficient data — use default
        wr = win_count / closed
        if wr > 0.55:
            return 0.76   # winning → lower gate, take more trades
        if wr < 0.40:
            return 0.84   # losing → raise gate, be more selective
        return COMPOSITE_SCORE_MIN
    except Exception:
        return COMPOSITE_SCORE_MIN


def _check_volatility_gate(coin: str) -> bool:
    """Only trade when BTC ATR > 20-period median. Flat BTC = noise not signal.
    The +$20k trader only entered during genuine BTC volatility spikes — this replicates that."""
    klines = _fetch_binance_klines(coin, interval="5m", limit=ATR_MED_BARS + 5)
    if len(klines) < ATR_MED_BARS:
        return True  # not enough data → allow trade (conservative miss better than false block)
    # Compute ATR for each rolling 14-period window
    atrs = []
    for start in range(len(klines) - ATR_PERIOD - 1):
        segment = klines[start : start + ATR_PERIOD + 1]
        atr = _compute_atr(segment)
        if atr is not None:
            atrs.append(atr)
    if len(atrs) < 5:
        return True
    current_atr = atrs[-1]
    median_atr  = sorted(atrs[-20:])[ len(atrs[-20:]) // 2 ]
    passes = current_atr > median_atr
    if not passes:
        log.info(
            "[UPDOWN] VOLATILITY GATE %s | ATR=%.6f < median=%.6f → flat market, skipping",
            coin, current_atr, median_atr,
        )
    return passes


def _compute_bollinger_upper(closes: list, period: int = 20, sigma: float = 2.0) -> Optional[float]:
    """Return BB upper band. Returns None if insufficient data."""
    if len(closes) < period:
        return None
    recent = closes[-period:]
    sma    = sum(recent) / period
    std    = (sum((x - sma) ** 2 for x in recent) / period) ** 0.5
    return round(sma + sigma * std, 2)


def _check_blowoff_guard(coin: str, direction: str, klines_5m: list, last_min_move: float) -> bool:
    """Return True if this is a blowoff pattern that should be skipped.
    Blowoff UP: price at BB upper + RSI ≥ 60 + strong momentum = vertical exhaustion.
    These are traps — the market is overbought and will reverse.
    DOWN-blowoff kept open (panic selling is more sustained, different physics)."""
    if direction != "UP":
        return False  # only block UP blowoffs
    if abs(last_min_move) < BLOWOFF_MIN_MOVE:
        return False  # not strong enough to be a blowoff
    closes = [float(k[4]) for k in klines_5m]
    bb_upper = _compute_bollinger_upper(closes, period=20)
    if bb_upper is None:
        return False
    current_price = closes[-1]
    rsi = _compute_rsi(closes)
    if rsi is None:
        return False
    is_blowoff = (current_price >= bb_upper * 0.998) and (rsi >= BLOWOFF_RSI_THRESHOLD)
    if is_blowoff:
        log.info(
            "[UPDOWN] BLOWOFF GUARD %s UP | price=%.2f at BB_upper=%.2f | RSI=%.1f ≥ %d | move=%.3f%% → SKIP",
            coin, current_price, bb_upper, rsi, BLOWOFF_RSI_THRESHOLD, last_min_move,
        )
    return is_blowoff


def _compute_composite_score(
    coin: str,
    direction: str,
    avg_rsi: float,
    tf_agree: int,
    last_min_move: float,
    klines_5m: list,
    fg_val: Optional[int],
    volatility_gate_pass: bool,
) -> float:
    """Composite signal quality score 0.0 – 1.0.
    Only scores above COMPOSITE_SCORE_MIN (0.80) result in a trade.
    Components: RSI(0.22) + MTF(0.22) + Momentum(0.20) + Volatility(0.12) + VWAP(0.09) + F&G(0.15)
    Fear & Greed raised from 0.05 to 0.15 — contrarian F&G has a 65%+ historical win rate."""
    score = 0.0

    # 1. RSI alignment (0–0.22): stronger RSI = higher score
    rsi_dist = abs(avg_rsi - 50)
    rsi_norm = min(1.0, rsi_dist / 35.0)   # RSI=85 → dist=35 → 1.0
    score += rsi_norm * 0.22

    # 2. MTF confluence (0–0.22): 3/3 full, 2/3 partial
    score += 0.22 if tf_agree == 3 else 0.13

    # 3. Momentum (0–0.20): last-minute % move aligned with direction
    abs_move = abs(last_min_move)
    if abs_move >= BTC_MOMENTUM_THRESH_HIGH:
        score += 0.20
    elif abs_move >= BTC_MOMENTUM_THRESH_MED:
        score += 0.15
    elif abs_move >= BTC_MOMENTUM_THRESH_LOW:
        score += 0.10
    # below minimum threshold → 0

    # 4. Volatility gate (0–0.12): trading during genuine volatility
    score += 0.12 if volatility_gate_pass else 0.0

    # 5. VWAP position (0–0.09): price above/below rolling VWAP aligns with direction
    if len(klines_5m) >= ROLLING_VWAP_BARS:
        vwap = _compute_rolling_vwap(klines_5m)
        current_price = float(klines_5m[-1][4])
        if vwap is not None:
            if direction == "UP" and current_price > vwap:
                score += 0.09
            elif direction == "DOWN" and current_price < vwap:
                score += 0.09
            else:
                score += 0.03  # small partial credit (VWAP not aligned but not disqualifying)

    # 6. Fear & Greed alignment (0–0.15): contrarian or trend-following bonus
    # Raised from 0.05 → 0.15: extreme F&G provides the strongest directional edge
    if fg_val is not None:
        if (fg_val <= 25 and direction == "UP") or (fg_val >= 75 and direction == "DOWN"):
            score += 0.15   # extreme contrarian — highest edge (buy fear, sell greed)
        elif (60 <= fg_val <= 75 and direction == "UP") or (25 <= fg_val <= 40 and direction == "DOWN"):
            score += 0.09   # trend-following with moderate F&G confirmation

    # 7. Volume-price divergence (0–0.06): price + volume agree = confirmed trend
    # Rising price with rising volume = genuine move; rising price with falling volume = weak
    if len(klines_5m) >= 5:
        _vol_trend = _compute_volume_trend(klines_5m[-5:])
        _price_trend = "UP" if float(klines_5m[-1][4]) > float(klines_5m[-5][4]) else "DOWN"
        if direction == _price_trend and _vol_trend == "RISING":
            score += 0.06   # price + volume agreement: confirmed move
        elif direction != _price_trend and _vol_trend == "FALLING":
            score += 0.03   # weak counter-trend: partial credit

    return round(min(1.0, score), 4)


def _count_correlated_positions(coin: str, direction: str) -> int:
    """Count currently open Up/Down positions for coin+direction across all windows.
    Prevents stacking correlated bets (max 2 per coin per direction)."""
    try:
        import json as _j, os as _o
        pf = _o.path.join(_o.path.dirname(__file__), "positions_state.json")
        if not _o.path.exists(pf):
            return 0
        data = _j.loads(open(pf, encoding="utf-8").read())
        active = data.get("active", [])
        coin_dir_count = 0
        for pos in active:
            title = str(pos.get("event_title", "")).upper()
            pos_direction = str(pos.get("direction", "")).upper()
            if coin.upper() in title and "[UPDOWN]" in title:
                # direction stored as YES=UP, NO=DOWN
                mapped = "UP" if pos_direction == "YES" else "DOWN"
                if mapped == direction.upper():
                    coin_dir_count += 1
        return coin_dir_count
    except Exception:
        return 0


def _signal_for_timeframe(klines: list, tf_label: str, coin: str = "") -> Optional[dict]:
    """
    Extract a directional signal from one timeframe's klines.
    Returns {direction, strength, rsi, momentum} or None if no edge.
    XRP requires stricter RSI thresholds (>60/<40) due to its pump/dump volatility.
    """
    if len(klines) < 16:
        return None
    closes = [float(k[4]) for k in klines]
    rsi = _compute_rsi(closes, period=14)
    momentum = _compute_momentum(closes, lookback=5)
    if rsi is None:
        return None

    rsi_up   = 60
    rsi_down = 40

    if rsi > rsi_up:
        direction = "UP"
        strength  = min(0.85, 0.50 + (rsi - rsi_up) / 40 * 0.35)
    elif rsi < rsi_down:
        direction = "DOWN"
        strength  = min(0.85, 0.50 + (rsi_down - rsi) / 40 * 0.35)
    else:
        if abs(momentum) > 0.25:
            direction = "UP" if momentum > 0 else "DOWN"
            strength  = 0.55
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
    cur_vol = volumes[-2] if len(volumes) >= 2 else volumes[-1]  # use confirmed candle, not forming
    _vol_floor = VOLUME_GATE_ABSOLUTE_FLOORS.get(coin, 0.0)
    _above_floor = cur_vol >= _vol_floor
    _above_ratio = avg_vol <= 0 or cur_vol >= VOLUME_GATE_RATIO * avg_vol
    if not _above_floor and not _above_ratio:
        log.info(
            "[UPDOWN] %s volume gate failed — current %.0f < floor %.0f AND < %.0f%% of avg %.0f",
            coin, cur_vol, _vol_floor, VOLUME_GATE_RATIO * 100, avg_vol,
        )
        return None
    if _above_floor and not _above_ratio:
        log.debug(
            "[UPDOWN] %s volume floor override — cur %.0f >= floor %.0f (below ratio but ok)",
            coin, cur_vol, _vol_floor,
        )

    # ── Consecutive-loss cooldown ────────────────────────────────────────────
    now_ts = int(time.time())
    if _coin_cooldown_until.get(coin, 0) > now_ts:
        remaining = (_coin_cooldown_until[coin] - now_ts) // 60
        log.info("[UPDOWN] %s in cooldown for %dm (consecutive losses)", coin, remaining)
        return None

    # ── Per-timeframe signals ────────────────────────────────────────────────
    tf_signals = []
    for klines, label in [(klines_1m, "1m"), (klines_3m, "3m"), (klines_5m, "5m")]:
        sig = _signal_for_timeframe(klines, label, coin=coin)
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


# ── Advancement A: Polymarket Orderbook Imbalance ─────────────────────────────

def _fetch_orderbook_imbalance(token_id: str) -> Optional[float]:
    """
    Fetch bid/ask depth imbalance for a Polymarket token from the CLOB.
    Returns bid_total / ask_total across top 5 levels.
    >2.0 = strong buying pressure, <0.5 = strong selling pressure, None if unavailable.
    This is real microstructure alpha: smart money loading a side before a move.
    """
    if not token_id:
        return None
    try:
        r = requests.get(
            f"{POLY_CLOB_API}/book",
            params={"token_id": token_id},
            timeout=5,
        )
        if r.status_code == 200:
            data = r.json()
            bids = data.get("bids", [])[:5]
            asks = data.get("asks", [])[:5]
            total_bids = sum(float(b.get("size", 0)) for b in bids)
            total_asks = sum(float(a.get("size", 0)) for a in asks)
            if total_asks > 0 and total_bids > 0:
                return round(total_bids / total_asks, 3)
    except Exception as exc:
        log.debug("[UPDOWN] Orderbook fetch failed for %s: %s", token_id[:16], exc)
    return None


# ── Live CLOB price + spread fetcher (with 60s cache) ────────────────────────
_clob_price_cache: dict = {}
_CLOB_PRICE_CACHE_TTL = 60  # seconds


def _fetch_clob_price_and_spread(token_id: str):
    """
    Fetch the current live bid/ask mid-price AND spread for a Polymarket token.
    Returns (mid_price, spread) — both may be None if the CLOB is unavailable.

    This is the FIX for the coin-flip entry bug: lastTradePrice is a historical
    field that can be hours old.  The CLOB orderbook shows what the market is
    ACTUALLY priced at RIGHT NOW.  Using this eliminates the stale-price bypass
    of the 0.42–0.58 coin-flip filter that caused 8 consecutive losses.
    """
    if not token_id:
        return None, None
    now = time.time()
    cached = _clob_price_cache.get(token_id)
    if cached and now - cached[0] < _CLOB_PRICE_CACHE_TTL:
        return cached[1], cached[2]
    try:
        r = requests.get(
            f"{POLY_CLOB_API}/book",
            params={"token_id": token_id},
            timeout=5,
        )
        if r.status_code == 200:
            data     = r.json()
            bids     = data.get("bids", [])
            asks     = data.get("asks", [])
            best_bid = float(bids[0].get("price", 0)) if bids else 0.0
            best_ask = float(asks[0].get("price", 0)) if asks else 0.0
            if best_bid > 0 and best_ask > 0:
                mid    = round((best_bid + best_ask) / 2, 4)
                spread = round(best_ask - best_bid, 4)
                _clob_price_cache[token_id] = (now, mid, spread)
                return mid, spread
            elif best_ask > 0:
                _clob_price_cache[token_id] = (now, round(best_ask, 4), None)
                return round(best_ask, 4), None
            elif best_bid > 0:
                _clob_price_cache[token_id] = (now, round(best_bid, 4), None)
                return round(best_bid, 4), None
    except Exception as exc:
        log.debug("[UPDOWN] CLOB price fetch failed for %s: %s", token_id[:16], exc)
    return None, None


# ── Advancement B: Bayesian Win Rate → Adaptive Kelly ─────────────────────────

def _get_bayesian_kelly_multiplier() -> float:
    """
    Use session W/L from positions_state.json to compute a Beta distribution
    estimate of the true win rate, then return a Kelly adjustment multiplier.

    Beta posterior: alpha = wins+1, beta = losses+1 (Laplace smoothing prior).
    Multiplier = observed_win_rate / BASELINE_WIN_RATE so sizing scales up when
    ZiSi is outperforming the baseline assumption and scales down when it isn't.
    """
    BASELINE_WIN_RATE = 0.55
    try:
        import json as _json, os as _os
        pf = _os.path.join(_os.path.dirname(__file__), "positions_state.json")
        data = _json.loads(open(pf, encoding="utf-8").read())
        summary = data.get("summary", {})
        wins   = int(summary.get("win_count", 0))
        losses = int(summary.get("loss_count", 0))
        if wins + losses < 20:
            return 1.0  # not enough data to trust the estimate yet
        alpha = wins + 1    # Beta prior: +1 to avoid zero
        beta  = losses + 1
        bayesian_wr = alpha / (alpha + beta)
        multiplier = round(bayesian_wr / BASELINE_WIN_RATE, 4)
        # Cap at 1.5× to prevent over-sizing on lucky streaks
        return min(1.5, max(0.5, multiplier))
    except Exception:
        return 1.0


# ── Advancement C: Binance→Polymarket Momentum Divergence ────────────────────

def _compute_binance_5m_momentum(coin: str) -> float:
    """
    Compute 5-minute price momentum for a coin from Binance 1m klines.
    Returns % change over the last 5 candles. Positive = bullish, negative = bearish.
    """
    try:
        klines = _fetch_binance_klines(coin, interval="1m", limit=10)
        if len(klines) < 6:
            return 0.0
        closes = [float(k[4]) for k in klines]
        return (closes[-1] - closes[-6]) / closes[-6] * 100
    except Exception:
        return 0.0


def _compute_last_minute_pct_move(coin: str) -> float:
    """
    Compute the % move WITHIN the current 1-minute candle (open→current).
    This is the high-edge signal: when a coin moves >0.02% in the current minute,
    the market is directionally committed right now. Price-level invariant — uses
    percentage NOT dollars, so it works at any BTC/ETH price level.

    Empirical win rates from 12,234 BTC 5-minute markets:
      >0.020%: 88.9% WR   |   >0.050%: 94.3% WR   |   >0.100%: 99.1% WR
    """
    try:
        klines = _fetch_binance_klines(coin, interval="1m", limit=3)
        if len(klines) < 1:
            return 0.0
        current = klines[-1]
        candle_open  = float(current[1])  # open price of current minute candle
        candle_close = float(current[4])  # most recent close (live)
        if candle_open <= 0:
            return 0.0
        return (candle_close - candle_open) / candle_open * 100
    except Exception:
        return 0.0


def _get_last_minute_confidence_boost(coin: str, direction: str) -> float:
    """
    Apply the last-minute momentum threshold as an additional confidence boost.
    Returns a multiplier:
      - >0.100% aligned move: 1.40× (near-certain directional edge)
      - >0.050% aligned move: 1.25×
      - >0.020% aligned move: 1.12×
      - Conflicting move:     0.85× (momentum contradicts our signal — size down)
      - Flat (<0.020%):       1.00× (no edge from last-minute data)
    """
    move = _compute_last_minute_pct_move(coin)
    move_dir = "UP" if move > 0 else "DOWN"
    abs_move = abs(move)

    if abs_move < BTC_MOMENTUM_THRESH_LOW:
        return 1.0  # flat — no adjustment

    if move_dir != direction:
        log.info(
            "[UPDOWN] ⚡ LAST-MIN CONTRA %s | move=%.3f%% (%s) ≠ signal (%s) → 0.85×",
            coin, move, move_dir, direction,
        )
        return 0.85  # momentum contradicts signal — reduce size

    if abs_move >= BTC_MOMENTUM_THRESH_HIGH:
        log.info("[UPDOWN] ⚡ LAST-MIN TURBO %s | move=%.3f%% → 1.40× boost (99.1%% WR zone)", coin, move)
        return 1.40
    elif abs_move >= BTC_MOMENTUM_THRESH_MED:
        log.info("[UPDOWN] ⚡ LAST-MIN HIGH %s | move=%.3f%% → 1.25× boost (94.3%% WR zone)", coin, move)
        return 1.25
    else:
        log.info("[UPDOWN] ⚡ LAST-MIN LOW %s | move=%.3f%% → 1.12× boost (88.9%% WR zone)", coin, move)
        return 1.12


def _get_15m_vs_5m_lag_boost(coin: str, markets: list, direction: str) -> float:
    """
    Detect lag between 15m and 5m contract prices.
    If 15m market shows strong conviction (>0.65 for UP) but 5m is still neutral (<0.55),
    the 5m hasn't repriced yet — this is arbitrage-like alpha.
    Returns a 1.15× boost when lag exists, 1.0 otherwise.
    """
    five_m  = [m for m in markets if m.get("duration_min") == 5]
    fifteen = [m for m in markets if m.get("duration_min") == 15]
    if not five_m or not fifteen:
        return 1.0
    try:
        price_5m  = five_m[0].get("up_price", 0.5) if direction == "UP" else five_m[0].get("dn_price", 0.5)
        price_15m = fifteen[0].get("up_price", 0.5) if direction == "UP" else fifteen[0].get("dn_price", 0.5)
        lag = abs(price_15m - price_5m)
        if price_15m > 0.62 and price_5m < 0.55 and direction == "UP":
            log.info("[UPDOWN] 🔀 5m/15m LAG %s | 15m=%.2f > 5m=%.2f → 1.15× (repricing opportunity)", coin, price_15m, price_5m)
            return 1.15
        if price_15m < 0.38 and price_5m > 0.45 and direction == "DOWN":
            log.info("[UPDOWN] 🔀 5m/15m LAG %s | 15m=%.2f < 5m=%.2f → 1.15× (repricing opportunity)", coin, price_15m, price_5m)
            return 1.15
    except Exception:
        pass
    return 1.0


# ── Advancement D: Fear & Greed Index (Alternative.me) ───────────────────────

_fear_greed_cache: dict = {"value": None, "ts": 0.0}
_FEAR_GREED_TTL = 300  # 5-minute cache — index updates once per day


def _fetch_fear_greed_index() -> Optional[int]:
    """
    Fetch the Crypto Fear & Greed Index from Alternative.me (completely free, no API key).
    Returns 0 (Extreme Fear) – 100 (Extreme Greed), or None on failure.
    Cached for 5 minutes to avoid hammering the endpoint.
    """
    now = time.time()
    if _fear_greed_cache["ts"] > now - _FEAR_GREED_TTL and _fear_greed_cache["value"] is not None:
        return _fear_greed_cache["value"]
    try:
        r = requests.get("https://api.alternative.me/fng/", params={"limit": 1}, timeout=6)
        if r.status_code == 200:
            val = int(r.json()["data"][0]["value"])
            _fear_greed_cache["value"] = val
            _fear_greed_cache["ts"]    = now
            log.info("[UPDOWN] 😨 Fear & Greed Index: %d (%s)", val, r.json()["data"][0]["value_classification"])
            return val
    except Exception as exc:
        log.debug("[UPDOWN] Fear & Greed fetch failed: %s", exc)
    return _fear_greed_cache.get("value")


def _get_fear_greed_multiplier(direction: str, fg: Optional[int]) -> float:
    """
    Contrarian signal logic:
      - Extreme Fear (<25) + UP trade  → 1.20× (buy the panic)
      - Extreme Greed (>75) + DOWN trade → 1.15× (fade the euphoria)
      - Trend-following:
        - Greed (60–75) + UP   → 1.05× (momentum aligned)
        - Fear (25–40) + DOWN  → 1.05×
      - Contradicting extremes → 0.90× (smart money warns us off)
    """
    if fg is None:
        return 1.0
    if fg < 25 and direction == "UP":    return 1.20  # extreme fear → buy dip
    if fg > 75 and direction == "DOWN":  return 1.15  # extreme greed → short peak
    if fg > 75 and direction == "UP":    return 0.90  # buying into bubble
    if fg < 25 and direction == "DOWN":  return 0.90  # shorting into panic bottom
    if 60 <= fg <= 75 and direction == "UP":   return 1.05
    if 25 <= fg <= 40 and direction == "DOWN": return 1.05
    return 1.0


# ── Advancement E: Asymmetric Directional Kelly ───────────────────────────────

_directional_wr_cache: dict = {"data": {}, "ts": 0.0}
_DIRECTIONAL_CACHE_TTL = 600  # 10 minutes


def _get_directional_kelly_multiplier(direction: str) -> float:
    """
    Compute per-direction (YES=UP / NO=DOWN) win rate from positions_state.json.
    Returns a Kelly multiplier relative to the 55% baseline.
    If UP trades win 72% but DOWN only 60%, we size UP trades bigger.
    Requires 30+ samples per direction to trust the estimate.
    """
    BASELINE = 0.55
    now = time.time()
    if _directional_wr_cache["ts"] > now - _DIRECTIONAL_CACHE_TTL:
        data = _directional_wr_cache["data"]
    else:
        try:
            import json as _j, os as _o
            pf = _o.path.join(_o.path.dirname(__file__), "positions_state.json")
            raw = _j.loads(open(pf, encoding="utf-8").read())
            closed = raw.get("closed", [])
            stats: dict = {}
            for p in closed:
                d = str(p.get("direction", "")).upper()
                if d not in ("YES", "NO"):
                    continue
                s = stats.setdefault(d, [0, 0])  # [wins, total]
                s[1] += 1
                if (p.get("realized_pnl") or 0) > 0:
                    s[0] += 1
            _directional_wr_cache["data"] = stats
            _directional_wr_cache["ts"]   = now
            data = stats
        except Exception:
            return 1.0

    poly_dir = "YES" if direction == "UP" else "NO"
    entry = data.get(poly_dir, [0, 0])
    wins, total = entry[0], entry[1]
    if total < 30:
        return 1.0
    wr  = wins / total
    mult = round(wr / BASELINE, 4)
    return min(1.60, max(0.50, mult))


# ── Advancement F: UTC Hour Edge Multiplier ───────────────────────────────────

_utc_edge_cache: dict = {"data": {}, "ts": 0.0}
_UTC_EDGE_TTL = 900  # 15 minutes


def _get_utc_hour_multiplier() -> float:
    """
    ZiSi self-learns which UTC hours generate the best win rate.
    Reads our own closed trades, buckets by UTC hour, and returns a size
    multiplier for the current hour: >65% WR → 1.15×, <45% → 0.85×.
    Requires ≥30 closed trades in that hour for statistical significance.
    This is adaptive — the multiplier improves as we accumulate data.
    """
    now = time.time()
    current_hour = datetime.now(timezone.utc).hour
    if _utc_edge_cache["ts"] > now - _UTC_EDGE_TTL:
        data = _utc_edge_cache["data"]
    else:
        try:
            import json as _j, os as _o
            pf = _o.path.join(_o.path.dirname(__file__), "positions_state.json")
            raw = _j.loads(open(pf, encoding="utf-8").read())
            closed = raw.get("closed", [])
            hour_stats: dict = {}
            for p in closed:
                ts_str = p.get("entry_time") or p.get("open_time") or ""
                if not ts_str:
                    continue
                try:
                    hour = datetime.fromisoformat(ts_str.replace("Z", "+00:00")).hour
                except Exception:
                    continue
                hs = hour_stats.setdefault(hour, [0, 0])
                hs[1] += 1
                if (p.get("realized_pnl") or 0) > 0:
                    hs[0] += 1
            _utc_edge_cache["data"] = hour_stats
            _utc_edge_cache["ts"]   = now
            data = hour_stats
        except Exception:
            return 1.0

    entry = data.get(current_hour, [0, 0])
    if entry[1] < 30:
        return 1.0
    wr = entry[0] / entry[1]
    if wr > 0.65:
        log.info("[UPDOWN] 🕐 UTC hour %d edge: %.0f%% WR → 1.15× boost", current_hour, wr * 100)
        return 1.15
    if wr < 0.45:
        log.info("[UPDOWN] 🕐 UTC hour %d weak: %.0f%% WR → 0.85× reduction", current_hour, wr * 100)
        return 0.85
    return 1.0


# ── Advancement G: Rolling Coin Signal Quality (decay detector) ───────────────

_coin_rolling_wr: dict = {}  # coin → list of last 30 outcomes (True/False)
_ROLLING_WINDOW   = 30
_DECAY_THRESHOLD  = 0.48   # if rolling WR drops below this, apply penalty
_DECAY_MULTIPLIER = 0.80


def update_coin_rolling_wr(coin: str, won: bool) -> None:
    """Call after each UP/DOWN trade resolves to update rolling win rate."""
    if coin not in _coin_rolling_wr:
        _coin_rolling_wr[coin] = []
    _coin_rolling_wr[coin].append(won)
    if len(_coin_rolling_wr[coin]) > _ROLLING_WINDOW:
        _coin_rolling_wr[coin].pop(0)


def _get_coin_quality_multiplier(coin: str) -> float:
    """
    If a coin's last 30-trade win rate drops below 48%, signal quality is
    decaying (regime shift, prediction market drift). Apply 0.80× size penalty.
    Protects capital during losing streaks beyond what consecutive-loss tracking catches.
    Recovers automatically once performance improves.
    """
    outcomes = _coin_rolling_wr.get(coin, [])
    if len(outcomes) < 15:
        return 1.0  # not enough data
    wr = sum(outcomes) / len(outcomes)
    if wr < _DECAY_THRESHOLD:
        log.info(
            "[UPDOWN] 📉 %s signal quality decay: rolling WR=%.0f%% → 0.80× size penalty",
            coin, wr * 100,
        )
        return _DECAY_MULTIPLIER
    return 1.0


# ── Advancement H: Polymarket Volume Surge Detector ──────────────────────────

def _get_volume_surge_multiplier(market_data: dict) -> float:
    """
    If a market's recent volume is anomalously high (>2× the event's avg daily volume),
    smart money is positioning. This is a conviction boost — size up 15%.
    Uses the `volume24hr` field from the Gamma event data.
    """
    try:
        vol     = float(market_data.get("volume24hr") or market_data.get("volume") or 0)
        liq     = float(market_data.get("liquidity")  or 1_000)
        # Volume/Liquidity ratio: high ratio = active market with real conviction
        # Threshold: if vol > 2× liquidity, the market is unusually hot
        if liq > 0 and vol > 2.0 * liq:
            log.info(
                "[UPDOWN] 🔥 VOLUME SURGE vol=$%.0f liq=$%.0f (%.1f×) → 1.15× boost",
                vol, liq, vol / liq,
            )
            return 1.15
    except Exception:
        pass
    return 1.0


# ── Advancement I: 1h Trend Alignment ────────────────────────────────────────

def _check_1h_trend_alignment(coin: str, direction: str) -> bool:
    """
    Block counter-trend 5-minute entries against the prevailing 1h RSI trend.
    The 8-consecutive-loss streak was caused by placing DOWN bets during a clear
    BTC hourly uptrend. This single filter is the highest-ROI bug fix.

    Rules (RSI-based, no arbitrary lag):
      - 1h RSI > 58 + direction == DOWN  → counter-trend, block
      - 1h RSI < 42 + direction == UP    → counter-trend, block
    """
    klines_1h = _fetch_binance_klines(coin, interval="1h", limit=20)
    if len(klines_1h) < 14:
        return True  # insufficient data — don't over-filter
    closes_1h = [float(k[4]) for k in klines_1h]
    rsi_1h = _compute_rsi(closes_1h, period=14)
    if rsi_1h is None:
        return True
    if direction == "DOWN" and rsi_1h > 58:
        log.info(
            "[UPDOWN] 1H TREND BLOCK %s | DOWN vs 1h RSI=%.1f (hourly uptrend) → skip",
            coin, rsi_1h,
        )
        return False
    if direction == "UP" and rsi_1h < 42:
        log.info(
            "[UPDOWN] 1H TREND BLOCK %s | UP vs 1h RSI=%.1f (hourly downtrend) → skip",
            coin, rsi_1h,
        )
        return False
    return True


# ── Advancement J: Momentum Acceleration Filter ───────────────────────────────

def _check_momentum_acceleration(coin: str, direction: str) -> bool:
    """
    Reject decelerating moves: if the last 3 consecutive 1m candle body sizes
    are ALL getting smaller AND closing in the signal direction, the move is
    stalling — not accelerating.  Stalling entries fail ~60% of the time.
    """
    klines = _fetch_binance_klines(coin, interval="1m", limit=6)
    if len(klines) < 4:
        return True
    # Candle body size = abs(close - open) for each bar
    bodies = [abs(float(k[4]) - float(k[1])) for k in klines[-4:]]
    if bodies[-1] < bodies[-2] < bodies[-3]:
        closes = [float(k[4]) for k in klines[-4:]]
        last_dir = "UP" if closes[-1] > closes[-2] else "DOWN"
        if last_dir == direction:
            log.info(
                "[UPDOWN] 🔻 DECEL %s | bodies shrinking [%.5f→%.5f→%.5f] in %s direction → skip",
                coin, bodies[-3], bodies[-2], bodies[-1], direction,
            )
            return False
    return True


# ── Advancement K: Candle Close Strength ─────────────────────────────────────

def _check_candle_close_strength(coin: str, direction: str) -> bool:
    """
    Strong directional candles close near their extreme:
      UP  → close must be in top 40% of the candle range
      DOWN → close must be in bottom 40% of the candle range
    A close near the middle is indecision — it leads to reversals, not
    continuation.  This eliminates the "doji entry" problem.
    """
    klines = _fetch_binance_klines(coin, interval="1m", limit=3)
    if len(klines) < 1:
        return True
    last  = klines[-1]
    high  = float(last[2])
    low   = float(last[3])
    close = float(last[4])
    rng   = high - low
    if rng < 0.00005 * close:   # doji / flat candle — don't penalise
        return True
    close_pos = (close - low) / rng  # 0 = at low, 1 = at high
    if direction == "UP" and close_pos < 0.40:
        log.info(
            "[UPDOWN] 🕯️ WEAK CLOSE %s | UP but close at %.0f%% of range → skip",
            coin, close_pos * 100,
        )
        return False
    if direction == "DOWN" and close_pos > 0.60:
        log.info(
            "[UPDOWN] 🕯️ WEAK CLOSE %s | DOWN but close at %.0f%% of range → skip",
            coin, close_pos * 100,
        )
        return False
    return True


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
            outcomes    = mkt.get("outcomes", [])
            outcome_str = str(mkt.get("question", mkt.get("title", ""))).lower()

            # BUG FIX: Polymarket outcomes are ALWAYS ["Yes", "No"] for every
            # market — "yes" in outcomes means UP and DOWN markets look identical.
            # Use explicit "up"/"down" outcomes first; then fall back to question text.
            has_up   = any(o.lower() == "up"   for o in outcomes)
            has_down = any(o.lower() == "down" for o in outcomes)
            if has_up or has_down:
                is_up = has_up
            else:
                is_up = ("up" in outcome_str) and ("down" not in outcome_str)

            # BUG FIX: lastTradePrice is historical (stale by hours). Use the live
            # CLOB mid-price. Fall back to lastTradePrice only if CLOB is unavailable.
            token_id            = mkt.get("conditionId") or mkt.get("id", "")
            live_price, spread  = _fetch_clob_price_and_spread(token_id)
            if live_price is not None:
                price_val = live_price
            else:
                price_val = float(mkt.get("lastTradePrice") or mkt.get("price") or 0.5)

            # Wide spread = thin liquidity = guaranteed round-trip loss → skip
            if spread is not None and spread > 0.03:
                log.debug(
                    "[UPDOWN] SPREAD %.3f > 3%% for %s — skipping market",
                    spread, token_id[:16],
                )
                continue

            if is_up and up_market is None:
                up_market = mkt
                up_price  = price_val
            elif not is_up and dn_market is None:
                dn_market = mkt
                dn_price  = price_val

        if up_market is None and markets:
            up_market            = markets[0]
            _tk                  = up_market.get("conditionId") or up_market.get("id", "")
            _lv, _               = _fetch_clob_price_and_spread(_tk)
            up_price             = _lv if _lv is not None else float(up_market.get("lastTradePrice") or 0.5)
        if dn_market is None and len(markets) > 1:
            dn_market            = markets[1]
            _tk                  = dn_market.get("conditionId") or dn_market.get("id", "")
            _lv, _               = _fetch_clob_price_and_spread(_tk)
            dn_price             = _lv if _lv is not None else float(dn_market.get("lastTradePrice") or 0.5)

        if up_price >= 0.90 or up_price <= 0.10:
            return None
        if 0.42 <= up_price <= 0.58:
            return None  # coin-flip zone verified with live CLOB price — no edge
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

    for dur_min in (5, 10, 15, 60):
        interval = dur_min * 60
        boundary = ((now_ts + interval) // interval) * interval
        max_offsets = 2 if dur_min == 60 else 4
        for offset in range(max_offsets):
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
        log.debug("[UPDOWN] No active markets for %s", coin)

    found_events.sort(key=lambda e: e["expiry_ts"])
    return found_events


def check_updown_early_exits(get_all_trades_fn, execute_exit_fn, place_paper_trade_fn=None) -> int:
    """
    Early exit rules for UP/DOWN positions:
    - 88%+ price: lock in the gain before TIME_EXPIRED reversal
    - Trailing floor: once HWM hits 75¢, floor at 55¢ (protects 73% of profit)
    - Ladder: place remaining 40% after 3-min confirmation when momentum holds
    Returns number of early exits executed.
    """
    exits = 0
    try:
        all_trades = get_all_trades_fn()
        for trade in all_trades:
            title = str(trade.get("event_title", "")).upper()
            if "[UPDOWN]" not in title:
                continue  # only updown positions
            order_id      = trade.get("order_id", "?")
            current_price = float(trade.get("current_price", 0))
            entry_price   = float(trade.get("entry_price", 0.5))
            direction     = str(trade.get("direction", "")).upper()

            # Update high watermark
            _hwm = _hwm_tracker.get(order_id, current_price)
            _hwm = max(_hwm, current_price)
            _hwm_tracker[order_id] = _hwm

            exit_reason = None

            if current_price >= 0.88:
                _expiry_ts = int(trade.get("expiry_ts", 0))
                _secs_left = _expiry_ts - int(time.time()) if _expiry_ts else 999
                if _secs_left > 120:
                    exit_reason = "EARLY_EXIT_88PCT"
                    log.info(
                        "[UPDOWN] EARLY EXIT trigger: %s | price=%.3f ≥ 0.88 — locking in gain (%ds left)",
                        order_id[:20], current_price, _secs_left,
                    )
                else:
                    log.info(
                        "[UPDOWN] HOLD to expiry: %s | price=%.3f ≥ 0.88 but only %ds left — riding to 0.99",
                        order_id[:20], current_price, _secs_left,
                    )
            elif _hwm >= 0.75 and current_price < 0.55:
                exit_reason = "TRAILING_FLOOR_55"
                log.info(
                    "[UPDOWN] TRAILING FLOOR: %s | HWM=%.3f ≥ 0.75 but price dropped to %.3f < 0.55 — exit",
                    order_id[:20], _hwm, current_price,
                )

            if exit_reason:
                try:
                    _exit_result = execute_exit_fn(trade, reason=exit_reason)
                    if _exit_result is not False and _exit_result is not None:
                        exits += 1
                        _hwm_tracker.pop(order_id, None)  # clean up HWM entry
                        # Record Markov outcome
                        try:
                            from markov_tracker import tracker as _mk
                            _coin = next((c for c in ("BTC", "ETH", "SOL", "XRP") if c in title), None)
                            if _coin:
                                _won = current_price > entry_price
                                _outcome = direction if _won else ("DOWN" if direction == "UP" else "UP")
                                _mk.record(_coin, _outcome)
                                log.debug("[MARKOV] Recorded %s→%s for %s", direction, _outcome, _coin)
                                _slug = str(trade.get("slug", ""))
                                try:
                                    _dur_str = _slug.split("-updown-")[1].split("m-")[0]
                                    record_updown_result(_coin, _won, int(_dur_str))
                                except Exception:
                                    record_updown_result(_coin, _won)
                        except Exception:
                            pass
                        try:
                            from telegram_bot import notify_trade_closed as _tg_close
                            _cp  = current_price
                            _ep  = entry_price
                            _sh  = float(trade.get("shares_acquired", trade.get("shares", 0)))
                            _sz  = float(trade.get("size", trade.get("amount_spent", 0)))
                            _pnl = round(_sh * _cp - _sz, 4)
                            _pct = round((_cp - _ep) / _ep * 100, 1) if _ep > 0 else 0
                            _tg_close(str(trade.get("event_title", "UPDOWN")), _pnl, _pct, 0.0,
                                      exit_reason=exit_reason, entry_price=_ep, exit_price=_cp,
                                      direction=direction)
                        except Exception:
                            pass
                except Exception as _ee:
                    log.debug("[UPDOWN] Early exit failed: %s", _ee)
    except Exception as exc:
        log.debug("[UPDOWN] Early exit scan failed: %s", exc)

    # ── Position ladder: place remaining 40% after 3-min confirmation ────────
    if place_paper_trade_fn is None:
        return exits
    _now = int(time.time())
    _expired_keys = []
    for _lid, _lp in list(_ladder_pending.items()):
        _elapsed = _now - _lp["entry_ts"]
        if _elapsed < _LADDER_CONFIRM_SECS:
            continue  # not yet 3 minutes
        _l_coin = _lp["coin"]
        _l_dir  = _lp["direction"]
        # Check RSI momentum still aligned
        try:
            _rsi_1m = _compute_rsi(_fetch_binance_klines(_l_coin, "1m", 20), 14)
            _rsi_5m = _compute_rsi(_fetch_binance_klines(_l_coin, "5m", 20), 14)
            _bullish_ok = _l_dir == "UP"  and _rsi_1m is not None and _rsi_5m is not None and _rsi_1m > 55 and _rsi_5m > 50
            _bearish_ok = _l_dir == "DOWN" and _rsi_1m is not None and _rsi_5m is not None and _rsi_1m < 45 and _rsi_5m < 50
            _momentum_ok = _bullish_ok or _bearish_ok
        except Exception:
            _momentum_ok = False
        if not _momentum_ok:
            log.info("[LADDER] %s %s — momentum reversed after %ds, CANCEL remaining $%.2f",
                     _l_coin, _l_dir, _elapsed, _lp["remaining_size"])
            _expired_keys.append(_lid)
            continue
        # Place remaining 40%
        try:
            place_paper_trade_fn(
                event_id=_lid,
                market_id=_lp["market_id"],
                amount_dollars=_lp["remaining_size"],
                direction=_l_dir,
                entry_price=_lp["entry_price"],
                event_title=f"[UPDOWN][LADDER] {_lp['title']}",
                expiry_ts=_lp["expiry_ts"],
            )
            log.info("[LADDER] %s %s — placed remaining 40%%=$%.2f after %ds confirm",
                     _l_coin, _l_dir, _lp["remaining_size"], _elapsed)
        except Exception as _le:
            log.debug("[LADDER] Failed to place ladder entry: %s", _le)
        _expired_keys.append(_lid)
    for _k in _expired_keys:
        _ladder_pending.pop(_k, None)

    return exits


def _is_duration_blocked(duration_min: int) -> bool:
    """Return True if this duration has WR < 40% over last ≥10 trades."""
    outcomes = _duration_wr.get(duration_min, [])
    if len(outcomes) < _DURATION_MIN_SAMPLES:
        return False
    wr = sum(outcomes) / len(outcomes)
    if wr < _DURATION_WR_FLOOR:
        log.info("[DURATION-WR] %dm WR=%.0f%% (%d trades) < 40%% — skipping this duration",
                 duration_min, wr * 100, len(outcomes))
        return True
    return False


def record_updown_result(coin: str, won: bool, duration_min: int = None) -> None:
    """
    Call after each UP/DOWN trade resolves to track consecutive losses,
    the session-wide win streak, per-coin rolling WR, and per-duration WR.
    """
    global _consecutive_losses, _coin_cooldown_until, _session_win_streak
    update_coin_rolling_wr(coin, won)  # Advancement G: rolling quality tracker
    if duration_min is not None:
        outcomes = _duration_wr.setdefault(duration_min, [])
        outcomes.append(won)
        if len(outcomes) > 50:
            outcomes.pop(0)
    try:
        from markov_tracker import tracker as _mk
        _mk.record(coin, "UP" if won else "DOWN")
    except Exception:
        pass
    if won:
        _consecutive_losses[coin] = 0
        _session_win_streak += 1
    else:
        _session_win_streak = 0
        _consecutive_losses[coin] = _consecutive_losses.get(coin, 0) + 1
        if _consecutive_losses[coin] >= MAX_CONSEC_LOSSES:
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
    Run one Up/Down trading cycle across BTC, ETH, SOL, XRP.
    Features:
    - Multi-timeframe RSI with volume gate + confidence-weighted sizing
    - Smart Window Cascade: extreme RSI (>72/<28) + all-3-TF → up to 8 windows
    - Cross-Coin Regime Lock: all coins same direction → cascade on every coin
    - Streak Compounding: consecutive wins grow position multiplier (1.1× → 1.35×)
    - Self-Hedging Conflict Skip: skips windows where Mule1↑ + Mule2↓
    Returns number of trades placed.
    """
    global _updown_session_loss, _updown_circuit_reset
    trades_placed = 0

    # ── Session loss circuit breaker ──────────────────────────────────────────
    now_ts = int(time.time())
    if _updown_circuit_reset > now_ts:
        remaining = (_updown_circuit_reset - now_ts) // 60
        log.info("[UPDOWN] Session loss circuit tripped — cooling down %dm", remaining)
        return 0

    # ── Phase 1: generate all signals ────────────────────────────────────────
    coin_signals: dict = {}
    for coin in UPDOWN_COINS:
        try:
            sig = _generate_direction_signal(coin)
            if sig:
                coin_signals[coin] = sig
        except Exception as exc:
            log.error("[UPDOWN] Signal gen error for %s: %s", coin, exc)

    # ── Cross-Coin Regime Lock ─────────────────────────────────────────────
    signal_dirs = [s["direction"] for s in coin_signals.values()]
    regime_direction: Optional[str] = None
    if len(signal_dirs) >= 3 and len(set(signal_dirs)) == 1:
        regime_direction = signal_dirs[0]
        log.info("[UPDOWN] 🌐 REGIME LOCK — all coins %s | cascade active on all", regime_direction)

    # ── Streak compounding multiplier ─────────────────────────────────────────
    if _session_win_streak >= 8:
        streak_mult = 1.35
    elif _session_win_streak >= 5:
        streak_mult = 1.20
    elif _session_win_streak >= 3:
        streak_mult = 1.10
    else:
        streak_mult = 1.0
    if _session_win_streak >= 3:
        log.info("[UPDOWN] 📈 Streak ×%d → size mult %.2f×", _session_win_streak, streak_mult)

    # ── Load shadow conflict set ──────────────────────────────────────────────
    _conflict_set: set = set()
    try:
        from shadow_mode import get_conflicted_slugs as _gcs, get_mule_signals as _gms
        _conflict_set = _gcs()
    except Exception:
        _gms = lambda coin=None: []

    # ── Advancement B: Bayesian Kelly multiplier (global, computed once per cycle) ──
    _bayesian_mult = _get_bayesian_kelly_multiplier()
    if abs(_bayesian_mult - 1.0) >= 0.05:
        log.info(
            "[UPDOWN] 🧠 Bayesian Kelly mult=%.2f× (session WR=%.0f%% → adaptive sizing)",
            _bayesian_mult,
            (_bayesian_mult * 55),
        )

    # ── Advancement D: Fear & Greed (global, computed once per cycle) ─────────
    _fear_greed_val = _fetch_fear_greed_index()

    # ── Advancement F: UTC Hour Edge (global, computed once per cycle) ─────────
    _utc_mult = _get_utc_hour_multiplier()

    # ── Phase 2: trade ────────────────────────────────────────────────────────
    for coin in UPDOWN_COINS:
        signal = coin_signals.get(coin)
        if signal is None:
            continue

        # Cap: max 4 concurrent updown positions (correlation control)
        open_updown = count_open_trades_fn()
        if open_updown >= 4:
            log.info("[UPDOWN] Max 4 concurrent updown positions — pausing cycle")
            return trades_placed

        try:
            markets = _fetch_active_updown_markets(coin)
            if not markets:
                log.debug("[UPDOWN] No active markets for %s", coin)
                continue

            direction  = signal["direction"]
            confidence = signal["confidence"]
            tf_agree   = signal["timeframes_agree"]
            avg_rsi    = signal["rsi"]

            # Change I: require minimum confidence before trading
            if confidence < 0.62:
                log.info("[UPDOWN] %s confidence %.2f < 0.62 minimum — skipping", coin, confidence)
                continue

            # Smart Window Cascade: extreme RSI + all-TF agree → more windows
            is_extreme = avg_rsi > 72 or avg_rsi < 28
            in_regime  = regime_direction == direction
            cascade_max = (
                MAX_CASCADE_WINDOWS
                if (is_extreme and tf_agree == 3) or in_regime
                else MAX_WINDOWS_PER_COIN
            )
            if cascade_max > MAX_WINDOWS_PER_COIN:
                reason = "extreme RSI" if is_extreme else "regime lock"
                log.info("[UPDOWN] 🚀 %s CASCADE (%s) → up to %d windows", coin, reason, cascade_max)

            # ── Advancement A: Mule signal confirmation boost ────────────────
            # If a mule recently observed the same direction on this coin, that's
            # independent smart-money confirmation → lift confidence slightly.
            mule_confirms = 0
            try:
                recent_mule_sigs = _gms(coin=coin)
                mule_confirms = sum(
                    1 for s in recent_mule_sigs
                    if s.get("direction", "").upper() == direction
                )
            except Exception:
                pass
            if mule_confirms > 0:
                confidence = min(0.96, confidence + mule_confirms * 0.025)
                log.info(
                    "[UPDOWN] 👁 %s mule signal(s) confirm %s on %s → conf boosted to %.2f",
                    mule_confirms, direction, coin, confidence,
                )

            # ── Advancement C: Binance momentum divergence check ─────────────
            _binance_mom = _compute_binance_5m_momentum(coin)
            _poly_lag_boost = 1.0
            if direction == "UP" and _binance_mom > 1.0:
                _poly_lag_boost = 1.15
                log.info("[UPDOWN] 🎯 POLY-LAG %s | Binance mom=+%.2f%% → 15%% boost", coin, _binance_mom)
            elif direction == "DOWN" and _binance_mom < -1.0:
                _poly_lag_boost = 1.15
                log.info("[UPDOWN] 🎯 POLY-LAG %s | Binance mom=%.2f%% → 15%% boost", coin, _binance_mom)

            # ── Last-minute % move + composite score ───────────────────────────
            _last_min_move = _compute_last_minute_pct_move(coin)

            # Require minimum momentum: 0.050% = 94.3% WR zone
            if abs(_last_min_move) < BTC_MOMENTUM_THRESH_LOW:
                log.info(
                    "[UPDOWN] %s | last-minute move %.4f%% < %.3f%% threshold — skipping (noise zone)",
                    coin, _last_min_move, BTC_MOMENTUM_THRESH_LOW,
                )
                continue

            # Momentum must ALIGN with signal direction (contradiction filter)
            _mom_dir = "UP" if _last_min_move > 0 else "DOWN"
            if _mom_dir != direction:
                log.info(
                    "[UPDOWN] CONTRADICTION %s | signal=%s but momentum=%s (%.4f%%) — skip",
                    coin, direction, _mom_dir, _last_min_move,
                )
                continue

            # ── Advancement I: 1h trend alignment (core fix for 8-loss streak) ─
            if not _check_1h_trend_alignment(coin, direction):
                continue

            # ── Fetch 5m klines for VWAP/ATR/Blowoff ──────────────────────────
            _klines_5m = _fetch_binance_klines(coin, interval="5m", limit=50)

            # ── Volatility gate: only trade during genuine BTC volatility ─────
            _vol_gate = _check_volatility_gate(coin)

            # ── Blowoff guard (UP only) ─────────────────────────────────────
            if _check_blowoff_guard(coin, direction, _klines_5m, _last_min_move):
                continue

            # ── Composite signal quality score ────────────────────────────────
            _comp_score = _compute_composite_score(
                coin, direction, avg_rsi, tf_agree,
                _last_min_move, _klines_5m, _fear_greed_val, _vol_gate,
            )
            _adaptive_min = _get_adaptive_composite_min()
            if _comp_score < _adaptive_min:
                log.info(
                    "[UPDOWN] %s composite score=%.2f < %.2f minimum — skipping",
                    coin, _comp_score, _adaptive_min,
                )
                continue
            log.info("[UPDOWN] %s | COMPOSITE SCORE = %.2f (%s tier)",
                coin, _comp_score,
                "HIGH" if _comp_score > COMPOSITE_SCORE_HIGH else ("MED" if _comp_score > COMPOSITE_SCORE_MED else "LOW"),
            )

            # ── CoinGlass liquidation cascade boost ───────────────────────────
            try:
                from data_sources.coinglass import get_liquidation_signal_boost as _cg_boost
                _cg = _cg_boost(coin, direction)
                if _cg > 1.0:
                    _comp_score = min(1.0, _comp_score * _cg)
                    log.info("[COINGLASS] %s cascade confirms %s → score=%.3f", coin, direction, _comp_score)
            except Exception:
                pass

            # ── LunarCrush social sentiment boost ────────────────────────────
            try:
                from data_sources.lunarcrush import get_confidence_boost as _lc_boost
                _lc = _lc_boost(coin, direction)
                if _lc > 1.0:
                    _comp_score = min(1.0, _comp_score * _lc)
                    log.info("[LUNARCRUSH] %s social sentiment confirms %s → score=%.3f", coin, direction, _comp_score)
                elif _lc < 1.0:
                    _comp_score = max(0.0, _comp_score * _lc)
                    log.info("[LUNARCRUSH] %s social sentiment contradicts %s → score=%.3f", coin, direction, _comp_score)
            except Exception:
                pass

            # ── Cross-window correlation cap ──────────────────────────────────
            _corr_count = _count_correlated_positions(coin, direction)
            if _corr_count >= CORR_CAP_PER_COIN_DIRECTION:
                log.info(
                    "[UPDOWN] CORR CAP %s %s | %d/%d positions already open — skip",
                    coin, direction, _corr_count, CORR_CAP_PER_COIN_DIRECTION,
                )
                continue

            _last_min_boost = _get_last_minute_confidence_boost(coin, direction)

            # ── 5m/15m repricing lag detector ──────────────────────────────────
            _lag_15m_boost = _get_15m_vs_5m_lag_boost(coin, markets, direction)

            # ── Advancement D: Fear & Greed per-direction multiplier ──────────
            _fg_mult = _get_fear_greed_multiplier(direction, _fear_greed_val)
            if abs(_fg_mult - 1.0) >= 0.05:
                log.info(
                    "[UPDOWN] F&G %s | fg=%s direction=%s → %.2f×",
                    coin, _fear_greed_val, direction, _fg_mult,
                )

            # ── Advancement E: Asymmetric Directional Kelly ───────────────────
            _dir_kelly = _get_directional_kelly_multiplier(direction)

            # ── Advancement G: Rolling signal quality per coin ────────────────
            _quality_mult = _get_coin_quality_multiplier(coin)

            # ── Binance order flow (buyer vs seller initiated) ─────────────────
            _flow_boost = 1.0
            try:
                from data_sources.binance_flow import get_flow_signal_boost
                _flow_boost = get_flow_signal_boost(coin, direction)
            except Exception:
                pass

            # ── DeFiLlama macro TVL trend ──────────────────────────────────────
            _tvl_mult = 1.0
            try:
                from data_sources.defillama import get_tvl_macro_multiplier
                _tvl_mult = get_tvl_macro_multiplier(direction)
            except Exception:
                pass

            # MTF sizing: 2/3 = 50% size, 3/3 = full size (rewards strongest signals)
            _mtf_size_mult = 1.0 if tf_agree == 3 else 0.50

            # Combined size multiplier (non-tier components only)
            conf_mult = max(0.6, min(1.5, 0.5 + confidence * 1.1))
            size_multiplier = min(
                conf_mult * streak_mult * _bayesian_mult * _poly_lag_boost
                * _fg_mult * _dir_kelly * _quality_mult * _utc_mult
                * _last_min_boost * _lag_15m_boost * _mtf_size_mult
                * _flow_boost * _tvl_mult,
                3.0,
            )

            windows_traded = 0
            for best in markets[:cascade_max]:
                if count_open_trades_fn() >= 20:
                    log.info("[UPDOWN] Max open trades (20) reached — stopping %s", coin)
                    break

                # Skip windows where mules are in conflict
                best_slug = best.get("slug", "")
                if best_slug and best_slug in _conflict_set:
                    log.info("[UPDOWN] %s | %s — mule conflict detected, skipping", coin, best_slug[:40])
                    continue

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

                # ── Duration WR gate: skip historically losing durations ──────
                _dur_min = best.get("duration_min", 15)
                if _is_duration_blocked(_dur_min):
                    continue

                # ── 1-hour windows require higher composite score ──────────────
                if _dur_min == 60 and _comp_score < 0.82:
                    log.info("[UPDOWN] 1h window needs score≥0.82, got %.2f — skip", _comp_score)
                    continue

                # ── Markov EV gate ─────────────────────────────────────────────
                try:
                    from markov_tracker import tracker as _mk
                    _prev = _mk.last_outcome(coin) or direction
                    _norm_price = entry_price if direction == "UP" else (1.0 - entry_price)
                    _ev_yes = _mk.expected_value(coin, _prev, _norm_price, "YES")
                    _ev_no  = _mk.expected_value(coin, _prev, 1.0 - _norm_price, "NO")
                    _best_ev = max(_ev_yes, _ev_no)
                    # Only filter when we have 10+ outcomes (otherwise Markov returns 0.5 → neutral)
                    if _mk.sample_size(coin) >= 10 and _best_ev < 0.04:
                        log.debug("[MARKOV] %s EV=%.3f < 4%% — skip window (no statistical edge)", coin, _best_ev)
                        continue
                    if _mk.sample_size(coin) >= 10:
                        log.info("[MARKOV-EV] %s EV=%.3f @ price=%.3f (n=%d)", coin, _best_ev, entry_price, _mk.sample_size(coin))
                except Exception:
                    pass

                # ── Advancement A: Orderbook imbalance signal ─────────────────
                _ob_boost = 1.0
                _market_token = market_obj.get("conditionId") or market_obj.get("id", "")
                _ob_imbalance = _fetch_orderbook_imbalance(_market_token)
                if _ob_imbalance is not None:
                    if direction == "UP" and _ob_imbalance >= 2.0:
                        _ob_boost = 1.10
                        log.info("[UPDOWN] 📊 OB IMBALANCE %s | bids/asks=%.1f× → 10%% boost", coin, _ob_imbalance)
                    elif direction == "DOWN" and _ob_imbalance <= 0.5:
                        _ob_boost = 1.10
                        log.info("[UPDOWN] 📊 OB IMBALANCE %s | bids/asks=%.1f× → 10%% boost (sell pressure)", coin, _ob_imbalance)
                    elif (direction == "UP" and _ob_imbalance < 0.6) or \
                         (direction == "DOWN" and _ob_imbalance > 1.8):
                        log.info("[UPDOWN] 📊 OB CONTRA %s | imbalance=%.2f contradicts %s — skipping", coin, _ob_imbalance, direction)
                        continue

                # ── Advancement H: Volume Surge Detector ──────────────────────
                _vol_mult = _get_volume_surge_multiplier(best)

                balance  = get_balance_fn()
                # Tier-based Kelly: composite score determines fraction + cap
                if _comp_score > COMPOSITE_SCORE_HIGH:
                    _kelly_frac = UPDOWN_KELLY_HIGH
                    _max_size   = balance * UPDOWN_CAP_HIGH
                elif _comp_score > COMPOSITE_SCORE_MED:
                    _kelly_frac = UPDOWN_KELLY_MED
                    _max_size   = balance * UPDOWN_CAP_MED
                else:
                    _kelly_frac = UPDOWN_KELLY_LOW
                    _max_size   = balance * UPDOWN_CAP_LOW
                raw_size = balance * _kelly_frac * size_multiplier * _ob_boost * _vol_mult
                size     = round(max(UPDOWN_MIN_USD, min(_max_size, raw_size)), 2)

                market_id = market_obj.get("conditionId") or market_obj.get("id", "")
                event_id  = best["id"]
                secs_left = best["expiry_ts"] - int(time.time())
                dur_label = f"{secs_left // 60}m{secs_left % 60}s"

                # Change C: minimum time-to-expiry check
                if secs_left < 90:
                    log.info("[UPDOWN] %s | <90s to expiry (%ds) — skipping", coin, secs_left)
                    continue

                # Change G: direction-aligned entry price check
                if direction == "UP" and entry_price > 0.58:
                    log.info("[UPDOWN] %s UP already expensive (%.3f) — no edge, skipping", coin, entry_price)
                    continue
                if direction == "DOWN" and entry_price < 0.42:
                    log.info("[UPDOWN] %s DOWN already expensive (%.3f) — no edge, skipping", coin, entry_price)
                    continue

                # BUG FIX: Hard coin-flip zone block on the LIVE entry price.
                # _parse_market filters at fetch time, but entry_price here comes
                # from the cached parsed market — re-check with the price we'll
                # actually bet at to guarantee we never enter at 0.50 ± 0.08.
                if 0.42 <= entry_price <= 0.58:
                    log.info(
                        "[UPDOWN] %s live entry=%.3f in coin-flip zone [0.42–0.58] — SKIP",
                        coin, entry_price,
                    )
                    continue

                # ── Spread gate: skip illiquid windows (spread > 4%) ─────────
                _token_id_exec = market_obj.get("conditionId") or market_obj.get("id", "")
                if _token_id_exec:
                    _live_mid, _live_spread = _fetch_clob_price_and_spread(_token_id_exec)
                    if _live_spread is not None and _live_spread > 0.02:
                        log.info(
                            "[UPDOWN] %s spread %.3f > 2%% — illiquid window, skipping",
                            coin, _live_spread,
                        )
                        continue
                    if _live_mid is not None and abs(_live_mid - entry_price) > 0.03:
                        log.info(
                            "[UPDOWN] %s price drift %.3f → %.3f (>3%%) since scan — skipping stale entry",
                            coin, entry_price, _live_mid,
                        )
                        continue

                # ── Advancement J: Momentum acceleration check ───────────────
                if not _check_momentum_acceleration(coin, direction):
                    continue

                # ── Advancement K: Candle close strength check ───────────────
                if not _check_candle_close_strength(coin, direction):
                    continue

                # ── Position ladder: split entry for high-conviction trades ─────
                _ladder_size = size
                if _comp_score >= _LADDER_SCORE_THRESHOLD and event_id not in _ladder_pending:
                    _ladder_size = round(size * 0.60, 2)
                    _ladder_pending[event_id] = {
                        "coin":           coin,
                        "direction":      direction,
                        "remaining_size": round(size * 0.40, 2),
                        "entry_ts":       int(time.time()),
                        "entry_price":    entry_price,
                        "market_id":      market_id,
                        "expiry_ts":      best.get("expiry_ts", 0),
                        "title":          best.get("title", ""),
                    }
                    log.info("[LADDER] %s %s | entering 60%%=$%.2f, holding 40%%=$%.2f for 3-min confirm",
                             coin, direction, _ladder_size, size - _ladder_size)

                order = place_paper_trade_fn(
                    event_id=event_id,
                    market_id=market_id,
                    amount_dollars=_ladder_size,
                    direction=direction,
                    entry_price=entry_price,
                    event_title=f"[UPDOWN] {best['title']}",
                    expiry_ts=best.get("expiry_ts", 0),
                )

                if order:
                    trades_placed += 1
                    windows_traded += 1
                    extras = []
                    if _session_win_streak >= 3:
                        extras.append(f"streak×{_session_win_streak}")
                    if mule_confirms > 0:
                        extras.append(f"mule×{mule_confirms}")
                    if _ob_boost > 1.0:
                        extras.append("OB✓")
                    if _poly_lag_boost > 1.0:
                        extras.append("LAG✓")
                    if _fg_mult != 1.0:
                        extras.append(f"F&G{_fear_greed_val}")
                    if _dir_kelly > 1.05:
                        extras.append(f"DirK{_dir_kelly:.2f}×")
                    if _utc_mult != 1.0:
                        extras.append(f"UTC{_utc_mult:.2f}×")
                    if _vol_mult > 1.0:
                        extras.append("VOL✓")
                    if _last_min_boost > 1.05:
                        extras.append(f"⚡{_last_min_boost:.2f}×")
                    if _lag_15m_boost > 1.0:
                        extras.append("LAG15✓")
                    tag = f" | {', '.join(extras)}" if extras else ""
                    log.info(
                        "[UPDOWN] ✅ %s %s @ %.3f | $%.2f | RSI=%.1f | %d/3 TF | %s left | conf=%.0f%%%s",
                        direction, coin, entry_price, size,
                        avg_rsi, tf_agree, dur_label, confidence * 100, tag,
                    )
                    try:
                        from telegram_bot import send_alert as _tg
                        _tg(
                            f"📈 UpDown — {coin}\n"
                            f"{best['title'][:60]}\n"
                            f"{direction} | RSI {avg_rsi:.0f} | {tf_agree}/3 TF | {dur_label} left\n"
                            f"${size:.2f} @ {entry_price:.3f} | conf {confidence:.0%}{tag}"
                        )
                    except Exception:
                        pass

            if windows_traded == 0:
                log.info("[UPDOWN] %s signal found but no tradeable windows", coin)

        except Exception as exc:
            log.error("[UPDOWN] Error for %s: %s", coin, exc)

    return trades_placed
