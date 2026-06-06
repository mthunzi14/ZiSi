"""
updown_engine.py - ZiSi Intelligence Up/Down Engine (Async Restructured)
"""
import asyncio
import logging
import time
import requests
import aiohttp
from datetime import datetime, timezone
from typing import Optional

from infrastructure.state.technical_cache import TechnicalDataCache
from infrastructure.websocket.spot_websocket_ingest import get_current_ofi

log = logging.getLogger("zisi.engine")

# Live engine instances keyed by "ASSET/timeframe" for outcome feedback
_ENGINE_REGISTRY: dict[str, "UpDownEngine"] = {}


def register_engine(instance: "UpDownEngine") -> None:
    key = f"{instance.asset}/{instance.timeframe}"
    _ENGINE_REGISTRY[key] = instance


def notify_trade_outcome(event_title: str, won: bool) -> None:
    """Feed closed trade result into the matching UpDownEngine (circuit breaker / inversion)."""
    import re
    ma = re.search(r"\[(BTC|ETH|SOL|XRP)\]", event_title or "")
    mt = re.search(r"\[(5m|15m|1h)\]", event_title or "")
    if not ma or not mt:
        return
    eng = _ENGINE_REGISTRY.get(f"{ma.group(1)}/{mt.group(1)}")
    if eng:
        eng.record_outcome(won)

POLY_GAMMA_API = "https://gamma-api.polymarket.com"
POLY_CLOB_API  = "https://clob.polymarket.com"
BINANCE_API    = "https://api.binance.com/api/v3"

_GATE_LOG_PATH = None  # resolved lazily on first write


def _write_gate_event(asset: str, timeframe: str, gate: str, direction: str, reason: str) -> None:
    """Append one gate-block event to gate_log.jsonl for dashboard visibility."""
    global _GATE_LOG_PATH
    try:
        import json
        from pathlib import Path
        if _GATE_LOG_PATH is None:
            _GATE_LOG_PATH = Path(__file__).parent.parent.parent / "gate_log.jsonl"
        entry = {
            "ts": time.time(),
            "asset": asset,
            "tf": timeframe,
            "gate": gate,
            "direction": direction,
            "reason": reason,
        }
        with open(_GATE_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass

# Global single-flight Technical Cache shared across all engine instances
_cache = TechnicalDataCache()

# Tier-based Kelly sizing
KELLY = {
    "HIGH": (0.040, 0.150),   # score >= 0.85: 4% Kelly, 15% cap
    "MED":  (0.030, 0.100),   # score 0.75-0.85: 3% Kelly, 10% cap
    "LOW":  (0.015, 0.050),   # score 0.62-0.75: 1.5% Kelly, 5% cap
}
MIN_USD = 1.00
VOLUME_GATE_FLOORS = {"BTC": 2.0, "ETH": 10.0, "SOL": 75.0, "XRP": 5000.0, "DOGE": 10000.0}
UPDOWN_MIN_LIQUIDITY = 0.0

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


# ── Sync Fallbacks (retained for safety / backwards compatibility) ─────────────
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


def _fetch_clob_price(token_id: str) -> Optional[float]:
    if not token_id:
        return None
    try:
        r = requests.get(f"{POLY_CLOB_API}/book", params={"token_id": token_id}, timeout=5)
        if r.status_code == 200:
            data = r.json()
            bids = data.get("bids", [])
            asks = data.get("asks", [])
            bb = max([float(b.get("price", 0)) for b in bids]) if bids else 0.0
            ba = min([float(a.get("price", 0)) for a in asks]) if asks else 0.0
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
            bb = max([float(b.get("price", 0)) for b in bids]) if bids else 0.0
            ba = min([float(a.get("price", 0)) for a in asks]) if asks else 0.0
            if bb > 0 and ba > 0:
                return round(ba - bb, 4)
    except Exception:
        return None
    return None


# ── Asynchronous Non-Blocking High-Frequency Adapters ─────────────────────────

async def _fetch_klines_async(session: aiohttp.ClientSession, symbol: str, interval: str, limit: int) -> list:
    """Fetch Binance klines using non-blocking, cached, collapsed requests."""
    async def _fetch():
        url = f"{BINANCE_API}/klines"
        params = {"symbol": f"{symbol}USDT", "interval": interval, "limit": limit}
        async with session.get(url, params=params, timeout=8) as r:
            if r.status == 200:
                return await r.json()
            return []

    cache_key = f"binance:klines:{symbol}:{interval}:{limit}"
    try:
        return await _cache.get(cache_key, 5.0, _fetch)
    except Exception:
        return []


async def _fetch_clob_book_async(session: aiohttp.ClientSession, token_id: str) -> Optional[dict]:
    """Fetch Polymarket order book using non-blocking, cached, collapsed requests."""
    if not token_id:
        return None
    async def _fetch():
        url = f"{POLY_CLOB_API}/book"
        params = {"token_id": token_id}
        async with session.get(url, params=params, timeout=5) as r:
            if r.status == 200:
                return await r.json()
            return None

    cache_key = f"clob:book:{token_id}"
    try:
        return await _cache.get(cache_key, 2.0, _fetch)
    except Exception:
        return None


def _parse_clob_book(book: Optional[dict]) -> tuple[Optional[float], Optional[float]]:
    """Parse bids/asks from CLOB book JSON to extract mid-price and spread in 1-pass."""
    if not book:
        return None, None
    bids = book.get("bids", [])
    asks = book.get("asks", [])
    bb = max([float(b.get("price", 0)) for b in bids]) if bids else 0.0
    ba = min([float(a.get("price", 0)) for a in asks]) if asks else 0.0
    price = None
    spread = None
    if bb > 0 and ba > 0:
        price = round((bb + ba) / 2, 4)
        spread = round(ba - bb, 4)
    elif ba > 0 or bb > 0:
        price = ba or bb or None
    return price, spread


async def _fetch_gamma_events_async(session: aiohttp.ClientSession, slug: str) -> list:
    """Fetch event details from Gamma API non-blockingly."""
    async def _fetch():
        url = f"{POLY_GAMMA_API}/events"
        params = {"slug": slug}
        async with session.get(url, params=params, timeout=10) as r:
            if r.status == 200:
                raw = await r.json()
                return [raw] if isinstance(raw, dict) and "id" in raw else (raw if isinstance(raw, list) else raw.get("data", []))
            return []

    cache_key = f"gamma:events:{slug}"
    try:
        return await _cache.get(cache_key, 10.0, _fetch)
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


class UpDownEngine:
    """Per-asset Up/Down trading engine with ZiSi intelligence."""

    def __init__(self, asset: str, timeframe: str, state_mgr, telegram_fn=None):
        self.asset      = asset
        self.timeframe  = timeframe
        self.state_mgr  = state_mgr
        self.telegram   = telegram_fn or (lambda msg: None)

        self.consecutive_losses: int = 0
        self.skip_windows:       int = 0
        self.invert_signal:     bool = False
        self._recent_outcomes:  list = []   # True=win, False=loss; rolling 40
        self._prefetched_markets: dict = {}  # boundary_ts -> market_dict
        self.last_edge_context: Optional[dict] = None
        self._slope_history:    list = []   # rolling 4 slope readings for choppy detection
        self._choppy_candles:   int  = 0    # candles remaining in choppy cooldown
        # L2 book circuit breaker — backs off 15 min after 5 consecutive failures
        self._l2_fail_count:    int   = 0
        self._l2_backoff_until: float = 0.0  # epoch seconds

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

    # ── Fair-value entry helper ───────────────────────────────────────────────

    def _fair_value_entry(self, klines, spot, up_price, dn_price, elapsed_min):
        """Fair-value (spot-distance) margin decision at the REAL live quotes.
        Returns decide_value_entry's dict plus fp_up/sigma_frac for logging."""
        from core.engine.fair_value import fair_prob_up, decide_value_entry
        from core.engine.regime_filter import get_regime_mode
        try:
            s_0 = float(klines[-1][1])          # current window open = strike
        except (IndexError, ValueError, TypeError):
            return {"direction": None, "edge": 0.0, "archetype": None, "fp_up": 0.5, "sigma_frac": 0.0}
        total_min = 60.0 if self.timeframe == "1h" else float(int(self.timeframe.rstrip("m")))
        trs = []
        for i in range(max(1, len(klines) - 14), len(klines)):
            h, l, pc = float(klines[i][2]), float(klines[i][3]), float(klines[i - 1][4])
            trs.append(max(h - l, abs(h - pc), abs(l - pc)))
        atr = (sum(trs) / len(trs)) if trs else 0.0
        sigma_frac = (atr / s_0) if s_0 else 0.01
        # ETH sigma floor: prevents FV from firing on micro-moves for ETH
        if self.asset == "ETH" and sigma_frac < 0.0040:
            sigma_frac = 0.0040
            log.debug("[ETH-SIGMA] ETH sigma_frac floored to 0.0040")

        # EMA-based drift calculation
        def _ema(prices, period):
            if len(prices) < period:
                return prices[-1] if prices else 0.0
            mult = 2.0 / (period + 1)
            ema = prices[0]
            for p in prices[1:]:
                ema = (p - ema) * mult + ema
            return ema

        closes = [float(k[4]) for k in klines]
        ema_5 = _ema(closes, 5)
        ema_20 = _ema(closes, 20)
        drift = (ema_5 - ema_20) / ema_20 if ema_20 > 0 else 0.0

        regime = get_regime_mode()

        fp_up = fair_prob_up(spot, s_0, sigma_frac, elapsed_min, total_min, drift=drift)
        dec = decide_value_entry(fp_up, up_price, dn_price, elapsed_min, total_min, regime=regime)
        dec["fp_up"] = round(fp_up, 4)
        dec["sigma_frac"] = round(sigma_frac, 6)
        return dec

    # ── Signal generation ─────────────────────────────────────────────────────

    async def generate_signal(self, session: aiohttp.ClientSession) -> Optional[dict]:
        """Return {direction, score, price_up, price_dn, market} or None."""
        if self.skip_windows > 0:
            log.info("[ENGINE] %s/%s: skipping window (circuit breaker active)", self.asset, self.timeframe)
            return None

        from core.engine.regime_filter import get_regime_mode, time_gate_open, apply_regime
        if not time_gate_open():
            log.debug("[ENGINE] %s/%s: time gate closed", self.asset, self.timeframe)
            return None

        # Fetch klines for the primary timeframe
        tf_map = {"5m": ("5m", 30), "15m": ("15m", 30), "1h": ("1h", 30)}
        interval, limit = tf_map.get(self.timeframe, ("5m", 30))
        klines = await _fetch_klines_async(session, self.asset, interval, limit)
        if len(klines) < 16:
            log.warning("[ENGINE] %s/%s: Insufficient candles (%d < 16) to calculate indicators.", self.asset, self.timeframe, len(klines))
            return None

        closes = [float(k[4]) for k in klines]

        # Pyth Hermes Real-time Price Integration
        try:
            from core.pyth_oracle_service import GLOBAL_ORACLE_CACHE
            pyth_price = GLOBAL_ORACLE_CACHE.get(self.asset, {}).get("price", 0.0)
            if pyth_price > 0.0:
                closes[-1] = pyth_price
                log.debug("[ENGINE] %s/%s overwrote last close with Pyth price: %.4f", self.asset, self.timeframe, pyth_price)
        except Exception as pyth_err:
            log.debug("[ENGINE] Failed to read Pyth price from cache: %s", pyth_err)

        # Recalculate market regime dynamically using BTC candle closes
        if self.asset == "BTC":
            try:
                from core.engine.regime_detector import RegimeDetector
                detector = RegimeDetector(timeframe=self.timeframe, atr_window=14)
                detector.update_prices(closes, symbol="BTC")
            except Exception as e:
                log.warning("[ENGINE] Failed to update regime detector: %s", e)

        rsi = _compute_rsi(closes)
        self._last_rsi = rsi  # Store RSI for fair-value paper fallbacks
        mom = _compute_momentum(closes)
        if rsi is None:
            log.warning("[ENGINE] %s/%s: RSI calculation returned None.", self.asset, self.timeframe)
            return None

        # Volume gate
        volumes = [float(k[5]) for k in klines]
        avg_vol = sum(volumes[:-1]) / max(1, len(volumes) - 1)
        cur_vol = volumes[-2] if len(volumes) >= 2 else volumes[-1]
        floor = VOLUME_GATE_FLOORS.get(self.asset, 0.0)
        if cur_vol < floor and cur_vol < 0.30 * avg_vol:
            log.info("[ENGINE] %s/%s: volume gate fail (current vol %.1f < floor %.1f or < 30%% of avg %.1f)", self.asset, self.timeframe, cur_vol, floor, avg_vol)
            return None
            
        # Volume Climax Detector (Blow-off Top/Bottom) - Loosened to 6.0x for 5m markets to avoid false blocking of strong breakouts
        vol_climax_threshold = 6.0 if self.timeframe == "5m" else 3.0
        if cur_vol > vol_climax_threshold * avg_vol:
            log.info("[ENGINE] %s/%s: Volume climax detected (current vol %.1f > %.1fx avg %.1f). Blocking trade to avoid blow-off top/bottom.", self.asset, self.timeframe, cur_vol, vol_climax_threshold, avg_vol)
            return None

        # Volume surge block: a sudden 4× spike vs rolling 5-candle avg signals a macro
        # move starting — FV model is too slow to reprice mid-surge, pause 2 candles.
        if len(volumes) >= 7:
            _roll_avg_vol = sum(volumes[-7:-2]) / 5
            if _roll_avg_vol > 0 and cur_vol > 4.0 * _roll_avg_vol:
                log.info(
                    "[VOL-SURGE] %s/%s: spike %.0f > 4x avg %.0f — 2-candle pause",
                    self.asset, self.timeframe, cur_vol, _roll_avg_vol,
                )
                self._choppy_candles = max(self._choppy_candles, 2)
                return None

        # Retrieve real-time Spot Order Flow Imbalance (OFI)
        ofi = await get_current_ofi(self.asset)

        from core.engine.regime_filter import get_regime_mode
        regime = get_regime_mode(self.timeframe)

        # Check if there is a strong 4/4 trend agreement for RSI trigger loosening (Sprint 11)
        trend_up_agreement = False
        trend_dn_agreement = False
        try:
            from core.engine.confluence_engine import ConfluenceEngine
            from core.engine.edge_orchestrator import edge_orchestrator
            if edge_orchestrator and getattr(edge_orchestrator, "_confluence", None):
                conf_engine = edge_orchestrator._confluence
            else:
                conf_engine = ConfluenceEngine()
            conf_up = await conf_engine.get_confluence(session, self.asset, "UP")
            if conf_up.get("score", 0) == 4:
                trend_up_agreement = True
                log.info("[ENGINE] %s/%s: Strong 4/4 UP trend agreement detected. Activating UP RSI trigger loosening.", self.asset, self.timeframe)
            else:
                conf_dn = await conf_engine.get_confluence(session, self.asset, "DOWN")
                if conf_dn.get("score", 0) == 4:
                    trend_dn_agreement = True
                    log.info("[ENGINE] %s/%s: Strong 4/4 DOWN trend agreement detected. Activating DOWN RSI trigger loosening.", self.asset, self.timeframe)
        except Exception as e:
            log.warning("[ENGINE] Failed to check trend agreement for RSI loosening: %s", e)

        # Read live volatility percentiles for the 5m volatility veto. Kept OUT of the pure
        # signal core (decide_signal does no file I/O) so the signal stays deterministic.
        _atr_pct = _bbw_pct = None
        try:
            import json as _json
            from pathlib import Path as _Path
            _rs = _Path(__file__).parent.parent.parent / "regime_status.json"
            if _rs.exists():
                _d = _json.loads(_rs.read_text(encoding="utf-8"))
                _atr_pct = float(_d.get("atr_percentile", 50.0))
                _bbw_pct = float(_d.get("bbw_percentile", 50.0))
        except Exception:
            pass

        # Raw direction from the shared signal core (single source of truth)
        from core.engine.signal_core import decide_signal
        _dec = decide_signal(
            rsi,
            mom,
            ofi,
            self.timeframe,
            regime=regime,
            trend_up_agreement=trend_up_agreement,
            trend_dn_agreement=trend_dn_agreement,
            use_session_scaling=True,
            atr_percentile=_atr_pct,
            bbw_percentile=_bbw_pct,
        )
        if _dec["blocked"]:
            log.info("[ENGINE] %s/%s: Spot OFI divergence — blocking entry.", self.asset, self.timeframe)
            return None
        raw_dir = _dec["direction"]
        score_base = _dec["score"]
        if _dec["is_reversal"]:
            log.warning("[REVERSAL] %s/%s RSI=%.2f reversal-snipe %s.", self.asset, self.timeframe, rsi, raw_dir)
        elif raw_dir is None:
            log.info("[ENGINE] %s/%s: RSI=%.2f Mom=%.4f -> NEUTRAL (dual-only path).", self.asset, self.timeframe, rsi, mom)

        # Market + real L2 prices (no 50c fallback)
        market = await self._fetch_market(session)
        if not market:
            return None

        up_price = market["up_price"]
        dn_price = market["dn_price"]
        is_dual_eligible = self.should_dual_enter(up_price, dn_price)

        # ── Fair-value primary entry (additive). Reversal-snipe keeps priority;
        #    fair-value fills at the REAL L2 quote (never at fair value). ──
        _fv = {"direction": None}  # default: no FV signal
        try:
            from config import FAIR_VALUE_MODE
        except Exception:
            FAIR_VALUE_MODE = False
        if FAIR_VALUE_MODE and not _dec["is_reversal"]:
            _now_ts = datetime.now(timezone.utc).timestamp()
            _candle_open_ts = float(klines[-1][0]) / 1000.0
            # Guard: if candle open is older than 2× the candle duration it's bad data — use now
            _candle_duration_s = 3600 if self.timeframe == "1h" else (900 if self.timeframe == "15m" else 300)
            if _candle_open_ts < (_now_ts - _candle_duration_s * 2):
                _candle_open_ts = _now_ts
            _elapsed_min = max(0.0, (_now_ts - _candle_open_ts) / 60.0)
            # Candle timing gate: FV edge decays near candle close — check max elapsed
            if self.timeframe == "5m" and _elapsed_min > 4.5:
                log.info("[TIMING-GATE] %s/5m: %.1f min — too late, skip", self.asset, _elapsed_min)
                return None
            if self.timeframe == "1h" and _elapsed_min > 59.5:
                log.info("[TIMING-GATE] %s/1h: %.1f min — too late, skip", self.asset, _elapsed_min)
                return None
            # Minimum time gate: need real price movement before FV can be meaningful
            _fv_min = 5.0 if self.timeframe == "1h" else 1.0
            if _elapsed_min < _fv_min:
                log.info(
                    "[TIMING-GATE-MIN] %s/%s: %.1f min — FV needs ≥%.0fmin of data",
                    self.asset, self.timeframe, _elapsed_min, _fv_min,
                )
                _fv = {"direction": None, "edge": 0.0, "archetype": None}
            else:
                _fv = self._fair_value_entry(klines, closes[-1], up_price, dn_price, _elapsed_min)

            # P4 15m FV Archetype Gate
            if self.timeframe == "15m" and _fv.get("direction") is not None:
                _fv_arch = _fv.get("archetype", "moderate")
                if _fv_arch == "moderate":
                    from config import get_config
                    start_utc = int(get_config("FV_NIGHT_SESSION_START_UTC", 2))
                    end_utc = int(get_config("FV_NIGHT_SESSION_END_UTC", 9))
                    cur_utc_hour = datetime.now(timezone.utc).hour
                    is_night = (start_utc <= cur_utc_hour < end_utc) if start_utc < end_utc else (cur_utc_hour >= start_utc or cur_utc_hour < end_utc)
                    is_range = (regime == "RANGE")
                    if not is_range and not is_night:
                        log.info(
                            "[FV-15M-ARCH-GATE] %s/15m: moderate FV archetype blocked — regime is %s, hour=%d UTC (not range/night)",
                            self.asset, regime, cur_utc_hour
                        )
                        _fv = {"direction": None, "edge": 0.0, "archetype": None}
            if _fv["direction"] is not None:
                raw_dir = _fv["direction"]
                score_base = min(0.90, 0.55 + min(0.30, _fv["edge"]) +
                                 (0.05 if _fv["archetype"] == "near_certainty" else 0.0))
                log.info("[FAIR-VALUE] %s/%s %s | fp=%.3f quote=%.3f edge=%.3f (%s)",
                         self.asset, self.timeframe, raw_dir, _fv["fp_up"],
                         up_price if raw_dir == "UP" else dn_price, _fv["edge"], _fv["archetype"])
                try:
                    from infrastructure.state.fair_value_log import log_fair_value_entry
                    log_fair_value_entry({
                        "asset": self.asset, "timeframe": self.timeframe, "direction": raw_dir,
                        "fp_up": _fv["fp_up"], "quote": (up_price if raw_dir == "UP" else dn_price),
                        "edge": _fv["edge"], "archetype": _fv["archetype"],
                        "elapsed_min": round(_elapsed_min, 2), "entry_ts": _now_ts,
                    })
                except Exception:
                    pass

        # ── FV edge gate (tiered) + cross-TF conflict check ──────────────────
        # Applies only when FV fired. Does not touch the SIG path.
        if _fv.get("direction") is not None:
            _entry_price_fv = up_price if _fv["direction"] == "UP" else dn_price

            # Cross-TF: check whether last closed 15m candle on same asset
            # points OPPOSITE to the 5m FV direction (medium penalty = +0.08 edge bar).
            _cross_tf_conflict = False
            if self.timeframe == "5m":
                try:
                    _k15 = await _fetch_klines_async(session, self.asset, "15m", 5)
                    if len(_k15) >= 2:
                        _last15_bull = float(_k15[-2][4]) > float(_k15[-2][1])
                        _cross_tf_conflict = (_last15_bull != (_fv["direction"] == "UP"))
                        if _cross_tf_conflict:
                            log.info(
                                "[CROSS-TF] %s/5m: 15m candle %s contradicts FV %s — raising edge bar",
                                self.asset, "UP" if _last15_bull else "DN", _fv["direction"],
                            )
                except Exception:
                    pass

            # Range-based minimum edge derived from 136-trade session data:
            # 50-65¢: 45% WR (losing zone) → raise bar to 0.12
            # >65¢: risky, moderate raise to 0.10
            # 25-50¢: profit zone → keep base at 0.05
            # Cross-TF conflict still adds a penalty on top
            if _entry_price_fv >= 0.50 and _entry_price_fv < 0.65:
                _min_edge = 0.12
            elif _entry_price_fv >= 0.65:
                _min_edge = 0.10
            else:
                _min_edge = 0.05
            # Cross-TF conflict always raises bar by additional 0.03
            if _cross_tf_conflict:
                _min_edge = max(_min_edge, _min_edge + 0.03)
            # ETH-specific: 40-65¢ range was 0W/3L in session data
            if self.asset == "ETH" and 0.40 <= _entry_price_fv < 0.65:
                _min_edge = max(_min_edge, 0.15)
                log.info("[ETH-FV-GATE] ETH %.0fc in weak zone — min_edge raised to %.2f",
                         _entry_price_fv * 100, _min_edge)

            # SOL FV floor: SOL amplifies BTC trend moves 1.5-2x — requires stronger
            # mean-reversion confirmation before entering. 14% WR in trending sessions.
            if self.asset == "SOL":
                _min_edge = max(_min_edge, 0.15)
                log.info("[SOL-FV-FLOOR] SOL FV min_edge floor=0.15 → %.2f", _min_edge)

            # FV/15m base floor: 15-minute candles carry more trend momentum than 5m;
            # require meaningful edge before entering. 18.2% WR in trending sessions.
            if self.timeframe == "15m":
                _min_edge = max(_min_edge, 0.10)
                log.info("[FV-15M-FLOOR] %s/15m min_edge floor=0.10 → %.2f", self.asset, _min_edge)

            # FV trend-confirm soft penalty: if both recent closed candles oppose FV
            # direction, raise the bar — mean reversion against consecutive same-direction
            # candles is low-probability in trending markets.
            if len(klines) >= 4:
                _tc_last_bull = float(klines[-2][4]) > float(klines[-2][1])
                _tc_prev_bull = float(klines[-3][4]) > float(klines[-3][1])
                _fv_wants_up = _fv["direction"] == "UP"
                if _tc_last_bull == _tc_prev_bull and _tc_last_bull != _fv_wants_up:
                    _min_edge = max(_min_edge, _min_edge + 0.06)
                    log.info(
                        "[FV-TREND-SOFT] %s/%s: 2 consec %s candles oppose FV %s — min_edge→%.2f",
                        self.asset, self.timeframe,
                        "UP" if _tc_last_bull else "DOWN",
                        _fv["direction"], _min_edge,
                    )

            # Macro-aware FV edge penalty: raises the edge bar when the 8-candle macro
            # trend conflicts with FV direction, preventing macro-opposing FV entries.
            # 5+/8 conflict (soft) → +0.08 penalty; 6+/8 conflict (hard) → +0.15 penalty.
            if self.asset == "DOGE" and len(klines) >= 10:
                _fv_m8 = klines[-9:-1]
                _fv_m_up = sum(1 for k in _fv_m8 if float(k[4]) > float(k[1]))
                _fv_m_dn = 8 - _fv_m_up
                _fv_is_up = _fv["direction"] == "UP"
                if (_fv_m_up >= 6 and not _fv_is_up) or (_fv_m_dn >= 6 and _fv_is_up):
                    _min_edge = max(_min_edge, 0.25)
                    log.info(
                        "[FV-MACRO] %s/%s: hard macro conflict %d/8 vs FV %s — edge bar %.2f",
                        self.asset, self.timeframe,
                        _fv_m_up if not _fv_is_up else _fv_m_dn,
                        _fv["direction"], _min_edge,
                    )
                elif (_fv_m_up >= 5 and not _fv_is_up) or (_fv_m_dn >= 5 and _fv_is_up):
                    _min_edge = max(_min_edge, 0.18)
                    log.info(
                        "[FV-MACRO] %s/%s: soft macro conflict %d/8 vs FV %s — edge bar %.2f",
                        self.asset, self.timeframe,
                        _fv_m_up if not _fv_is_up else _fv_m_dn,
                        _fv["direction"], _min_edge,
                    )

            if _fv["edge"] < _min_edge:
                log.info(
                    "[FV-EDGE-GATE] %s/%s: edge %.3f < required %.3f (price=%.2f) — skip",
                    self.asset, self.timeframe, _fv["edge"], _min_edge, _entry_price_fv,
                )
                _fv = {"direction": None, "edge": 0.0, "archetype": None}

        # Multi-asset corroboration (5m FV only): require ≥1 peer asset's last
        # closed candle to agree with the FV direction before committing.
        _corroboration_multiplier = 1.0  # default: no corroboration effect
        if self.timeframe == "5m" and _fv.get("direction") is not None:
            _PEERS = {
                "BTC": ["ETH", "SOL"], "ETH": ["BTC", "SOL"],
                "SOL": ["BTC", "ETH"], "XRP": ["BTC", "ETH"],
                "DOGE": ["BTC"],
            }
            _corroborated = False
            for _peer in _PEERS.get(self.asset, []):
                try:
                    _pk = await _fetch_klines_async(session, _peer, "5m", 5)
                    if len(_pk) >= 2:
                        _peer_bull = float(_pk[-2][4]) > float(_pk[-2][1])
                        if _peer_bull == (_fv["direction"] == "UP"):
                            _corroborated = True
                            break
                except Exception:
                    pass
            _corroboration_multiplier = 1.3 if _corroborated else (0.7 if self.asset == "DOGE" else 1.0)
            log.info(
                "[CORROBORATE] %s/5m: %s FV %s — size_mult=%.1f",
                self.asset,
                "peer agrees" if _corroborated else "no peer",
                _fv["direction"],
                _corroboration_multiplier,
            )
        # ─────────────────────────────────────────────────────────────────────

        # Track whether this entry is driven by fair-value or pure RSI/OFI signal
        entry_source = "FAIR_VAL" if (FAIR_VALUE_MODE and not _dec["is_reversal"] and _fv.get("direction") is not None) else "SIG"

        if raw_dir is None:
            if is_dual_eligible and abs(ofi) >= 0.12:
                raw_dir = "UP" if ofi >= 0 else "DOWN"
                score_base = 0.62
                log.info(
                    "[ENGINE] %s/%s: Dual-eligible (sum=%.2fc) neutral RSI — OFI → %s",
                    self.asset, self.timeframe, (up_price + dn_price) * 100, raw_dir,
                )
            else:
                return None

        # Apply regime
        direction = apply_regime(raw_dir, regime)
        if self.invert_signal:
            direction = "DOWN" if direction == "UP" else "UP"
            # Bug fix: re-validate FV floor for inverted direction.
            # _entry_price_fv was assigned from _fv["direction"] (pre-inversion).
            # After flip, the actual entry side may be sub-35c — must re-check.
            if _fv.get("direction") is not None:
                _inv_price = up_price if direction == "UP" else dn_price
                if _inv_price < 0.35:
                    log.warning("[PRICE-FLOOR-INV] %s/%s: inverted FV entry %.0fc < 35c — skip",
                                self.asset, self.timeframe, _inv_price * 100)
                    _fv = {"direction": None, "edge": 0.0, "archetype": None}
                    return None

        # P5 & P6: SIGNAL Price Gates
        if entry_source == "SIG":
            _quote = up_price if direction == "UP" else dn_price
            if direction == "UP":
                _ceil = 0.60 if self.timeframe == "5m" else 0.65
                if _quote > _ceil:
                    log.info("[SIG-PRICE-GATE] %s/%s UP entry price %.2f > ceiling %.2f — skip",
                             self.asset, self.timeframe, _quote, _ceil)
                    return None
            if _quote < 0.20:
                log.info("[SIG-PRICE-GATE] %s/%s %s entry price %.2f < floor 0.20 — skip",
                         self.asset, self.timeframe, direction, _quote)
                return None

        # DIR-COOLDOWN removed: Bone Reaper Mode fires every candle regardless of prior direction

        # ── Real-time trend gate + per-asset choppy detection ────────────────
        # Slope of closes[-5:] measures current 5-candle drift.
        # Trend gate: blocks entries that contradict a clear trend direction.
        # Choppy detection: if slope flipped sign 2+ times in last 4 candles
        # while still ranging, enter a 2-candle cooldown (Option C).
        if len(closes) >= 10:
            _c0 = closes[-5] if closes[-5] > 0 else 1.0
            _slope = (closes[-1] - closes[-5]) / _c0
            _TREND_GATE = 0.004  # 0.4% drift = clear trend (raised from 0.25%)
            _ranging = abs(_slope) < _TREND_GATE
            if not _ranging:
                _trend_dn = _slope < 0
                _signal_dn = direction == "DOWN"
                if _trend_dn != _signal_dn:
                    log.info(
                        "[TREND-GATE] %s/%s: %s signal contradicts trend (slope=%.3f%%) — skip",
                        self.asset, self.timeframe, direction, _slope * 100,
                    )
                    return None

            # Serve active choppy cooldown before updating history
            if self.asset == "DOGE" and self._choppy_candles > 0:
                self._choppy_candles -= 1
                log.info(
                    "[CHOPPY] %s/%s: cooling down (%d candle(s) remaining)",
                    self.asset, self.timeframe, self._choppy_candles,
                )
                return None

            # Accumulate slope into rolling 4-reading history
            self._slope_history.append(_slope)
            if len(self._slope_history) > 4:
                self._slope_history = self._slope_history[-4:]

            # Detect choppy: 2+ sign flips while slope is still unclear
            if len(self._slope_history) >= 4 and _ranging:
                _flips = sum(
                    1 for i in range(1, len(self._slope_history))
                    if (self._slope_history[i] >= 0) != (self._slope_history[i - 1] >= 0)
                )
                if _flips >= 2:
                    self._choppy_candles = 2
                    log.info(
                        "[CHOPPY] %s/%s: %d slope flips, slope=%.3f%% — 2-candle pause",
                        self.asset, self.timeframe, _flips, _slope * 100,
                    )
                    if self.asset == "DOGE":
                        return None
        # ─────────────────────────────────────────────────────────────────────

        # Macro trend gate (8-candle): if 6+/8 last closed candles all point in
        # one direction, only signals that agree are allowed through.
        # Applies to BOTH FV and SIG — prevents the recurring loss cluster pattern
        # where FV keeps firing DN while the market is bouncing UP for 45+ minutes.
        # Macro gate: extended to ALL assets — if 6+/8 last closed candles unanimously
        # point in one direction, countertrend entries are blocked for both FV and SIG.
        # In choppy sessions (alternating candles), 6/8 consensus is rarely reached →
        # gate is invisible at night. In trending sessions, it fires on nearly every
        # countertrend attempt. 3/3 unanimity already handled by FV-TREND-SOFT above.
        if len(klines) >= 10:
            _macro_candles = klines[-9:-1]  # last 8 closed candles
            _macro_up = sum(1 for k in _macro_candles if float(k[4]) > float(k[1]))
            _macro_dn = 8 - _macro_up
            _signal_is_up = direction == "UP"
            if _macro_up >= 6 and not _signal_is_up:
                log.info(
                    "[MACRO-GATE] %s/%s: blocked DN — %d/8 candles bullish",
                    self.asset, self.timeframe, _macro_up,
                )
                _write_gate_event(self.asset, self.timeframe, "MACRO-GATE", direction, f"{_macro_up}/8 candles bullish")
                return None
            if _macro_dn >= 6 and _signal_is_up:
                log.info(
                    "[MACRO-GATE] %s/%s: blocked UP — %d/8 candles bearish",
                    self.asset, self.timeframe, _macro_dn,
                )
                _write_gate_event(self.asset, self.timeframe, "MACRO-GATE", direction, f"{_macro_dn}/8 candles bearish")
                return None

        # SIG trend confirmation: both last 2 closed candles must resolve in the
        # signal direction. Skipping this for FAIR-VAL entries — they enter on
        # divergence which may precede the candle trend shift.
        if entry_source == "SIG" and self.asset == "DOGE" and len(klines) >= 4:
            c_last_bull = float(klines[-2][4]) > float(klines[-2][1])
            c_prev_bull = float(klines[-3][4]) > float(klines[-3][1])
            signal_bull = direction == "UP"
            if not (c_last_bull == c_prev_bull == signal_bull):
                _c_desc = f"{('UP' if c_prev_bull else 'DN')}/{('UP' if c_last_bull else 'DN')}"
                log.info(
                    "[TREND-CONFIRM] %s/%s: SIG %s blocked — last 2 closed candles: %s",
                    self.asset, self.timeframe, direction, _c_desc,
                )
                _write_gate_event(self.asset, self.timeframe, "TREND-CONFIRM", direction, f"candles: {_c_desc}")
                return None

        # Composite score
        abs_mom = abs(mom)
        score = score_base
        if abs_mom >= 0.15:
            score = min(1.0, score + 0.20)
        elif abs_mom >= 0.08:
            score = min(1.0, score + 0.15)
        elif abs_mom >= 0.05:
            score = min(1.0, score + 0.10)

        if raw_dir == "UP" and ofi > 0.20:
            score = min(1.0, score + 0.08)
        elif raw_dir == "DOWN" and ofi < -0.20:
            score = min(1.0, score + 0.08)

        if is_dual_eligible:
            score = min(1.0, score + 0.06)
            log.info(
                "[ENGINE] %s/%s: Dual boost — combined=%.2fc",
                self.asset, self.timeframe, up_price + dn_price,
            )

        # Polymarket CLOB OBI (Proposal 1)
        clob_obi = 0.0
        try:
            from infrastructure.websocket.extraterrestrial_ws_gateway import polymarket_l2_gateway
            up_tk = market.get("up_market", {}).get("id")
            dn_tk = market.get("dn_market", {}).get("id")
            if direction == "UP" and up_tk:
                clob_obi = polymarket_l2_gateway.get_obi(up_tk)
                if clob_obi < -0.60:
                    log.info("[ENGINE] %s/%s: Polymarket YES OBI extreme selling pressure (%.2f < -0.60) — blocking entry.",
                             self.asset, self.timeframe, clob_obi)
                    return None
                elif clob_obi > 0.0:
                    score = min(1.0, score + 0.04)
                    log.info("[ENGINE] %s/%s: YES OBI confirms direction (%.2f > 0.0) -> boost +0.04", self.asset, self.timeframe, clob_obi)
                elif clob_obi < 0.0:
                    score = max(0.10, score - 0.03)
                    log.info("[ENGINE] %s/%s: YES OBI conflicts direction (%.2f < 0.0) -> penalty -0.03", self.asset, self.timeframe, clob_obi)
            elif direction == "DOWN" and dn_tk:
                clob_obi = polymarket_l2_gateway.get_obi(dn_tk)
                if clob_obi < -0.60:
                    log.info("[ENGINE] %s/%s: Polymarket NO OBI extreme selling pressure (%.2f < -0.60) — blocking entry.",
                             self.asset, self.timeframe, clob_obi)
                    return None
                elif clob_obi > 0.0:
                    score = min(1.0, score + 0.04)
                    log.info("[ENGINE] %s/%s: NO OBI confirms direction (%.2f > 0.0) -> boost +0.04", self.asset, self.timeframe, clob_obi)
                elif clob_obi < 0.0:
                    score = max(0.10, score - 0.03)
                    log.info("[ENGINE] %s/%s: NO OBI conflicts direction (%.2f < 0.0) -> penalty -0.03", self.asset, self.timeframe, clob_obi)
        except Exception as e:
            log.warning("[ENGINE] Failed to read or apply Polymarket CLOB OBI: %s", e)

        # ── PyTorch AI Predictor Injection (trained model only) ──
        try:
            from core.ml.ai_injector import injector
            if injector.is_trained:
                seq = []
                for i in range(max(1, len(klines) - 10), len(klines)):
                    subset = [float(k[4]) for k in klines[:i+1]]
                    vol = float(klines[i][5])
                    p_delta = float(klines[i][4]) - float(klines[i][1])
                    sub_rsi = _compute_rsi(subset) or 50.0
                    sub_mom = _compute_momentum(subset) or 0.0
                    # Aligned to exact order of FEATURE_NAMES: ["rsi", "momentum", "ofi", "volume", "price_delta"]
                    seq.append([sub_rsi, sub_mom, 0.0, vol, p_delta])
                if seq:
                    seq[-1][2] = ofi  # Index 2 is "ofi"
                
                # Predict passing the active regime for 9-feature one-hot encoding
                ai_up_prob = injector.predict(seq, regime)
                
                if direction == "UP" and ai_up_prob < 0.35:
                    log.warning("[AI-VETO] %s/%s UP entry vetoed by PyTorch LSTM (probability: %.1f%% < 35%%)", self.asset, self.timeframe, ai_up_prob * 100)
                    return None
                elif direction == "DOWN" and ai_up_prob > 0.65:
                    log.warning("[AI-VETO] %s/%s DOWN entry vetoed by PyTorch LSTM (probability: %.1f%% > 65%%)", self.asset, self.timeframe, ai_up_prob * 100)
                    return None
                
                if direction == "UP" and ai_up_prob > 0.60:
                    score = min(1.0, score + 0.05)
                    log.info("[AI-BOOST] %s/%s UP entry score boosted by PyTorch LSTM (probability: %.1f%% > 60%%)", self.asset, self.timeframe, ai_up_prob * 100)
                elif direction == "DOWN" and ai_up_prob < 0.40:
                    score = min(1.0, score + 0.05)
                    log.info("[AI-BOOST] %s/%s DOWN entry score boosted by PyTorch LSTM (probability: %.1f%% < 40%%)", self.asset, self.timeframe, ai_up_prob * 100)
        except Exception as e:
            log.error("[ENGINE] AI Predictor failed: %s", e)

        # ── Edge Architecture Integration (Advancements A-M) ──
        edge_ctx = {}
        try:
            from core.engine.edge_orchestrator import edge_orchestrator
            sig_dict = {
                "signal_type": "TYPE_A_HIGH" if (score >= 0.75) else "TYPE_A_LOW",
                "score": score,
                "affected_cryptos": [self.asset],
                "entry_price": up_price if direction == "UP" else dn_price,
            }
            edge_ctx = await edge_orchestrator.get_trade_context(
                session=session,
                asset=self.asset,
                direction=direction,
                signal=sig_dict,
                market=market,
                current_price=closes[-1]
            )
            self.last_edge_context = edge_ctx
            
            boost = edge_ctx.get("combined_confidence_boost", 0.0)
            if boost != 0.0:
                old_score = score
                score = max(0.10, min(1.0, score + boost))
                log.info("[EDGE] %s/%s Score adjusted by boost: %.2f -> %.2f (boost=%+.2f)", self.asset, self.timeframe, old_score, score, boost)
            
            regime = edge_ctx.get("regime_name", regime)
            
        except Exception as e:
            log.warning("[EDGE] Failed to query EdgeOrchestrator in generate_signal: %s", e)
            self.last_edge_context = None

        # Whale-Veto: block when whale pressure strongly contradicts trade direction
        # e.g. 11 whales buying (pressure=0.81) but we're entering DOWN → bad trade
        _whale_pressure = edge_ctx.get("whale_pressure", 0.0) if edge_ctx else 0.0
        if _whale_pressure > 0.70 and direction == "DOWN":
            log.warning("[WHALE-VETO] %s/%s: bullish whale pressure %.2f contradicts DOWN — skip",
                        self.asset, self.timeframe, _whale_pressure)
            return None
        elif _whale_pressure < -0.70 and direction == "UP":
            log.warning("[WHALE-VETO] %s/%s: bearish whale pressure %.2f contradicts UP — skip",
                        self.asset, self.timeframe, abs(_whale_pressure))
            return None

        # Confluence-Veto Gate: SIG-only — FV signal is Pyth divergence, not multi-TF RSI consensus
        if entry_source != "FAIR_VAL" and not is_dual_eligible and edge_ctx and edge_ctx.get("confluence_score", 2) == 0:
            log.warning(
                "[CONFLUENCE-VETO] %s/%s: Blocking directional entry due to complete lack of multi-timeframe agreement (score = 0)",
                self.asset, self.timeframe
            )
            return None

        if score < 0.50 and not is_dual_eligible:
            return None

        # Directional saturation gate: SIG-only — FV fires on Pyth divergence every candle like Bone Reaper
        _dir_streak = self._recent_same_direction_streak(direction, n=6)
        if entry_source != "FAIR_VAL" and _dir_streak >= 4:
            log.info(
                "[DIR-SAT] %s/%s: %d consecutive %s entries — directional saturation, SKIP",
                self.asset, self.timeframe, _dir_streak, direction,
            )
            return None
        elif entry_source != "FAIR_VAL" and _dir_streak == 3:
            old_score = score
            score = max(0.50, score - 0.12)
            log.info(
                "[DIR-SAT] %s/%s: 3 consecutive %s — soft penalty %.2f -> %.2f (smaller size)",
                self.asset, self.timeframe, direction, old_score, score,
            )

        # Correlated asset loss brake (soft filter): after 3+ full losses
        # (settled ≤10¢) in the last 20 min across ANY asset, the macro environment
        # has likely reversed — raise bar to edge ≥0.20 (FV) or score ≥0.82 (SIG).
        _full_loss_count = self._recent_full_loss_count(lookback_minutes=20)
        if _full_loss_count >= 8:
            if entry_source == "FAIR_VAL":
                _fv_edge = _fv.get("edge", 0.0) if _fv.get("direction") is not None else 0.0
                if _fv_edge < 0.20:
                    log.info(
                        "[LOSS-BRAKE] %s/%s: %d full losses in 20min — FV edge %.3f < 0.20, skip",
                        self.asset, self.timeframe, _full_loss_count, _fv_edge,
                    )
                    return None
            elif score < 0.82:
                log.info(
                    "[LOSS-BRAKE] %s/%s: %d full losses in 20min — SIG score %.2f < 0.82, skip",
                    self.asset, self.timeframe, _full_loss_count, score,
                )
                return None

        log.info(
            "[ENGINE] %s/%s SIGNAL: %s | Score=%.2f | up=%.0fc dn=%.0fc | dual=%s | %s",
            self.asset, self.timeframe, direction, score,
            up_price * 100, dn_price * 100, is_dual_eligible, market["event_title"],
        )

        return {
            "asset":        self.asset,
            "timeframe":    self.timeframe,
            "direction":    direction,
            "score":        score,
            "regime":       regime,
            "inverted":     self.invert_signal,
            "rsi":          rsi,
            "momentum":     round(mom, 4),
            "market":       market,
            "is_dual_eligible": is_dual_eligible,
            "edge_context": edge_ctx,
            "entry_source": entry_source,
            "corroboration_multiplier": _corroboration_multiplier,
        }

    async def _resolve_l2_prices(
        self,
        session: aiohttp.ClientSession,
        up_tk: str,
        dn_tk: str,
        max_spread: float = 0.15,
        is_latency_scan: bool = False,
    ) -> Optional[tuple[float, float, float]]:
        """Return (up_price, dn_price, spread) or None if book invalid."""
        from infrastructure.websocket.extraterrestrial_ws_gateway import polymarket_l2_gateway
        from config import get_config

        if not polymarket_l2_gateway.is_active:
            await polymarket_l2_gateway.start_gateway()

        polymarket_l2_gateway.subscribe(up_tk)
        polymarket_l2_gateway.subscribe(dn_tk)

        # Always enforce live spread gate regardless of mode — this is a live simulation
        effective_max_spread = max_spread

        up_price, dn_price = None, None
        attempts = 2 if is_latency_scan else 4
        for attempt in range(attempts):
            # Enforce 0s sleep on attempt 0 if latency scan to fail fast / act instantly
            if attempt > 0 or not is_latency_scan:
                await asyncio.sleep(0.5 if is_latency_scan else (1.0 if attempt == 0 else 1.5))
            up_price, up_spread = polymarket_l2_gateway.get_price(up_tk)
            dn_price, dn_spread = polymarket_l2_gateway.get_price(dn_tk)
            
            # Near-certain L2 gate: sweeper entries at 95-99¢ need a wider validity window
            _price_ceil = 0.99 if is_latency_scan else 0.97

            # 1. If we have both prices, verify and use them
            if up_price and dn_price and 0.03 < up_price < _price_ceil and 0.03 < dn_price < _price_ceil:
                spread = (up_spread or 0.02) + (dn_spread or 0.02)
                if spread <= effective_max_spread:
                    return up_price, dn_price, spread

            # 2. Derive DOWN price if only UP exists and is valid
            if up_price and 0.03 < up_price < _price_ceil and (not dn_price or dn_price <= 0.03 or dn_price >= _price_ceil):
                derived_dn = round(1.0 - up_price, 4)
                spread = (up_spread or 0.02) + 0.02
                if spread <= effective_max_spread:
                    return up_price, derived_dn, spread

            # 3. Derive UP price if only DOWN exists and is valid
            if dn_price and 0.03 < dn_price < _price_ceil and (not up_price or up_price <= 0.03 or up_price >= _price_ceil):
                derived_up = round(1.0 - dn_price, 4)
                spread = (dn_spread or 0.02) + 0.02
                if spread <= effective_max_spread:
                    return derived_up, dn_price, spread

        # Single REST fallback check executed exactly once if all WebSocket attempts failed
        up_book = await _fetch_clob_book_async(session, up_tk)
        dn_book = await _fetch_clob_book_async(session, dn_tk)
        up_p, up_s = _parse_clob_book(up_book)
        dn_p, dn_s = _parse_clob_book(dn_book)
        
        # REST 1. Both valid
        if up_p and dn_p and 0.03 < up_p < 0.97 and 0.03 < dn_p < 0.97:
            spread = (up_s or 0.03) + (dn_s or 0.03)
            if spread <= effective_max_spread:
                return up_p, dn_p, spread
        
        # REST 2. Derive REST DOWN from REST UP
        if up_p and 0.03 < up_p < 0.97 and (not dn_p or dn_p <= 0.03 or dn_p >= 0.97):
            derived_dn = round(1.0 - up_p, 4)
            spread = (up_s or 0.03) + 0.03
            if spread <= effective_max_spread:
                return up_p, derived_dn, spread
                
        # REST 3. Derive REST UP from REST DOWN
        if dn_p and 0.03 < dn_p < 0.97 and (not up_p or up_p <= 0.03 or up_p >= 0.97):
            derived_up = round(1.0 - dn_p, 4)
            spread = (dn_s or 0.03) + 0.03
            if spread <= effective_max_spread:
                return derived_up, dn_p, spread

        # Hard skip — no live book means no trade. PAPER-FALLBACK removed.
        # This bot simulates live capital. RSI-derived fake prices produce blind bets.
        # If Polymarket has no live book for this candle yet, we wait for the next one.
        self._l2_fail_count += 1
        _BACKOFF_THRESHOLD = 5
        _BACKOFF_SECS = 15 * 60  # 15 minutes
        if self._l2_fail_count >= _BACKOFF_THRESHOLD:
            self._l2_backoff_until = time.time() + _BACKOFF_SECS
            self._l2_fail_count = 0  # reset so next recovery period tries fresh
            log.warning(
                "[L2-CIRCUIT-BREAKER] %s/%s: %d consecutive L2 failures — backing off for 15 min (likely weekend dead zone).",
                self.asset, self.timeframe, _BACKOFF_THRESHOLD
            )
        else:
            log.warning(
                "[LIVE-BOOK-REQUIRED] %s/%s: No valid L2 book after 4 attempts. Hard-skipping candle. (fail %d/%d)",
                self.asset, self.timeframe, self._l2_fail_count, _BACKOFF_THRESHOLD
            )
        return None

    async def prefetch_upcoming_market(self, session: aiohttp.ClientSession, next_boundary: int) -> None:
        """Prefetch token IDs for the upcoming market 20s before start and warm WebSocket."""
        coin_lower = self.asset.lower()
        dur_min = 60 if self.timeframe == "1h" else (5 if self.timeframe == "5m" else 15)
        slug = f"{coin_lower}-updown-{dur_min}m-{next_boundary}"
        
        gamma_url = "https://gamma-api.polymarket.com/events"
        try:
            log.info("[ENGINE] %s/%s: Pre-fetching upcoming market slug: %s", self.asset, self.timeframe, slug)
            async with session.get(gamma_url, params={"slug": slug}, timeout=5) as r:
                if r.status == 200:
                    raw = await r.json()
                    evs = []
                    if isinstance(raw, dict) and "id" in raw:
                        evs = [raw]
                    elif isinstance(raw, list):
                        evs = raw
                    else:
                        evs = raw.get("data", raw.get("events", []))

                    for ev in evs:
                        for mkt in ev.get("markets", []):
                            import json as _json
                            outcomes = mkt.get("outcomes", [])
                            if isinstance(outcomes, str):
                                try:
                                    outcomes = _json.loads(outcomes)
                                except Exception:
                                    outcomes = []
                            clob_token_ids = mkt.get("clobTokenIds", [])
                            if isinstance(clob_token_ids, str):
                                try:
                                    clob_token_ids = _json.loads(clob_token_ids)
                                except Exception:
                                    clob_token_ids = []

                            if len(outcomes) < 2 or len(clob_token_ids) < 2:
                                continue

                            up_idx, dn_idx = -1, -1
                            for i, o in enumerate(outcomes):
                                o_lower = str(o).lower()
                                if o_lower in ("yes", "up"):
                                    up_idx = i
                                elif o_lower in ("no", "down"):
                                    dn_idx = i

                            if up_idx == -1 or dn_idx == -1:
                                continue

                            up_tk = clob_token_ids[up_idx]
                            dn_tk = clob_token_ids[dn_idx]
                            
                            # Warm the WebSocket cache!
                            from infrastructure.websocket.extraterrestrial_ws_gateway import polymarket_l2_gateway
                            if not polymarket_l2_gateway.is_active:
                                await polymarket_l2_gateway.start_gateway()
                            polymarket_l2_gateway.subscribe(up_tk)
                            polymarket_l2_gateway.subscribe(dn_tk)
                            
                            self._prefetched_markets[next_boundary] = {
                                "event_id": ev.get("id", ""),
                                "event_title": ev.get("title", ""),
                                "expiry_ts": next_boundary + (dur_min * 60),
                                "duration_min": dur_min,
                                "liquidity": float(ev.get("liquidity", 0) or 1000.0),
                                "up_market": {"id": up_tk},
                                "dn_market": {"id": dn_tk},
                                "slug": slug,
                            }
                            # Prune cache to keep only recent entries (older than 1 hour)
                            now_ts = int(time.time())
                            self._prefetched_markets = {
                                k: v for k, v in self._prefetched_markets.items()
                                if k > now_ts - 3600
                            }
                            log.info(
                                "[ENGINE] %s/%s: Upcoming market pre-fetched & WS subscribed! Yes=%s No=%s",
                                self.asset, self.timeframe, up_tk[:10], dn_tk[:10]
                            )
                            return
        except Exception as e:
            log.warning("[ENGINE] Failed to pre-fetch upcoming market %s: %s", slug, e)

    async def _fetch_market(self, session: aiohttp.ClientSession, is_latency_scan: bool = False) -> Optional[dict]:
        """Fetch active Up/Down market with verified L2/REST pricing (no 50c fallback)."""
        coin_lower = self.asset.lower()
        dur_min = 60 if self.timeframe == "1h" else (5 if self.timeframe == "5m" else 15)
        now_ts = int(time.time())
        interval = dur_min * 60
        boundary = ((now_ts + interval) // interval) * interval
        start_ts = boundary - interval

        # Check if we have a valid pre-fetched market for the current candle start
        # L2 circuit breaker: if backed off, skip immediately without wasting retry attempts
        if time.time() < self._l2_backoff_until:
            _remaining = int(self._l2_backoff_until - time.time()) // 60
            log.info(
                "[L2-CIRCUIT-BREAKER] %s/%s: In backoff — skipping for ~%d more min (no weekend liquidity).",
                self.asset, self.timeframe, _remaining
            )
            return None

        if start_ts in self._prefetched_markets:
            cached_market = self._prefetched_markets[start_ts]
            up_tk = cached_market["up_market"]["id"]
            dn_tk = cached_market["dn_market"]["id"]
            resolved = await self._resolve_l2_prices(session, up_tk, dn_tk, is_latency_scan=is_latency_scan)
            if resolved:
                up_price, dn_price, spread = resolved
                market = dict(cached_market)
                market["up_price"] = up_price
                market["dn_price"] = dn_price
                market["spread"] = spread
                log.info(
                    "[ENGINE] %s/%s: [PRE-FETCH HIT] %s up=%.0fc dn=%.0fc spread=%.0fc",
                    self.asset, self.timeframe, market["slug"],
                    up_price * 100, dn_price * 100, spread * 100,
                )
                return market

        gamma_url = "https://gamma-api.polymarket.com/events"
        offsets = [0, -1, 1]

        try:
            for offset in offsets:
                offset_ts = start_ts + (offset * interval)
                slug = f"{coin_lower}-updown-{dur_min}m-{offset_ts}"

                async with session.get(gamma_url, params={"slug": slug}, timeout=5) as r:
                    if r.status != 200:
                        continue
                    raw = await r.json()
                    evs = []
                    if isinstance(raw, dict) and "id" in raw:
                        evs = [raw]
                    elif isinstance(raw, list):
                        evs = raw
                    else:
                        evs = raw.get("data", raw.get("events", []))

                    for ev in evs:
                        for mkt in ev.get("markets", []):
                            import json as _json
                            outcomes = mkt.get("outcomes", [])
                            if isinstance(outcomes, str):
                                try:
                                    outcomes = _json.loads(outcomes)
                                except Exception:
                                    outcomes = []
                            clob_token_ids = mkt.get("clobTokenIds", [])
                            if isinstance(clob_token_ids, str):
                                try:
                                    clob_token_ids = _json.loads(clob_token_ids)
                                except Exception:
                                    clob_token_ids = []

                            if len(outcomes) < 2 or len(clob_token_ids) < 2:
                                continue

                            up_idx, dn_idx = -1, -1
                            for i, o in enumerate(outcomes):
                                o_lower = str(o).lower()
                                if o_lower in ("yes", "up"):
                                    up_idx = i
                                elif o_lower in ("no", "down"):
                                    dn_idx = i

                            if up_idx == -1 or dn_idx == -1:
                                continue

                            up_tk = clob_token_ids[up_idx]
                            dn_tk = clob_token_ids[dn_idx]
                            resolved = await self._resolve_l2_prices(session, up_tk, dn_tk, is_latency_scan=is_latency_scan)
                            if not resolved:
                                log.info(
                                    "[ENGINE] %s/%s: slug %s — no valid L2 book (skip phantom 50c)",
                                    self.asset, self.timeframe, slug,
                                )
                                continue

                            up_price, dn_price, spread = resolved
                            # Successful book fetch — reset the circuit breaker
                            self._l2_fail_count = 0
                            self._l2_backoff_until = 0.0
                            log.info(
                                "[ENGINE] %s/%s: %s up=%.0fc dn=%.0fc spread=%.0fc",
                                self.asset, self.timeframe, slug,
                                up_price * 100, dn_price * 100, spread * 100,
                            )
                            return {
                                "event_id": ev.get("id", ""),
                                "event_title": ev.get("title", ""),
                                "expiry_ts": offset_ts + interval,
                                "duration_min": dur_min,
                                "up_price": up_price,
                                "dn_price": dn_price,
                                "spread": spread,
                                "up_market": {"id": up_tk},
                                "dn_market": {"id": dn_tk},
                            }
        except Exception as exc:
            log.warning("[ENGINE] CLOB L2 market fetch error: %s", exc)

        return None


    # ── Sizing ────────────────────────────────────────────────────────────────

    def compute_size(self, score: float, price: float, balance: float) -> float:
        """Return USD amount to bet based on AI confidence (Dynamic Kelly) scaled by regime volatility and price bands."""
        # ── Edge Architecture Adaptive Kelly Sizer (Advancement D) ──
        if getattr(self, "last_edge_context", None):
            try:
                from core.risk.position_sizer import PositionSizer
                sizer = PositionSizer(account_balance=balance, max_cycle_capital=balance)
                
                sig_dict = {
                    "signal_type": "TYPE_A_HIGH" if (score >= 0.75) else "TYPE_A_LOW",
                    "score": score,
                    "affected_cryptos": [self.asset],
                    "entry_price": price,
                }
                mkt_dict = {
                    "market_type": "UP_DOWN",
                }
                
                ctx = self.last_edge_context
                # Unified sizing bounds — IDENTICAL to the legacy fallback path
                # below, so position size no longer depends on whether the edge
                # context happened to load. Floor = MIN_USD ($1), ceiling = the
                # same score-based sliding cap ($5–$20), bankroll fraction = 15%
                # (matches the downstream safety cap in main._validate_trade_slot).
                unified_max_cap = max(5.00, min(20.00, 5.00 + (score - 0.50) * 40.0))
                usd_size = sizer.calculate_adaptive(
                    signal=sig_dict,
                    market=mkt_dict,
                    regime_kelly=ctx.get("regime_kelly", 1.0),
                    confluence_boost=ctx.get("confluence_boost", 0.0),
                    antifragile_mult=ctx.get("antifragile_mult", 1.0),
                    heat_mult=ctx.get("heat_mult", 1.0),
                    sentiment_modifier=ctx.get("sentiment_modifier", 0.0),
                    whale_mult=ctx.get("whale_mult", 1.0),
                    category_weight=1.0,
                    min_position_usd=MIN_USD,
                    max_position_usd=unified_max_cap,
                    max_bankroll_fraction=0.15,
                )
                
                # Retrieve and apply session sizing multiplier (Sprint 11)
                session_sizing_mult = 1.0
                try:
                    from core.shared.session_manager import TradingSessionManager
                    session_params = TradingSessionManager.get_active_session_params()
                    session_sizing_mult = session_params.get("sizing_mult", 1.0)
                    usd_size *= session_sizing_mult
                    log.info("[SIZE] Adaptive Kelly scaled by session multiplier %.2fx -> $%.2f", session_sizing_mult, usd_size)
                except Exception as e:
                    log.warning("[SIZE] Failed to scale by session multiplier: %s", e)
                
                # Price-Scaled Risk Sizer calibration to bypass 70¢ trap and extreme pricing risk
                price_scalar = 1.0
                if price > 0.65 and price <= 0.78:
                    price_scalar = 0.40  # 60% reduction
                    log.info("[SIZE] Price %.4f in 70¢ trap -> applying 60%% scaling (x0.40) in adaptive Kelly", price)
                elif price > 0.78:
                    price_scalar = 0.25  # 75% reduction
                    log.info("[SIZE] Price %.4f extremely expensive -> applying 75%% scaling (x0.25) in adaptive Kelly", price)
                usd_size *= price_scalar

                # Range-based multiplier for 25-65¢ range (only when price_scalar=1.0, i.e. price<65¢)
                # <25¢: high variance entries — 40% size to control risk
                # 25-50¢: profit zone — full size
                # 50-65¢: 45% WR historically — 65% size
                if price_scalar == 1.0:
                    if price < 0.25:
                        usd_size *= 0.40
                        log.info("[SIZE-RANGE] <25c entry → 40%% size: $%.2f", usd_size)
                    elif price >= 0.50:
                        usd_size *= 0.65
                        log.info("[SIZE-RANGE] 50-65c entry → 65%% size: $%.2f", usd_size)

                # Consecutive Loss Streak Brake
                consecutive_losses = self._recent_closed_loss_streak()
                if consecutive_losses >= 2:
                    usd_size *= 0.5
                    log.warning(
                        "[SIZE] %s/%s loss streak brake active (%d losses) -> halving size in adaptive Kelly",
                        self.asset, self.timeframe, consecutive_losses,
                    )

                shares = round(usd_size / price) if price > 0 else 0
                actual_cost = shares * price
                log.info("[SIZE] Adaptive Kelly calculated actual cost: $%.2f (shares=%d)", actual_cost, shares)
                return actual_cost
            except Exception as e:
                log.warning("[SIZE] Failed to compute adaptive Kelly size, falling back: %s", e)

        # ── Legacy Sizer Fallback ──
        # Base multiplier from 1.0% to 5.0% depending on score
        if score >= 0.90:
            kelly_pct = 0.05
        elif score >= 0.80:
            kelly_pct = 0.03
        elif score >= 0.65:
            kelly_pct = 0.015
        else:
            kelly_pct = 0.01

        # Get regime multiplier from regime_status.json
        regime_mult = 1.0
        try:
            import json
            from pathlib import Path
            regime_path = Path(__file__).parent.parent.parent / "regime_status.json"
            if regime_path.exists():
                data = json.loads(regime_path.read_text(encoding="utf-8"))
                # Canonical regimes from RegimeDetector + legacy aliases for
                # backward compat with any stale regime_status.json on disk.
                REGIME_SIZE_MULT = {
                    "TRENDING":      1.30,  # directional momentum → size up
                    "COMPRESSION":   1.10,  # low-vol squeeze → slight size up
                    "MEAN_REVERTING": 0.85, # chop → size down
                    "VOLATILE_CHAOS": 0.30, # unpredictable → size way down
                    # legacy aliases
                    "RANGE":   1.30,
                    "NORMAL":  1.00,
                    "VOLATILE": 0.60,
                    "SHOCK":   0.20,
                }
                regime = str(data.get("regime", "COMPRESSION")).upper()
                regime_mult = REGIME_SIZE_MULT.get(regime, 1.0)
                log.info("[SIZE] Active regime is %s -> applying multiplier %.2fx", regime, regime_mult)
        except Exception as e:
            log.warning("[SIZE] Failed to read regime multiplier: %s", e)

        # Price-Scaled Risk Sizer calibration to bypass 70¢ trap and extreme pricing risk
        price_scalar = 1.0
        if price > 0.65 and price <= 0.78:
            price_scalar = 0.40  # 60% reduction
            log.info("[SIZE] Price %.4f in 70¢ trap -> applying 60%% scaling (x0.40)", price)
        elif price > 0.78:
            price_scalar = 0.25  # 75% reduction
            log.info("[SIZE] Price %.4f extremely expensive -> applying 75%% scaling (x0.25)", price)

        # Dynamic max cap based on AI confidence (up to $20.00)
        # If balance is large, kelly_pct * balance could exceed 20.
        # We cap it strictly to a sliding scale between $5.00 and $20.00
        max_usd_cap = min(20.00, 5.00 + (score - 0.50) * 40.0) # score=0.5->5, score=0.875->20
        max_usd_cap = max(5.00, max_usd_cap)

        raw_usd = kelly_pct * balance * regime_mult * price_scalar

        # Retrieve and apply session sizing multiplier (Sprint 11)
        session_sizing_mult = 1.0
        try:
            from core.shared.session_manager import TradingSessionManager
            session_params = TradingSessionManager.get_active_session_params()
            session_sizing_mult = session_params.get("sizing_mult", 1.0)
            raw_usd *= session_sizing_mult
            log.info("[SIZE] Fallback Kelly scaled by session multiplier %.2fx -> $%.2f", session_sizing_mult, raw_usd)
        except Exception as e:
            log.warning("[SIZE] Failed to scale by session multiplier: %s", e)

        usd = max(MIN_USD, min(raw_usd, max_usd_cap))

        consecutive_losses = self._recent_closed_loss_streak()
        if consecutive_losses >= 2:
            usd *= 0.5
            log.warning(
                "[SIZE] %s/%s loss streak brake active (%d losses) -> halving size",
                self.asset, self.timeframe, consecutive_losses,
            )
        
        shares = round(usd / price)
        return shares * price  # actual cost from shares-first rounding

    def _recent_same_direction_streak(self, direction: str, n: int = 6) -> int:
        """Count consecutive recent closed trades in the same direction as signal."""
        try:
            import json
            from pathlib import Path
            path = Path(__file__).parent.parent.parent / "infrastructure" / "exchange" / "positions_state.json"
            if not path.exists():
                return 0
            data = json.loads(path.read_text(encoding="utf-8"))
            closed = data.get("closed", [])[:n]
        except Exception:
            return 0
        signal_up = direction == "UP"
        streak = 0
        for trade in closed:
            trade_dir = trade.get("direction", "")
            trade_is_up = trade_dir in ("YES", "UP")
            if trade_is_up == signal_up:
                streak += 1
            else:
                break
        return streak

    def _recent_full_loss_count(self, lookback_minutes: int = 20) -> int:
        """Count trades that settled near zero (≤10¢) within the last N minutes (cross-asset)."""
        try:
            import json, time as _time
            from pathlib import Path
            path = Path(__file__).parent.parent.parent / "infrastructure" / "exchange" / "positions_state.json"
            if not path.exists():
                return 0
            data = json.loads(path.read_text(encoding="utf-8"))
            cutoff = _time.time() - lookback_minutes * 60
            count = 0
            for trade in data.get("closed", []):
                exit_ts = trade.get("exit_time") or trade.get("closed_at") or 0
                exit_price = float(trade.get("exit_price", 1.0) or 1.0)
                if exit_ts >= cutoff and exit_price <= 0.10:
                    count += 1
            return count
        except Exception:
            return 0

    def _is_dir_cooldown_active(self, direction: str, cooldown_minutes: int = 15) -> bool:
        """Return True if a trade on this asset+direction closed within the last N minutes."""
        try:
            import json, time as _time
            from datetime import datetime, timezone
            from pathlib import Path
            path = Path(__file__).parent.parent.parent / "infrastructure" / "exchange" / "positions_state.json"
            if not path.exists():
                return False
            data = json.loads(path.read_text(encoding="utf-8"))
            cutoff = _time.time() - cooldown_minutes * 60
            signal_up = direction == "UP"
            asset_tag = f"[{self.asset}]"
            for trade in data.get("closed", []):
                if asset_tag not in (trade.get("event_title") or ""):
                    continue
                trade_dir = trade.get("direction", "")
                trade_is_up = trade_dir in ("YES", "UP")
                if trade_is_up != signal_up:
                    continue
                raw_ts = trade.get("exit_time") or trade.get("closed_at")
                if not raw_ts:
                    continue
                try:
                    if isinstance(raw_ts, str):
                        exit_ts = datetime.fromisoformat(raw_ts.replace("Z", "+00:00")).timestamp()
                    else:
                        exit_ts = float(raw_ts)
                    if exit_ts >= cutoff:
                        return True
                except Exception:
                    continue
            return False
        except Exception:
            return False

    def _recent_closed_loss_streak(self, n: int = 3) -> int:
        """Return consecutive recent closed losses from positions_state.json."""
        try:
            closed = self.state_mgr.get_closed_positions(limit=n)
        except AttributeError:
            closed = []
            try:
                import json
                from pathlib import Path
                path = Path(__file__).parent.parent.parent / "infrastructure" / "exchange" / "positions_state.json"
                if path.exists():
                    data = json.loads(path.read_text(encoding="utf-8"))
                    closed = data.get("closed", [])[:n]
            except Exception:
                return 0
        except Exception:
            return 0

        streak = 0
        for trade in closed[:n]:
            pnl = float(trade.get("realized_pnl", trade.get("profit", 0)) or 0)
            if pnl < 0:
                streak += 1
            else:
                break
        return streak

    # ── Dual-entry ────────────────────────────────────────────────────────────

    @staticmethod
    def should_dual_enter(up_price: float, dn_price: float) -> bool:
        from config import DUAL_ENTRY_MAX_COMBINED
        return (up_price + dn_price) < DUAL_ENTRY_MAX_COMBINED

    def compute_dual_sizes(self, score: float, main_price: float, hedge_price: float, balance: float):
        main_usd  = self.compute_size(score, main_price, balance)
        hedge_usd = round(0.25 * main_usd, 2)
        return main_usd, hedge_usd
