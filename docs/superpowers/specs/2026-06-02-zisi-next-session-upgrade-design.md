# ZiSi Next-Session Upgrade — Design Spec
**Date:** 2026-06-02  
**Status:** Approved  
**Triple Mandate:** ≥15 trades/day · 65–70% WR · steady growing P&L

---

## Context & Motivation

EU session analysis (17 trades, 58.8% WR, +$11.17) revealed two structural weaknesses:

1. **Macro-reversal blindness** — during a 45-minute BTC/XRP upswing (12:35–13:20), the FV model fired 5 consecutive losing DN signals, losing -$22.50. Every 5m loss in the session occurred within this cluster. Outside it: 100% win rate on 5m.
2. **Avg loss > avg win** ($5.27 vs $4.80) — marginal signals at expensive prices occasionally produce full wipeouts (~97% loss) while winning trades underperform on average. Tiered edge thresholds fix this.

SIG trades were absent because RSI in EU session rarely reaches the hard 60/40 thresholds. REV trades have the right mechanics but fire too rarely to measure. Both addressed here.

---

## Scope

### Engine — 8 changes
1. Macro trend gate (8-candle majority)
2. Correlated asset loss brake (soft filter)
3. FV edge thresholds — tiered
4. LAT-ARB 15m early-exit monitor
5. SIG RSI loosening in MEAN_REVERTING regime
6. Multi-asset corroboration (5m only)
7. Candle timing gate (5m only)
8. Volume surge block

### Dashboard — 5 changes
9. Regime pill
10. Session × Regime analytics table
11. Macro trend arrow in header
12. Loss cluster alert strip
13. P&L velocity gauge

### Out of scope
- LP farming module (Week 2+)
- REV parameter tuning (needs more data)

---

## Engine Design

### 1. Macro Trend Gate (8-candle)

**File:** `core/engine/updown_engine.py` — inside `generate_signal()`, after trend gate / choppy detection block, before FV block.

**Logic:**
```
macro_up_count = count of last 8 CLOSED candles (klines[-9:-1]) where close > open
macro_dn_count = 8 - macro_up_count

if macro_up_count >= 6 and direction == "DOWN":
    log [MACRO-GATE] blocked DN — 6+/8 candles bullish
    return None

if macro_dn_count >= 6 and direction == "UP":
    log [MACRO-GATE] blocked UP — 6+/8 candles bearish
    return None
```

**Why 6/8:** Requires clear majority (75%) before blocking. At 5/8 (62.5%) it's too aggressive and would suppress real reversal entries.

**Applies to:** Both FV and SIG entry paths. FAIR_VAL entries are NOT exempt — the session analysis showed FV was exactly the path being hurt by macro reversals.

**Guard:** Only fire this gate when `len(klines) >= 10`. If insufficient history, pass through.

---

### 2. Correlated Asset Loss Brake (Soft Filter)

**File:** `core/engine/updown_engine.py` — helper method `_recent_full_loss_count()`, called inside `generate_signal()` before the final return, after score is calculated.

**Logic:**
```python
def _recent_full_loss_count(self, lookback_minutes: int = 20) -> int:
    # Reads positions_state.json closed[], counts trades where:
    #   exit_price <= 0.10 AND
    #   exit_time >= now - lookback_minutes * 60
    # Returns count
```

In `generate_signal()`:
```
full_loss_count = self._recent_full_loss_count()
if full_loss_count >= 3:
    required_edge = 0.20
    if entry_source == "FAIR_VAL" and _fv["edge"] < required_edge:
        log [LOSS-BRAKE] 3+ recent full losses — requires edge 0.20, got {edge}
        return None
    if entry_source == "SIG" and score < 0.82:
        log [LOSS-BRAKE] 3+ recent full losses — requires score 0.82, got {score}
        return None
```

**Brake is cross-asset:** Full loss on BTC counts toward ETH's brake too. The cluster problem is macro-directional — one asset going wrong means all assets are in the same environment.

**Auto-lifts:** The `lookback_minutes=20` window is rolling. Once the cluster is 20 minutes old, the brake auto-releases without any manual reset.

---

### 3. FV Edge Thresholds — Tiered (with Cross-TF Gate)

**File:** `core/engine/updown_engine.py` — add explicit edge gate after the FV block sets `_fv`.

Current `edge_margin` in `fair_value.py` is `0.05`. We do NOT change `fair_value.py` (shared with backtester). Instead, add a post-FV edge gate in `generate_signal()` that also checks a cross-timeframe conflict:

```python
if _fv["direction"] is not None:
    entry_price = up_price if _fv["direction"] == "UP" else dn_price

    # Cross-TF conflict: check if last closed 15m candle for this same
    # asset points OPPOSITE to our 5m FV direction (medium penalty).
    # Only evaluated on 5m — 15m engine runs independently.
    _cross_tf_conflict = False
    if self.timeframe == "5m":
        try:
            klines_15m = await _fetch_klines_async(session, self.asset, "15m", 5)
            if len(klines_15m) >= 2:
                last_15m_bull = float(klines_15m[-2][4]) > float(klines_15m[-2][1])
                signal_bull   = _fv["direction"] == "UP"
                _cross_tf_conflict = (last_15m_bull != signal_bull)
        except Exception:
            pass

    # Tiered minimum edge:
    # - Expensive entry (>50c): needs 0.18 edge
    # - Cross-TF conflict (15m opposes 5m FV): needs 0.18 edge
    # - Both conditions: needs 0.20 edge
    # - Default: needs 0.10 edge (raised from implicit 0.05)
    _min_edge = 0.10
    _expensive = entry_price > 0.50
    if _expensive and _cross_tf_conflict:
        _min_edge = 0.20
    elif _expensive or _cross_tf_conflict:
        _min_edge = 0.18

    if _fv["edge"] < _min_edge:
        log.info("[FV-EDGE-GATE] %s/%s: edge %.3f < required %.3f (price=%.2f) — skip",
                 self.asset, self.timeframe, _fv["edge"], _min_edge, entry_price)
        _fv = {"direction": None, "edge": 0.0, "archetype": None}
```

**15m klines are a cache hit:** `_fetch_klines_async` has TTL=5s so the 15m klines are already cached from the concurrent 15m engine cycle. No added latency.

**Impact:** Filters marginal FV signals at expensive prices and when the higher timeframe disagrees. The cross-TF check captures idea #4 from the 5m alpha analysis (medium penalty = raise edge bar, not hard block).

---

### 4. LAT-ARB 15m Early-Exit Monitor

**File:** `core/engine/cycle_manager.py` — inside `scan_and_trade()` coroutine, after the initial position is opened.

**Current behavior:** LAT-ARB opens a position and returns. The position sits until it hits TARGET (99¢) or expires.

**New behavior:** After `_execute_order_flow` confirms entry, spawn a background monitor task:

```python
async def _monitor_lat_position(asset, timeframe, direction, token_id, open_price, boundary_ts):
    """Check quote every 60s. If quote > 0.75 against direction, attempt early close."""
    while True:
        await asyncio.sleep(60)
        now = time.time()
        if now >= boundary_ts:
            break  # market has expired
        
        # Fetch live quote for our token
        current_quote = await _fetch_live_quote(token_id)
        if current_quote is None:
            continue
        
        # "Against us" means: if we hold NO (DN), quote going toward 0 is good.
        # If current NO quote < 0.25 (YES quote > 0.75), we're badly wrong.
        if direction == "DOWN" and current_quote < 0.25:
            log [LAT-EXIT] {asset}/{timeframe} DN position at {open_price:.2f} — quote now {current_quote:.2f}, attempting early close
            await _attempt_early_close(asset, token_id, current_quote)
            break
        elif direction == "UP" and current_quote < 0.25:
            log [LAT-EXIT] {asset}/{timeframe} UP position at {open_price:.2f} — quote now {current_quote:.2f}, attempting early close
            await _attempt_early_close(asset, token_id, current_quote)
            break
```

**`_attempt_early_close`:** Places a market sell order at best available bid. Accepts partial recovery over full wipeout. If the order fails (no liquidity), logs and lets the position expire naturally.

**Applies to 15m LAT only.** 5m LAT expires too quickly (5 min) for monitoring to be meaningful. Gate: `if timeframe == "15m"`.

---

### 5. SIG RSI Loosening — MEAN_REVERTING Regime

**File:** `core/engine/signal_core.py` — in `REGIME_RSI_PARAMS`.

**Current MEAN_REVERTING entry:**
```python
"MEAN_REVERTING": DEFAULT_SIGNAL_PARAMS,
# which is: rsi_up=60.0, rsi_up_soft=54.0, rsi_dn=40.0, rsi_dn_soft=46.0
```

**New MEAN_REVERTING entry:**
```python
"MEAN_REVERTING": {
    "rsi_up":      55.0,   # lowered from 60.0 — mean-rev reversal starts earlier
    "rsi_up_soft": 50.0,   # lowered from 54.0
    "mom_up":      0.015,  # slightly loosened
    "mom_up_soft": 0.008,
    "ofi_confirm_up": 0.40,
    "rsi_dn":      45.0,   # raised from 40.0 — symmetric
    "rsi_dn_soft": 50.0,   # raised from 46.0
    "mom_dn":      -0.015,
    "mom_dn_soft": -0.008,
    "ofi_confirm_dn": -0.40,
    "reversal_lo": 20.0,
    "reversal_hi": 80.0,
    "reversal_score": 0.70,
    "ofi_block_neutral": 0.30,
    "ofi_block_5m": 0.25,
    "ofi_block_15m": 0.18,
},
```

**Rationale:** In a mean-reverting regime, price oscillates around a midpoint. RSI extremes at 60/40 rarely trigger because price doesn't sustain trends long enough to push RSI there. Lowering to 55/45 catches the tops and bottoms of the oscillation while it's still happening. The SIG trend confirmation gate (last 2 candles must agree) remains in place to prevent false entries.

---

### 6. Multi-Asset Corroboration (5m Only)

**File:** `core/engine/updown_engine.py` — new method `_corroborate_fv_direction()`, called after FV block, before the edge gate, but only when `self.timeframe == "5m"`.

**Logic:** Inline async check after FV fires, before the edge gate. Uses `_fetch_klines_async` which has a 5s TTL — all peer asset klines are already cached from concurrent engine cycles (no added latency):

```python
if self.timeframe == "5m" and entry_source == "FAIR_VAL" and _fv["direction"] is not None:
    PEER_ASSETS = {
        "BTC": ["ETH", "SOL"], "ETH": ["BTC", "SOL"],
        "SOL": ["BTC", "ETH"], "XRP": ["BTC", "ETH"],
        "DOGE": ["BTC"], "HYPE": ["BTC"], "BNB": ["BTC"],
    }
    peers = PEER_ASSETS.get(self.asset, [])
    corroborated = False
    for peer in peers:
        try:
            pk = await _fetch_klines_async(session, peer, "5m", 5)
            if len(pk) >= 2:
                peer_bull = float(pk[-2][4]) > float(pk[-2][1])
                signal_bull = _fv["direction"] == "UP"
                if peer_bull == signal_bull:
                    corroborated = True
                    break
        except Exception:
            pass
    if not corroborated:
        log.info("[CORROBORATE] %s/5m: no peer asset agrees with FV %s — skip",
                 self.asset, _fv["direction"])
        _fv = {"direction": None, "edge": 0.0, "archetype": None}
```

---

### 7. Candle Timing Gate (5m Only)

**File:** `core/engine/updown_engine.py` — inside `generate_signal()`, after the FV block calculates `_elapsed_min`, before score calculation.

```python
if self.timeframe == "5m" and FAIR_VALUE_MODE and _fv["direction"] is not None:
    if _elapsed_min > 4.0:  # final 60s of 5m candle
        log [TIMING-GATE] {asset}/5m: {_elapsed_min:.1f}min elapsed — too late to enter (>4min)
        return None
```

**Why:** FV signal is freshest at candle open. Entering in the last 60 seconds means: (a) most of the move has already happened, (b) there are only 60 seconds left for the market to resolve correctly. The risk/reward collapses at the tail end of the candle.

**Does not apply to 15m** — 15m candles have more runway and the FV edge compounds across the full window.

---

### 8. Volume Surge Block

**File:** `core/engine/updown_engine.py` — inside `generate_signal()`, after the volume gate check, before FV block.

```python
# Volume surge: 4× spike vs 5-candle rolling avg signals macro move starting
if len(volumes) >= 7:
    _roll_avg = sum(volumes[-7:-2]) / 5
    _cur_vol = volumes[-2]
    if _roll_avg > 0 and _cur_vol > 4.0 * _roll_avg:
        log [VOL-SURGE] {asset}/{timeframe}: volume spike {_cur_vol:.0f} > 4x avg {_roll_avg:.0f} — skip 2 candles
        self._choppy_candles = max(self._choppy_candles, 2)  # reuse existing cooldown mechanism
        return None
```

**Reuses `_choppy_candles`** — already in `UpDownEngine.__init__`. Volume surge sets a 2-candle pause through the same cooldown path as choppy detection. No new state variable needed.

---

## Dashboard Design

### 9. Regime Pill

**Location:** `TradeFeed.jsx` header — immediately right of `<MarketSessionPill />`.

**Backend:** New file `presentation/dashboard/backend/routes/regime.js` following the existing Express Router pattern (mirrors `routes/performance.js` structure):
```javascript
import express from 'express';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const router = express.Router();
const BOT_ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), '../../../..');

router.get('/', (req, res) => {
    try {
        const data = JSON.parse(fs.readFileSync(path.join(BOT_ROOT, 'regime_status.json'), 'utf8'));
        res.json({
            regime:         data.regime           || 'UNKNOWN',
            label:          data.label            || data.regime || 'UNKNOWN',
            confidence:     data.regime_confidence || 0,
            atr_percentile: data.atr_percentile    || 50,
            bbw_percentile: data.bbw_percentile    || 50,
        });
    } catch { res.json({ regime: 'UNKNOWN', label: 'UNKNOWN', confidence: 0 }); }
});

export default router;
```

Mounted in `server.js`: `app.use('/api/regime', regimeRouter);`

**Frontend component:**
```jsx
const REGIME_COLORS = {
    TRENDING:         '#2b7fff',  // blue
    MEAN_REVERTING:   '#00d4a3',  // teal
    COMPRESSION:      '#f59e0b',  // amber
    VOLATILE_CHAOS:   '#ef4444',  // red
    UNKNOWN:          '#6b7280',  // gray
};

function RegimePill() {
    const [regime, setRegime] = useState({ label: 'UNKNOWN', confidence: 0 });
    useEffect(() => {
        const fetch = () => apiGet('/api/regime').then(setRegime).catch(() => {});
        fetch();
        const id = setInterval(fetch, 15_000);
        return () => clearInterval(id);
    }, []);
    const color = REGIME_COLORS[regime.regime] || REGIME_COLORS.UNKNOWN;
    return (
        <div style={{ display: 'flex', alignItems: 'center', gap: 5, ... }}>
            <span style={{ width: 8, height: 8, borderRadius: '50%', background: color, ... }} />
            <span>{regime.label}</span>
            {regime.confidence > 0 && <span style={{ opacity: 0.6 }}>{Math.round(regime.confidence * 100)}%</span>}
        </div>
    );
}
```

**Polls every 15s.** `regime_status.json` is already written by the bot's RegimeDetector on each BTC engine cycle.

---

### 10. Session × Regime Analytics Table

**Prerequisite:** Add `regime` field to each trade record at entry time.

**Engine change (small):** In `app/main.py` → `_place_trade()`, read current `regime_status.json` and add `"regime"` to the position dict before writing to `positions_state.json`:
```python
try:
    import json as _j
    _rs_path = Path("regime_status.json")
    _regime_now = _j.loads(_rs_path.read_text())["regime"] if _rs_path.exists() else "UNKNOWN"
except Exception:
    _regime_now = "UNKNOWN"
position["regime"] = _regime_now
```

**Frontend — new Analytics panel:** Reads `closed[]` from `/api/positions`. Groups by `session × regime`:

```
Session | TRENDING      | MEAN_REVERTING | COMPRESSION | VOLATILE_CHAOS
--------+---------------+----------------+-------------+---------------
Asian   | 3W/1L  75%    | —              | —           | —
EU      | 8W/3L  72.7%  | 2W/2L  50%    | —           | 1W/2L  33%
US      | —             | 5W/2L  71.4%  | —           | —
```

Each cell shows: `WR%` in bold + net P&L in small text below. Empty cells show `—`. Cells with <3 trades show in gray (insufficient sample).

**Session derivation:** From `entry_time` UTC using the same `getMarketSession()` logic as the session pill.

---

### 11. Macro Trend Arrow

**Location:** TradeFeed header bar, between RegimePill and right edge.

**Backend:** New file `presentation/dashboard/backend/routes/macroTrend.js`:
```javascript
import express from 'express';
const router = express.Router();

router.get('/', async (req, res) => {
    try {
        const resp = await fetch('https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=5m&limit=10');
        const klines = await resp.json();
        const last8 = klines.slice(0, 8);  // last 8 closed candles
        const upCount = last8.filter(k => parseFloat(k[4]) > parseFloat(k[1])).length;
        const direction = upCount >= 6 ? 'UP' : upCount <= 2 ? 'DOWN' : 'NEUTRAL';
        res.json({ direction, up_count: upCount, total: 8 });
    } catch { res.json({ direction: 'NEUTRAL', up_count: 4, total: 8 }); }
});

export default router;
```

Mounted in `server.js`: `app.use('/api/macro-trend', macroTrendRouter);`

**Frontend:**
```jsx
const ARROW = { UP: '↑', DOWN: '↓', NEUTRAL: '→' };
const ARROW_COLOR = { UP: '#00d4a3', DOWN: '#ef4444', NEUTRAL: '#6b7280' };

function MacroTrendArrow() {
    const [trend, setTrend] = useState({ direction: 'NEUTRAL', up_count: 4 });
    useEffect(() => {
        const fetch = () => apiGet('/api/macro-trend').then(setTrend).catch(() => {});
        fetch();
        const id = setInterval(fetch, 30_000);
        return () => clearInterval(id);
    }, []);
    return (
        <span style={{ fontSize: 18, color: ARROW_COLOR[trend.direction], fontWeight: 700 }}
              title={`BTC 8-candle: ${trend.up_count}/8 UP`}>
            {ARROW[trend.direction]}
        </span>
    );
}
```

This arrow mirrors exactly what the macro trend gate (Engine Change 1) is evaluating. When the arrow is ↓ and the bot is firing DN signals, they're aligned. When the arrow flips ↑ mid-cluster, you see the danger before the losses hit.

---

### 12. Loss Cluster Alert Strip

**Location:** Below the summary stats bar in Trade History tab, only visible when condition is active.

**Frontend — pure computation from existing `/api/positions` data:**
```javascript
const now = Date.now();
const recentFullLosses = closed.filter(t =>
    (now - t.closed_at * 1000) < 20 * 60 * 1000  // last 20 min
    && (t.exit_price || 1.0) <= 0.10               // settled near zero
).length;

if (recentFullLosses >= 3) {
    // Show red alert strip
}
```

**Visual:**
```
⚠  3 FULL LOSSES IN LAST 20 MIN — MACRO REVERSAL RISK — HIGH-CONFIDENCE ENTRIES ONLY
```

Red background (`#7f1d1d`), white text, pulsing opacity animation. Auto-dismisses when the condition clears (next data refresh).

---

### 13. P&L Velocity Gauge

**Location:** Summary stats bar in Trade History tab, alongside Total P&L.

**Computation:**
```javascript
// Find oldest trade in session (first entry_time after last clean slate)
// P&L velocity = total_pnl / hours_elapsed
const oldest = closed[closed.length - 1];
const hoursElapsed = oldest
    ? (Date.now() - oldest.entry_time * 1000) / 3_600_000
    : 1;
const velocity = totalPnl / Math.max(0.1, hoursElapsed);
const velocityStr = (velocity >= 0 ? '+' : '') + velocity.toFixed(2) + '/hr';
```

**Visual:** Small secondary stat below or beside Total P&L:
```
Total P&L    P&L Rate
+$11.17      +$2.80/hr
```

Color: green if positive, red if negative. Gives instant trajectory feedback toward the day target.

---

## Data Flow Summary

```
Binance klines → UpDownEngine.generate_signal()
    │
    ├─ Macro trend gate (8-candle)      → blocks if 6+/8 same direction opposes signal
    ├─ Volume surge block               → 2-candle pause on 4× volume spike
    ├─ FV block → _fv dict             
    │   └─ FV edge gate (tiered)        → drops weak/expensive signals
    ├─ Corroboration (5m only)          → requires 1 peer asset agreement
    ├─ Candle timing gate (5m only)     → blocks last 60s of candle
    ├─ Loss brake (soft filter)         → raises bar after 3 full losses in 20min
    └─ SIG path (MEAN_REVERTING)        → RSI 55/45 instead of 60/40

positions_state.json ← regime added at trade entry
    │
    └─ Dashboard reads closed[]
        ├─ Session × Regime table
        ├─ Loss cluster alert strip
        └─ P&L velocity gauge

regime_status.json ← written by RegimeDetector on BTC engine cycles
    └─ RegimePill (/api/regime, 15s poll)

Binance klines (server-side fetch)
    └─ MacroTrendArrow (/api/macro-trend, 30s poll)
```

---

## Files to Create / Modify

| File | Change |
|---|---|
| `core/engine/updown_engine.py` | Changes 1, 2, 3, 6, 7, 8 — add gates inside `generate_signal()` and new helpers |
| `core/engine/signal_core.py` | Change 5 — update `REGIME_RSI_PARAMS["MEAN_REVERTING"]` |
| `core/engine/cycle_manager.py` | Change 4 — add `_monitor_lat_position` coroutine inside `scan_and_trade` |
| `app/main.py` | Changes 4, 10 — thread early-close logic; add `regime` field to position at entry |
| `presentation/dashboard/backend/routes/regime.js` (new) | Change 9 — `/api/regime` route |
| `presentation/dashboard/backend/routes/macroTrend.js` (new) | Change 11 — `/api/macro-trend` route |
| `presentation/dashboard/backend/server.js` | Import and mount both new routes |
| `presentation/dashboard/frontend/src/components/TradeFeed.jsx` | Changes 9, 10, 11, 12, 13 — RegimePill, MacroTrendArrow, table, alert strip, velocity |

---

## Triple Mandate Check

| Mandate | Impact |
|---|---|
| ≥15 trades/day | Gates are conditional (not blanket blocks). Macro gate fires only during clear 6+/8 trends. Corroboration gate fires only on 5m FV. SIG threshold loosening *adds* signals. Net effect: frequency preserved. |
| 65–70% WR | Macro trend gate eliminates the cluster pattern (responsible for all 7 losses in this session). Tiered edge threshold cuts marginal expensive entries. Combined: +5–8% WR expected. |
| Steady growing P&L | Loss brake caps cluster damage. Velocity gauge tracks trajectory. Macro arrow gives visual early warning of environment shift. |

---

## Not Changing

- Circuit breaker (conflicts with triple mandate)
- 5m position sizing Kelly multipliers (we find the 5m edge, not reduce it)
- REV threshold (needs more data)
- `fair_value.py` core (edge gate applied in engine layer, not in shared module)
