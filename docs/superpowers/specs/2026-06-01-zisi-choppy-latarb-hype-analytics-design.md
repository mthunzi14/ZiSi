# ZiSi — Choppy Detection, Latency Arb Restore, HYPE Enablement, Analytics Rebuild

**Date:** 2026-06-01  
**Status:** Approved  
**Mandates:** ≥15 trades/day · 65–70% WR · steady growing P&L  

---

## 1. Problem Statement

Four gaps degrade performance against the mandates:

| Gap | Impact |
|---|---|
| Trend gate reacts but does not remember — bot re-enters immediately after a slope flip, bleeding on ranging reversals | Transition-zone losses wipe wins (ETH −$19.97, DOGE −$5.40 in one session) |
| Latency-arb module has a dead import (`scratch.pyth_oracle_service`) | Near-certainty entries never fire; 20–30% of theoretical edge silent |
| HYPE 5m/15m markets enabled in config but slug unverified — bot never bets on them | Lower trade volume than mandated |
| Analytics tab is bot-facing (confluence radar, regime heatmap) — no trader insight | Can't see which asset/exit is driving P&L or WR |

---

## 2. Scope

This spec covers exactly four changes, in priority order:

1. **Per-asset choppy detection + cooldown** (transition zone, Option C)  
2. **Latency arb import fix** (one line, zero risk)  
3. **HYPE market slug verification + enablement**  
4. **Analytics tab rebuild** (frontend only, no engine logic)  

---

## 3. Transition Zone Fix — Option C

### Design

Each engine instance tracks a rolling slope history per-asset. When the slope reversal count in the last 4 readings crosses a threshold the asset enters a "choppy cooldown" and will not accept entries until conditions are clear.

**State structure** (added to `UpDownEngine.__init__`):
```python
self._slope_history: list[float] = []   # last 4 slope readings (rolling)
self._choppy_candles: int = 0           # candles remaining in pause
```

**Algorithm (called inside `generate_signal`, after the existing trend gate):**

```
Step 1 — Compute slope
  slope = (closes[-1] - closes[-5]) / closes[-5]   (same calc as trend gate)
  Append slope to _slope_history; keep only last 4.

Step 2 — Count slope reversals in last 4 readings
  reversals = number of consecutive sign flips in _slope_history
  If reversals >= 2  AND  abs(slope) < 0.004 (0.4% = not a clear trend):
      _choppy_candles = 2   # minimum 2-candle cooldown
      return None           # skip this entry

Step 3 — Serve the cooldown
  If _choppy_candles > 0:
      _choppy_candles -= 1
      return None

Step 4 — Resume guard (slope must be clear before re-entering)
  slope_clear = abs(slope) >= 0.004
  If not slope_clear:
      return None           # still ranging, wait one more candle

Step 5 — Pass through to existing trend gate (no double-skip needed;
          existing trend gate runs first as primary filter)
```

**Threshold changes:**
- Raise `_TREND_GATE` from `0.0025` → `0.004` (0.4%) — same value used in choppy detection for consistency.

### Files changed

| File | Change |
|---|---|
| `core/engine/updown_engine.py` | Add `_slope_history`, `_choppy_candles` to `__init__`; insert choppy detection block after trend gate; raise `_TREND_GATE` constant to `0.004` |

### Mandates alignment

- Volume: cooldowns are short (2 candles ≈ 10–30 min); healthy trending markets unaffected.
- WR: eliminates the ranging-reversal losses that pulled WR from 66.7% → 58.3%.
- P&L: fewer losing trades during choppy windows, more decisive entries otherwise.

---

## 4. Latency Arb Import Fix

### Root cause

`core/engine/cycle_manager.py` line 171:
```python
from scratch.pyth_oracle_service import GLOBAL_ORACLE_CACHE
```
`scratch/` is gitignored and does not exist on VPS. Import silently fails → `pyth_price = 0.0` every scan → near-certainty entries never fire.

### Fix

Change line 171 to:
```python
from core.pyth_oracle_service import GLOBAL_ORACLE_CACHE
```

`core/pyth_oracle_service.py` already exists, is version-controlled, and exports `GLOBAL_ORACLE_CACHE`.

### Files changed

| File | Change |
|---|---|
| `core/engine/cycle_manager.py` | Line 171: `scratch.` → `core.` |

### Mandates alignment

- Volume: latency-arb fires in the last 15–30 s of a window; each fire is a high-confidence trade that counts toward the volume mandate.
- WR: near-certainty entries on already-resolved-direction binary options are the highest-WR entries in the system (~80%+ theoretical).
- P&L: each near-certainty entry at ~0.88 that resolves to 0.99 is +$0.12 per share.

---

## 5. HYPE Market Enablement

### Context

`core/pyth_oracle_service.py` already has the HYPE Pyth feed ID:
```python
"HYPE": "0x4279e31cc369bbcc2faf022b382b080e32a8e689ff20fbc530d2a603eb6cd98b"
```
HYPE 5m/15m binary markets exist on Polymarket. The slug format used by the bot (`hype-updown-5m-{timestamp}`) may differ from the actual Polymarket slug.

### Fix

1. **Verify live slug format** — at deploy time, query Polymarket CLOB API for the active HYPE 5m/15m market slug and compare to the format in `updown_engine.py`. Expected candidates:
   - `hype-updown-5m-{timestamp}`
   - `hyperliquid-updown-5m-{timestamp}`
2. **Add HYPE to asset lists** — ensure HYPE appears in both 5m and 15m engine configurations if the slug resolves.
3. **No Pyth feed change needed** — feed already registered.

### Files changed

| File | Change |
|---|---|
| `core/engine/updown_engine.py` (or config) | Confirm/fix HYPE slug prefix; add HYPE to asset list |

### Mandates alignment

- Volume: +2 assets × 2 timeframes = +4 concurrent market streams → directly increases trade count.
- WR/P&L: HYPE is a high-volume, high-volatility asset; Pyth lead should be similar to BTC/ETH.

---

## 6. Analytics Tab Rebuild

### Current state

The analytics tab shows: confluence radar, backtest heatmap, regime chart — all bot-internal, not actionable for a trader.

### New design

Replace entirely with six trader-facing panels:

#### Panel 1 — Per-Asset Win Rate
Bar chart. X = asset (BTC, ETH, BNB, DOGE, SOL, HYPE). Y = WR% derived from `positions_state.json` closed trades filtered by asset. Shows N trades per bar as subtitle.

#### Panel 2 — Per-Asset P&L
Bar chart (positive green, negative red). Same asset grouping. Cumulative realized P&L per asset from closed trades.

#### Panel 3 — Exit Reason Breakdown
Donut/pie chart. Slices: `timeout`, `trend_reversal`, `near_certainty`, `stop_loss`. Derived from `exit_reason` field in closed positions.

#### Panel 4 — Trade Volume Per Asset Per Day
Stacked bar chart. X = day (last 7 days). Y = trade count. Colour per asset. Shows whether volume mandate is met and which assets are active.

#### Panel 5 — Hourly P&L Timeline
Line chart. X = hour of day (UTC 00–23). Y = average P&L per trade in that hour. Identifies which trading hours are profitable vs draining.

#### Panel 6 — Running EV Per Trade
Line chart. X = trade number (1…N, newest right). Y = running average P&L per trade. A flat/rising line = positive EV; declining = signal degradation signal.

### Data source

All panels read exclusively from `positions_state.json` (closed array + summary). No new API endpoints needed — the existing `/api/positions` route or SSE `positions_snapshot` event carries everything required.

### Files changed

| File | Change |
|---|---|
| `presentation/dashboard/frontend/src/components/Analytics.jsx` | Full rewrite: 6 panels using existing recharts library |

### Mandates alignment

- Volume: Panel 4 makes the volume mandate visible in real time.
- WR: Panel 1 + Panel 3 show which assets/exit types drive WR up or down.
- P&L: Panel 2 + Panel 6 show running EV per trade — the core mandate health signal.

---

## 7. Implementation Sequence

Execute in this order (each is independently deployable):

1. **Latency arb fix** — one line, no tests needed, deploy immediately.
2. **Choppy detection** — modify `updown_engine.py`, unit-test slope logic, deploy.
3. **HYPE enablement** — verify slug live, add to config, deploy.
4. **Analytics rebuild** — frontend only, no bot restart needed, deploy separately.

**Clean slate** runs after step 2 (choppy detection) is deployed and the bot has stabilized. Command:
```
venv/bin/python3 miscellaneous/clean_slate.py \
  --archive \
  --label "session_june1_2026_vps_launch" \
  --notes "21W/15L final, 58.3% WR, peak $124 P&L, transition zone identified, trend gate added, choppy detection deployed" \
  --balance 50 \
  --force
```

---

## 8. Out of Scope

- P&L chart history fix (separate session)
- Bone Reaper / P-Bot wallet-based strategy analysis (research, not implementation)
- BNB/DOGE removal (wait for 50+ trade sample to decide)
- Any changes to `signal_core.py`, `state_manager.py`, or `events.js`
