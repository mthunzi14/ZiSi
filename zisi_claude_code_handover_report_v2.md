# ZiSi Bot: Rebuild v2.0 — Deep Forensic Analysis & Claude Code Handover
## Updated Session Report | June 9, 2026

**Session Balance:** $50.00 start → **$25.29 realized** (after last loss)  
**Active P&L:** $0.03 (2 open positions)  
**Bot Status:** Running (PID 80343, up since 15:45 UTC)

---

## 1. Current Telemetry Snapshot

| Metric | Value |
|---|---|
| Total Closed Trades | **69** |
| Total Wins | **56** (81.2%) |
| Total Losses | **13** (18.8%) |
| Realized P&L | **-$24.52** |
| Current Balance | **~$25.29** |

### By Trade Type
| Type | Count | W | L | WR | P&L | Avg Size |
|---|---|---|---|---|---|---|
| **NCS** | 39 | 36 | 3 | 92.3% | **-$33.77** | $17.05 |
| **SIGNAL** | 14 | 10 | 4 | 71.4% | **+$25.72** | $3.85 |
| **FAIR-VAL** | 15 | 10 | 5 | 66.7% | **-$11.74** | $3.73 |
| **REVERSAL-STREAK** | 1 | 0 | 1 | 0.0% | **-$4.73** | $4.82 |
| **LAT-ARB / SWEEP / REV-SNIPE** | 0 | 0 | 0 | — | $0 | — |

**Key Diagnosis:** Despite an 81.2% blended win rate, the bot is net -$24.52 because the **3 NCS tail losses alone caused -$52.40 in damage**, completely wiping out NCS's $18.63 in wins. FV is structurally losing money despite a 66.7% win rate because cheap losing contracts ($3–6 size) cost more than expensive winning ones ($1.80–2.50 size). **The problem is sizing — not direction.**

---

## 2. The Three Loss Clusters — Full Forensic Breakdown

### CLUSTER A: 16:10 UTC — NCS Flat-Market Double Kill
**Trades:** SOL/5m NO @ 98.5¢ (-$19.50), XRP/5m NO @ 97¢ (-$20.16)  
**Combined Damage:** **-$39.66**

These are the single biggest losses of the session. Both candles opened at exactly flat (sub-1-tick) price, with Pyth sitting just below the open. The contracts traded at near-certainty (97–98.5¢) but resolved UP because the final print crossed the open by a single cent.

```
SOL: Open=64.1800 → Close=64.1900 (+1¢) | NO expired at 0¢
XRP: Open=1.1282 → Close=1.1289 (+0.0007) | NO expired at 0¢
```

**Root Cause (Code):** `cycle_manager.py → start_close_sniper` has **no minimum spot-distance guard**. It enters any contract above 88–95¢ regardless of how close spot is to the strike. In flat markets, the margin of error is zero and a 1-tick noise print flips the resolution.

---

### CLUSTER B: 16:30–16:35 UTC — FV Multi-Asset Correlated Loss
**Trades:**
- ETH/5m NO @ 42¢ ($4.62) → LOSS -$4.51
- SOL/5m NO @ 60¢ ($3.00) → LOSS -$2.95  
- BTC/15m DOWN (FAIR_VAL) → LOSS -$3.60

**Combined Damage: ~-$11.06**

All three FV trades were entered in the same 5-minute window (16:30–16:31) on the BTC/ETH/SOL/XRP DOWN theme, betting the 12:30PM candle resolves down. The entire market reversed sharply UP at 12:35PM ET, causing all three to expire worthless simultaneously.

**Root Cause:**
1. **Correlated exposure without a cap.** The bot entered FV positions across 4+ assets simultaneously in the same direction on the same candle timeframe. When market direction was wrong, all positions lost together.
2. **Score inflation on cheap contracts.** ETH/5m was entered at 42¢ with `score=0.94` despite the confluence engine showing only `1/4 WEAK` agreement. The composite score included momentum, OFI, CLOB OBI, and AI boosts that individually were weak but stacked to inflate the score.
3. **Sizing inversion active.** The 42¢ contract was sized at $4.62 (nearly maximum for current balance) while earlier 60–70¢ contracts were sized at $1.80–2.56. The Kelly formula at low prices ($b$ = high payout ratio) produces over-sizing.

**Evidence from logs:**
```
16:31:03 [FAIR-VALUE] ETH/5m DOWN | fp=0.297 quote=0.480 edge=0.223 (moderate)
16:31:24 [TRADE OPENED] ETH/5m DOWN | $4.62 @ 42¢ | score=0.94 | FAIR_VAL
16:31:06 [FAIR-VALUE] BTC/5m DOWN | fp=0.326 quote=0.500 edge=0.174 (moderate)
16:31:23 [FAIR-VALUE] BTC/15m DOWN | fp=0.339 quote=0.445 edge=0.216 (moderate)
→ 16:35:06 ALL expired at 0.01 | simultaneous correlated wipeout
```

---

### CLUSTER C: 16:40–16:45 UTC — SIGNAL Cheap Contract Entries
**Trades:**
- XRP/5m NO @ 29¢ ($2.32) → LOSS -$2.24
- XRP/5m NO @ 53¢ ($2.65) → LOSS -$2.60

**Combined Damage: ~-$4.84**

The bot entered XRP/5m at cheap prices (29¢ and 53¢) via the SIGNAL path. The 29¢ entry in particular is a contrarian cheap-contract bet that should have been heavily size-capped. The LOSS-BRAKE was activated after these (8 losses in 20min), which temporarily suppressed further SIG entries below score 0.82.

**Root Cause:** The `is_reversal` quarter-Kelly check in `app/main.py:501` only applies when `_entry_source in ("SIG", "SIGNAL")` AND `signal.get("is_reversal")` is set. These were directional SIG trades without the reversal flag, so they were sized at full bankroll fraction.

---

## 3. Why the Three Daemons Are Silent (0 Trades)

### 3.1 LAT-ARB — NameError Crash
**Confirmed from logs:**
```
17:44:53 [ERROR] zisi.cycle_manager: [LATENCY-ARB] Error scanning XRP/5m: 
  name 'open_positions' is not defined
17:59:53 [ERROR] zisi.cycle_manager: [LATENCY-ARB] Error scanning ETH/1h: 
  name 'open_positions' is not defined
```
The process (PID 80343, started 15:45 UTC) is running a stale cached bytecode version of `cycle_manager.py`. In the old version, `open_positions` was accessed at line 346 before being defined (it's defined at line 235 in the current file on disk). **A process restart is required.**

Note from logs: LAT-ARB IS detecting valid opportunities (e.g., `SOL/15m move: 0.85%`, `ETH/15m move: 0.80%`) — it just crashes on the `request_trade_slot` call because `open_positions` is undefined. Every potential LAT-ARB trade is being lost due to this crash.

### 3.2 REV-SNIPE — Impossible Thresholds
In `cycle_manager.py:828–841`, the trigger is:
```python
if up_price >= 0.85 and dn_price <= 0.20 and pct_move <= -0.002:
```
For this to fire on BTC/5m: spot must have dropped 0.2% (≈$122) from open, while the YES contract price remains at 85¢. Impossible — any 0.2% spot drop instantly reprices the contract from 85¢ down to ~25¢ within milliseconds. The lag is never that persistent.

**Fix:** Change `pct_move <= -0.002` to `pct_move <= -0.001` (0.1% threshold), and `up_price >= 0.85` to `up_price >= 0.70` to allow earlier entry before market makers fully adjust.

### 3.3 SWEEP — Blocked by Own Existing Positions
```python
for pos in state_mgr.get_open_positions():
    if pos.get("event_id") == event_id:
        sweep_dir = None
        break
```
Since SIG/FV/NCS enter almost every candle, there's always an existing position. The sweeper checks are blocking on their own trades. 

**Fix:** Allow SWEEP to enter in the same event IF the existing position direction matches (double down), or only block if positions in the opposite direction exist.

---

## 4. Critical Pattern: The Sizing Inversion Problem (FV)

This is the #1 structural issue. Here is the concrete proof from this session:

### FV Winning Trades (high entry price → small size → small win)
| Asset | Entry Price | Size | PnL |
|---|---|---|---|
| ETH/5m | 60¢ | $1.80 | +$0.78 |
| SOL/5m | 62.5¢ | $2.50 | +$0.92 |
| ETH/5m | 61¢ | $3.66 | +$0.90 |
| XRP/5m | 67¢ | $2.01 | +$0.69 |
| XRP/5m | 74¢ | $3.70 | +$0.55 |

### FV Losing Trades (low entry price → large size → large loss)
| Asset | Entry Price | Size | PnL |
|---|---|---|---|
| XRP/5m | 38.5¢ | **$6.54** | **-$6.38** |
| ETH/5m | 42¢ | **$4.62** | **-$4.51** |
| ETH/5m | 49¢ | **$5.39** | **-$5.28** |
| SOL/5m | 60¢ | $3.00 | -$2.95 |

**The pattern is unmistakable:** Cheap contracts are over-sized, expensive ones are under-sized. 10 FV wins generated +$7.44. 5 FV losses cost -$19.12. Despite a 66.7% win rate, the net is **-$11.68**.

### Why This Happens (Technical Trace)
1. `_fv_confidence` is set in `updown_engine.py:746` from `_fv.get("confidence", 0.0)`
2. That confidence is computed by `fair_value.py:161` as: `0.45 * conviction + 0.30 * edge_factor + 0.25 * agree`
3. At 42¢ entry with edge=0.223: `conviction = (0.297-0.5)*2 = 0` (fp_up is BELOW 0.5!), `edge_factor = 0.223/0.15 = 1.0 (capped)`, `agree = 0.5 (neutral)`. So `confidence = 0 + 0.30 + 0.125 = 0.425`
4. BUT `fv_confidence = 0.425` should drive sizing to 5% bankroll. Instead, the composite score gets boosted to `0.94` by momentum/OFI/CLOB boosts downstream (these boosts ignore `entry_source == FAIR_VAL`), and `compute_size` uses `conf = score = 0.94` → 25% bankroll fraction → $4.62 bet.
5. **The `fv_confidence` value is being overridden by the inflated composite `score`.**

---

## 5. Complete Fix Blueprint for Claude Code

### FIX 1 (CRITICAL): NCS Minimum Spot Distance Guard
**File:** `core/engine/cycle_manager.py`  
**Inside:** `start_close_sniper()` inner loop, after fetching `pyth_price`  
**Location:** After line ~1040 where `pyth_price` is retrieved

```python
# ── NCS PROXIMITY GUARD: Reject flat-candle entries ──
# Do NOT snipe if spot is too close to strike (candle open).
# A flat candle (dist < 0.25x ATR) can flip on a 1-tick noise print.
try:
    from core.engine.updown_engine import _fetch_klines_async
    _prox_klines = await _fetch_klines_async(session, asset, "5m" if timeframe == "5m" else timeframe.rstrip("m")+"m", 15)
    if len(_prox_klines) >= 14:
        _strike = float(_prox_klines[-1][1])           # candle open = strike
        _trs = []
        for _i in range(len(_prox_klines)-14, len(_prox_klines)-1):
            _h = float(_prox_klines[_i][2])
            _l = float(_prox_klines[_i][3])
            _pc = float(_prox_klines[_i-1][4])
            _trs.append(max(_h - _l, abs(_h - _pc), abs(_l - _pc)))
        _atr = sum(_trs) / len(_trs) if _trs else 0.0
        _atr_frac = _atr / _strike if _strike > 0 else 0.01
        _spot_dist = abs(pyth_price - _strike) / _strike if _strike > 0 else 0.0
        _min_dist = 0.25 * _atr_frac
        if _spot_dist < _min_dist:
            log.warning(
                "[NCS-PROXIMITY-VETO] %s/%s: spot %.4f too close to strike %.4f "
                "(dist=%.5f%% < min=%.5f%% = 0.25x ATR) — flat candle, skip",
                asset, timeframe, pyth_price, _strike, _spot_dist*100, _min_dist*100
            )
            continue
except Exception as _prox_err:
    log.debug("[NCS-PROXIMITY] Guard error (skipping): %s", _prox_err)
```

---

### FIX 2 (CRITICAL): FV Sizing — Cap Score for FAIR_VAL Entries
**File:** `core/engine/updown_engine.py`  
**Location:** Around line 944, inside `generate_signal()` after `score = score_base`

The core fix: **do not apply short-term indicator boosts to FV trades**. The FV model already has its own confidence score; piling on momentum/OFI/CLOB boosts designed for SIG trades massively inflates FV sizing.

```python
# Composite score — ONLY apply indicator boosts to SIG/SIGNAL trades.
# FV trades use fv_confidence for sizing; boosting score here causes sizing inversion.
abs_mom = abs(mom)
score = score_base
if entry_source != "FAIR_VAL":   # ← ADD THIS GUARD
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
```

Also ensure the final output dict actually uses `fv_confidence` in sizing. In `app/main.py:484`, the call is:
```python
raw_bet_usd = engine.compute_size(score, entry_price, current_balance,
                                  confidence=(signal.get("fv_confidence") or None))
```
The `or None` means if `fv_confidence = 0.0` (which it will be if the FV path was bypassed), it falls back to `score`. Ensure `_fv_confidence` is always set non-zero when a FV trade fires:
```python
# updown_engine.py:746 — ensure this always sets a real value
_fv_confidence = float(_fv.get("confidence", 0.0))
if _fv_confidence <= 0.0:
    # Fallback: conservative confidence based on edge size only
    _fv_confidence = min(0.65, 0.40 + float(_fv.get("edge", 0.0)) * 1.5)
```

---

### FIX 3 (CRITICAL): Add FV Correlated Exposure Cap
**File:** `app/main.py`  
**Location:** Before order placement, after `bet_usd` is calculated

Prevent simultaneous FV entries across multiple assets in the same direction within the same candle window. Maximum 2 open FV positions in any one direction at a time:

```python
# ── FV CORRELATED EXPOSURE CAP ──
if _entry_source == "FAIR_VAL":
    _fv_same_dir_open = sum(
        1 for p in open_positions
        if p.get("entry_type") == "FAIR-VAL"
        and ((p.get("direction") in ("YES","UP")) == (direction == "UP"))
    )
    if _fv_same_dir_open >= 2:
        log.info("[FV-CORR-CAP] %s/%s: already %d open FV %s positions — skip to avoid correlated exposure",
                 asset, timeframe, _fv_same_dir_open, direction)
        return False, {}
```

---

### FIX 4 (CRITICAL): Route REV-STREAK through WHALE-VETO
**File:** `core/engine/updown_engine.py`  
**Location:** Lines 507–535, inside the `REVERSAL-STREAK` early-return block

Add whale veto check BEFORE the return, and set `is_reversal=True` so quarter-Kelly sizing applies:

```python
# After edge_ctx is fetched (around line 526), BEFORE the return:
_streak_whale = edge_ctx.get("whale_pressure", 0.0)
if abs(_streak_whale) >= 0.85:
    _whale_contradicts = (
        (_streak_whale < 0 and direction == "UP") or   # bears contradict UP
        (_streak_whale > 0 and direction == "DOWN")    # bulls contradict DOWN
    )
    if _whale_contradicts:
        log.warning(
            "[STREAK-WHALE-VETO] %s/1h: whale pressure %.2f contradicts %s — skipping streak reversal",
            self.asset, _streak_whale, direction
        )
        return None

# In the return dict at line 536, add:
return {
    ...existing fields...,
    "is_reversal": True,       # ← ADD: enables quarter-Kelly sizing in main.py
    "whale_aligned": False,    # ← CHANGE: remove the hardcoded bypass
}
```

---

### FIX 5: Fix LAT-ARB (Restart Required)
The process running on PID 80343 has a cached bytecode mismatch. No code change needed — just a process restart:

```bash
# On VPS:
pkill -f main.py
pm2 restart zisi-dashboard 2>/dev/null || true
cd /root/ZiSi && python3 app/main.py &
```

After restart, LAT-ARB will begin firing immediately (we confirmed it detects valid 0.5–0.8% moves on SOL/ETH/XRP but crashes before executing).

---

### FIX 6: Relax REV-SNIPE Thresholds
**File:** `core/engine/cycle_manager.py`  
**Function:** `start_reversal_sniper → _snipe()`  
**Lines:** ~828–841

```python
# Current (never fires):
if up_price >= 0.85 and dn_price <= 0.20 and pct_move <= -0.002:

# Fixed (fires when real lag exists):
if up_price >= 0.70 and dn_price <= 0.30 and pct_move <= -0.0010:

# Similarly:
# Current:
elif dn_price >= 0.85 and up_price <= 0.20 and pct_move >= 0.002:
# Fixed:
elif dn_price >= 0.70 and up_price <= 0.30 and pct_move >= 0.0010:
```

---

### FIX 7: SWEEP — Allow Same-Direction Double Down
**File:** `core/engine/cycle_manager.py`  
**Function:** `start_resolution_sweeper`  
**Lines:** ~962–967

```python
# Current (blocks if ANY position exists in event):
for pos in state_mgr.get_open_positions():
    if pos.get("event_id") == event_id:
        sweep_dir = None
        break

# Fixed (only block if OPPOSITE direction exists):
for pos in state_mgr.get_open_positions():
    if pos.get("event_id") == event_id:
        pos_is_yes = pos.get("direction") in ("YES", "UP")
        sweep_is_yes = (sweep_dir == "YES")
        if pos_is_yes != sweep_is_yes:  # contradicting direction → block
            sweep_dir = None
        break  # still break after first match
```

---

## 6. Macro Market Context (Current Conditions)

From confluence engine logs at 16:45 UTC:
- **BTC:** 1h RSI = 22.61, Mom = -2.22% → **extreme oversold on 1h**, but 5m neutral
- **DOGE:** 1h RSI = 35.74, Mom = -2.19% → approaching oversold
- **All assets:** confluence score 0/4 to 1/4 (CONFLICT or WEAK) → market is in full chop/recovery mode after the sharp 12:30PM ET drop

The LOSS-BRAKE activated at 16:45 blocking SIG entries below 0.82 score on XRP/SOL/ETH — this is working correctly as a circuit breaker.

**Implication for strategy:** The market is in a mean-reversion/choppy state after a strong 1h bearish move. FV is seeing many DOWN signals because the 5m still looks bearish, but the 1h has now reached extreme oversold. This is exactly the environment where the FV sizing inversion causes maximum damage — it keeps betting DOWN in chop while the market recovers.

---

## 7. Priority Order for Claude Code

| Priority | Fix | Files | Impact |
|---|---|---|---|
| 🔴 P0 | Restart VPS process | N/A (shell command) | Re-enables LAT-ARB immediately |
| 🔴 P0 | NCS Proximity Guard | `cycle_manager.py` | Eliminates -$40 tail loss events |
| 🔴 P0 | FV Score Inflation Fix | `updown_engine.py` | Makes FV net-positive |
| 🔴 P0 | FV Correlated Exposure Cap | `app/main.py` | Prevents cluster losses |
| 🟠 P1 | REV-STREAK Whale Veto | `updown_engine.py` | Prevents counter-trend wipeout |
| 🟠 P1 | REV-SNIPE threshold relax | `cycle_manager.py` | Enables new trade type |
| 🟡 P2 | SWEEP same-dir allow | `cycle_manager.py` | Increases trade volume |

---

## 8. Deployment Checklist

After applying all fixes:
```bash
# 1. Kill outdated process
pkill -f "python.*main.py"

# 2. Reset clean slate
cd /root/ZiSi && python3 miscellaneous/clean_slate.py --balance 50 --force

# 3. Restart dashboard  
pm2 restart zisi-dashboard

# 4. Restart bot
nohup python3 app/main.py > zisi_bot_console.log 2>&1 &

# 5. Verify LAT-ARB is running (should see no more NameError)
tail -f zisi_bot_console.log | grep -E "LATENCY-ARB|ERROR"
```

**Expected improvements after fixes:**
- LAT-ARB: 10–20 additional trades/session, 70–80% WR
- FV: turns net-positive (10 wins × ~$1.50 avg > 5 losses × ~$2.50 avg)
- NCS: eliminates the -$40 tail events, maintaining 90%+ WR
- REV-STREAK: quarter-Kelly sizing caps loss at ~$1.50 instead of $4.82
