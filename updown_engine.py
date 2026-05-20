"""
updown_engine.py - ZiSi pBot-Intelligence Up/Down Engine
UpDownEngine class: regime-aware, dual-entry, circuit-breaker, inversion monitor.
"""
import asyncio
import logging
import time
import requests
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger("zisi.engine")

POLY_GAMMA_API = "https://gamma-api.polymarket.com"
POLY_CLOB_API  = "https://clob.polymarket.com"
BINANCE_API    = "https://api.binance.com/api/v3"

# ── Tier-based Kelly sizing ───────────────────────────────────────────────────
KELLY = {
    "HIGH": (0.040, 0.150),   # score >= 0.85: 4% Kelly, 15% cap
    "MED":  (0.030, 0.100),   # score 0.75-0.85: 3% Kelly, 10% cap
    "LOW":  (0.015, 0.050),   # score 0.62-0.75: 1.5% Kelly, 5% cap
}
MIN_USD = 1.00
VOLUME_GATE_FLOORS = {"BTC": 2.0, "ETH": 10.0, "SOL": 75.0, "XRP": 5000.0}
UPDOWN_MIN_LIQUIDITY = 500.0

# ── Score -> WR -> max entry price ───────────────────────────────────────────
SCORE_TO_WR = [
    (0.85, 0.70),   # score >= 0.85 -> est WR 70% -> max entry 60c
    (0.75, 0.65),   # score 0.75-0.85 -> est WR 65% -> max entry 55c
    (0.62, 0.57),   # score 0.62-0.75 -> est WR 57% -> max entry 47c
]


def _lookup_wr(score: float) -> Optional[float]:
    for threshold, wr in SCORE_TO_WR:
        if score >= threshold:
            return wr
    return None


def price_gate_passes(price: float, score: float) -> bool:
    """Punisher rule: entry price must be >= 10c below estimated WR."""
    est_wr = _lookup_wr(score)
    if est_wr is None:
        return False
    passes = price <= (est_wr - 0.10)
    if not passes:
        log.info("[ENGINE] Price gate FAIL: %.2f > WR(%.2f)-0.10=%.2f", price, est_wr, est_wr - 0.10)
    return passes


def _fetch_klines(symbol: str, interval: str, limit: int) -> list:
    try:
        r = requests.get(
            f"{BINANCE_API}/klines",
            params={"symbol": f"{symbol}USDT", "interval": interval, "limit": limit},
            timeout=8,
        )
        return r.json() if r.status_code == 200 else []
    except Exception:
        return []


def _compute_rsi(closes: list, period: int = 14) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period
    if al == 0:
        return 100.0
    return round(100 - (100 / (1 + ag / al)), 2)


def _compute_momentum(closes: list, lookback: int = 5) -> float:
    if len(closes) < lookback + 1:
        return 0.0
    return (closes[-1] - closes[-lookback]) / closes[-lookback] * 100


def _fetch_clob_price(token_id: str) -> Optional[float]:
    if not token_id:
        return None
    try:
        r = requests.get(f"{POLY_CLOB_API}/book", params={"token_id": token_id}, timeout=5)
        if r.status_code == 200:
            data = r.json()
            bids = data.get("bids", [])
            asks = data.get("asks", [])
            bb = float(bids[0].get("price", 0)) if bids else 0.0
            ba = float(asks[0].get("price", 0)) if asks else 0.0
            if bb > 0 and ba > 0:
                return round((bb + ba) / 2, 4)
            return ba or bb or None
    except Exception:
        return None
    return None


def _fetch_spread(token_id: str) -> Optional[float]:
    if not token_id:
        return None
    try:
        r = requests.get(f"{POLY_CLOB_API}/book", params={"token_id": token_id}, timeout=5)
        if r.status_code == 200:
            data = r.json()
            bids = data.get("bids", [])
            asks = data.get("asks", [])
            bb = float(bids[0].get("price", 0)) if bids else 0.0
            ba = float(asks[0].get("price", 0)) if asks else 0.0
            if bb > 0 and ba > 0:
                return round(ba - bb, 4)
    except Exception:
        return None
    return None


class UpDownEngine:
    """Per-asset Up/Down trading engine with pBot intelligence."""

    def __init__(self, asset: str, timeframe: str, state_mgr, telegram_fn=None):
        self.asset      = asset
        self.timeframe  = timeframe
        self.state_mgr  = state_mgr
        self.telegram   = telegram_fn or (lambda msg: None)

        self.consecutive_losses: int = 0
        self.skip_windows:       int = 0
        self.invert_signal:     bool = False
        self._recent_outcomes:  list = []   # True=win, False=loss; rolling 40

    # ── Circuit breaker ───────────────────────────────────────────────────────

    def record_outcome(self, won: bool) -> None:
        """Update consecutive-loss counter and rolling WR for inversion check."""
        self._recent_outcomes.append(won)
        if len(self._recent_outcomes) > 40:
            self._recent_outcomes.pop(0)

        if won:
            self.consecutive_losses = 0
        else:
            self.consecutive_losses += 1
            if self.consecutive_losses >= 2:
                self.skip_windows = 2
                self.consecutive_losses = 0
                self.telegram(
                    f"WARNING {self.asset}/{self.timeframe}: 2 consecutive losses — pausing 2 windows"
                )
                log.info("[ENGINE] %s/%s: circuit breaker — skip 2 windows", self.asset, self.timeframe)

        self._check_inversion()

    def _check_inversion(self) -> None:
        if len(self._recent_outcomes) < 40:
            return
        rolling_wr = sum(self._recent_outcomes) / 40
        from config import INVERSION_TRIGGER_WR, INVERSION_RECOVERY_WR
        if rolling_wr < INVERSION_TRIGGER_WR and not self.invert_signal:
            self.invert_signal = True
            self.telegram(
                f"INVERT {self.asset}/{self.timeframe}: WR={rolling_wr:.0%} over 40 windows — INVERTING signal"
            )
            log.warning("[ENGINE] %s/%s: WR=%.0f%% — signal INVERTED", self.asset, self.timeframe, rolling_wr * 100)
        elif rolling_wr > INVERSION_RECOVERY_WR and self.invert_signal:
            self.invert_signal = False
            self.telegram(
                f"REVERT {self.asset}/{self.timeframe}: WR recovered to {rolling_wr:.0%} — reverting inversion"
            )
            log.info("[ENGINE] %s/%s: WR=%.0f%% — inversion REVERTED", self.asset, self.timeframe, rolling_wr * 100)

    # ── Signal generation ─────────────────────────────────────────────────────

    def generate_signal(self) -> Optional[dict]:
        """Return {direction, score, price_up, price_dn, market} or None."""
        if self.skip_windows > 0:
            self.skip_windows -= 1
            log.info("[ENGINE] %s/%s: skipping window (circuit breaker: %d left)", self.asset, self.timeframe, self.skip_windows + 1)
            return None

        from regime_filter import get_regime_mode, time_gate_open, apply_regime
        if not time_gate_open():
            log.debug("[ENGINE] %s/%s: time gate closed", self.asset, self.timeframe)
            return None

        # Fetch klines for the primary timeframe
        tf_map = {"5m": ("5m", 30), "15m": ("15m", 30)}
        interval, limit = tf_map.get(self.timeframe, ("5m", 30))
        klines = _fetch_klines(self.asset, interval, limit)
        if len(klines) < 16:
            return None

        closes = [float(k[4]) for k in klines]
        rsi = _compute_rsi(closes)
        mom = _compute_momentum(closes)
        if rsi is None:
            return None

        # Volume gate
        volumes = [float(k[5]) for k in klines]
        avg_vol = sum(volumes[:-1]) / max(1, len(volumes) - 1)
        cur_vol = volumes[-2] if len(volumes) >= 2 else volumes[-1]
        floor = VOLUME_GATE_FLOORS.get(self.asset, 0.0)
        if cur_vol < floor and cur_vol < 0.30 * avg_vol:
            log.info("[ENGINE] %s/%s: volume gate fail (%.0f < floor %.0f)", self.asset, self.timeframe, cur_vol, floor)
            return None

        # Raw direction from RSI
        if rsi > 60 and mom > 0:
            raw_dir = "UP"
            score_base = min(0.85, 0.50 + (rsi - 60) / 40 * 0.35)
        elif rsi < 40 and mom < 0:
            raw_dir = "DOWN"
            score_base = min(0.85, 0.50 + (40 - rsi) / 40 * 0.35)
        else:
            return None

        # Apply regime (invert on weekends)
        regime = get_regime_mode()
        direction = apply_regime(raw_dir, regime)
        if self.invert_signal:
            direction = "DOWN" if direction == "UP" else "UP"

        # Composite score
        abs_mom = abs(mom)
        score = score_base
        if abs_mom >= 0.15:
            score = min(1.0, score + 0.20)
        elif abs_mom >= 0.08:
            score = min(1.0, score + 0.15)
        elif abs_mom >= 0.05:
            score = min(1.0, score + 0.10)
        score = round(score, 4)

        if score < 0.62:
            return None

        # Fetch active market
        market = self._fetch_market()
        if not market:
            return None

        return {
            "asset":     self.asset,
            "timeframe": self.timeframe,
            "direction": direction,
            "score":     score,
            "regime":    regime,
            "inverted":  self.invert_signal,
            "rsi":       rsi,
            "momentum":  round(mom, 4),
            "market":    market,
        }

    def _fetch_market(self) -> Optional[dict]:
        """Fetch the nearest active Up/Down market for this asset/timeframe."""
        coin_lower = self.asset.lower()
        dur_min = 5 if self.timeframe == "5m" else 15
        now_ts = int(time.time())
        interval = dur_min * 60
        boundary = ((now_ts + interval) // interval) * interval

        for offset in range(4):
            expiry_ts = boundary + offset * interval
            if expiry_ts < now_ts + 30:
                continue
            slug = f"{coin_lower}-updown-{dur_min}m-{expiry_ts}"
            try:
                r = requests.get(f"{POLY_GAMMA_API}/events", params={"slug": slug}, timeout=10)
                if r.status_code != 200:
                    continue
                raw = r.json()
                evs = [raw] if isinstance(raw, dict) and "id" in raw else (raw if isinstance(raw, list) else raw.get("data", []))
                for ev in evs:
                    liq = float(ev.get("liquidity", 0))
                    if liq < UPDOWN_MIN_LIQUIDITY:
                        continue
                    markets = ev.get("markets", [])
                    up_m = dn_m = None
                    up_price = dn_price = 0.5
                    for mkt in markets:
                        outcomes = mkt.get("outcomes", [])
                        q = str(mkt.get("question", mkt.get("title", ""))).lower()
                        is_up = any(o.lower() == "up" for o in outcomes) or ("up" in q and "down" not in q)
                        token_id = mkt.get("conditionId") or mkt.get("id", "")
                        price = _fetch_clob_price(token_id) or float(mkt.get("lastTradePrice") or 0.5)
                        spread = _fetch_spread(token_id)
                        if spread is not None and spread > 0.03:
                            continue
                        if is_up and up_m is None:
                            up_m = mkt; up_price = price
                        elif not is_up and dn_m is None:
                            dn_m = mkt; dn_price = price
                    if up_m is None or dn_m is None:
                        continue
                    if up_price >= 0.90 or up_price <= 0.10:
                        continue
                    if 0.42 <= up_price <= 0.58:
                        continue
                    return {
                        "event_id":    ev.get("id", ""),
                        "event_title": ev.get("title", ""),
                        "expiry_ts":   expiry_ts,
                        "duration_min": dur_min,
                        "liquidity":   liq,
                        "up_price":    up_price,
                        "dn_price":    dn_price,
                        "up_market":   up_m,
                        "dn_market":   dn_m,
                    }
            except Exception as exc:
                log.debug("[ENGINE] Market fetch error %s: %s", slug, exc)
        return None

    # ── Sizing ────────────────────────────────────────────────────────────────

    def compute_size(self, score: float, price: float, balance: float) -> float:
        """Return USD amount to bet (shares-first approach)."""
        if score >= 0.85:
            kelly_pct, cap_pct = KELLY["HIGH"]
        elif score >= 0.75:
            kelly_pct, cap_pct = KELLY["MED"]
        else:
            kelly_pct, cap_pct = KELLY["LOW"]
        usd = max(MIN_USD, min(kelly_pct * balance, cap_pct * balance))
        shares = round(usd / price)
        return shares * price  # actual cost from shares-first rounding

    # ── Dual-entry ────────────────────────────────────────────────────────────

    @staticmethod
    def should_dual_enter(up_price: float, dn_price: float) -> bool:
        from config import DUAL_ENTRY_MAX_COMBINED
        return (up_price + dn_price) < DUAL_ENTRY_MAX_COMBINED

    def compute_dual_sizes(self, score: float, main_price: float, hedge_price: float, balance: float):
        main_usd  = self.compute_size(score, main_price, balance)
        hedge_usd = round(0.25 * main_usd, 2)
        return main_usd, hedge_usd
