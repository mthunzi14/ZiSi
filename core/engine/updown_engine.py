"""
updown_engine.py - ZiSi Intelligence Up/Down Engine (Async Restructured)
"""
import asyncio
import logging
import os
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
UPDOWN_MIN_LIQUIDITY = float(os.getenv("UPDOWN_MIN_LIQUIDITY", "200.0"))

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

    def _get_hourly_slug(self, timestamp: int) -> str:
        from zoneinfo import ZoneInfo
        import datetime
        dt_utc = datetime.datetime.fromtimestamp(timestamp, datetime.timezone.utc)
        dt_et = dt_utc.astimezone(ZoneInfo("America/New_York"))
        
        months = [
            "january", "february", "march", "april", "may", "june",
            "july", "august", "september", "october", "november", "december"
        ]
        month_name = months[dt_et.month - 1]
        
        hour_12 = dt_et.hour % 12
        if hour_12 == 0:
            hour_12 = 12
        am_pm = "pm" if dt_et.hour >= 12 else "am"
        
        asset_map = {
            "BTC": "bitcoin",
            "ETH": "ethereum",
            "SOL": "solana",
            "XRP": "xrp",
            "DOGE": "dogecoin",
        }
        asset_name = asset_map.get(self.asset, self.asset.lower())
        return f"{asset_name}-up-or-down-{month_name}-{dt_et.day}-{dt_et.year}-{hour_12}{am_pm}-et"

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

        # ── Momentum/flow DRIFT (REBUILD): give FV a real directional edge ──
        # The old model was driftless => coin-flip at ATM, exactly the band mentor PBot-6
        # prints in. We project a fraction of the prevailing momentum (EMA-5 vs EMA-20
        # slope) over the remaining window, and DAMPEN it in mean-reverting/chop regimes.
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
        mom = ((ema_5 - ema_20) / ema_20) if ema_20 else 0.0  # signed normalized momentum

        regime = get_regime_mode()
        from core.engine.fair_value import directional_drift, DEFAULT_CONTINUATION
        _cont = DEFAULT_CONTINUATION
        if regime in ("MEAN_REVERSION", "MEAN_REVERTING", "COMPRESSION"):
            _cont *= 0.35  # momentum unlikely to persist in chop — dampen the projected drift
        drift = directional_drift(mom, sigma_frac=sigma_frac, continuation=_cont)

        fp_up = fair_prob_up(spot, s_0, sigma_frac, elapsed_min, total_min, drift=drift)
        dec = decide_value_entry(fp_up, up_price, dn_price, elapsed_min, total_min,
                                 regime=regime, timeframe=self.timeframe, pct_move=mom)
        dec["drift"] = round(drift, 6)
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

        # Volatility Gate: block 5m entries under high volatility (DISABLED to allow continuous execution)
        if False:  # self.timeframe == "5m":
            try:
                import json as _json
                from pathlib import Path as _Path
                _rs = _Path(__file__).parent.parent.parent / "regime_status.json"
                if _rs.exists():
                    _d = _json.loads(_rs.read_text(encoding="utf-8"))
                    _reg = _d.get("regime")
                    _price_samples = int(_d.get("price_samples", 0))
                    # Require >=20 samples for a meaningful percentile rank.
                    # With <20 samples the mean ATR lands near the 75th percentile by construction,
                    # causing a false-positive VOL-VETO that blocks all 5m entries after a clean slate.
                    _atr_pct = float(_d.get("atr_percentile", 50.0)) if _price_samples >= 20 else 50.0
                    if _reg == "VOLATILE_CHAOS" or _atr_pct >= 80.0:
                        log.info(
                            "[VOL-VETO] %s/5m: Volatility too high (regime=%s, atr_percentile=%.1f%%) — blocking 5m entry.",
                            self.asset, _reg, _atr_pct
                        )
                        _write_gate_event(self.asset, self.timeframe, "VOLATILE_CHAOS", "N/A", f"regime={_reg}, atr_pct={_atr_pct:.1f}%")
                        return None
            except Exception as e:
                log.warning("[ENGINE] Volatility gate error: %s", e)

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

        # Retrieve real-time Spot Order Flow Imbalance (OFI)
        ofi = await get_current_ofi(self.asset)

        # Fetch market L2 quotes early
        market = await self._fetch_market(session)
        if not market:
            return None

        # Verify that the fetched market's start timestamp matches the current candle start timestamp
        # Prevents timeframe mismatch where we place trades on upcoming or previous candles
        # based on the current candle's indicators.
        import sys, os as _os_t
        is_testing = _os_t.environ.get("ZISI_TESTING") == "True" or any("unittest" in a or "pytest" in a for a in sys.argv)

        duration_min = market.get("duration_min")
        if duration_min is None:
            duration_min = 60 if self.timeframe == "1h" else int(self.timeframe.rstrip("m"))
        market_start_ts = market["expiry_ts"] - duration_min * 60
        last_kline_ts = int(klines[-1][0]) // 1000
        if market_start_ts != last_kline_ts and not is_testing:
            log.info(
                "[ENGINE] %s/%s Timeframe mismatch detected: market_start_ts=%d last_kline_ts=%d — skipping entry",
                self.asset, self.timeframe, market_start_ts, last_kline_ts
            )
            return None

        up_price = market["up_price"]
        dn_price = market["dn_price"]
        is_dual_eligible = self.should_dual_enter(up_price, dn_price)
        regime = get_regime_mode(self.timeframe)

        # 1-Hour Streak Reversal Check
        if self.timeframe == "1h" and len(klines) >= 5:
            closed_klines = klines[-5:-1]
            all_green = all(float(k[4]) > float(k[1]) for k in closed_klines)
            all_red = all(float(k[4]) < float(k[1]) for k in closed_klines)
            if all_green or all_red:
                raw_dir = "DOWN" if all_green else "UP"
                direction = apply_regime(raw_dir, regime, is_momentum=False)  # reversal — already contrarian
                if self.invert_signal:
                    direction = "DOWN" if direction == "UP" else "UP"
                
                score = 0.75
                log.warning(
                    "[REVERSAL-STREAK-1H] %s/1h: 4 consecutive %s closed candles. Sniping counter-trend %s (regime=%s, raw=%s)",
                    self.asset, "green" if all_green else "red", direction, regime, raw_dir
                )
                
                edge_ctx = {}
                try:
                    from core.engine.edge_orchestrator import edge_orchestrator
                    sig_dict = {
                        "signal_type": "TYPE_A_HIGH",
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
                        score = max(0.10, min(1.0, score + boost))
                        log.info("[EDGE] %s/%s Score adjusted by boost: %.2f (boost=%+.2f)", self.asset, self.timeframe, score, boost)
                    
                    regime = edge_ctx.get("regime_name", regime)
                except Exception as e:
                    log.warning("[EDGE] Failed to query EdgeOrchestrator in 1h streak reversal: %s", e)
                    self.last_edge_context = None

                # Tier 1: REV-STREAK whale veto — don't bet against strong whale momentum.
                # Whale pressure > 0.5 in the opposite direction means smart money is aligned
                # with the streak; fading it into a whale wall has bad expected value.
                _rev_whale = edge_ctx.get("whale_pressure", 0.0)
                if abs(_rev_whale) > 0.5:
                    _whale_bullish = _rev_whale > 0
                    _rev_is_up = direction == "UP"
                    if _whale_bullish != _rev_is_up:
                        log.info(
                            "[WHALE-VETO] %s/1h REV-STREAK: whale=%.2f (%s) contradicts %s — skip",
                            self.asset, _rev_whale,
                            "bullish" if _whale_bullish else "bearish", direction,
                        )
                        return None

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
                    "entry_source": "REVERSAL_STREAK",
                    "corroboration_multiplier": 1.0,
                    "whale_aligned": True,   # default allow for streak-reversal path
                    "confluence_score": 2,   # default allow for streak-reversal path
                }


        # ── Fair-value primary entry (prioritized check) ──
        # Check if we have a valid fair-value signal that clears the margin first.
        # If we do, we bypass volume, OFI, and trend gates entirely.
        _fv = {"direction": None}
        is_fv_trade = False
        _fv_confidence = 0.0          # REBUILD: FV directional confidence (drives ATM guard + sizing)
        _fv_archetype = "moderate"
        try:
            from config import FAIR_VALUE_MODE
        except Exception:
            FAIR_VALUE_MODE = False

        if FAIR_VALUE_MODE:
            _now_ts = datetime.now(timezone.utc).timestamp()
            _candle_duration_s = 3600 if self.timeframe == "1h" else (900 if self.timeframe == "15m" else 300)

            # Verify klines list is updated for the current candle start
            _expected_start_ts = int(_now_ts // _candle_duration_s * _candle_duration_s)
            _last_kline_ts = int(klines[-1][0]) // 1000 if klines else 0

            import sys
            import os
            is_testing = os.environ.get("ZISI_TESTING") == "True" or any("unittest" in arg or "pytest" in arg for arg in sys.argv)

            if _last_kline_ts != _expected_start_ts and not is_testing:
                # Klines list is lagged — wait for next tick to resolve current strike
                log.info(
                    "[ENGINE] %s/%s: Lagged klines list at candle boundary (last_kline_ts=%d expected_start_ts=%d) — skipping Fair Value decision for this tick",
                    self.asset, self.timeframe, _last_kline_ts, _expected_start_ts
                )
                _timing_ok = False
            else:
                _candle_open_ts = _last_kline_ts
                _elapsed_min = max(0.0, (_now_ts - _candle_open_ts) / 60.0)

                # Timing gate check
                # Deep contrarian (<40c): 0.5 min minimum — the best setup is the very first minute
                # after a strong directional candle (market overreacts, spot starts at 0% from open).
                # ATM/moderate: 1.0 min minimum to let price action settle.
                _is_deep_contra_price = min(up_price, dn_price) < 0.40
                _fv_min = 1.0 if self.timeframe == "1h" else (0.05 if _is_deep_contra_price else 1.0)  # 0.1min=6s: catch early-candle overreaction
                _timing_ok = True

                # Strict upper-bound timing gates: block late-candle entries
                if not is_testing:
                    if self.timeframe == "5m" and _elapsed_min > 4.0:
                        _timing_ok = False
                    elif self.timeframe == "15m" and _elapsed_min > 13.0:
                        _timing_ok = False
                    elif self.timeframe == "1h" and _elapsed_min > 55.0:
                        _timing_ok = False
                    elif _elapsed_min < _fv_min:
                        _timing_ok = False

            if _timing_ok:
                _fv = self._fair_value_entry(klines, closes[-1], up_price, dn_price, _elapsed_min)

                if _fv.get('direction') is not None:
                    # FV spot-direction alignment gate
                    # Block FV entries where current spot is moving AGAINST the signal direction.
                    # fair_value_entry() can lag spot by 1-2 ticks; if spot has already moved
                    # 0.25%+ against the FV call, the edge has been arbitraged away.
                    _fv_spot_align = True
                    try:
                        _candle_open = float(klines[-1][1])
                        _spot_now = closes[-1]  # Pyth-overwritten if available
                        _spot_pct = (_spot_now - _candle_open) / _candle_open if _candle_open > 0 else 0.0
                        _ALIGN_THRESH = 0.0050  # 0.50% — ATM directional-conflict gate
                        # Deep contrarian (<40c) BYPASSES this gate. A cheap contract exists
                        # because spot moved strongly against it — that IS the contrarian setup.
                        _fv_entry_p = dn_price if _fv['direction'] == 'DOWN' else up_price
                        _fv_is_deep_contra = _fv_entry_p < 0.40
                        if not _fv_is_deep_contra:
                            if _fv['direction'] == 'DOWN' and _spot_pct > _ALIGN_THRESH:
                                log.info(
                                    '[FV-SPOT-ALIGN] %s/%s: FV=DOWN but spot +%.3f%% above open — misaligned — skip',
                                    self.asset, self.timeframe, _spot_pct * 100,
                                )
                                _fv_spot_align = False
                            elif _fv['direction'] == 'UP' and _spot_pct < -_ALIGN_THRESH:
                                log.info(
                                    '[FV-SPOT-ALIGN] %s/%s: FV=UP but spot -%.3f%% below open — misaligned — skip',
                                    self.asset, self.timeframe, abs(_spot_pct) * 100,
                                )
                                _fv_spot_align = False
                        else:
                            # Tier 0: deep contrarian bypass requires high confidence AND
                            # sufficient time remaining (5m <90s left = no time to resolve).
                            _dc_min_conf = float(os.getenv("FV_DEEP_CONTRA_MIN_CONF", "0.72"))
                            _dc_conf = float(_fv.get("confidence", 0.0))
                            if _dc_conf < _dc_min_conf:
                                log.info(
                                    '[FV-SPOT-ALIGN] %s/%s: deep contrarian %.0fc — conf %.2f < %.2f (FV_DEEP_CONTRA_MIN_CONF) — blocked',
                                    self.asset, self.timeframe, _fv_entry_p * 100, _dc_conf, _dc_min_conf,
                                )
                                _fv_spot_align = False
                            elif self.timeframe == "5m" and _elapsed_min > 3.5:
                                log.info(
                                    '[FV-SPOT-ALIGN] %s/%s: deep contrarian %.0fc — <90s remaining (elapsed=%.2fmin) — blocked',
                                    self.asset, self.timeframe, _fv_entry_p * 100, _elapsed_min,
                                )
                                _fv_spot_align = False
                            else:
                                log.info(
                                    '[FV-SPOT-ALIGN] %s/%s: deep contrarian %.0fc @ conf=%.2f — bypass approved (spot %.3f%%)',
                                    self.asset, self.timeframe, _fv_entry_p * 100, _dc_conf, _spot_pct * 100,
                                )
                    except Exception:
                        pass  # fail open — do not block if price data unavailable
                    if not _fv_spot_align:
                        _fv = {'direction': None, 'edge': 0.0, 'archetype': None}

                # FV Archetype Gate REMOVED (REBUILD 2026-06-09): it blocked ALL moderate FV
                # unless regime == "RANGE" — a label get_regime_mode() NEVER emits (it returns
                # only "TREND"/"MEAN_REVERSION") — so daytime FV was zeroed out entirely
                # (live: FAIR-VALUE signals = 0). FV is now gated by its directional CONFIDENCE
                # + edge thresholds (the real quality controls), not a regime archetype.

                # Tier 2A: 4-Regime FV gate — MEAN_REVERSION + 5m requires higher confidence.
                # Deep contrarian continuation bets on 5m fail in MEAN_REVERSION because the
                # candle window is too short for the trend to assert itself (Punisher confirmed).
                # 15m/1h unaffected — more time for signal to resolve.
                if _fv.get("direction") is not None and self.timeframe == "5m" and regime == "MEAN_REVERSION":
                    _regime_fv_ep = dn_price if _fv["direction"] == "DOWN" else up_price
                    _regime_req_conf = 0.78 if _regime_fv_ep < 0.42 else 0.70
                    _regime_fv_conf = float(_fv.get("confidence", 0.0))
                    if _regime_fv_conf < _regime_req_conf:
                        log.info(
                            "[FV-REGIME-GATE] %s/5m: MEAN_REVERSION regime — FV %.0fc conf=%.2f < %.2f — skip",
                            self.asset, _regime_fv_ep * 100, _regime_fv_conf, _regime_req_conf,
                        )
                        _fv = {"direction": None, "edge": 0.0, "archetype": None, "confidence": 0.0, "fp_up": 0.0}

                if _fv.get("direction") is not None:
                    # Apply tiered edge gate and penalties
                    _entry_price_fv = up_price if _fv["direction"] == "UP" else dn_price
                    _cross_tf_conflict = False
                    if self.timeframe == "5m":
                        try:
                            _k15 = await _fetch_klines_async(session, self.asset, "15m", 5)
                            if len(_k15) >= 2:
                                _last15_bull = float(_k15[-2][4]) > float(_k15[-2][1])
                                _cross_tf_conflict = (_last15_bull != (_fv["direction"] == "UP"))
                        except Exception:
                            pass

                    if _entry_price_fv >= 0.50 and _entry_price_fv < 0.65:
                        _min_edge = 0.10  # REBUILD: 0.12->0.10, lift mid-band FV flow
                    elif _entry_price_fv >= 0.65:
                        _min_edge = 0.08  # REBUILD: 0.10->0.08
                    else:
                        _min_edge = 0.05

                    if _cross_tf_conflict:
                        _min_edge = max(_min_edge, _min_edge + 0.03)
                    # Asset-specific min_edge penalties REMOVED — were blocking ETH/SOL FV trades
                    # if self.asset == "ETH" and 0.40 <= _entry_price_fv < 0.65:
                    #     _min_edge = max(_min_edge, 0.15)
                    # if self.asset == "SOL":
                    #     _min_edge = max(_min_edge, 0.15)
                    # if self.timeframe == "15m":
                    #     _min_edge = max(_min_edge, 0.10)

                    # Macro-aware FV edge penalty
                    if len(klines) >= 10:
                        _fv_m8 = klines[-9:-1]
                        _fv_m_up = sum(1 for k in _fv_m8 if float(k[4]) > float(k[1]))
                        _fv_m_dn = 8 - _fv_m_up
                        _fv_is_up = _fv["direction"] == "UP"
                        # REBUILD: relaxed from near-block (0.25) to a moderate tilt — with the new
                        # momentum drift FV rarely fights a strong trend, and high-conviction
                        # counter-trend FV (fading an exhausted run) should still be allowed.
                        if (_fv_m_up >= 6 and not _fv_is_up) or (_fv_m_dn >= 6 and _fv_is_up):
                            _min_edge = _min_edge + 0.08
                        elif (_fv_m_up >= 5 and not _fv_is_up) or (_fv_m_dn >= 5 and _fv_is_up):
                            _min_edge = _min_edge + 0.04

                    if _fv["edge"] >= _min_edge:
                        # Peer corroboration size calculation
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
                        _corroboration_multiplier = 1.3 if _corroborated else 1.0

                        # We have a valid Fair Value trade signal! Set direction and score.
                        raw_dir = _fv["direction"]
                        direction = apply_regime(raw_dir, regime, is_momentum=False)  # FV carries its own directional edge
                        if self.invert_signal:
                            direction = "DOWN" if direction == "UP" else "UP"
                        
                        score_base = min(0.90, 0.55 + min(0.30, _fv["edge"]) + (0.05 if _fv["archetype"] == "near_certainty" else 0.0))
                        
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

                        # Set entry source and bypass momentum cascade
                        entry_source = "FAIR_VAL"
                        is_fv_trade = True
                        _fv_confidence = float(_fv.get("confidence", 0.0))
                        _fv_archetype = _fv.get("archetype", "moderate")

        if not is_fv_trade:
            # Volume gate
            volumes = [float(k[5]) for k in klines]
            avg_vol = sum(volumes[:-1]) / max(1, len(volumes) - 1)
            cur_vol = volumes[-2] if len(volumes) >= 2 else volumes[-1]
            floor = VOLUME_GATE_FLOORS.get(self.asset, 0.0)
            if cur_vol < floor and cur_vol < 0.30 * avg_vol:
                log.info("[ENGINE] %s/%s: volume gate fail (current vol %.1f < floor %.1f or < 30%% of avg %.1f)", self.asset, self.timeframe, cur_vol, floor, avg_vol)
                return None
                
            # Volume Climax Detector
            vol_climax_threshold = 6.0 if self.timeframe == "5m" else 3.0
            if cur_vol > vol_climax_threshold * avg_vol:
                log.info("[ENGINE] %s/%s: Volume climax detected (current vol %.1f > %.1fx avg %.1f). Blocking trade to avoid blow-off top/bottom.", self.asset, self.timeframe, cur_vol, vol_climax_threshold, avg_vol)
                return None

            # Volume surge block
            if len(volumes) >= 7:
                _roll_avg_vol = sum(volumes[-7:-2]) / 5
                if _roll_avg_vol > 0 and cur_vol > 4.0 * _roll_avg_vol:
                    log.info(
                        "[VOL-SURGE] %s/%s: spike %.0f > 4x avg %.0f — 2-candle pause",
                        self.asset, self.timeframe, cur_vol, _roll_avg_vol,
                    )
                    self._choppy_candles = max(self._choppy_candles, 2)
                    return None

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

            # Read live volatility percentiles for the 5m volatility veto
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

            # Raw direction from the shared signal core
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

            if _dec["is_reversal"]:
                # Reversal-snipe gets priority in non-FV
                entry_source = "REVERSAL_SNIPE"  # distinct label so confluence veto is bypassed
                _corroboration_multiplier = 1.0
                direction = apply_regime(raw_dir, regime, is_momentum=False)  # reversal — already contrarian
                if self.invert_signal:
                    direction = "DOWN" if direction == "UP" else "UP"
            else:
                entry_source = "SIG"
                _corroboration_multiplier = 1.0

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

                # Apply regime (fade weak momentum in mean-reversion; follow strong trends)
                direction = apply_regime(raw_dir, regime, mom=mom)
                if self.invert_signal:
                    direction = "DOWN" if direction == "UP" else "UP"

                # Tier 3: SIG last-candle trap guard — prevents entering after candle has already
                # moved against the signal direction. "Getting caught in traps" is almost always
                # a prior candle that went the wrong way and the signal is still stale.
                # Exempt: reversals (prior candle IS expected to be opposite), MEAN_REVERSION fades.
                if (not _dec.get("is_reversal")
                        and regime != "MEAN_REVERSION"
                        and score_base < 0.75
                        and self.timeframe in ("5m", "15m")
                        and len(klines) >= 2):
                    try:
                        _trap_prev = klines[-2]
                        _trap_prev_bull = float(_trap_prev[4]) >= float(_trap_prev[1])
                        _trap_sig_bull = direction == "UP"
                        if _trap_prev_bull != _trap_sig_bull:
                            log.info(
                                "[SIG-TRAP-GATE] %s/%s: SIG %s (score=%.2f) but prior candle %s — trap risk — skip",
                                self.asset, self.timeframe, direction, score_base,
                                "UP" if _trap_prev_bull else "DN",
                            )
                            return None
                    except Exception:
                        pass

                # 15m RSI overbought/oversold gate for 5m SIG.
                # Entering a 5m trend trade when 15m RSI > 76 (overbought) or < 24 (oversold)
                # is buying into exhaustion — the candle that caused the large 15m RSI is already done.
                # BTC/ETH @ 20:05 loss case: 15m RSI 80/84 → SIG entered UP → reversed -$12.
                # Exempt: reversal signals (they explicitly bet against the exhaustion).
                if self.timeframe == "5m" and not _dec.get("is_reversal"):
                    try:
                        _15m_tf = conf_up.get("timeframes", {}).get("15m", {})  # conf_up from L~833
                        _15m_rsi_v = _15m_tf.get("rsi")
                        if _15m_rsi_v is not None:
                            _rsi_ob = float(os.getenv("SIG_15M_RSI_OB", "76"))
                            _rsi_os = float(os.getenv("SIG_15M_RSI_OS", "24"))
                            if direction == "UP" and _15m_rsi_v > _rsi_ob:
                                log.info(
                                    "[SIG-15M-RSI] %s/5m: UP blocked — 15m RSI=%.1f > %.0f (overbought exhaustion)",
                                    self.asset, _15m_rsi_v, _rsi_ob,
                                )
                                return None
                            elif direction == "DOWN" and _15m_rsi_v < _rsi_os:
                                log.info(
                                    "[SIG-15M-RSI] %s/5m: DOWN blocked — 15m RSI=%.1f < %.0f (oversold exhaustion)",
                                    self.asset, _15m_rsi_v, _rsi_os,
                                )
                                return None
                    except (NameError, AttributeError, KeyError):
                        pass  # fail-open: no confluence data → don't block

                # Tier 3: SIG 5m late-entry gate — don't enter weak signals with < 90s remaining.
                # At T-90s (3.5 min elapsed), the edge is already priced in; remaining upside minimal.
                import os as _os_sig, sys as _sys_sig
                _sig_is_testing = (_os_sig.environ.get("ZISI_TESTING") == "True"
                                   or any("unittest" in a or "pytest" in a for a in _sys_sig.argv))
                if not _sig_is_testing and self.timeframe == "5m" and score_base < 0.80:
                    try:
                        from datetime import datetime as _dt_sig, timezone as _tz_sig
                        _sig_now_ts = _dt_sig.now(_tz_sig.utc).timestamp()
                        _sig_candle_start = int(_sig_now_ts // 300) * 300
                        _sig_elapsed = (_sig_now_ts - _sig_candle_start) / 60.0
                        if _sig_elapsed > 3.5:
                            log.info(
                                "[SIG-LATE-GATE] %s/5m: %.2fmin elapsed, score=%.2f < 0.80 — late entry risk — skip",
                                self.asset, _sig_elapsed, score_base,
                            )
                            return None
                    except Exception:
                        pass

                # Trend gate + choppy detection
                if len(closes) >= 10:
                    _c0 = closes[-5] if closes[-5] > 0 else 1.0
                    _slope = (closes[-1] - closes[-5]) / _c0
                    _TREND_GATE = 0.004
                    _ranging = abs(_slope) < _TREND_GATE
                    if not _ranging and regime != "MEAN_REVERSION":  # REBUILD: trend-confirm only in TREND (fade is intentional in MR)
                        _trend_dn = _slope < 0
                        _signal_dn = direction == "DOWN"
                        if _trend_dn != _signal_dn:
                            log.info(
                                "[TREND-GATE] %s/%s: %s signal contradicts trend (slope=%.3f%%) — skip",
                                self.asset, self.timeframe, direction, _slope * 100,
                            )
                            return None

                    # Serve choppy cooldown
                    if self.asset == "DOGE" and self._choppy_candles > 0:
                        self._choppy_candles -= 1
                        log.info(
                            "[CHOPPY] %s/%s: cooling down (%d candle(s) remaining)",
                            self.asset, self.timeframe, self._choppy_candles,
                        )
                        return None

                    # Accumulate slope
                    self._slope_history.append(_slope)
                    if len(self._slope_history) > 4:
                        self._slope_history = self._slope_history[-4:]

                    # Detect choppy
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

                # Macro trend gate (8-candle)
                if self.asset == "DOGE" and len(klines) >= 10:
                    _macro_candles = klines[-9:-1]
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

                # SIG trend confirmation
                if self.asset == "DOGE" and len(klines) >= 4:
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
        # FV Score Isolation (Tier 1): FV signals have their own confidence model.
        # Applying raw momentum/OFI boosts inflates the FV score and inverts sizing
        # (small-edge FV bets become over-sized relative to their actual conviction).
        # FV sizing is driven by fv_confidence, not composite score — skip boosts for FV.
        abs_mom = abs(mom)
        score = score_base
        if not is_fv_trade:
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
        if _whale_pressure > 0.90 and direction == "DOWN":  # was 0.70 — too aggressive
            log.warning("[WHALE-VETO] %s/%s: extreme bullish whale pressure %.2f contradicts DOWN — skip",
                        self.asset, self.timeframe, _whale_pressure)
            return None
        elif _whale_pressure < -0.90 and direction == "UP":  # was -0.70
            log.warning("[WHALE-VETO] %s/%s: extreme bearish whale pressure %.2f contradicts UP — skip",
                        self.asset, self.timeframe, abs(_whale_pressure))
            return None

        # Confluence-Veto Gate: REMOVED to emulate Friday June 5th state
        # if entry_source != "FAIR_VAL" and not is_dual_eligible and edge_ctx and edge_ctx.get("confluence_score", 2) == 0:
        #     log.warning(
        #         "[CONFLUENCE-VETO] %s/%s: Blocking directional entry due to complete lack of multi-timeframe agreement (score = 0)",
        #         self.asset, self.timeframe
        #     )
        #     return None

        if score < 0.55 and not is_dual_eligible:
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

        # Add whale alignment and confluence to signal for downstream gates
        if self.last_edge_context:
            ec = self.last_edge_context
            whale_pressure = ec.get('whale_pressure', 0.0)
            # whale_pressure > 0 means bullish, < 0 means bearish
            whale_is_up = whale_pressure >= 0.0
            _whale_aligned = (whale_is_up == (direction == 'UP'))
            _confluence_score = ec.get('confluence_score', 0)
        else:
            _whale_aligned = True   # default allow if no edge context
            _confluence_score = 2   # default allow

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
            "fv_confidence": _fv_confidence,
            "fv_archetype": _fv_archetype,
            "whale_aligned": _whale_aligned,
            "confluence_score": _confluence_score,
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

        # Always enforce live spread gate regardless of mode — this is a live simulation.
        # REBUILD: non-latency FV/SIG tolerate a wider spread (thin early-candle books are
        # real, just illiquid) so they aren't hard-skipped; latency sweeps stay tight.
        effective_max_spread = max_spread if is_latency_scan else max(max_spread, 0.20)

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
        _BACKOFF_THRESHOLD = 10  # was 5 — trigger less aggressively
        _BACKOFF_SECS = 2 * 60  # 2 minutes (was 15 — too long, blocks NCS and FV prefetch trades)
        if self._l2_fail_count >= _BACKOFF_THRESHOLD:
            self._l2_backoff_until = time.time() + _BACKOFF_SECS
            self._l2_fail_count = 0  # reset so next recovery period tries fresh
            log.warning(
                "[L2-CIRCUIT-BREAKER] %s/%s: %d consecutive L2 failures — backing off for 2 min (was 15 min).",
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
        if self.timeframe == "1h":
            slug = self._get_hourly_slug(next_boundary)
        else:
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
                if offset_ts + interval <= now_ts:
                    # Skip expired markets to prevent calling _resolve_l2_prices on them
                    # which always fails and triggers the L2 circuit breaker backoff.
                    continue
                if self.timeframe == "1h":
                    slug = self._get_hourly_slug(offset_ts)
                else:
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

    def compute_size(self, score: float, price: float, balance: float, confidence: float = None) -> float:
        """Return USD amount to bet, sized by directional CONFIDENCE (Dynamic Kelly) scaled by regime and asset weight."""
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
                # ── Confidence-tiered sizing (REBUILD): size by CONVICTION, not by price ──
                # Mentors bet big on a strong read regardless of entry price (PBot-6 $194@54c,
                # Rith $2,295@46c). confidence = FV directional confidence; falls back to score.
                conf = confidence if confidence is not None else score
                if conf >= 0.80:
                    _bk_frac = 0.25
                elif conf >= 0.70:
                    _bk_frac = 0.18
                elif conf >= 0.62:
                    _bk_frac = 0.10
                else:
                    _bk_frac = 0.05
                # Cheap longshots (<35c) hit ~40% — cap unless conviction is high (Rith only
                # sizes these big with a strong read); otherwise keep them small.
                if price < 0.35 and conf < 0.75:
                    _bk_frac = min(_bk_frac, 0.05)
                unified_max_cap = max(5.00, min(40.00, 5.00 + (conf - 0.50) * 80.0))
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
                    max_bankroll_fraction=_bk_frac,
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
                    price_scalar = 0.70  # REBUILD: softened 0.40->0.70 (confidence gate handles the 70c trap)
                    log.info("[SIZE] Price %.4f in 70c zone -> x0.70 scaling", price)
                elif price > 0.78:
                    price_scalar = 0.50  # REBUILD: softened 0.25->0.50 — mentors size near-certainty up
                    log.info("[SIZE] Price %.4f expensive -> x0.50 scaling", price)
                usd_size *= price_scalar

                # REBUILD: removed the blanket 50-65c x0.65 haircut — confidence-tiered _bk_frac
                # above already sizes ATM by conviction (mentors bet ATM big on a strong read).

                # Consecutive Loss Streak Brake
                consecutive_losses = self._recent_closed_loss_streak()
                if consecutive_losses >= 2:
                    usd_size *= 0.5
                    log.warning(
                        "[SIZE] %s/%s loss streak brake active (%d losses) -> halving size in adaptive Kelly",
                        self.asset, self.timeframe, consecutive_losses,
                    )

                # REBUILD: BTC > ETH asset weighting — user wants BTC as the heaviest asset
                # (mentors put the biggest dollar size on BTC).
                _asset_w = {"BTC": 1.0, "ETH": 0.85, "SOL": 0.70, "XRP": 0.65, "DOGE": 0.55}.get(self.asset, 0.70)
                usd_size *= _asset_w

                shares = round(usd_size / price) if price > 0 else 0
                actual_cost = shares * price
                log.info("[SIZE] Adaptive Kelly cost $%.2f (shares=%d, conf=%.2f, asset_w=%.2f)", actual_cost, shares, conf, _asset_w)
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
            closed = data.get("closed", [])[-n:][::-1]  # most recent n trades, newest first
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
                exit_iso = trade.get("exit_time") or trade.get("closed_at") or ""
                try:
                    exit_ts = datetime.fromisoformat(exit_iso).timestamp() if exit_iso else 0.0
                except Exception:
                    exit_ts = float(exit_iso) if exit_iso else 0.0
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
