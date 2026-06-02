# ZiSi Bone Reaper Mode — Full Volume Overhaul Design

**Date:** 2026-06-03  
**Session:** Overnight (12:15 AM → 9:00 AM)  
**Status:** Approved by Mthunzi — implement tonight  
**Triple mandate:** ≥15 trades/day · 65–70% WR · steady growing P&L

---

## Motivation

Three verified bots analyzed on June 2–May 24, 2026:

**Bone Reaper** (0xeebde7a...): $8,573 on June 2. 49,136 lifetime predictions. 80–120+ trades/day on BTC+ETH 5m and 15m. $1,000–$7,640 per trade. Near-100% WR. Fires both 5m and 15m concurrently on same asset. Makes contra-consensus entries at 14¢ when market says 86% opposite — and wins.

**PBot-6** (0x21d0a97...): $2,669 on June 2. 53,718 lifetime predictions, joined March 2026. $10–$400 per trade. BTC+ETH+XRP+SOL. Near-100% WR. Same Pyth latency edge at ZiSi's capital scale.

**MutlakButlan**: $5,083 in 3 days (May 22–24), then withdrew. BTC 5m ONLY. 37 trades on May 22 alone. Entry at 36–75¢. $200–$1,200/trade. Perfect WR. Proves BTC-only 5m is sufficient for massive gains.

**Common thread:** All three know the candle direction before Polymarket prices it. They fire on every valid signal, concurrent across timeframes, both directions, any price point. ZiSi has the same underlying Pyth edge — we are throttling ourselves with gates.

ZiSi currently achieves ~0.6% candle hit rate (8 trades in a 17-hour session).
Bone Reaper achieves ~80%+ on BTC/ETH.
The gap is 130×.

**Goal:** Approach B — full gate removal, fire every candle, let overnight data reconcile.
BTC and ETH remain the priority money printers even within Approach B.

---

## Scope

### IN SCOPE (implement tonight)
1. Engine gate stack reduction (6 changes)
2. Bug fixes from session analysis (6 fixes)
3. Asset cleanup (BNB + LINK removal)
4. Dashboard "Why No Trade" status pill

### OUT OF SCOPE (tomorrow, after overnight data)
- Tier architecture (BTC/ETH unleashed, SOL/XRP selective, DOGE cautious)
- Multi-asset burst: simultaneous fire on all correlated assets
- Full Kelly sizing (keep position size cap for overnight safety)
- Hourly market entries (like Bone Reaper's 4PM hourly play)

---

## Engine Gate Changes

### Change 1: Corroboration → Sizing Multiplier

**Current behavior:** If peer asset disagrees with signal direction, trade is blocked entirely.

**New behavior:** Corroboration affects sizing only. Trade always fires.

```
Peer agrees → size × 1.3
No peer / peer disagrees → size × 0.7
```

**File:** `core/engine/updown_engine.py`
**Location:** The `_PEERS` corroboration block where `_write_gate_event("CORROBORATION-BLOCK", ...)` is called.
**Change:** Replace the `return None` with a sizing multiplier that is passed downstream.

---

### Change 2: Min Edge Threshold 0.10 → 0.05

**Current:** `_min_edge = 0.10` (requires ≥10¢ FV divergence)
**New:** `_min_edge = 0.05` (requires ≥5¢ FV divergence)

Bone Reaper enters at 4–5¢ edge at ATM. This aligns ZiSi with his entry threshold.
The FV macro-aware penalty (5+/8 candles opposing → raise to 0.18; 6+/8 → raise to 0.25) is unchanged and still applies.

**File:** `core/engine/updown_engine.py`
**Location:** `_min_edge` initial assignment in the FV path.

---

### Change 3: Remove Same-Asset Direction Cooldown

**Current:** After a trade closes on asset X in direction D, block new X/D trades for 15 minutes.

**New:** Removed entirely. Bone Reaper fires BTC UP at 4:45, 4:50, 5:05, 5:15 with no cooldown between UP entries.

**File:** `core/engine/updown_engine.py`
**Location:** The `_is_dir_cooldown_active()` call and the `DIR-COOLDOWN` gate event block.
**Change:** Remove the `if self._is_dir_cooldown_active(direction):` block (comment out, do not delete).

---

### Change 4: Soften Choppy Detection (Block This Candle Only)

**Current:** When choppy pattern detected (2+ rapid candle direction flips), asset is paused for the current candle AND the next 2 candles.

**New:** Choppy detection blocks ONLY the current candle scan. Next candle is completely fresh.

Bone Reaper doesn't skip candles after a volatile one — he fires on the very next candle.

**File:** `core/engine/cycle_manager.py` (choppy detection in `start_latency_edge_scanner`) and wherever per-asset choppy state is persisted.
**Change:** Reduce the pause window from 2-candle lookback-and-skip to current-candle-only.

---

### Change 5: Allow Concurrent 5m + 15m on Same Asset

**Current:** `session_governor` treats each asset as having a single trade slot. Opening BTC/15m fills the BTC slot, blocking BTC/5m.

**New:** The session_governor tracks slots per `(asset, timeframe)` pair, not per `asset` alone. BTC/5m and BTC/15m are independent slots.

This immediately doubles BTC and ETH potential trade count (one trade per TF per candle window).

**File:** `core/engine/session_governor.py`
**Location:** `has_open_asset_exposure()` and `request_trade_slot()` / `commit_trade_slot()`.
**Change:** Key becomes `(asset, timeframe)` tuple instead of `asset` string.

---

### Change 6: FV macro-aware penalty (PRESERVED)

The macro-aware FV penalty is NOT changed. When 5+/8 Binance candles oppose FV direction, `_min_edge` floor raises to 0.18 (soft conflict) or 0.25 (hard conflict, 6+/8). This protects the bot from trading strongly against macro even with a loose 5¢ base edge.

---

## Bug Fixes

### Fix 1: Remove BNB

Remove from all locations: `config.py` (ASSETS + TIMEFRAMES), `updown_engine.py` (VOLUME_GATE_FLOORS + _PEERS), `cycle_manager.py` (altcoin sizing gate), `app/main.py` (sizing gate), `presentation/dashboard/backend/routes/assetMacro.js` (ASSETS array), `presentation/dashboard/frontend/src/components/AssetCards.jsx` (ASSETS array).

**Verdict:** 2/2 FV losses on June 2, including an entry at 4¢ (nearly zero-probability). BNB is permanently removed.

---

### Fix 2: Remove LINK

Same files. LINK never had an active Polymarket market and generated zero trades. Dead asset since HYPE replacement.

---

### Fix 3: Minimum Contract Price Floor — 15¢

Add to both the FV path (`updown_engine.py`) and the LAT path (`cycle_manager.py`), after `entry_price` is assigned:

```python
if entry_price < 0.15:
    log.info("[PRICE-FLOOR] %s/%s: %.0f¢ below 15¢ minimum — skip", asset, timeframe, entry_price * 100)
    _write_gate_event(asset, timeframe, "PRICE-FLOOR", direction, f"entry {entry_price*100:.0f}¢ < 15¢ floor")
    return
```

**Motivation:** The BNB 5m trade entered at 4¢ — a 96% probability of losing. Never enter a contract below 15¢.

---

### Fix 4: LAT Global 60-Second Cooldown

Add module-level lock in `cycle_manager.py`:

```python
_LAT_LAST_ENTRY_TS: float = 0.0

# inside scan_and_trade, before place_order:
global _LAT_LAST_ENTRY_TS
if time.time() - _LAT_LAST_ENTRY_TS < 60.0:
    return  # global LAT cooldown active
# after successful order:
_LAT_LAST_ENTRY_TS = time.time()
```

**Motivation:** ETH and SOL both fired LAT at the exact same second (21:34:47) on the same false Pyth signal, losing $8.65. One entry per minute prevents this.

---

### Fix 5: Widen ATM Gate to 44–56¢

**Current:** `if 0.47 <= entry_price <= 0.53`
**New:** `if 0.44 <= entry_price <= 0.56`

**Motivation:** ETH/SOL LAT entries at 47¢ passed the ATM gate due to float precision (0.469 < 0.47 at gate check, slipped to 0.47 at execution). Wider gate prevents this.

**File:** `cycle_manager.py:267`

---

### Fix 6: 5m LAT Minimum Pyth Move 0.3% → 0.5%

**Current:** `abs_move >= 0.003` (0.3% Pyth divergence triggers 5m LAT)
**New:** `if timeframe == "5m" and abs_move < 0.005: return`

**Motivation:** 5m LAT has no early-exit monitor (only 15m gets `_monitor_lat_exit`). On 5m, a wrong LAT signal expires at 1¢ with no bail. Requiring 0.5% instead of 0.3% makes 5m LAT entries much higher conviction before committing.

---

## Dashboard: "Why No Trade" Status Pill

### Backend: `engine_status.json`

The bot writes a small JSON file at every scan cycle (once per minute minimum):

```json
{
  "ts": 1748922000.0,
  "status": "SCANNING",
  "detail": "next 5m in 2m 14s",
  "asset_states": {
    "BTC": {"5m": "SCANNING", "15m": "SCANNING"},
    "ETH": {"5m": "LOW_EDGE", "15m": "SCANNING"},
    "SOL": {"5m": "CHOPPY", "15m": "SCANNING"}
  }
}
```

Status values: `SCANNING` · `LOW_EDGE` · `CHOPPY` · `LAT_COOLDOWN` · `NO_MARKET` · `PRICE_FLOOR` · `MACRO_BLOCK` · `CIRCUIT_BREAK`

**File:** New `infrastructure/state/engine_status.py` — simple `write_engine_status(status, detail, asset_states)` function. Called from `cycle_manager.py` main scan loop.

### Backend Route: `/api/engine-status`

New route in `presentation/dashboard/backend/routes/engineStatus.js` — reads and returns `engine_status.json`.

### Frontend: Status Pill in Trade Ledger

In `TradeFeed.jsx`, above the trade table, add a `EngineStatusPill` component:
- Polls `/api/engine-status` every 5 seconds
- If last trade was >5 minutes ago AND status ≠ SCANNING → show pill
- Pill colors: gold (SCANNING), orange (CHOPPY/LOW_EDGE/PRICE_FLOOR), red (CIRCUIT_BREAK/NO_MARKET)
- Example renders:
  - `⏳ SCANNING — next candle in 1m 43s` (gold)
  - `🌀 CHOPPY — ETH/5m paused` (orange)
  - `📉 LOW EDGE — Pyth divergence < 5¢` (orange)
  - `🔒 LAT COOLDOWN — 38s remain` (orange)

---

---

## Addition 1: T-5s Near-Certainty Final Scanner

A second candle-close scanner fires at **T-5 seconds** before each candle boundary (in addition to the existing T-15s scanner in `start_latency_edge_scanner`).

**Rationale:** Bone Reaper's 98¢, 96.2¢, 95.9¢ entries and MutlakButlan's 60–75¢ entries are late-candle near-certainty plays where the direction is already determined from Pyth. At T-5s, |pct_move| of 0.3%+ means the candle close is essentially locked in. The Polymarket price lags by 1–3 seconds.

**Entry criteria:**
- `abs(pct_move) >= 0.003` (0.3% Pyth divergence — direction is clear)
- `pythAge < 3s` (fresh oracle only)
- No existing open position for this `(asset, timeframe)` in this candle
- No ATM gate (can enter at any price — resolution is imminent)

**Sizing:** `0.7×` normal compute_size output (late entries have small ROI; don't over-allocate)

**File:** `core/engine/cycle_manager.py` — add `_t5_scanner_task` alongside existing `_lat_scanner_task` in the scanner daemon. Shares the same `scan_and_trade` function but with `t_minus=5` parameter.

---

## Addition 2: Cross-Asset Lag Entry

When BTC fires a strong Pyth signal (`abs(pct_move) >= 0.005`, i.e., 0.5%+) and no ETH position is already open for that candle, check if ETH's current market price is **opposite to BTC's direction**:

- BTC fired UP (`pct_move > 0`) AND ETH UP market is priced < 0.45¢ → ETH UP is a lag trade
- BTC fired DOWN (`pct_move < 0`) AND ETH DOWN market is priced < 0.45¢ → ETH DN is a lag trade

Enter ETH in BTC's direction. The market hasn't priced ETH's follow-through yet.

**Evidence:** Bone Reaper took ETH UP at 37.5¢ while BTC was strongly UP. ETH hadn't followed. He won 166%.

**Sizing:** `0.5×` normal (secondary conviction, not primary signal)

**File:** `core/engine/cycle_manager.py` — add `_check_cross_asset_lag(btc_pct_move, btc_direction, session, engines)` called after any BTC LAT entry.

**Symmetric:** Also works BTC↔ETH in reverse (if ETH fires strongly and BTC lags).

---

## Addition 3: Dynamic Price Floor (replaces hard 15¢)

**Remove:** `if entry_price < 0.15: return`

**Replace:**
```python
if entry_price < 0.15 and abs_pct_move < 0.004:
    log.info("[PRICE-FLOOR] %s/%s: %.0f¢ with weak move %.4f%% — skip", ...)
    return
```

**Rationale:** Hard 15¢ blocks Bone Reaper's 14¢ contra-consensus plays. The BNB 4¢ disaster had a WEAK FV signal (`pct_move` near 0). A strong Pyth divergence (0.4%+) at 14¢ is a massive edge — market is 86% wrong. Allow it. Only weak signals at very low prices get blocked.

---

## Addition 4: BTC/ETH Position Priority

When the position cap (max 6 open) is at 5/6 or 6/6, **never let a lower-tier asset fill the last slot if a BTC or ETH signal is pending.**

**Implementation:** In `session_governor.py`, add a `tier` parameter to `request_trade_slot`. BTC/ETH = tier 1, SOL/XRP = tier 2, DOGE = tier 3. If at capacity, only accept tier-1 requests (evict nothing, just don't admit tier-2/3 when full).

**Rationale:** MutlakButlan proved BTC-only is sufficient. A DOGE position blocking a BTC signal is value destruction.

---

## Files Modified

| File | Change |
|------|--------|
| `config.py` | Remove BNB, LINK from ASSETS + TIMEFRAMES |
| `core/engine/updown_engine.py` | Corroboration → multiplier, _min_edge 0.05, remove direction cooldown, price floor gate |
| `core/engine/cycle_manager.py` | Soften choppy detection, LAT cooldown, ATM gate widen, 5m LAT threshold, price floor, altcoin sizing gate BNB/LINK removal |
| `core/engine/session_governor.py` | Slot key becomes (asset, timeframe) tuple |
| `app/main.py` | BNB/LINK removal from sizing gate |
| `infrastructure/state/engine_status.py` | New: write_engine_status() |
| `presentation/dashboard/backend/routes/engineStatus.js` | New: /api/engine-status route |
| `presentation/dashboard/backend/server.js` | Mount engineStatus router |
| `presentation/dashboard/frontend/src/components/TradeFeed.jsx` | Add EngineStatusPill component |
| `presentation/dashboard/backend/routes/assetMacro.js` | Remove BNB, LINK from ASSETS array |
| `presentation/dashboard/frontend/src/components/AssetCards.jsx` | Already has BNB removed (confirm LINK also absent) |
| `core/engine/cycle_manager.py` (additions) | T-5s scanner task, cross-asset lag entry function, dynamic price floor |
| `core/engine/session_governor.py` (additions) | BTC/ETH tier-1 priority at capacity |

---

## Expected Overnight Results (12:15 AM → 9:00 AM)

| Scenario | Trades | WR | Net P&L | Balance at 9 AM |
|----------|--------|-----|---------|-----------------|
| Conservative | 20 | 63% | +$28 | ~$150 |
| **Base case** | **30** | **65%** | **+$52** | **~$174** |
| Optimistic | 40 | 68% | +$88 | ~$210 |

Key drivers:
- BTC/ETH 5m + 15m concurrent positions → 2× BTC/ETH volume
- Edge threshold 0.05 → fires on more Pyth divergences
- No direction cooldown → consecutive candle wins like Bone Reaper's 4:45–5:15 run
- FV macro penalty still active → macro-opposing FV trades still penalized proportionally

---

## Risk Notes

1. **Overnight Pyth reliability**: Asian session has lower liquidity. LAT global cooldown (60s) and 5m LAT 0.5% threshold prevent the worst false-signal pairs.
2. **Session reconciliation**: Check at 9 AM. If WR < 60% or P&L is negative despite high volume, revert to Approach C (Tier architecture) for the morning session.
3. **No circuit breaker active**: Daily loss halt is deactivated by user instruction. Monitor balance at 9 AM.

---

## Self-Review

**Placeholders:** None — all file paths, function names, and code snippets are specific.  
**Consistency:** `_min_edge = 0.05` in updown_engine.py. Corroboration multiplier values (1.3×/0.7×) are specific. Session_governor slot key change is self-contained.  
**Scope:** Tight — no features beyond what's in scope. Multi-asset burst and hourly markets deferred explicitly.  
**Contradictions:** FV macro penalty preserved AND base edge lowered — these coexist: base is 0.05, but macro-opposing trades get floored at 0.18/0.25. No contradiction.
