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
    mt = re.search(r"\[(5m|15m)\]", event_title or "")
    if not ma or not mt:
        return
    eng = _ENGINE_REGISTRY.get(f"{ma.group(1)}/{mt.group(1)}")
    if eng:
        eng.record_outcome(won)

POLY_GAMMA_API = "https://gamma-api.polymarket.com"
POLY_CLOB_API  = "https://clob.polymarket.com"
BINANCE_API    = "https://api.binance.com/api/v3"

# Global single-flight Technical Cache shared across all engine instances
_cache = TechnicalDataCache()

# Tier-based Kelly sizing
KELLY = {
    "HIGH": (0.040, 0.150),   # score >= 0.85: 4% Kelly, 15% cap
    "MED":  (0.030, 0.100),   # score 0.75-0.85: 3% Kelly, 10% cap
    "LOW":  (0.015, 0.050),   # score 0.62-0.75: 1.5% Kelly, 5% cap
}
MIN_USD = 1.00
VOLUME_GATE_FLOORS = {"BTC": 2.0, "ETH": 10.0, "SOL": 75.0, "XRP": 5000.0}
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
    bb = float(bids[0].get("price", 0)) if bids else 0.0
    ba = float(asks[0].get("price", 0)) if asks else 0.0
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
        self._prefetched_market: Optional[dict] = None
        self._prefetched_boundary: int = 0
        self.last_edge_context: Optional[dict] = None

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
        tf_map = {"5m": ("5m", 30), "15m": ("15m", 30)}
        interval, limit = tf_map.get(self.timeframe, ("5m", 30))
        klines = await _fetch_klines_async(session, self.asset, interval, limit)
        if len(klines) < 16:
            log.warning("[ENGINE] %s/%s: Insufficient candles (%d < 16) to calculate indicators.", self.asset, self.timeframe, len(klines))
            return None

        closes = [float(k[4]) for k in klines]

        # Pyth Hermes Real-time Price Integration
        try:
            from scratch.pyth_oracle_service import GLOBAL_ORACLE_CACHE
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

        # Retrieve real-time Spot Order Flow Imbalance (OFI)
        ofi = await get_current_ofi(self.asset)

        from core.engine.regime_filter import get_regime_mode
        regime = get_regime_mode(self.timeframe)

        # Raw direction from the shared signal core (single source of truth)
        from core.engine.signal_core import decide_signal
        _dec = decide_signal(rsi, mom, ofi, self.timeframe, regime=regime)
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
                    seq.append([p_delta, 0.0, sub_rsi, sub_mom, vol])
                if seq:
                    seq[-1][1] = ofi
                ai_up_prob = injector.predict(seq)
                if direction == "UP" and ai_up_prob < 0.35:
                    return None
                elif direction == "DOWN" and ai_up_prob > 0.65:
                    return None
                if direction == "UP" and ai_up_prob > 0.60:
                    score = min(1.0, score + 0.05)
                elif direction == "DOWN" and ai_up_prob < 0.40:
                    score = min(1.0, score + 0.05)
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

        score = round(score, 4)

        if score < 0.50 and not is_dual_eligible:
            return None

        log.info(
            "[ENGINE] %s/%s SIGNAL: %s | Score=%.2f | up=%.0fc dn=%.0fc | dual=%s | %s",
            self.asset, self.timeframe, direction, score,
            up_price * 100, dn_price * 100, is_dual_eligible, market["event_title"],
        )

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
            "is_dual_eligible": is_dual_eligible,
            "edge_context": edge_ctx,
        }

    async def _resolve_l2_prices(
        self,
        session: aiohttp.ClientSession,
        up_tk: str,
        dn_tk: str,
        max_spread: float = 0.15,
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
        for attempt in range(4):
            # Stretch sleep window (1.0s then 1.5s) to allow Polymarket order books to populate naturally
            await asyncio.sleep(1.0 if attempt == 0 else 1.5)
            up_price, up_spread = polymarket_l2_gateway.get_price(up_tk)
            dn_price, dn_spread = polymarket_l2_gateway.get_price(dn_tk)
            
            # 1. If we have both prices, verify and use them
            if up_price and dn_price and 0.03 < up_price < 0.97 and 0.03 < dn_price < 0.97:
                spread = (up_spread or 0.02) + (dn_spread or 0.02)
                if spread <= effective_max_spread:
                    return up_price, dn_price, spread
            
            # 2. Derive DOWN price if only UP exists and is valid
            if up_price and 0.03 < up_price < 0.97 and (not dn_price or dn_price <= 0.03 or dn_price >= 0.97):
                derived_dn = round(1.0 - up_price, 4)
                spread = (up_spread or 0.02) + 0.02
                if spread <= effective_max_spread:
                    return up_price, derived_dn, spread

            # 3. Derive UP price if only DOWN exists and is valid
            if dn_price and 0.03 < dn_price < 0.97 and (not up_price or up_price <= 0.03 or up_price >= 0.97):
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
        log.warning(
            "[LIVE-BOOK-REQUIRED] %s/%s: No valid L2 book after 4 attempts. Hard-skipping candle.",
            self.asset, self.timeframe
        )
        return None

    async def prefetch_upcoming_market(self, session: aiohttp.ClientSession, next_boundary: int) -> None:
        """Prefetch token IDs for the upcoming market 20s before start and warm WebSocket."""
        coin_lower = self.asset.lower()
        dur_min = 5 if self.timeframe == "5m" else 15
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
                            
                            self._prefetched_market = {
                                "event_id": ev.get("id", ""),
                                "event_title": ev.get("title", ""),
                                "expiry_ts": next_boundary + (dur_min * 60),
                                "duration_min": dur_min,
                                "liquidity": float(ev.get("liquidity", 0) or 1000.0),
                                "up_market": {"id": up_tk},
                                "dn_market": {"id": dn_tk},
                                "slug": slug,
                            }
                            self._prefetched_boundary = next_boundary
                            log.info(
                                "[ENGINE] %s/%s: Upcoming market pre-fetched & WS subscribed! Yes=%s No=%s",
                                self.asset, self.timeframe, up_tk[:10], dn_tk[:10]
                            )
                            return
        except Exception as e:
            log.warning("[ENGINE] Failed to pre-fetch upcoming market %s: %s", slug, e)

    async def _fetch_market(self, session: aiohttp.ClientSession) -> Optional[dict]:
        """Fetch active Up/Down market with verified L2/REST pricing (no 50c fallback)."""
        coin_lower = self.asset.lower()
        dur_min = 5 if self.timeframe == "5m" else 15
        now_ts = int(time.time())
        interval = dur_min * 60
        boundary = ((now_ts + interval) // interval) * interval
        start_ts = boundary - interval

        # Check if we have a valid pre-fetched market for the current candle start
        if self._prefetched_market and self._prefetched_boundary == start_ts:
            up_tk = self._prefetched_market["up_market"]["id"]
            dn_tk = self._prefetched_market["dn_market"]["id"]
            resolved = await self._resolve_l2_prices(session, up_tk, dn_tk)
            if resolved:
                up_price, dn_price, spread = resolved
                market = dict(self._prefetched_market)
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
                            resolved = await self._resolve_l2_prices(session, up_tk, dn_tk)
                            if not resolved:
                                log.info(
                                    "[ENGINE] %s/%s: slug %s — no valid L2 book (skip phantom 50c)",
                                    self.asset, self.timeframe, slug,
                                )
                                continue

                            up_price, dn_price, spread = resolved
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
                                "liquidity": float(ev.get("liquidity", 0) or 1000.0),
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
