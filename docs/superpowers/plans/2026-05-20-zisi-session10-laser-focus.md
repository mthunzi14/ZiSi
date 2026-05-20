# ZiSi Session 10 — Laser Focus + pBot Intelligence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform ZiSi from a multi-platform news bot into a precision Polymarket Up/Down engine modelled on pbot-6's $100k+ strategy — 6 asyncio tasks, 5 assets, Air-design dashboard.

**Architecture:** Replace 15-min threading loop with `asyncio.gather()` of 6 independent per-asset coroutines aligned to candle boundaries. Add regime filter, price gate, dual-entry, circuit breaker, inversion monitor, and silent fill reconciliation. Replace all 5 dashboard components with 6 new Air-design panels fed by SSE stream.

**Tech Stack:** Python 3.11+, asyncio, aiohttp, requests, Polymarket CLOB API, Binance REST API, React 18, Recharts, Inter/Montserrat/JetBrains Mono (Google Fonts), Express.js SSE

---

## Task 1: Surgical Deletion

**Files:**
- Delete: `kalshi/` directory
- Delete: `markets_orchestrator.py`, `category_suspensions.json`
- Delete: `sentiment_analyzer.py`, `data_fetcher.py`, `rss_fetcher.py`, `signal_router.py`, `event_matcher.py`, `smart_money.py`, `consensus_engine.py`
- Delete: `data_sources/` directory (all 14 files)
- Delete: `shadow_mode.py`, `markov_tracker.py`, `price_drift_tracker.py`, `regime_adaptive_weights.py`
- Delete: `shadow_state.json`, `markov_state.json`, `macro_context.json`, `rapid_fire_queue.json`

- [ ] **Step 1: Delete Kalshi and markets orchestrator**

```powershell
Remove-Item -Recurse -Force kalshi/
Remove-Item -Force markets_orchestrator.py, category_suspensions.json
```

Expected: no error, files gone.

- [ ] **Step 2: Delete news/LLM pipeline modules**

```powershell
Remove-Item -Force sentiment_analyzer.py, data_fetcher.py, rss_fetcher.py, signal_router.py, event_matcher.py, smart_money.py, consensus_engine.py
```

- [ ] **Step 3: Delete data_sources directory**

```powershell
Remove-Item -Recurse -Force data_sources/
```

- [ ] **Step 4: Delete obsolete state modules**

```powershell
Remove-Item -Force shadow_mode.py, markov_tracker.py, price_drift_tracker.py, regime_adaptive_weights.py
```

- [ ] **Step 5: Delete dead JSON state files**

```powershell
Remove-Item -Force shadow_state.json, macro_context.json, rapid_fire_queue.json
if (Test-Path markov_state.json) { Remove-Item -Force markov_state.json }
```

- [ ] **Step 6: Verify deletions**

```powershell
Get-ChildItem | Where-Object { $_.Name -match "kalshi|data_sources|shadow_mode|markov_tracker|price_drift|regime_adapt|sentiment_anal|data_fetcher|rss_fetcher|signal_router|event_matcher|smart_money|consensus" }
```

Expected: empty output (no matching files/dirs).

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "chore: surgical deletion — remove kalshi, news/LLM, data_sources, obsolete state modules"
```

---

## Task 2: Refactor config.py

**Files:**
- Modify: `config.py`

- [ ] **Step 1: Replace config.py with cleaned version**

Replace entire `config.py` with:

```python
"""
config.py - ZiSi Bot Configuration Loader
Polymarket Up/Down focus — no Kalshi, no LLM, no news pipeline.
"""

import os
import re
import logging
from dotenv import load_dotenv
from state_manager import initialize_state, get_current_balance

_ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
load_dotenv(_ENV_PATH)

_initial_balance = initialize_state()
_log = logging.getLogger("zisi.config")

_REQUIRED_KEYS = [
    "POLYMARKET_GAMMA_API_URL",
    "POLYMARKET_DATA_API_URL",
    "POLYMARKET_CLOB_API_URL",
]

_SECRET_KEYS = {
    "GMAIL_APP_PASSWORD",
}

# ── Asset config ──────────────────────────────────────────────────────────────
ASSETS: list = ["BTC", "ETH", "SOL", "XRP"]
TIMEFRAMES: dict = {"BTC": ["5m", "15m"], "ETH": ["5m"], "SOL": ["5m"], "XRP": ["5m"]}

# ── Regime + time gate ────────────────────────────────────────────────────────
TIME_GATE_UTC: tuple = (13, 23)          # trade only UTC 13:00–23:00

# ── pBot intelligence parameters ─────────────────────────────────────────────
INVERSION_WINDOW: int          = 40      # trades before inversion eligibility
INVERSION_TRIGGER_WR: float    = 0.45   # flip signal below this WR
INVERSION_RECOVERY_WR: float   = 0.52   # revert above this WR
DUAL_ENTRY_MAX_COMBINED: float = 0.92   # don't dual-enter if combined cost > this
CIRCUIT_BREAKER_LOSSES: int    = 2      # consecutive losses before circuit break
CIRCUIT_BREAKER_SKIP: int      = 2      # windows to skip after circuit break
MAX_DAILY_LOSS_PCT: float      = 0.15   # halt at 15% daily drawdown
WARMUP_SECONDS: int            = 15     # seconds before candle close to connect
WARMUP_MIN_TICKS: int          = 3      # min ticks required in final 5s
WARMUP_MAX_JUMP: float         = 0.05   # reject if any tick jumps > 5¢
RECONCILE_INTERVAL: int        = 30     # seconds between reconciliation passes

# ── Exposure caps ─────────────────────────────────────────────────────────────
MAX_OPEN_PER_ASSET: int = 2
MAX_TOTAL_OPEN: int     = 6


def load_config() -> dict:
    raw = {
        "POLYMARKET_GAMMA_API_URL":  os.getenv("POLYMARKET_GAMMA_API_URL",  "https://gamma-api.polymarket.com"),
        "POLYMARKET_DATA_API_URL":   os.getenv("POLYMARKET_DATA_API_URL",   "https://data-api.polymarket.com"),
        "POLYMARKET_CLOB_API_URL":   os.getenv("POLYMARKET_CLOB_API_URL",   "https://clob.polymarket.com"),
        "GOOGLE_DRIVE_FOLDER_ID":    os.getenv("GOOGLE_DRIVE_FOLDER_ID",    ""),
        "GOOGLE_CREDENTIALS_FILE":   os.getenv("GOOGLE_CREDENTIALS_FILE",   "credentials.json"),
        "GMAIL_SENDER_EMAIL":        os.getenv("GMAIL_SENDER_EMAIL",        ""),
        "GMAIL_APP_PASSWORD":        os.getenv("GMAIL_APP_PASSWORD",        ""),
        "GMAIL_ENABLED":             os.getenv("GMAIL_ENABLED", "true").lower() == "true",
        "BOT_NAME":                  os.getenv("BOT_NAME",    "ZiSi"),
        "BOT_VERSION":               os.getenv("BOT_VERSION", "2.0"),
        "BOT_MODE":                  os.getenv("BOT_MODE",    "paper_trading"),
        "ACCOUNT_BALANCE":           get_current_balance(),
        "RISK_PER_TRADE_PERCENT":    float(os.getenv("RISK_PER_TRADE_PERCENT", "2")),
        "MAX_SIMULTANEOUS_TRADES":   int(os.getenv("MAX_SIMULTANEOUS_TRADES", "6")),
        "MIN_EVENT_LIQUIDITY_USD":   float(os.getenv("MIN_EVENT_LIQUIDITY_USD", "500")),
        "LOG_TO_DRIVE":              os.getenv("LOG_TO_DRIVE",  "true").lower() == "true",
        "LOG_TO_CONSOLE":            os.getenv("LOG_TO_CONSOLE","true").lower() == "true",
        "DAILY_REPORT_TIME":         os.getenv("DAILY_REPORT_TIME", "09:00"),
        "DAILY_REPORT_EMAIL":        os.getenv("DAILY_REPORT_EMAIL","true").lower() == "true",
        "API_TIMEOUT_SECONDS":       int(os.getenv("API_TIMEOUT_SECONDS", "10")),
        "API_RETRY_COUNT":           int(os.getenv("API_RETRY_COUNT", "3")),
        "LOG_LEVEL":                 os.getenv("LOG_LEVEL", "INFO"),
    }

    missing = [k for k in _REQUIRED_KEYS if not raw.get(k)]
    if missing:
        raise ValueError(f"Missing required config keys: {', '.join(missing)}")

    _validate(raw)
    return raw


def _validate(config: dict) -> None:
    errors = []
    url_pattern = re.compile(r"^https?://")
    for k in ("POLYMARKET_GAMMA_API_URL", "POLYMARKET_DATA_API_URL", "POLYMARKET_CLOB_API_URL"):
        if not url_pattern.match(config.get(k, "")):
            errors.append(f"{k} must start with http(s)://")
    balance = config.get("ACCOUNT_BALANCE", 0)
    if not isinstance(balance, (int, float)) or balance <= 0:
        errors.append("ACCOUNT_BALANCE must be > 0")
    if config.get("BOT_MODE") not in ("paper_trading", "live_trading"):
        errors.append("BOT_MODE must be 'paper_trading' or 'live_trading'")
    if errors:
        raise ValueError("Config validation failed:\n  " + "\n  ".join(errors))


def get_config(key: str, default=None):
    try:
        return load_config().get(key, default)
    except Exception:
        return default


def log_config_startup(config: dict | None = None) -> None:
    cfg = config or load_config()
    mode_tag = "PAPER" if cfg["BOT_MODE"] == "paper_trading" else "LIVE"
    print(
        f"BOT STARTING: {cfg['BOT_NAME']} v{cfg['BOT_VERSION']} | "
        f"Account: ${cfg['ACCOUNT_BALANCE']:.0f} | "
        f"Risk: {cfg['RISK_PER_TRADE_PERCENT']:.0f}% | "
        f"Mode: {mode_tag}"
    )
```

- [ ] **Step 2: Verify config loads**

```bash
python -c "from config import load_config; c = load_config(); print('OK:', c['BOT_NAME'], c['BOT_VERSION'])"
```

Expected: `OK: ZiSi 2.0`

- [ ] **Step 3: Commit**

```bash
git add config.py
git commit -m "refactor(config): remove Kalshi/LLM/news params, add pBot intelligence params"
```

---

## Task 3: Create regime_filter.py

**Files:**
- Create: `regime_filter.py`

- [ ] **Step 1: Create the file**

```python
# regime_filter.py - Weekday/weekend regime + UTC time gate
from datetime import datetime, timezone
from typing import Literal


def get_regime_mode() -> Literal["TREND", "MEAN_REVERSION"]:
    """Mon–Fri = TREND (follow RSI). Sat–Sun = MEAN_REVERSION (fade RSI extremes)."""
    return "TREND" if datetime.now(timezone.utc).weekday() < 5 else "MEAN_REVERSION"


def time_gate_open() -> bool:
    """Return True only during UTC 13:00–23:00 (US + EU active sessions)."""
    from config import TIME_GATE_UTC
    hour = datetime.now(timezone.utc).hour
    start, end = TIME_GATE_UTC
    return start <= hour < end


def apply_regime(direction: str, regime: str) -> str:
    """
    Apply regime logic to a raw RSI signal direction.
    TREND: keep the signal as-is (follow momentum).
    MEAN_REVERSION: invert the signal (fade extremes).
    """
    if regime == "MEAN_REVERSION":
        return "DOWN" if direction == "UP" else "UP"
    return direction
```

- [ ] **Step 2: Verify import**

```bash
python -c "from regime_filter import get_regime_mode, time_gate_open, apply_regime; print(get_regime_mode(), time_gate_open())"
```

Expected: prints `TREND True` (or `MEAN_REVERSION` on weekends).

- [ ] **Step 3: Commit**

```bash
git add regime_filter.py
git commit -m "feat(regime): add weekday/weekend regime filter + UTC time gate"
```

---

## Task 4: Create reconciliation.py

**Files:**
- Create: `reconciliation.py`

- [ ] **Step 1: Create the file**

```python
# reconciliation.py - 30s asyncio fill verification loop
import asyncio
import logging
import requests

log = logging.getLogger("zisi.reconcile")

POLY_CLOB_API = "https://clob.polymarket.com"


async def reconciliation_loop(state_mgr, telegram_fn=None) -> None:
    """
    Run forever; every 30 seconds verify open positions against CLOB.
    Fixes 'ghost fills' — positions that filled at the exchange but
    weren't recorded locally due to a timeout on the submission call.
    """
    from config import RECONCILE_INTERVAL
    while True:
        await asyncio.sleep(RECONCILE_INTERVAL)
        try:
            _run_reconcile_pass(state_mgr, telegram_fn)
        except Exception as exc:
            log.warning("[RECONCILE] Pass failed: %s", exc)


def _run_reconcile_pass(state_mgr, telegram_fn=None) -> int:
    """Check all open positions for ghost fills. Returns number corrected."""
    corrected = 0
    try:
        positions = state_mgr.get_open_positions()
    except Exception:
        return 0

    for pos in positions:
        order_id = pos.get("order_id") or pos.get("id")
        if not order_id or pos.get("confirmed"):
            continue
        try:
            r = requests.get(
                f"{POLY_CLOB_API}/order/{order_id}",
                timeout=5,
            )
            if r.status_code != 200:
                continue
            data = r.json()
            status = str(data.get("status", "")).upper()
            if status in ("FILLED", "MATCHED"):
                state_mgr.force_confirm(pos)
                corrected += 1
                log.warning("[RECONCILE] Ghost fill corrected: %s %s", pos.get("asset", "?"), order_id[:20])
                if telegram_fn:
                    telegram_fn(f"👻 Ghost fill detected + corrected: {pos.get('asset','?')} | {order_id[:20]}")
        except Exception as exc:
            log.debug("[RECONCILE] Order check failed %s: %s", order_id[:16], exc)

    if corrected:
        log.info("[RECONCILE] Pass complete — %d ghost fill(s) corrected", corrected)
    return corrected
```

- [ ] **Step 2: Add `get_open_positions` and `force_confirm` to state_manager.py**

Open `state_manager.py` and append at the end:

```python
def get_open_positions() -> list:
    """Return all active (open) positions from positions_state.json."""
    if not _POSITIONS_FILE.exists():
        return []
    try:
        data = json.loads(_POSITIONS_FILE.read_text(encoding="utf-8"))
        return data.get("active", [])
    except Exception:
        return []


def is_confirmed(position_id: str) -> bool:
    """Return True if this position has been confirmed (marked filled)."""
    if not _POSITIONS_FILE.exists():
        return False
    try:
        data = json.loads(_POSITIONS_FILE.read_text(encoding="utf-8"))
        for pos in data.get("active", []):
            if pos.get("id") == position_id or pos.get("order_id") == position_id:
                return bool(pos.get("confirmed", False))
    except Exception:
        pass
    return False


def force_confirm(position: dict) -> None:
    """Mark a position as confirmed (ghost fill correction)."""
    if not _POSITIONS_FILE.exists():
        return
    try:
        with _lock:
            data = json.loads(_POSITIONS_FILE.read_text(encoding="utf-8"))
            for pos in data.get("active", []):
                if pos.get("order_id") == position.get("order_id"):
                    pos["confirmed"] = True
                    break
            _POSITIONS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception as exc:
        log.warning("[STATE] force_confirm failed: %s", exc)
```

- [ ] **Step 3: Verify imports**

```bash
python -c "from reconciliation import reconciliation_loop; from state_manager import get_open_positions, force_confirm; print('OK')"
```

Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add reconciliation.py state_manager.py
git commit -m "feat(reconcile): 30s ghost-fill correction loop + state_manager helpers"
```

---

## Task 5: Create updown_engine.py

**Files:**
- Create: `updown_engine.py` (replaces `updown_trader.py` — keep old file until main.py is wired)

- [ ] **Step 1: Create updown_engine.py**

```python
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
    "HIGH": (0.040, 0.150),   # score ≥ 0.85: 4% Kelly, 15% cap
    "MED":  (0.030, 0.100),   # score 0.75–0.85: 3% Kelly, 10% cap
    "LOW":  (0.015, 0.050),   # score 0.62–0.75: 1.5% Kelly, 5% cap
}
MIN_USD = 1.00
VOLUME_GATE_FLOORS = {"BTC": 2.0, "ETH": 10.0, "SOL": 75.0, "XRP": 5000.0}
UPDOWN_MIN_LIQUIDITY = 500.0

# ── Score → WR → max entry price ─────────────────────────────────────────────
SCORE_TO_WR = [
    (0.85, 0.70),   # score ≥ 0.85 → est WR 70% → max entry 60¢
    (0.75, 0.65),   # score 0.75–0.85 → est WR 65% → max entry 55¢
    (0.62, 0.57),   # score 0.62–0.75 → est WR 57% → max entry 47¢
]


def _lookup_wr(score: float) -> Optional[float]:
    for threshold, wr in SCORE_TO_WR:
        if score >= threshold:
            return wr
    return None


def price_gate_passes(price: float, score: float) -> bool:
    """Punisher rule: entry price must be ≥ 10¢ below estimated WR."""
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
                    f"⚠️ {self.asset}/{self.timeframe}: 2 consecutive losses — pausing 2 windows"
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
                f"🔄 {self.asset}/{self.timeframe}: WR={rolling_wr:.0%} over 40 windows — INVERTING signal"
            )
            log.warning("[ENGINE] %s/%s: WR=%.0f%% — signal INVERTED", self.asset, self.timeframe, rolling_wr * 100)
        elif rolling_wr > INVERSION_RECOVERY_WR and self.invert_signal:
            self.invert_signal = False
            self.telegram(
                f"✅ {self.asset}/{self.timeframe}: WR recovered to {rolling_wr:.0%} — reverting inversion"
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

        # Composite score (simplified version — keeps core RSI + momentum signal)
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
                        "event_id":   ev.get("id", ""),
                        "event_title": ev.get("title", ""),
                        "expiry_ts":  expiry_ts,
                        "duration_min": dur_min,
                        "liquidity":  liq,
                        "up_price":   up_price,
                        "dn_price":   dn_price,
                        "up_market":  up_m,
                        "dn_market":  dn_m,
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
```

- [ ] **Step 2: Verify the class imports**

```bash
python -c "from updown_engine import UpDownEngine, price_gate_passes; print('OK — price_gate_passes(0.45, 0.85)=', price_gate_passes(0.45, 0.85))"
```

Expected: `OK — price_gate_passes(0.45, 0.85)= True`

- [ ] **Step 3: Verify price gate boundary**

```bash
python -c "from updown_engine import price_gate_passes; print('0.65 @ 0.85 =', price_gate_passes(0.65, 0.85), '(expect False)')"
```

Expected: `0.65 @ 0.85 = False (expect False)`

- [ ] **Step 4: Commit**

```bash
git add updown_engine.py
git commit -m "feat(engine): UpDownEngine class with dual-entry, circuit-breaker, inversion monitor"
```

---

## Task 6: Add entry price gate + exposure caps to risk_manager.py

**Files:**
- Modify: `risk_manager.py`

- [ ] **Step 1: Append price gate + exposure cap functions to risk_manager.py**

Open `risk_manager.py` and append:

```python
# ── Entry price gate (pBot Session 10) ───────────────────────────────────────

_SCORE_TO_WR = [
    (0.85, 0.70),
    (0.75, 0.65),
    (0.62, 0.57),
]


def entry_price_gate(price: float, score: float) -> bool:
    """
    Punisher rule: entry price must be ≤ (estimated_WR - 0.10).
    Returns False = skip this window. No fallback — the edge is in cheap entries.
    """
    est_wr = None
    for threshold, wr in _SCORE_TO_WR:
        if score >= threshold:
            est_wr = wr
            break
    if est_wr is None:
        return False
    return price <= (est_wr - 0.10)


# ── Exposure caps ─────────────────────────────────────────────────────────────

def check_exposure_caps(asset: str, open_positions: list) -> bool:
    """
    Return True (OK to trade) if:
    - Fewer than MAX_OPEN_PER_ASSET open positions for this asset
    - Fewer than MAX_TOTAL_OPEN total open positions
    """
    from config import MAX_OPEN_PER_ASSET, MAX_TOTAL_OPEN
    if len(open_positions) >= MAX_TOTAL_OPEN:
        log.info("[RISK] Total open %d >= %d — skip", len(open_positions), MAX_TOTAL_OPEN)
        return False
    asset_open = sum(1 for p in open_positions if p.get("asset", "").upper() == asset.upper())
    if asset_open >= MAX_OPEN_PER_ASSET:
        log.info("[RISK] %s open %d >= %d — skip", asset, asset_open, MAX_OPEN_PER_ASSET)
        return False
    return True


def check_daily_loss_halt(starting_balance: float, current_balance: float) -> bool:
    """Return True if daily drawdown exceeds MAX_DAILY_LOSS_PCT threshold."""
    from config import MAX_DAILY_LOSS_PCT
    if starting_balance <= 0:
        return False
    drawdown = (starting_balance - current_balance) / starting_balance
    if drawdown >= MAX_DAILY_LOSS_PCT:
        log.warning("[RISK] Daily loss halt: drawdown=%.1f%% >= %.1f%%", drawdown * 100, MAX_DAILY_LOSS_PCT * 100)
        return True
    return False
```

- [ ] **Step 2: Verify import**

```bash
python -c "from risk_manager import entry_price_gate, check_exposure_caps, check_daily_loss_halt; print(entry_price_gate(0.50, 0.85), check_exposure_caps('BTC', []))"
```

Expected: `True True`

- [ ] **Step 3: Commit**

```bash
git add risk_manager.py
git commit -m "feat(risk): add entry price gate, exposure caps, daily loss halt"
```

---

## Task 7: Add inversion monitor to metrics_engine.py

**Files:**
- Modify: `metrics_engine.py`

- [ ] **Step 1: Append inversion monitor state and functions**

Open `metrics_engine.py` and append:

```python
# ── Inversion monitor (Session 10) ───────────────────────────────────────────

_inversion_state: dict[str, bool] = {}       # asset/tf key → inverted bool
_recent_outcomes: dict[str, list] = {}        # asset/tf key → rolling 40 outcomes


def record_updown_outcome(asset: str, timeframe: str, won: bool) -> dict:
    """
    Record a resolved Up/Down trade outcome and check inversion threshold.
    Returns state dict with rolling_wr and invert_signal.
    """
    from config import INVERSION_WINDOW, INVERSION_TRIGGER_WR, INVERSION_RECOVERY_WR
    key = f"{asset}/{timeframe}"
    outcomes = _recent_outcomes.setdefault(key, [])
    outcomes.append(won)
    if len(outcomes) > INVERSION_WINDOW:
        outcomes.pop(0)

    rolling_wr = sum(outcomes) / len(outcomes) if outcomes else 0.5
    inverted   = _inversion_state.get(key, False)

    if len(outcomes) >= INVERSION_WINDOW:
        if rolling_wr < INVERSION_TRIGGER_WR and not inverted:
            _inversion_state[key] = True
            log.warning(
                "[METRICS] %s WR=%.0f%% over %d trades — INVERTING signal",
                key, rolling_wr * 100, INVERSION_WINDOW,
            )
        elif rolling_wr > INVERSION_RECOVERY_WR and inverted:
            _inversion_state[key] = False
            log.info("[METRICS] %s WR=%.0f%% recovered — inversion REVERTED", key, rolling_wr * 100)

    return {
        "key":        key,
        "rolling_wr": round(rolling_wr, 4),
        "inverted":   _inversion_state.get(key, False),
        "samples":    len(outcomes),
    }


def get_inversion_state() -> dict:
    """Return full inversion state for all tracked asset/timeframe pairs."""
    return {
        key: {
            "inverted":   _inversion_state.get(key, False),
            "rolling_wr": round(sum(v) / len(v), 4) if v else 0.5,
            "samples":    len(v),
        }
        for key, v in _recent_outcomes.items()
    }
```

- [ ] **Step 2: Verify import**

```bash
python -c "from metrics_engine import record_updown_outcome, get_inversion_state; r = record_updown_outcome('BTC', '5m', True); print(r)"
```

Expected: `{'key': 'BTC/5m', 'rolling_wr': 1.0, 'inverted': False, 'samples': 1}`

- [ ] **Step 3: Commit**

```bash
git add metrics_engine.py
git commit -m "feat(metrics): 40-window inversion monitor for per-asset rolling WR"
```

---

## Task 8: Rewrite main.py

**Files:**
- Modify: `main.py`

- [ ] **Step 1: Replace main.py entirely**

```python
"""
main.py - ZiSi Bot — Polymarket Up/Down asyncio engine
6 independent asyncio tasks: BTC-5m, BTC-15m, ETH-5m, SOL-5m, XRP-5m, reconciliation.
"""
import asyncio
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from config import load_config, log_config_startup, ASSETS, TIMEFRAMES
from logger import setup_file_logging
from state_manager import (
    initialize_state, initialize_runtime_tracking, update_runtime_tracking,
    update_heartbeat, get_current_balance,
)
from updown_engine import UpDownEngine, price_gate_passes
from risk_manager import entry_price_gate, check_exposure_caps, check_daily_loss_halt
from reconciliation import reconciliation_loop
from regime_filter import time_gate_open, get_regime_mode
from metrics_engine import record_updown_outcome, get_inversion_state
import state_manager

log = logging.getLogger("zisi.main")

# ── Global state ──────────────────────────────────────────────────────────────
_engines: dict[str, UpDownEngine] = {}   # "BTC/5m" → engine
_starting_balance: float = 0.0


def _try_telegram(msg: str) -> None:
    try:
        from telegram_bot import send_alert
        send_alert(msg)
    except Exception:
        pass


# ── Candle boundary alignment ────────────────────────────────────────────────

async def _sleep_to_next_candle(interval_minutes: int) -> None:
    """Sleep until the next candle close boundary (e.g. next :00/:05/:15 mark)."""
    interval_secs = interval_minutes * 60
    now = datetime.now(timezone.utc).timestamp()
    next_boundary = (int(now) // interval_secs + 1) * interval_secs
    sleep_secs = next_boundary - now + 1.5   # +1.5s safety margin after close
    if sleep_secs > 0:
        await asyncio.sleep(sleep_secs)


async def _align_to_candle_boundary(interval_minutes: int) -> None:
    """On startup, align to the next candle boundary."""
    await _sleep_to_next_candle(interval_minutes)


# ── Per-asset loop ────────────────────────────────────────────────────────────

async def asset_loop(asset: str, timeframe: str, offset_seconds: int = 0) -> None:
    """Independent asyncio task for one asset/timeframe pair."""
    global _starting_balance

    if offset_seconds > 0:
        await asyncio.sleep(offset_seconds)

    interval_minutes = int(timeframe.rstrip("m"))
    engine = _engines[f"{asset}/{timeframe}"]

    log.info("[MAIN] %s/%s task started — aligning to next candle boundary", asset, timeframe)
    await _align_to_candle_boundary(interval_minutes)

    while True:
        try:
            # Time gate
            if not time_gate_open():
                log.debug("[MAIN] %s/%s: time gate closed — sleeping to next candle", asset, timeframe)
                await _sleep_to_next_candle(interval_minutes)
                continue

            # Daily loss halt
            current_balance = get_current_balance()
            if check_daily_loss_halt(_starting_balance, current_balance):
                log.warning("[MAIN] Daily loss halt active — all trading paused")
                _try_telegram("🛑 ZiSi: daily loss halt triggered — all trading paused for today")
                await asyncio.sleep(3600)
                continue

            # Exposure caps
            open_positions = state_manager.get_open_positions()
            if not check_exposure_caps(asset, open_positions):
                await _sleep_to_next_candle(interval_minutes)
                continue

            # Generate signal
            signal = engine.generate_signal()
            if signal is None:
                await _sleep_to_next_candle(interval_minutes)
                continue

            direction = signal["direction"]
            score     = signal["score"]
            market    = signal["market"]
            entry_price = market["up_price"] if direction == "UP" else market["dn_price"]

            # Entry price gate (Punisher rule)
            if not entry_price_gate(entry_price, score):
                log.info("[MAIN] %s/%s: price gate blocked %.2f @ score %.2f", asset, timeframe, entry_price, score)
                await _sleep_to_next_candle(interval_minutes)
                continue

            # Compute bet size
            bet_usd = engine.compute_size(score, entry_price, current_balance)

            # Dual-entry check
            up_price = market["up_price"]
            dn_price = market["dn_price"]
            is_dual  = UpDownEngine.should_dual_enter(up_price, dn_price)

            if is_dual:
                main_usd, hedge_usd = engine.compute_dual_sizes(score, entry_price,
                    dn_price if direction == "UP" else up_price, current_balance)
                _place_trade(asset, timeframe, direction, market, main_usd, entry_price, score, trade_type="DUAL_MAIN")
                hedge_dir = "DOWN" if direction == "UP" else "UP"
                hedge_price = dn_price if direction == "UP" else up_price
                _place_trade(asset, timeframe, hedge_dir, market, hedge_usd, hedge_price, score, trade_type="DUAL_HEDGE")
                log.info("[MAIN] %s/%s: DUAL entry — main $%.2f %s + hedge $%.2f %s",
                         asset, timeframe, main_usd, direction, hedge_usd, hedge_dir)
            else:
                _place_trade(asset, timeframe, direction, market, bet_usd, entry_price, score, trade_type="SINGLE")

            update_runtime_tracking()

        except Exception as exc:
            log.error("[MAIN] %s/%s loop error: %s", asset, timeframe, exc, exc_info=True)

        await _sleep_to_next_candle(interval_minutes)


def _place_trade(asset, timeframe, direction, market, usd_amount, entry_price, score, trade_type="SINGLE"):
    """Paper-trade a position: record in positions_state.json."""
    try:
        from trader import place_paper_trade
        shares = round(usd_amount / entry_price)
        actual_cost = shares * entry_price
        market_id = (market["up_market"] if direction == "UP" else market["dn_market"]).get("id", "")
        place_paper_trade(
            event_id=market["event_id"],
            market_id=market_id,
            amount_dollars=actual_cost,
            direction="YES" if direction == "UP" else "NO",
            entry_price=entry_price,
            event_title=f"[UPDOWN][{asset}][{timeframe}][{trade_type}] {market['event_title']}",
            expiry_ts=market["expiry_ts"],
        )
        log.info(
            "[MAIN] TRADE PLACED: %s/%s %s | $%.2f @ %.2f¢ | score=%.2f | type=%s",
            asset, timeframe, direction, actual_cost, entry_price * 100, score, trade_type,
        )
        _try_telegram(
            f"📈 {asset}/{timeframe} {direction} | ${actual_cost:.2f} @ {entry_price*100:.0f}¢ | score={score:.2f} | {trade_type}"
        )
    except Exception as exc:
        log.error("[MAIN] Trade placement failed: %s", exc)


# ── Main entry ────────────────────────────────────────────────────────────────

async def main() -> None:
    global _starting_balance

    cfg = load_config()
    setup_file_logging(cfg.get("LOG_LEVEL", "INFO"))
    log_config_startup(cfg)

    _starting_balance = get_current_balance()
    initialize_runtime_tracking()

    # Build one engine per asset/timeframe
    for asset in ASSETS:
        for tf in TIMEFRAMES.get(asset, ["5m"]):
            key = f"{asset}/{tf}"
            _engines[key] = UpDownEngine(asset, tf, state_manager, _try_telegram)
            log.info("[MAIN] Engine registered: %s", key)

    log.info("[MAIN] Launching 6 asyncio tasks (5 asset loops + reconciliation)")

    await asyncio.gather(
        asset_loop("BTC", "5m",  offset_seconds=0),
        asset_loop("BTC", "15m", offset_seconds=0),
        asset_loop("ETH", "5m",  offset_seconds=90),
        asset_loop("SOL", "5m",  offset_seconds=180),
        asset_loop("XRP", "5m",  offset_seconds=270),
        reconciliation_loop(state_manager, _try_telegram),
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("[MAIN] Shutdown requested")
        sys.exit(0)
```

- [ ] **Step 2: Verify syntax**

```bash
python -c "import ast; ast.parse(open('main.py').read()); print('Syntax OK')"
```

Expected: `Syntax OK`

- [ ] **Step 3: Commit**

```bash
git add main.py
git commit -m "feat(main): replace 15-min loop with asyncio.gather() — 6 independent per-asset tasks"
```

---

## Task 9: Dashboard Backend — New SSE Events

**Files:**
- Modify: `dashboard/backend/routes/health.js`

- [ ] **Step 1: Add SSE endpoint and new event types to health.js**

Read the current `health.js` and add an SSE endpoint after the existing `router.get('/')`:

```javascript
// ── SSE stream — pushes live events to frontend ───────────────────────────
const _sseClients = new Set();

router.get('/stream', (req, res) => {
  res.setHeader('Content-Type', 'text/event-stream');
  res.setHeader('Cache-Control', 'no-cache');
  res.setHeader('Connection', 'keep-alive');
  res.flushHeaders();

  _sseClients.add(res);
  req.on('close', () => _sseClients.delete(res));

  // Send heartbeat immediately
  res.write(`data: ${JSON.stringify({ type: 'heartbeat', ts: Date.now() })}\n\n`);
});

function broadcastSSE(eventObj) {
  const msg = `data: ${JSON.stringify(eventObj)}\n\n`;
  for (const client of _sseClients) {
    try { client.write(msg); } catch { _sseClients.delete(client); }
  }
}

// Poll positions_state.json and broadcast position_update every 2s
setInterval(() => {
  try {
    const posFile = path.join(__dirname, '../../../positions_state.json');
    if (!fs.existsSync(posFile)) return;
    const positions = JSON.parse(fs.readFileSync(posFile, 'utf-8').replace(/^﻿/, ''));
    broadcastSSE({ type: 'position_update', payload: positions, ts: Date.now() });
  } catch { /* ignore */ }
}, 2000);

// Poll account_state.json and broadcast balance_update every 5s
setInterval(() => {
  try {
    const stateFile = path.join(__dirname, '../../../account_state.json');
    if (!fs.existsSync(stateFile)) return;
    const state = JSON.parse(fs.readFileSync(stateFile, 'utf-8').replace(/^﻿/, ''));
    broadcastSSE({ type: 'balance_update', payload: state, ts: Date.now() });
  } catch { /* ignore */ }
}, 5000);

// Poll candle boundary timers every 10s
setInterval(() => {
  const now = Math.floor(Date.now() / 1000);
  const boundaries = [
    { asset: 'BTC', tf: '5m',  secs: 300 - (now % 300) },
    { asset: 'BTC', tf: '15m', secs: 900 - (now % 900) },
    { asset: 'ETH', tf: '5m',  secs: 300 - (now % 300) },
    { asset: 'SOL', tf: '5m',  secs: 300 - (now % 300) },
    { asset: 'XRP', tf: '5m',  secs: 300 - (now % 300) },
  ];
  broadcastSSE({ type: 'candle_boundary', payload: boundaries, ts: Date.now() });
}, 10000);

export { broadcastSSE };
```

- [ ] **Step 2: Verify the backend starts**

```bash
cd dashboard && node backend/server.js &
curl -s http://localhost:3001/api/health | python -c "import sys,json; d=json.load(sys.stdin); print('Backend OK:', d.get('status','?'))"
```

Expected: `Backend OK: running` (or `offline` if account_state.json is absent — both are fine).

Stop the background server: `kill %1`

- [ ] **Step 3: Commit**

```bash
git add dashboard/backend/routes/health.js
git commit -m "feat(dashboard-be): add SSE stream + position_update, balance_update, candle_boundary events"
```

---

## Task 10: Dashboard Frontend — Air Design System Base

**Files:**
- Modify: `dashboard/frontend/src/App.jsx`
- Copy logo: `dashboard/frontend/src/assets/ZiSi_Final_Logo.png`

- [ ] **Step 1: Copy logo asset**

```powershell
if (-not (Test-Path dashboard/frontend/src/assets)) { New-Item -ItemType Directory dashboard/frontend/src/assets }
Copy-Item ZiSi_Final_Logo.png dashboard/frontend/src/assets/ZiSi_Final_Logo.png
```

- [ ] **Step 2: Add Air CSS tokens to App.css**

Open `dashboard/frontend/src/App.css` and prepend:

```css
/* Air Design System — dark-mode trading terminal */
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;600&family=Montserrat:wght@500;700&family=Oswald:wght@700;900&display=swap');

:root {
  --color-bg-base:       #0a0a0a;
  --color-bg-surface:    #111111;
  --color-bg-elevated:   #1a1a1a;
  --color-text-primary:  #ffffff;
  --color-text-secondary:#f5f5f5;
  --color-text-muted:    #6b6b6b;
  --color-accent:        #2b7fff;
  --color-accent-muted:  #426188;
  --color-midnight:      #1b1b1b;
  --color-profit:        #00d4a3;
  --color-loss:          #ff4d4d;
  --color-neutral:       #f5f5f5;
  --color-amber:         #f5a623;
  --color-xrp:           #ff9500;
  --font-body:           'Inter', ui-sans-serif, system-ui, sans-serif;
  --font-heading:        'Montserrat', ui-sans-serif, system-ui, sans-serif;
  --font-display:        'Oswald', ui-sans-serif, system-ui, sans-serif;
  --font-mono:           'JetBrains Mono', 'Fira Code', ui-monospace, monospace;
  --spacing-4:  4px;  --spacing-8:  8px;  --spacing-12: 12px;
  --spacing-16: 16px; --spacing-20: 20px; --spacing-24: 24px;
  --spacing-32: 32px; --spacing-48: 48px;
  --radius-inputs:  4px;
  --radius-buttons: 8px;
  --radius-cards:   14px;
}

*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body { background: var(--color-bg-base); color: var(--color-text-primary); font-family: var(--font-body); }
```

- [ ] **Step 3: Replace App.jsx with new grid layout**

```jsx
import { useState, useEffect, useRef } from 'react';
import './App.css';
import CommandCentre from './components/CommandCentre';
import AssetCards    from './components/AssetCards';
import TradeFeed     from './components/TradeFeed';
import WinRateChart  from './components/WinRateChart';
import PositionMonitor from './components/PositionMonitor';
import SystemHealth  from './components/SystemHealth';

export default function App() {
  const [state,     setState]     = useState({});
  const [positions, setPositions] = useState({ active: [], summary: {} });
  const [trades,    setTrades]    = useState([]);
  const [candles,   setCandles]   = useState([]);
  const esRef = useRef(null);

  // Polling fallback for health data
  useEffect(() => {
    const poll = async () => {
      try {
        const r = await fetch('/api/health');
        const d = await r.json();
        setState(d);
      } catch { /* offline */ }
    };
    poll();
    const id = setInterval(poll, 5000);
    return () => clearInterval(id);
  }, []);

  // SSE stream for live events
  useEffect(() => {
    const es = new EventSource('/api/health/stream');
    esRef.current = es;

    es.onmessage = (e) => {
      try {
        const event = JSON.parse(e.data);
        if (event.type === 'position_update')  setPositions(event.payload);
        if (event.type === 'balance_update')   setState(s => ({ ...s, ...event.payload }));
        if (event.type === 'candle_boundary')  setCandles(event.payload);
        if (event.type === 'trade_executed' || event.type === 'trade_resolved') {
          setTrades(t => [event.payload, ...t].slice(0, 50));
        }
      } catch { /* ignore malformed */ }
    };

    return () => es.close();
  }, []);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', minHeight: '100vh', background: 'var(--color-bg-base)' }}>
      <CommandCentre state={state} positions={positions} />

      <div style={{ padding: 'var(--spacing-16)', flex: 1, display: 'flex', flexDirection: 'column', gap: 'var(--spacing-16)' }}>

        {/* Row 1: Asset Cards */}
        <AssetCards positions={positions} candles={candles} />

        {/* Row 2: Trade Feed + Win Rate Chart */}
        <div style={{ display: 'grid', gridTemplateColumns: '40% 1fr', gap: 'var(--spacing-16)' }}>
          <TradeFeed trades={trades} positions={positions} />
          <WinRateChart trades={trades} />
        </div>

        {/* Row 3: Position Monitor + System Health */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 'var(--spacing-16)' }}>
          <PositionMonitor positions={positions} candles={candles} />
          <SystemHealth state={state} positions={positions} candles={candles} />
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Delete old components**

```powershell
$old = @('BotStatus','MissedTrades','SignalPipeline','CompoundingProgress','MacroPanel',
         'Header','Sidebar','EdgeValidation','AnalyticsBySection','ByUTC','RiskMetrics',
         'MLStatus','SystemAlerts','RegimeIndicator','ToastTest','Positions','SignalQueue',
         'PerformanceCard')
foreach ($c in $old) {
  $f = "dashboard/frontend/src/components/$c.jsx"
  if (Test-Path $f) { Remove-Item -Force $f }
}
```

- [ ] **Step 5: Commit base**

```bash
git add dashboard/frontend/src/App.jsx dashboard/frontend/src/App.css dashboard/frontend/src/assets/
git commit -m "feat(dashboard): Air CSS tokens, new grid layout App.jsx, delete old components"
```

---

## Task 11: CommandCentre.jsx

**Files:**
- Create: `dashboard/frontend/src/components/CommandCentre.jsx`

- [ ] **Step 1: Create the component**

```jsx
// CommandCentre.jsx — sticky top bar with balance, regime, time gate, daily loss bar
import { useState, useEffect } from 'react';

const S = {
  bar: {
    position: 'sticky', top: 0, zIndex: 100,
    background: 'var(--color-bg-surface)',
    borderBottom: '1px solid rgba(255,255,255,0.06)',
    padding: '0 var(--spacing-24)',
    height: 64, display: 'flex', alignItems: 'center',
    gap: 'var(--spacing-24)',
  },
  logo: { height: 32, objectFit: 'contain' },
  div:  { color: 'rgba(255,255,255,0.15)', fontSize: 20, userSelect: 'none' },
  label: { fontFamily: 'var(--font-body)', fontSize: 11, color: 'var(--color-text-muted)', textTransform: 'uppercase', letterSpacing: '0.08em' },
  val:   { fontFamily: 'var(--font-mono)', fontSize: 15, fontWeight: 600 },
  badge: (color) => ({
    border: `1px solid ${color}`, borderRadius: 'var(--radius-buttons)',
    padding: '3px 10px', fontSize: 11, fontFamily: 'var(--font-body)', fontWeight: 600,
    color, background: 'transparent', letterSpacing: '0.06em',
  }),
  lossBarWrap: { flex: 1, maxWidth: 160 },
  lossBarTrack: { height: 4, background: 'rgba(255,255,255,0.08)', borderRadius: 2, overflow: 'hidden' },
  lossBarFill: (pct) => ({
    height: '100%', borderRadius: 2, background: 'var(--color-loss)',
    width: `${Math.min(pct, 100)}%`,
    transition: 'width 0.5s ease',
  }),
  utcClock: { fontFamily: 'var(--font-mono)', fontSize: 13, color: 'var(--color-text-muted)' },
  spacer: { flex: 1 },
};

export default function CommandCentre({ state = {}, positions = {} }) {
  const [utc, setUtc] = useState('');

  useEffect(() => {
    const tick = () => {
      const now = new Date();
      setUtc(now.toUTCString().slice(17, 25) + ' UTC');
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, []);

  const balance    = parseFloat(state.balance || 100);
  const startBal   = 100;
  const dailyPnl   = parseFloat(state.pnl || 0);
  const lossDrawPct = Math.max(0, ((startBal - balance) / startBal) * 100);
  const regime     = state.regime || 'TREND';
  const timeGateOn = state.time_gate_open !== false;

  const pnlColor = dailyPnl >= 0 ? 'var(--color-profit)' : 'var(--color-loss)';
  const pnlSign  = dailyPnl >= 0 ? '+' : '';

  return (
    <header style={S.bar}>
      <img src="/src/assets/ZiSi_Final_Logo.png" alt="ZiSi" style={S.logo} />
      <span style={S.div}>|</span>

      <div>
        <div style={S.label}>Balance</div>
        <div style={{ ...S.val, color: 'var(--color-text-primary)' }}>${balance.toFixed(2)}</div>
      </div>

      <div>
        <div style={S.label}>Daily P&amp;L</div>
        <div style={{ ...S.val, color: pnlColor }}>{pnlSign}${dailyPnl.toFixed(2)}</div>
      </div>

      <div>
        <div style={S.label}>Regime</div>
        <span style={S.badge(regime === 'TREND' ? 'var(--color-accent)' : 'var(--color-accent-muted)')}>
          {regime}
        </span>
      </div>

      <div>
        <div style={S.label}>Time Gate</div>
        <span style={S.badge(timeGateOn ? 'var(--color-profit)' : 'var(--color-loss)')}>
          {timeGateOn ? '● ACTIVE' : '● PAUSED'}
        </span>
      </div>

      <div style={S.spacer} />

      <div style={S.lossBarWrap}>
        <div style={{ ...S.label, marginBottom: 4 }}>Daily Loss {lossDrawPct.toFixed(1)}% / 15%</div>
        <div style={S.lossBarTrack}>
          <div style={S.lossBarFill(lossDrawPct / 15 * 100)} />
        </div>
      </div>

      <span style={S.utcClock}>{utc}</span>
    </header>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add dashboard/frontend/src/components/CommandCentre.jsx
git commit -m "feat(dashboard): CommandCentre — sticky top bar with balance, regime, time gate, loss bar"
```

---

## Task 12: AssetCards.jsx

**Files:**
- Create: `dashboard/frontend/src/components/AssetCards.jsx`

- [ ] **Step 1: Create the component**

```jsx
// AssetCards.jsx — 5 per-asset status cards (BTC-5m, BTC-15m, ETH, SOL, XRP)
const ASSETS = [
  { asset: 'BTC', tf: '5m',  color: 'var(--color-accent)' },
  { asset: 'BTC', tf: '15m', color: 'var(--color-accent-muted)' },
  { asset: 'ETH', tf: '5m',  color: 'var(--color-profit)' },
  { asset: 'SOL', tf: '5m',  color: 'var(--color-text-secondary)' },
  { asset: 'XRP', tf: '5m',  color: 'var(--color-xrp)' },
];

function getAssetStats(key, positions) {
  const active = (positions?.active || []).filter(p => {
    const t = (p.event_title || '').toUpperCase();
    return t.includes(`[${key.split('/')[0]}]`) && t.includes(`[${key.split('/')[1].toUpperCase()}]`);
  });
  const pnl = active.reduce((s, p) => s + parseFloat(p.unrealized_pnl || 0), 0);
  return { count: active.length, unrealizedPnl: pnl };
}

function AssetCard({ asset, tf, color, positions, candles }) {
  const key        = `${asset}/${tf}`;
  const stats      = getAssetStats(key, positions);
  const candleInfo = (candles || []).find(c => c.asset === asset && c.tf === tf);
  const secsLeft   = candleInfo?.secs ?? null;
  const timerColor = secsLeft === null ? 'var(--color-text-muted)'
    : secsLeft < 15 ? 'var(--color-loss)'
    : secsLeft < 60 ? 'var(--color-amber)'
    : 'var(--color-profit)';

  const fmtSecs = (s) => s === null ? '—' : `${Math.floor(s / 60)}m ${s % 60}s`;

  return (
    <div style={{
      background: 'var(--color-bg-elevated)',
      borderRadius: 'var(--radius-cards)',
      padding: 'var(--spacing-20)',
      border: '1px solid var(--color-midnight)',
      minWidth: 0, flex: 1,
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
        <span style={{ fontFamily: 'var(--font-display)', fontWeight: 900, fontSize: 18, color }}>
          {asset} <span style={{ fontSize: 13, fontWeight: 400, color: 'var(--color-text-muted)' }}>{tf}</span>
        </span>
      </div>

      <div style={{ fontSize: 12, color: 'var(--color-text-muted)', marginBottom: 6 }}>Open positions</div>
      <div style={{ fontFamily: 'var(--font-mono)', fontSize: 22, fontWeight: 600, color: 'var(--color-text-primary)' }}>
        {stats.count}
        {stats.count > 0 && (
          <span style={{ fontSize: 13, marginLeft: 8, color: stats.unrealizedPnl >= 0 ? 'var(--color-profit)' : 'var(--color-loss)' }}>
            {stats.unrealizedPnl >= 0 ? '+' : ''}${stats.unrealizedPnl.toFixed(2)} unr
          </span>
        )}
      </div>

      <div style={{ marginTop: 12, fontSize: 12, color: 'var(--color-text-muted)' }}>
        Next candle: <span style={{ fontFamily: 'var(--font-mono)', color: timerColor }}>{fmtSecs(secsLeft)}</span>
      </div>
    </div>
  );
}

export default function AssetCards({ positions, candles }) {
  return (
    <div style={{ display: 'flex', gap: 'var(--spacing-12)', flexWrap: 'wrap' }}>
      {ASSETS.map(a => (
        <AssetCard key={`${a.asset}/${a.tf}`} {...a} positions={positions} candles={candles} />
      ))}
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add dashboard/frontend/src/components/AssetCards.jsx
git commit -m "feat(dashboard): AssetCards — 5 per-asset live cards with candle countdown"
```

---

## Task 13: TradeFeed.jsx

**Files:**
- Create: `dashboard/frontend/src/components/TradeFeed.jsx`

- [ ] **Step 1: Create the component**

```jsx
// TradeFeed.jsx — scrolling trade log, last 50 trades, newest at top
function directionColor(dir) {
  return dir === 'UP' ? 'var(--color-profit)' : 'var(--color-loss)';
}

function ResultBadge({ result }) {
  const color = result === 'WIN' ? 'var(--color-profit)' : result === 'LOSS' ? 'var(--color-loss)' : 'var(--color-text-muted)';
  return (
    <span style={{ fontFamily: 'var(--font-body)', fontWeight: 700, fontSize: 11, color, letterSpacing: '0.05em' }}>
      {result || 'OPEN'}
    </span>
  );
}

function TradeRow({ trade, isOpen }) {
  const borderColor = trade.result === 'WIN' ? 'var(--color-profit)' : trade.result === 'LOSS' ? 'var(--color-loss)' : 'var(--color-text-muted)';
  return (
    <div style={{
      display: 'grid',
      gridTemplateColumns: '45px 45px 30px 55px 50px 50px 55px 55px 60px 50px',
      gap: 4, alignItems: 'center',
      padding: '6px 0',
      borderLeft: `3px solid ${borderColor}`,
      paddingLeft: 8,
      opacity: isOpen ? 0.75 : 1,
      fontStyle: isOpen ? 'italic' : 'normal',
      borderBottom: '1px solid rgba(255,255,255,0.04)',
      fontSize: 12,
    }}>
      <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--color-text-muted)' }}>{trade.time || '—'}</span>
      <span style={{ fontFamily: 'var(--font-heading)', fontWeight: 700 }}>{trade.asset || '—'}</span>
      <span style={{ color: 'var(--color-text-muted)' }}>{trade.timeframe || '—'}</span>
      <span style={{ color: directionColor(trade.direction), fontWeight: 600 }}>
        {trade.direction === 'UP' ? '↑ UP' : '↓ DOWN'}
      </span>
      <span style={{ fontFamily: 'var(--font-mono)' }}>{trade.entry_price ? `${(trade.entry_price * 100).toFixed(0)}¢` : '—'}</span>
      <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--color-text-muted)' }}>
        {trade.exit_price != null ? `${(trade.exit_price * 100).toFixed(0)}¢` : '—'}
      </span>
      <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--color-text-muted)' }}>{trade.score?.toFixed(2) || '—'}</span>
      <span style={{
        background: trade.type === 'DUAL' || trade.type === 'DUAL_MAIN' ? 'var(--color-accent-muted)' : 'transparent',
        borderRadius: 3, padding: '1px 4px', fontSize: 10,
      }}>{trade.type || 'SINGL'}</span>
      <span style={{
        fontFamily: 'var(--font-mono)', fontWeight: 600,
        color: (trade.pnl ?? 0) >= 0 ? 'var(--color-profit)' : 'var(--color-loss)',
      }}>
        {trade.pnl != null ? `${trade.pnl >= 0 ? '+' : ''}$${trade.pnl.toFixed(2)}` : '—'}
      </span>
      <ResultBadge result={trade.result} />
    </div>
  );
}

// Build trade rows from positions_state.json active + closed
function buildTrades(trades, positions) {
  const rows = [];

  const now = Date.now() / 1000;
  const fmt = (ts) => {
    const d = new Date(ts);
    return `${d.getUTCHours().toString().padStart(2,'0')}:${d.getUTCMinutes().toString().padStart(2,'0')}`;
  };

  // Closed trades from SSE feed
  for (const t of trades) {
    rows.push({ ...t, result: (t.pnl ?? 0) > 0 ? 'WIN' : 'LOSS' });
  }

  // Active positions from SSE stream
  for (const p of (positions?.active || [])) {
    const title = p.event_title || '';
    const assetMatch = title.match(/\[(BTC|ETH|SOL|XRP)\]/);
    const tfMatch    = title.match(/\[(5m|15m)\]/);
    const typeMatch  = title.match(/\[(SINGLE|DUAL_MAIN|DUAL_HEDGE|DUAL)\]/);
    rows.push({
      time:        fmt(p.open_time ? new Date(p.open_time).getTime() : now * 1000),
      asset:       assetMatch ? assetMatch[1] : '?',
      timeframe:   tfMatch ? tfMatch[1] : '?',
      direction:   p.direction === 'YES' ? 'UP' : 'DOWN',
      entry_price: parseFloat(p.entry_price || 0),
      exit_price:  null,
      score:       parseFloat(p.score || 0) || null,
      type:        typeMatch ? typeMatch[1].replace('_MAIN','') : 'SINGL',
      pnl:         parseFloat(p.unrealized_pnl || 0),
      result:      null,
    });
  }

  return rows.slice(0, 50);
}

export default function TradeFeed({ trades = [], positions = {} }) {
  const rows = buildTrades(trades, positions);

  const colHeaders = ['Time','Asset','TF','Dir','Entry¢','Exit¢','Score','Type','P&L','Result'];

  return (
    <div style={{
      background: 'var(--color-bg-surface)',
      borderRadius: 'var(--radius-cards)',
      border: '1px solid var(--color-midnight)',
      padding: 'var(--spacing-20)',
      overflow: 'hidden', display: 'flex', flexDirection: 'column',
    }}>
      <div style={{ fontFamily: 'var(--font-heading)', fontWeight: 500, fontSize: 16, marginBottom: 12 }}>
        Live Trade Feed
      </div>

      {/* Column headers */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: '45px 45px 30px 55px 50px 50px 55px 55px 60px 50px',
        gap: 4, paddingLeft: 11, marginBottom: 4,
        fontSize: 10, color: 'var(--color-text-muted)', textTransform: 'uppercase', letterSpacing: '0.07em',
      }}>
        {colHeaders.map(h => <span key={h}>{h}</span>)}
      </div>

      <div style={{ overflowY: 'auto', maxHeight: 340, flex: 1 }}>
        {rows.length === 0 ? (
          <div style={{ color: 'var(--color-text-muted)', fontSize: 13, textAlign: 'center', padding: 32 }}>
            Waiting for trades…
          </div>
        ) : rows.map((t, i) => (
          <TradeRow key={i} trade={t} isOpen={t.result === null} />
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add dashboard/frontend/src/components/TradeFeed.jsx
git commit -m "feat(dashboard): TradeFeed — live scrolling trade log with WIN/LOSS borders"
```

---

## Task 14: WinRateChart.jsx

**Files:**
- Create: `dashboard/frontend/src/components/WinRateChart.jsx`

- [ ] **Step 1: Verify recharts is installed**

```bash
cd dashboard/frontend && npm list recharts 2>/dev/null | grep recharts || npm install recharts
```

- [ ] **Step 2: Create WinRateChart.jsx**

```jsx
// WinRateChart.jsx — rolling 40-window WR per asset + inversion event markers
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ReferenceLine, ResponsiveContainer, Legend,
} from 'recharts';

const ASSET_COLORS = {
  'BTC/5m':  '#2b7fff',
  'BTC/15m': '#426188',
  'ETH/5m':  '#00d4a3',
  'SOL/5m':  '#f5f5f5',
  'XRP/5m':  '#ff9500',
};

const ASSETS = ['BTC/5m', 'BTC/15m', 'ETH/5m', 'SOL/5m', 'XRP/5m'];

function buildWrSeries(trades) {
  // Group trades by asset/tf and compute rolling 40-window WR
  const outcomes = {};
  for (const t of trades) {
    if (!t.asset || !t.timeframe || t.result === null) continue;
    const key = `${t.asset}/${t.timeframe}`;
    if (!outcomes[key]) outcomes[key] = [];
    outcomes[key].push(t.result === 'WIN' ? 1 : 0);
  }

  const maxLen = Math.max(...Object.values(outcomes).map(a => a.length), 0);
  if (maxLen === 0) return [];

  const points = [];
  for (let i = 0; i < maxLen; i++) {
    const pt = { index: i + 1 };
    for (const key of ASSETS) {
      const arr = outcomes[key] || [];
      if (i < arr.length) {
        const window = arr.slice(Math.max(0, i - 39), i + 1);
        pt[key] = window.length >= 5 ? parseFloat((window.reduce((s,v) => s+v,0)/window.length).toFixed(3)) : null;
      }
    }
    points.push(pt);
  }
  return points;
}

export default function WinRateChart({ trades = [] }) {
  const data = buildWrSeries(trades);

  return (
    <div style={{
      background: 'var(--color-bg-surface)',
      borderRadius: 'var(--radius-cards)',
      border: '1px solid var(--color-midnight)',
      padding: 'var(--spacing-20)',
    }}>
      <div style={{ fontFamily: 'var(--font-heading)', fontWeight: 500, fontSize: 16, marginBottom: 16 }}>
        Rolling Win Rate (40-window)
      </div>

      {data.length < 5 ? (
        <div style={{ color: 'var(--color-text-muted)', fontSize: 13, textAlign: 'center', padding: 48 }}>
          Building win rate data — need 5+ trades per asset
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={260}>
          <LineChart data={data} margin={{ top: 5, right: 16, left: 0, bottom: 5 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
            <XAxis dataKey="index" tick={{ fill: '#6b6b6b', fontSize: 11 }} />
            <YAxis
              domain={[0, 1]}
              tickFormatter={v => `${(v * 100).toFixed(0)}%`}
              tick={{ fill: '#6b6b6b', fontSize: 11 }}
            />
            <Tooltip
              formatter={(v, name) => [`${(v * 100).toFixed(1)}%`, name]}
              contentStyle={{ background: '#1a1a1a', border: '1px solid #333', borderRadius: 8 }}
              labelStyle={{ color: '#999' }}
            />
            <Legend wrapperStyle={{ fontSize: 12, color: '#999' }} />

            {/* Reference lines */}
            <ReferenceLine y={0.62} stroke="#2b7fff"  strokeDasharray="4 3" label={{ value: 'Edge', fill: '#2b7fff', fontSize: 10 }} />
            <ReferenceLine y={0.52} stroke="#00d4a3"  strokeDasharray="4 3" label={{ value: 'Recover', fill: '#00d4a3', fontSize: 10 }} />
            <ReferenceLine y={0.45} stroke="#ff4d4d"  strokeDasharray="4 3" label={{ value: 'Invert', fill: '#ff4d4d', fontSize: 10 }} />

            {ASSETS.map(key => (
              <Line
                key={key}
                type="monotone"
                dataKey={key}
                stroke={ASSET_COLORS[key]}
                strokeWidth={2}
                dot={false}
                connectNulls
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}
```

- [ ] **Step 3: Commit**

```bash
git add dashboard/frontend/src/components/WinRateChart.jsx
git commit -m "feat(dashboard): WinRateChart — Recharts rolling 40-window WR with inversion reference lines"
```

---

## Task 15: PositionMonitor.jsx

**Files:**
- Create: `dashboard/frontend/src/components/PositionMonitor.jsx`

- [ ] **Step 1: Create the component**

```jsx
// PositionMonitor.jsx — live open positions table with countdown timers
import { useState, useEffect } from 'react';

function CountdownTimer({ expiry_ts }) {
  const [secs, setSecs] = useState(0);

  useEffect(() => {
    const tick = () => {
      const s = Math.max(0, expiry_ts - Math.floor(Date.now() / 1000));
      setSecs(s);
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [expiry_ts]);

  const color = secs < 15 ? 'var(--color-loss)' : secs < 60 ? 'var(--color-amber)' : 'var(--color-profit)';
  const pulse = secs < 15 ? { animation: 'pulse 0.8s infinite' } : {};
  const m = Math.floor(secs / 60), s = secs % 60;

  return (
    <span style={{ fontFamily: 'var(--font-mono)', color, fontSize: 12, ...pulse }}>
      {m}m {s.toString().padStart(2,'0')}s ⏱
    </span>
  );
}

function parsePositionMeta(pos) {
  const title = pos.event_title || '';
  const assetMatch = title.match(/\[(BTC|ETH|SOL|XRP)\]/);
  const tfMatch    = title.match(/\[(5m|15m)\]/);
  const typeMatch  = title.match(/\[(SINGLE|DUAL_MAIN|DUAL_HEDGE|DUAL)\]/);
  return {
    asset:     assetMatch ? assetMatch[1] : '?',
    timeframe: tfMatch    ? tfMatch[1]    : '?',
    type:      typeMatch  ? typeMatch[1]  : 'SINGL',
  };
}

export default function PositionMonitor({ positions = {}, candles = [] }) {
  const active = positions?.active || [];

  const colHeaders = ['Asset','TF','Dir','Type','Entry¢','Current¢','Unr P&L','Closes In'];

  return (
    <div style={{
      background: 'var(--color-bg-surface)',
      borderRadius: 'var(--radius-cards)',
      border: '1px solid var(--color-midnight)',
      padding: 'var(--spacing-20)',
    }}>
      <div style={{ fontFamily: 'var(--font-heading)', fontWeight: 500, fontSize: 16, marginBottom: 12 }}>
        Open Positions ({active.length})
      </div>

      <div style={{
        display: 'grid',
        gridTemplateColumns: '50px 35px 55px 70px 60px 70px 75px 1fr',
        gap: 4, marginBottom: 6,
        fontSize: 10, color: 'var(--color-text-muted)', textTransform: 'uppercase', letterSpacing: '0.07em',
      }}>
        {colHeaders.map(h => <span key={h}>{h}</span>)}
      </div>

      <div style={{ overflowY: 'auto', maxHeight: 280 }}>
        {active.length === 0 ? (
          <div style={{ color: 'var(--color-text-muted)', fontSize: 13, textAlign: 'center', padding: 32 }}>
            No open positions
          </div>
        ) : active.map((pos, i) => {
          const meta   = parsePositionMeta(pos);
          const dir    = pos.direction === 'YES' ? 'UP' : 'DOWN';
          const entry  = parseFloat(pos.entry_price || 0);
          const cur    = parseFloat(pos.current_price || entry);
          const unrPnl = parseFloat(pos.unrealized_pnl || 0);
          const isDual = meta.type.startsWith('DUAL');
          const expiry = parseInt(pos.expiry_ts || '0');

          return (
            <div key={pos.order_id || i} style={{
              display: 'grid',
              gridTemplateColumns: '50px 35px 55px 70px 60px 70px 75px 1fr',
              gap: 4, alignItems: 'center',
              padding: '6px 0',
              borderLeft: `3px solid ${isDual ? 'var(--color-accent-muted)' : 'var(--color-text-muted)'}`,
              paddingLeft: 8,
              borderBottom: '1px solid rgba(255,255,255,0.04)',
              fontSize: 12,
            }}>
              <span style={{ fontFamily: 'var(--font-heading)', fontWeight: 700, color: 'var(--color-text-primary)' }}>{meta.asset}</span>
              <span style={{ color: 'var(--color-text-muted)' }}>{meta.timeframe}</span>
              <span style={{ color: dir === 'UP' ? 'var(--color-profit)' : 'var(--color-loss)', fontWeight: 600 }}>
                {dir === 'UP' ? '↑ UP' : '↓ DOWN'}
              </span>
              <span style={{
                background: isDual ? 'var(--color-accent-muted)' : 'transparent',
                borderRadius: 3, padding: '1px 5px', fontSize: 10, textAlign: 'center',
              }}>{meta.type.replace('_MAIN','').replace('_HEDGE','*')}</span>
              <span style={{ fontFamily: 'var(--font-mono)' }}>{(entry * 100).toFixed(0)}¢</span>
              <span style={{ fontFamily: 'var(--font-mono)', color: cur > entry ? 'var(--color-profit)' : 'var(--color-loss)' }}>
                {(cur * 100).toFixed(0)}¢
              </span>
              <span style={{
                fontFamily: 'var(--font-mono)', fontWeight: 600,
                color: unrPnl >= 0 ? 'var(--color-profit)' : 'var(--color-loss)',
              }}>
                {unrPnl >= 0 ? '+' : ''}${unrPnl.toFixed(2)}
              </span>
              {expiry > 0 ? <CountdownTimer expiry_ts={expiry} /> : <span style={{ color: 'var(--color-text-muted)' }}>—</span>}
            </div>
          );
        })}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Add pulse keyframe to App.css**

Append to `App.css`:

```css
@keyframes pulse {
  0%, 100% { opacity: 1; }
  50%       { opacity: 0.3; }
}
```

- [ ] **Step 3: Commit**

```bash
git add dashboard/frontend/src/components/PositionMonitor.jsx dashboard/frontend/src/App.css
git commit -m "feat(dashboard): PositionMonitor — open positions with live countdown timers"
```

---

## Task 16: SystemHealth.jsx

**Files:**
- Create: `dashboard/frontend/src/components/SystemHealth.jsx`

- [ ] **Step 1: Create the component**

```jsx
// SystemHealth.jsx — infrastructure status + circuit breaker + candle timers
const ASSETS_TF = ['BTC/5m', 'BTC/15m', 'ETH/5m', 'SOL/5m', 'XRP/5m'];

function StatusIcon({ ok, warn, off }) {
  if (off)  return <span style={{ color: 'var(--color-loss)',  fontSize: 14 }}>🔴</span>;
  if (warn) return <span style={{ color: 'var(--color-amber)', fontSize: 14 }}>⚠️</span>;
  return       <span style={{ color: 'var(--color-profit)',  fontSize: 14 }}>✅</span>;
}

function Row({ label, value, ok, warn, off }) {
  return (
    <div style={{
      display: 'flex', justifyContent: 'space-between', alignItems: 'center',
      padding: '5px 0', borderBottom: '1px solid rgba(255,255,255,0.04)',
      fontSize: 12,
    }}>
      <span style={{ color: 'var(--color-text-muted)' }}>{label}</span>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
        <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--color-text-primary)' }}>{value}</span>
        <StatusIcon ok={ok} warn={warn} off={off} />
      </div>
    </div>
  );
}

function fmtSecs(s) {
  if (s == null) return '—';
  return `${Math.floor(s / 60)}m ${(s % 60).toString().padStart(2,'0')}s`;
}

export default function SystemHealth({ state = {}, positions = {}, candles = [] }) {
  const active   = positions?.active || [];
  const summary  = positions?.summary || {};

  const minutesAgo = state.last_update_minutes_ago ?? state.minutesAgo ?? null;
  const isAlive    = minutesAgo !== null && minutesAgo < 10;
  const isStale    = minutesAgo !== null && minutesAgo >= 10 && minutesAgo < 30;

  const pnl     = parseFloat(state.pnl || 0);
  const balance = parseFloat(state.balance || 100);
  const drawPct = ((100 - balance) / 100 * 100).toFixed(1);

  return (
    <div style={{
      background: 'var(--color-bg-surface)',
      borderRadius: 'var(--radius-cards)',
      border: '1px solid var(--color-midnight)',
      padding: 'var(--spacing-20)',
    }}>
      <div style={{ fontFamily: 'var(--font-heading)', fontWeight: 500, fontSize: 16, marginBottom: 12 }}>
        System Health
      </div>

      {/* Infrastructure */}
      <div style={{ marginBottom: 12 }}>
        <div style={{ fontSize: 10, color: 'var(--color-accent)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 4 }}>
          Infrastructure
        </div>
        <Row label="Bot heartbeat" value={isAlive ? `${minutesAgo}m ago` : isStale ? `${minutesAgo}m ago (STALE)` : 'offline'} ok={isAlive} warn={isStale} off={!isAlive && !isStale} />
        <Row label="Open positions" value={active.length} ok={active.length <= 4} warn={active.length > 4} />
        <Row label="Daily drawdown" value={`${drawPct}%`} ok={parseFloat(drawPct) < 10} warn={parseFloat(drawPct) >= 10 && parseFloat(drawPct) < 15} off={parseFloat(drawPct) >= 15} />
        <Row label="Total P&L" value={`${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)}`} ok={pnl >= 0} warn={pnl < 0 && pnl > -5} off={pnl <= -5} />
      </div>

      {/* Candle timers */}
      <div>
        <div style={{ fontSize: 10, color: 'var(--color-accent)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 4 }}>
          Next Candle Boundaries
        </div>
        {ASSETS_TF.map(key => {
          const [asset, tf] = key.split('/');
          const candle = candles.find(c => c.asset === asset && c.tf === tf);
          const secs = candle?.secs ?? null;
          return (
            <Row
              key={key}
              label={key}
              value={fmtSecs(secs)}
              ok={secs !== null && secs > 60}
              warn={secs !== null && secs <= 60 && secs > 15}
              off={secs !== null && secs <= 15}
            />
          );
        })}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add dashboard/frontend/src/components/SystemHealth.jsx
git commit -m "feat(dashboard): SystemHealth — infra status, drawdown, candle boundary timers"
```

---

## Task 17: Build and Verify Dashboard

**Files:**
- Verify: full build passes, all components render

- [ ] **Step 1: Install dependencies and build**

```bash
cd dashboard/frontend && npm install && npm run build 2>&1 | tail -20
```

Expected: `✓ built in` — no errors. Warnings about unused imports are OK.

- [ ] **Step 2: Start dashboard dev server**

```bash
cd dashboard/frontend && npm run dev -- --port 5173 &
cd dashboard && node backend/server.js &
```

- [ ] **Step 3: Verify all 6 panels render without crash**

Open http://localhost:5173 and confirm:
- CommandCentre renders with balance and UTC clock
- 5 AssetCards visible
- TradeFeed shows "Waiting for trades…"
- WinRateChart shows "Building win rate data…"
- PositionMonitor shows "No open positions"
- SystemHealth shows candle timers

- [ ] **Step 4: Kill dev servers**

```bash
kill %1 %2 2>/dev/null; true
```

- [ ] **Step 5: Final commit**

```bash
git add -A
git commit -m "feat(session10): complete dashboard overhaul — 6 Air-design panels, full pBot intelligence"
```

---

## Task 18: Backtest Validation

**Files:**
- Run: `backtester.py`

- [ ] **Step 1: Run backtest on default parameters**

```bash
python backtester.py 2>&1 | tail -30
```

Expected: output showing win rate by score tier. If the backtester requires specific arguments, run:

```bash
python backtester.py --help
```

And use the appropriate flags to test the new price gate thresholds.

- [ ] **Step 2: Verify price gate thresholds look correct**

The score-to-WR mapping used in `updown_engine.py`:
- Score ≥ 0.85 → max entry 60¢ (est WR 70%)
- Score 0.75–0.85 → max entry 55¢ (est WR 65%)
- Score 0.62–0.75 → max entry 47¢ (est WR 57%)

If backtest shows actual WR significantly below these estimates, adjust `SCORE_TO_WR` in `updown_engine.py`.

- [ ] **Step 3: Commit backtest results if generated**

```bash
git add backtest_result.json 2>/dev/null; true
git add -p  # review and stage any threshold adjustments made
git commit -m "test(backtest): validate price gate WR thresholds post-session-10 refactor" --allow-empty
```

---

## Self-Review

**Spec coverage check:**
1. ✅ Surgical deletion (Task 1) — all Kalshi, news, LLM, data_sources files removed
2. ✅ `updown_trader.py` → `updown_engine.py` (Task 5) — `UpDownEngine` class with dual-entry, circuit breaker, inversion
3. ✅ `regime_filter.py` (Task 3) — weekday/weekend + time gate
4. ✅ `reconciliation.py` (Task 4) — 30s asyncio loop
5. ✅ `main.py` overhaul (Task 8) — `asyncio.gather()` of 6 tasks, staggered offsets
6. ✅ `risk_manager.py` (Task 6) — entry price gate + exposure caps + daily loss halt
7. ✅ `metrics_engine.py` (Task 7) — 40-window inversion monitor
8. ✅ `config.py` (Task 2) — dead params removed, all new pBot params added
9. ✅ Dashboard backend (Task 9) — SSE stream with position_update, balance_update, candle_boundary
10. ✅ Dashboard frontend (Tasks 10–16) — 6 Air panels: CommandCentre, AssetCards, TradeFeed, WinRateChart, PositionMonitor, SystemHealth
11. ✅ Backtest validation (Task 18)
12. ⬜ Paper trade run — 48h paper trading (runtime task, not a code task)

**Placeholder scan:** None found — all steps contain actual code.

**Type consistency:**
- `UpDownEngine.generate_signal()` → returns `{asset, timeframe, direction, score, regime, inverted, rsi, momentum, market}` — used correctly in `asset_loop()` in main.py
- `state_manager.get_open_positions()` → returns `list` — used in `check_exposure_caps()` and `reconciliation.py`
- `price_gate_passes()` in `updown_engine.py` and `entry_price_gate()` in `risk_manager.py` both implement same logic — main.py calls `entry_price_gate` (from risk_manager) as the primary gate
- SSE event type `position_update` broadcast by health.js → consumed by App.jsx `setPositions` — matches

---

**Plan complete and saved to `docs/superpowers/plans/2026-05-20-zisi-session10-laser-focus.md`.**

Two execution options:

**1. Subagent-Driven (recommended)** — fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** — execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
