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
UPDOWN_KELLY_BASE   = 0.022   # 2.2% baseline (increased for higher throughput)
UPDOWN_MIN_USD      = 1.00
UPDOWN_MAX_USD      = 15.00   # raised cap

# Volume gate: current candle volume must be >= this fraction of 20-period avg
VOLUME_GATE_RATIO = 0.65

# Min liquidity for Up/Down market to be tradeable
UPDOWN_MIN_LIQUIDITY = 500.0

# Coins we trade
UPDOWN_COINS = ["BTC", "ETH", "SOL", "XRP"]

# Max windows to trade per coin per cycle (normal)
MAX_WINDOWS_PER_COIN = 5
# Smart cascade: extreme RSI or regime lock → up to this many windows per coin
MAX_CASCADE_WINDOWS  = 8

# Per-coin consecutive loss tracking (resets to 0 on win)
_consecutive_losses: dict = {"BTC": 0, "ETH": 0, "SOL": 0, "XRP": 0}

# Auto-cooldown: skip a coin for N cycles after MAX_CONSEC_LOSSES consecutive losses
MAX_CONSEC_LOSSES = 5
_coin_cooldown_until: dict = {"BTC": 0, "ETH": 0, "SOL": 0, "XRP": 0}

# Session-wide win streak for compounding multiplier
_session_win_streak: int = 0


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
    Call after each UP/DOWN trade resolves to track consecutive losses,
    the session-wide win streak, and the per-coin rolling win rate (Advancement G).
    """
    global _consecutive_losses, _coin_cooldown_until, _session_win_streak
    update_coin_rolling_wr(coin, won)  # Advancement G: rolling quality tracker
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
    trades_placed = 0

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

        try:
            markets = _fetch_active_updown_markets(coin)
            if not markets:
                log.info("[UPDOWN] No active markets for %s", coin)
                continue

            direction  = signal["direction"]
            confidence = signal["confidence"]
            tf_agree   = signal["timeframes_agree"]
            avg_rsi    = signal["rsi"]

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

            # ── Advancement D: Fear & Greed per-direction multiplier ──────────
            _fg_mult = _get_fear_greed_multiplier(direction, _fear_greed_val)
            if abs(_fg_mult - 1.0) >= 0.05:
                log.info(
                    "[UPDOWN] 😨 F&G %s | fg=%s direction=%s → %.2f×",
                    coin, _fear_greed_val, direction, _fg_mult,
                )

            # ── Advancement E: Asymmetric Directional Kelly ───────────────────
            _dir_kelly = _get_directional_kelly_multiplier(direction)
            if abs(_dir_kelly - 1.0) >= 0.05:
                log.info("[UPDOWN] 📐 Directional Kelly %s %s → %.2f×", coin, direction, _dir_kelly)

            # ── Advancement G: Rolling signal quality per coin ────────────────
            _quality_mult = _get_coin_quality_multiplier(coin)

            # Combined size multiplier — all multipliers applied, capped at 2.8×
            conf_mult = max(0.6, min(1.5, 0.5 + confidence * 1.1))
            size_multiplier = min(
                conf_mult * streak_mult * _bayesian_mult * _poly_lag_boost
                * _fg_mult * _dir_kelly * _quality_mult * _utc_mult,
                2.8,
            )

            windows_traded = 0
            for best in markets[:cascade_max]:
                if count_open_trades_fn() >= 35:
                    log.info("[UPDOWN] Max open trades (35) reached — stopping %s", coin)
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
                raw_size = balance * UPDOWN_KELLY_BASE * size_multiplier * _ob_boost * _vol_mult
                size     = round(max(UPDOWN_MIN_USD, min(UPDOWN_MAX_USD, raw_size)), 2)

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
