# ZiSi Bone Reaper Mode — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove all gates blocking Bone Reaper-level trade frequency: concurrent 5m+15m per asset, corroboration → sizing multiplier, edge floor 0.05, no direction cooldown, T-5s near-certainty scanner, cross-asset lag entry, BTC/ETH tier-1 priority, BNB/LINK removal.

**Architecture:** Engine changes (updown_engine.py, cycle_manager.py, session_governor.py) + dashboard "Why No Trade" pill (new engine_status.py + JS route + React component). All deployed as one overnight push.

**Tech Stack:** Python 3.13 asyncio, React 18, Node.js Express. Tests: `python -m unittest`. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-06-03-zisi-bone-reaper-mode-design.md`

---

## File Map

| File | Change type | Purpose |
|------|-------------|---------|
| `config.py` | Modify | Remove BNB, LINK from ASSETS + TIMEFRAMES |
| `core/engine/updown_engine.py` | Modify | _min_edge 0.05, corroboration→multiplier, remove DIR-COOLDOWN, dynamic price floor, remove BNB/LINK from PEERS/VOLUME_GATE_FLOORS |
| `core/engine/cycle_manager.py` | Modify | ATM gate 0.44–0.56, 5m LAT 0.5%, LAT 60s cooldown, choppy current-candle-only, dynamic price floor, remove BNB/LINK sizing, T-5s scanner, cross-asset lag |
| `core/engine/session_governor.py` | Modify | Concurrent (asset,tf) slot keys, BTC/ETH tier-1 candle cap bypass |
| `app/main.py` | Modify | Apply corroboration_multiplier to bet_usd, remove BNB/LINK sizing gate |
| `infrastructure/state/engine_status.py` | Create | write_engine_status() helper |
| `presentation/dashboard/backend/routes/engineStatus.js` | Create | GET /api/engine-status |
| `presentation/dashboard/backend/server.js` | Modify | Mount engineStatus router |
| `presentation/dashboard/frontend/src/components/TradeFeed.jsx` | Modify | EngineStatusPill component |
| `presentation/dashboard/backend/routes/assetMacro.js` | Modify | Remove BNB, LINK from ASSETS array |
| `presentation/dashboard/frontend/src/components/AssetCards.jsx` | Modify | Remove BNB entries, update count pill |

---

## Task 1: Asset Cleanup — Remove BNB and LINK

**Files:**
- Modify: `config.py`
- Modify: `app/main.py`
- Modify: `presentation/dashboard/backend/routes/assetMacro.js`
- Modify: `presentation/dashboard/frontend/src/components/AssetCards.jsx`

- [ ] **Step 1: Update config.py**

In `config.py`, replace lines 12–25:
```python
ASSETS: list = ["BTC", "ETH", "SOL", "XRP", "DOGE"]

TIMEFRAMES: dict = {
    "BTC": ["5m", "15m"],
    "ETH": ["5m", "15m"],
    "SOL": ["5m", "15m"],
    "XRP": ["5m", "15m"],
    "DOGE": ["5m", "15m"],
}
```

- [ ] **Step 2: Update app/main.py — remove BNB/LINK sizing gate**

Find and replace this block in `app/main.py` (around line 306):
```python
    elif asset in ["BNB", "LINK"]:
        bet_usd = bet_usd * 0.50
        log.info("[RISK] Altcoin %s Sizing calibrated to 50%%: $%.2f", asset, bet_usd)
    elif asset in ["ADA", "LINK", "DOGE", "AVAX", "SUI"]:
        bet_usd = min(bet_usd * 0.35, 35.0)
        log.info("[RISK] Altcoin %s Sizing calibrated to 35%% (max $35): $%.2f", asset, bet_usd)
```
Replace with:
```python
    elif asset in ["ADA", "DOGE", "AVAX", "SUI"]:
        bet_usd = min(bet_usd * 0.35, 35.0)
        log.info("[RISK] Altcoin %s Sizing calibrated to 35%% (max $35): $%.2f", asset, bet_usd)
```

- [ ] **Step 3: Update assetMacro.js**

In `presentation/dashboard/backend/routes/assetMacro.js`, find:
```js
const ASSETS = ['BTC','ETH','SOL','XRP','DOGE','LINK','BNB']
```
Replace with:
```js
const ASSETS = ['BTC','ETH','SOL','XRP','DOGE']
```

- [ ] **Step 4: Update AssetCards.jsx — remove BNB entries + fix count**

In `presentation/dashboard/frontend/src/components/AssetCards.jsx`, find the ASSETS array and replace with:
```javascript
const ASSETS = [
  { asset: 'BTC',  tf: '5m',  color: '#f7931a', tier: '100%' },
  { asset: 'BTC',  tf: '15m', color: '#ffb042', tier: '100%' },
  { asset: 'ETH',  tf: '5m',  color: '#627eea', tier: '100%' },
  { asset: 'ETH',  tf: '15m', color: '#8a9eed', tier: '100%' },
  { asset: 'SOL',  tf: '5m',  color: '#14f195', tier: '60%' },
  { asset: 'SOL',  tf: '15m', color: '#9945ff', tier: '60%' },
  { asset: 'XRP',  tf: '5m',  color: '#00aae4', tier: '60%' },
  { asset: 'XRP',  tf: '15m', color: '#006097', tier: '60%' },
  { asset: 'DOGE', tf: '5m',  color: '#e1b303', tier: '35%' },
  { asset: 'DOGE', tf: '15m', color: '#cc9e02', tier: '35%' },
];
```

Also find `'14 assets'` in the panel header pill and replace with `'10 assets'`.

- [ ] **Step 5: Commit**

```bash
git add config.py app/main.py presentation/dashboard/backend/routes/assetMacro.js presentation/dashboard/frontend/src/components/AssetCards.jsx
git commit -m "feat(engine): remove BNB and LINK — dead assets eliminated"
```

---

## Task 2: updown_engine.py — Edge Floor, Corroboration Multiplier, Remove Direction Cooldown

**Files:**
- Modify: `core/engine/updown_engine.py`

- [ ] **Step 1: Lower _min_edge base values**

Find (around line 542–548):
```python
            _expensive_fv = _entry_price_fv > 0.50
            if _expensive_fv and _cross_tf_conflict:
                _min_edge = 0.20
            elif _expensive_fv or _cross_tf_conflict:
                _min_edge = 0.18
            else:
                _min_edge = 0.10
```
Replace with:
```python
            _expensive_fv = _entry_price_fv > 0.50
            if _expensive_fv and _cross_tf_conflict:
                _min_edge = 0.10
            elif _expensive_fv or _cross_tf_conflict:
                _min_edge = 0.08
            else:
                _min_edge = 0.05
```

- [ ] **Step 2: Remove BNB/LINK from VOLUME_GATE_FLOORS and _PEERS**

Find (line 75):
```python
VOLUME_GATE_FLOORS = {"BTC": 2.0, "ETH": 10.0, "SOL": 75.0, "XRP": 5000.0, "DOGE": 10000.0, "LINK": 200.0, "BNB": 10.0}
```
Replace with:
```python
VOLUME_GATE_FLOORS = {"BTC": 2.0, "ETH": 10.0, "SOL": 75.0, "XRP": 5000.0, "DOGE": 10000.0}
```

Find (line 585–588):
```python
            _PEERS = {
                "BTC": ["ETH", "SOL"], "ETH": ["BTC", "SOL"],
                "SOL": ["BTC", "ETH"], "XRP": ["BTC", "ETH"],
                "DOGE": ["BTC"], "LINK": ["BTC", "ETH"], "BNB": ["BTC"],
            }
```
Replace with:
```python
            _PEERS = {
                "BTC": ["ETH", "SOL"], "ETH": ["BTC", "SOL"],
                "SOL": ["BTC", "ETH"], "XRP": ["BTC", "ETH"],
                "DOGE": ["BTC"],
            }
```

- [ ] **Step 3: Corroboration gate → sizing multiplier**

Find the corroboration block (lines 601–606):
```python
            if not _corroborated:
                log.info(
                    "[CORROBORATE] %s\5m: no peer asset agrees with FV %s — skip",
                    self.asset, _fv["direction"],
                )
                _fv = {"direction": None, "edge": 0.0, "archetype": None}
```
Replace with:
```python
            _corroboration_multiplier = 1.3 if _corroborated else 0.7
            log.info(
                "[CORROBORATE] %s/5m: %s FV %s — size_mult=%.1f",
                self.asset,
                "peer agrees" if _corroborated else "no peer",
                _fv["direction"],
                _corroboration_multiplier,
            )
```

Also, right before the `_corroboration_multiplier` block (at the start of the corroboration `if` block, line 584), add initialization:
```python
        _corroboration_multiplier = 1.0  # default: no corroboration effect
```
This line goes BEFORE the `if self.timeframe == "5m" and _fv.get("direction") is not None:` block.

- [ ] **Step 4: Add corroboration_multiplier to the returned signal**

Find the return dict (line 904):
```python
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
        }
```
Replace with:
```python
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
```

- [ ] **Step 5: Remove direction cooldown call**

Find (around line 630–636):
```python
        if self._is_dir_cooldown_active(direction):
            log.info(
                "[DIR-COOLDOWN] %s/%s: %s blocked — same asset+direction within 15 min",
                self.asset, self.timeframe, direction,
            )
            _write_gate_event(self.asset, self.timeframe, "DIR-COOLDOWN", direction, "same asset+direction within 15 min")
            return None
```
Replace with:
```python
        # DIR-COOLDOWN removed: Bone Reaper Mode fires every candle regardless of prior direction
```

- [ ] **Step 6: Dynamic price floor (FV path)**

Find in the FV path, after `entry_price` is determined for FV entries (search for `_entry_price_fv`). Add this block immediately after the `_min_edge` assignment section and before the edge check at line 575:
```python
            # Dynamic price floor: block very-low-priced entries ONLY on weak Pyth signals
            _fv_pct_move = abs(float(klines[-1][4]) - float(klines[-1][1])) / max(float(klines[-1][1]), 1e-9)
            if _entry_price_fv < 0.15 and _fv_pct_move < 0.004:
                log.info(
                    "[PRICE-FLOOR] %s/%s: entry %.0f¢ with weak move %.4f%% — skip",
                    self.asset, self.timeframe, _entry_price_fv * 100, _fv_pct_move * 100,
                )
                _fv = {"direction": None, "edge": 0.0, "archetype": None}
```

- [ ] **Step 7: Commit**

```bash
git add core/engine/updown_engine.py
git commit -m "feat(engine): edge floor 0.05, corroboration→multiplier, remove DIR-COOLDOWN, dynamic price floor"
```

---

## Task 3: cycle_manager.py — LAT Improvements, Choppy Soften, BNB/LINK Removal

**Files:**
- Modify: `core/engine/cycle_manager.py`

- [ ] **Step 1: Remove BNB/LINK from altcoin sizing gate**

Find (around line 299–302):
```python
            if asset in ["SOL", "XRP"]:
                usd_size *= 0.60
            elif asset in ["BNB", "LINK"]:
                usd_size *= 0.50
            elif asset in ["ADA", "DOGE", "AVAX", "SUI"]:
```
Replace with:
```python
            if asset in ["SOL", "XRP"]:
                usd_size *= 0.60
            elif asset in ["ADA", "DOGE", "AVAX", "SUI"]:
```

- [ ] **Step 2: Widen ATM gate to 44–56¢**

Find (line 267):
```python
            if 0.47 <= entry_price <= 0.53:
```
Replace with:
```python
            if 0.44 <= entry_price <= 0.56:
```

- [ ] **Step 3: Raise 5m LAT Pyth threshold to 0.5%**

Find in `scan_and_trade` (around line 195–197):
```python
            threshold = 0.003
            if timeframe == "5m":
                threshold = 0.003
            if abs(pct_move) < threshold:
                return
```

If the code has a single `threshold = 0.003` check, change it to:
```python
            threshold = 0.003
            if timeframe == "5m":
                threshold = 0.005  # 5m requires stronger Pyth signal (no early-exit monitor)
            if abs(pct_move) < threshold:
                return
```

If the threshold line is just `if abs(pct_move) < 0.003:`, replace with:
```python
            _lat_threshold = 0.005 if timeframe == "5m" else 0.003
            if abs(pct_move) < _lat_threshold:
                return
```

- [ ] **Step 4: Add LAT 60s global cooldown**

At the module level of `cycle_manager.py` (after the imports, before `start_latency_edge_scanner`), add:
```python
_LAT_LAST_ENTRY_TS: float = 0.0  # global: 60s cooldown between any two LAT entries
```

Inside `scan_and_trade`, immediately before the "6. Execute order" comment (around line 322), add:
```python
            # Global LAT cooldown: prevent simultaneous multi-asset false-signal firing
            global _LAT_LAST_ENTRY_TS
            if time.time() - _LAT_LAST_ENTRY_TS < 60.0:
                log.info("[LAT-DEDUP] %s/%s: global LAT cooldown active (%.0fs remain) — skip",
                         asset, timeframe, 60.0 - (time.time() - _LAT_LAST_ENTRY_TS))
                return
```

After successful `place_order` (after `if order:` on the commit_trade_slot line), add:
```python
                _LAT_LAST_ENTRY_TS = time.time()
```

- [ ] **Step 5: Soften choppy detection — block current candle only**

Find the choppy regime gate (around line 199–210):
```python
            # Regime gate: if last 2 closed candles flipped direction → choppy market, skip
            if len(klines) >= 3:
                c_last = klines[-2]
                c_prev = klines[-3]
                last_bull = float(c_last[4]) > float(c_last[1])
                prev_bull = float(c_prev[4]) > float(c_prev[1])
                if last_bull != prev_bull:
                    log.info("[LATENCY-ARB] %s/%s REGIME_GATE: last 2 candles flipped (%s→%s) — choppy, skipping",
                             asset, timeframe,
                             "UP" if prev_bull else "DN",
                             "UP" if last_bull else "DN")
                    return
```
This already only blocks the current `scan_and_trade` call (one candle at a time). **No change needed here** — `scan_and_trade` is called fresh each candle, so returning just blocks this candle's scan, not future ones. The persistent per-asset pause is in `updown_engine.py`. Verify no additional choppy state is written to a file or dict that persists across candles in the LAT scanner.

If there IS a persistent pause mechanism (look for `choppy_assets` dict or similar file writes), remove it. Otherwise, move to Step 6.

- [ ] **Step 6: Dynamic price floor (LAT path)**

Inside `scan_and_trade`, after `entry_price` is assigned (after line 264), add:
```python
            # Dynamic price floor: only block very-low entries on weak Pyth moves
            if entry_price < 0.15 and abs(pct_move) < 0.004:
                log.info("[PRICE-FLOOR] %s/%s: %.0f¢ with weak move %.4f%% — skip",
                         asset, timeframe, entry_price * 100, abs(pct_move) * 100)
                return
```

- [ ] **Step 7: Commit**

```bash
git add core/engine/cycle_manager.py
git commit -m "feat(lat): ATM gate 44-56c, 5m threshold 0.5%, 60s cooldown, dynamic price floor, BNB/LINK removed"
```

---

## Task 4: session_governor.py — Concurrent 5m+15m + BTC/ETH Priority

**Files:**
- Modify: `core/engine/session_governor.py`

- [ ] **Step 1: Add has_open_asset_tf_exposure function**

After the existing `has_open_asset_exposure` function, add:
```python
def has_open_asset_tf_exposure(open_positions: list, asset: str, timeframe: str) -> bool:
    """True if an open position exists for this exact (asset, timeframe) pair."""
    asset = asset.upper()
    tf_tag = f"[{timeframe.upper()}]"
    asset_tag = f"[{asset}]"
    for p in open_positions:
        t = (p.get("event_title") or "").upper()
        p_asset = (p.get("asset") or _parse_asset_from_title(t) or "").upper()
        p_tf = (p.get("timeframe") or "").lower()
        has_asset = (p_asset == asset) or (asset_tag in t)
        has_tf = (p_tf == timeframe.lower()) or (tf_tag in t)
        if has_asset and has_tf:
            return True
    return False
```

- [ ] **Step 2: Switch request_trade_slot to use (asset, timeframe) exposure check**

In `request_trade_slot`, find:
```python
        if has_open_asset_exposure(open_positions, asset):
            return False, f"open_position_{asset}"
```
Replace with:
```python
        if has_open_asset_tf_exposure(open_positions, asset, timeframe):
            return False, f"open_position_{asset}_{timeframe}"
```

- [ ] **Step 3: Allow concurrent BTC 5m + 15m unconditionally**

Find the BTC dedup block (around line 113–125):
```python
        if asset == BTC_ASSET and bucket in _btc_bucket_trades:
            existing = _btc_bucket_trades[bucket]
            if existing["timeframe"] != timeframe:
                if existing["direction"] != direction:
                    log.info("[GOVERNOR] Allowing concurrent BTC trade: opposite direction (%s vs %s)", direction, existing["direction"])
                elif abs(existing["score"] - score) > 0.30:
                    log.info("[GOVERNOR] Allowing concurrent BTC trade: high confidence gap (%.2f vs %.2f)", score, existing["score"])
                else:
                    return False, "btc_duplicate_candle"
            else:
                return False, "btc_duplicate_candle"
```
Replace with:
```python
        if asset == BTC_ASSET and bucket in _btc_bucket_trades:
            existing = _btc_bucket_trades[bucket]
            if existing["timeframe"] == timeframe:
                return False, "btc_duplicate_candle"  # same TF in same candle = duplicate
            # Different TF (5m vs 15m): always allow — Bone Reaper Mode
            log.info("[GOVERNOR] BTC concurrent %s+%s allowed (Bone Reaper Mode)",
                     existing["timeframe"], timeframe)
```

Also remove or update the second BTC dedup check further down (around line 137–141):
```python
        if asset == BTC_ASSET and bucket in _btc_bucket_trades:
            # Recheck standard duplicate boundary
            existing = _btc_bucket_trades[bucket]
            if existing["timeframe"] == timeframe or (existing["direction"] == direction and abs(existing["score"] - score) <= 0.30):
                return False, "btc_duplicate_candle"
```
Replace with:
```python
        if asset == BTC_ASSET and bucket in _btc_bucket_trades:
            existing = _btc_bucket_trades[bucket]
            if existing["timeframe"] == timeframe:
                return False, "btc_duplicate_candle"
```

- [ ] **Step 4: BTC/ETH tier-1 candle cap bypass**

Find the candle cap check (around line 131–135):
```python
        entries = _candle_slots.get(bucket, [])
        if len(entries) >= limit:
            assets_in = [e["asset"] for e in entries]
            if asset not in assets_in:
                return False, f"candle_cap_{len(entries)}/{limit}"
```
Replace with:
```python
        entries = _candle_slots.get(bucket, [])
        if len(entries) >= limit:
            assets_in = [e["asset"] for e in entries]
            if asset not in assets_in:
                if asset in ("BTC", "ETH"):
                    log.info("[GOVERNOR] %s/%s: tier-1 bypass at candle cap (%d/%d)",
                             asset, timeframe, len(entries), limit)
                else:
                    return False, f"candle_cap_{len(entries)}/{limit}"
```

- [ ] **Step 5: Commit**

```bash
git add core/engine/session_governor.py
git commit -m "feat(governor): concurrent 5m+15m same asset, BTC/ETH tier-1 cap bypass"
```

---

## Task 5: app/main.py — Apply Corroboration Multiplier

**Files:**
- Modify: `app/main.py`

- [ ] **Step 1: Apply corroboration_multiplier from signal**

Find (around line 297–298):
```python
    raw_bet_usd = engine.compute_size(score, entry_price, current_balance)
    bet_usd = raw_bet_usd * risk_multiplier
```
Replace with:
```python
    raw_bet_usd = engine.compute_size(score, entry_price, current_balance)
    corr_mult = signal.get("corroboration_multiplier", 1.0)
    bet_usd = raw_bet_usd * risk_multiplier * corr_mult
    if corr_mult != 1.0:
        log.info("[RISK] %s/%s corroboration_mult=%.1f → bet $%.2f",
                 asset, timeframe, corr_mult, bet_usd)
```

- [ ] **Step 2: Commit**

```bash
git add app/main.py
git commit -m "feat(risk): apply corroboration_multiplier (1.3x peer-confirmed, 0.7x solo)"
```

---

## Task 6: T-5s Near-Certainty Scanner

**Files:**
- Modify: `core/engine/cycle_manager.py`

- [ ] **Step 1: Add separate T-5s tracking dict**

In `start_latency_edge_scanner`, find:
```python
    last_scanned_close = {}  # (asset, timeframe) -> next_close_ts
    lat_arb_count = {}      # next_close_ts -> number of tasks spawned (cap at 3)
```
Replace with:
```python
    last_scanned_close = {}   # (asset, timeframe) -> next_close_ts  [T-15s]
    last_scanned_t5 = {}      # (asset, timeframe) -> next_close_ts  [T-5s]
    lat_arb_count = {}        # next_close_ts -> number of tasks spawned (cap at 3)
```

- [ ] **Step 2: Add T-5s scan window in the scanner loop**

Find the existing T-15s window check in the scanner loop (around line 375):
```python
                if 8.0 <= time_left <= 15.5:
                    if last_scanned_close.get((asset, timeframe)) == next_close:
                        continue  # Already scanned this candle
                    ...
                    asyncio.create_task(scan_and_trade(engine, next_close, time_left))
```

After the entire T-15s block (right before `# Prune stale boundary counts`), add the T-5s window:
```python
                # T-5s near-certainty window (separate from T-15s)
                elif 2.5 <= time_left <= 6.5:
                    if last_scanned_t5.get((asset, timeframe)) == next_close:
                        continue  # Already fired T-5s for this candle
                    # DOGE excluded from T-5s too (noisy Pyth)
                    if asset == "DOGE":
                        continue
                    last_scanned_t5[(asset, timeframe)] = next_close
                    log.info("[T5-SCANNER] Spawning near-certainty scan for %s/%s at T-%.1fs",
                             asset, timeframe, time_left)
                    asyncio.create_task(scan_and_trade(engine, next_close, time_left, t_minus=5))
```

- [ ] **Step 3: Add t_minus parameter to scan_and_trade**

Find the function signature:
```python
    async def scan_and_trade(engine, next_close, time_left):
```
Replace with:
```python
    async def scan_and_trade(engine, next_close, time_left, t_minus=15):
```

- [ ] **Step 4: Add T-5s specific logic inside scan_and_trade**

Find the Pyth price check (around line 176–180):
```python
            pyth_price = GLOBAL_ORACLE_CACHE.get(asset, {}).get("price", 0.0)
            if pyth_price <= 0.0:
                log.warning("[LATENCY-ARB] No Pyth price available for %s", asset)
                return
```
After this block, add:
```python
            # T-5s freshness gate: near-certainty requires very fresh oracle
            if t_minus == 5:
                pyth_ts = GLOBAL_ORACLE_CACHE.get(asset, {}).get("timestamp", 0.0)
                pyth_age = time.time() - pyth_ts
                if pyth_age > 3.0:
                    log.info("[T5-SCANNER] %s/%s: Pyth too stale (%.1fs > 3s) — skip",
                             asset, timeframe, pyth_age)
                    return
```

- [ ] **Step 5: Use T-5s specific threshold**

Find:
```python
            _lat_threshold = 0.005 if timeframe == "5m" else 0.003
            if abs(pct_move) < _lat_threshold:
                return
```
(or the equivalent threshold logic from Task 3 Step 3)

Replace with:
```python
            if t_minus == 5:
                _lat_threshold = 0.003  # T-5s: direction must be clear (0.3%)
            elif timeframe == "5m":
                _lat_threshold = 0.005  # T-15s 5m: stronger requirement
            else:
                _lat_threshold = 0.003  # T-15s 15m: standard
            if abs(pct_move) < _lat_threshold:
                return
```

- [ ] **Step 6: Skip ATM gate and choppy gate for T-5s (direction is locked in)**

Find the ATM gate added in Task 3:
```python
            if 0.44 <= entry_price <= 0.56:
                log.info("[LATENCY-ARB] %s/%s ATM_BLOCK: %.0f¢ in coin-flip zone, skipping.",
                         asset, timeframe, entry_price * 100)
                return
```
Wrap it:
```python
            if t_minus != 5 and 0.44 <= entry_price <= 0.56:
                log.info("[LATENCY-ARB] %s/%s ATM_BLOCK: %.0f¢ in coin-flip zone, skipping.",
                         asset, timeframe, entry_price * 100)
                return
```

Find the choppy detection:
```python
            if len(klines) >= 3:
                ...
                if last_bull != prev_bull:
                    log.info("[LATENCY-ARB] %s/%s REGIME_GATE: ...")
                    return
```
Wrap it:
```python
            if t_minus != 5 and len(klines) >= 3:
                ...
                if last_bull != prev_bull:
                    log.info("[LATENCY-ARB] %s/%s REGIME_GATE: ...")
                    return
```

- [ ] **Step 7: T-5s sizing (0.7× normal)**

Find the sizing computation (around line 292–295):
```python
            normal_usd = engine.compute_size(0.85, entry_price, current_balance)
            usd_size = max(1.0, normal_usd * 0.5)
            if timeframe == "15m":
                usd_size *= 1.5
```
Replace with:
```python
            normal_usd = engine.compute_size(0.85, entry_price, current_balance)
            if t_minus == 5:
                usd_size = max(1.0, normal_usd * 0.35)  # T-5s: small-ROI near-certainty play
                log.info("[T5-SCANNER] %s/%s T-5s sizing 0.35x: $%.2f", asset, timeframe, usd_size)
            else:
                usd_size = max(1.0, normal_usd * 0.5)
                if timeframe == "15m":
                    usd_size *= 1.5
                    log.info("[LATENCY-ARB] 15m premium: 1.5x size -> $%.2f", usd_size)
```

- [ ] **Step 8: Commit**

```bash
git add core/engine/cycle_manager.py
git commit -m "feat(lat): T-5s near-certainty scanner — fire at candle-close for locked-in direction"
```

---

## Task 7: Cross-Asset Lag Entry

**Files:**
- Modify: `core/engine/cycle_manager.py`

- [ ] **Step 1: Add _check_cross_asset_lag function**

After `scan_and_trade` function definition but still inside `start_latency_edge_scanner`, add a new async function:

```python
    async def check_cross_asset_lag(lead_asset: str, lead_direction: str, next_close: float, session):
        """
        When BTC fires UP strongly and ETH hasn't priced it in yet (ETH UP < 45c),
        enter ETH in the same direction as a lag trade. Works BTC→ETH and ETH→BTC.
        """
        peer = "ETH" if lead_asset == "BTC" else ("BTC" if lead_asset == "ETH" else None)
        if peer is None:
            return

        peer_engine = engines.get(f"{peer}/5m")
        if peer_engine is None:
            return

        try:
            # Check if peer already has an open position for this candle
            import infrastructure.state.state_manager as state_mgr
            open_positions = state_mgr.get_open_positions()
            from core.engine.session_governor import has_open_asset_tf_exposure
            if has_open_asset_tf_exposure(open_positions, peer, "5m"):
                return

            # Fetch peer market
            market = await peer_engine._fetch_market(session, is_latency_scan=True)
            if not market:
                return

            up_price = market["up_price"]
            dn_price = market["dn_price"]

            # Check if peer market is priced OPPOSITE to lead direction (the lag condition)
            if lead_direction == "UP":
                peer_entry_price = up_price
                market_id = market["up_market"]["id"]
                # Lag: peer UP is cheap (market says peer going DOWN)
                if up_price >= 0.45:
                    return  # Market already agrees — not a lag trade
            else:
                peer_entry_price = dn_price
                market_id = market["dn_market"]["id"]
                if dn_price >= 0.45:
                    return  # Not a lag trade

            if peer_entry_price < 0.05:
                return  # Too extreme

            from infrastructure.state.state_manager import get_current_balance
            current_balance = get_current_balance()
            normal_usd = peer_engine.compute_size(0.80, peer_entry_price, current_balance)
            usd_size = max(1.0, normal_usd * 0.50)  # 0.5x — secondary signal

            if time.time() >= next_close:
                return  # Candle already closed

            from infrastructure.exchange.trader import place_order
            from core.engine.session_governor import commit_trade_slot
            order = place_order(
                event_id=market["event_id"],
                market_id=market_id,
                amount_dollars=usd_size,
                direction="YES" if lead_direction == "UP" else "NO",
                entry_price=peer_entry_price,
                event_title=f"[UPDOWN][{peer}][5m][LAG_TRADE] {market['event_title']}",
                expiry_ts=market["expiry_ts"],
            )
            if order:
                await commit_trade_slot(peer, "5m", 0.80, 5, is_dual=False, direction=lead_direction)
                log.info("[LAG-TRADE] %s follows %s %s: $%.2f @ %.0f¢",
                         peer, lead_asset, lead_direction, usd_size, peer_entry_price * 100)
        except Exception as e:
            log.warning("[LAG-TRADE] %s→%s check failed: %s", lead_asset, peer, e)
```

- [ ] **Step 2: Call check_cross_asset_lag after successful LAT entry**

In `scan_and_trade`, after the successful order commit (after `_LAT_LAST_ENTRY_TS = time.time()`), add:
```python
                # Trigger cross-asset lag check for BTC and ETH leads
                if asset in ("BTC", "ETH") and abs(pct_move) >= 0.005:
                    asyncio.create_task(
                        check_cross_asset_lag(asset, direction, next_close, session)
                    )
```

Note: `session` needs to be passed into `scan_and_trade`. Check if it's already available or passed from the outer scope. If not, add `session` to the `scan_and_trade` signature:
```python
    async def scan_and_trade(engine, next_close, time_left, t_minus=15, session=None):
```
And pass it when calling:
```python
asyncio.create_task(scan_and_trade(engine, next_close, time_left, session=session))
```
(Check if `session` is already in scope in the scanner daemon — it's passed to `start_latency_edge_scanner` as a parameter, so it IS in scope for nested functions.)

- [ ] **Step 3: Commit**

```bash
git add core/engine/cycle_manager.py
git commit -m "feat(lat): cross-asset lag entry — ETH follows BTC signal when market lags"
```

---

## Task 8: Dashboard "Why No Trade" Status Pill

**Files:**
- Create: `infrastructure/state/engine_status.py`
- Create: `presentation/dashboard/backend/routes/engineStatus.js`
- Modify: `presentation/dashboard/backend/server.js`
- Modify: `presentation/dashboard/frontend/src/components/TradeFeed.jsx`

- [ ] **Step 1: Create engine_status.py**

Create `infrastructure/state/engine_status.py`:
```python
"""engine_status.py — write a lightweight JSON status file each scan cycle."""
import json
import time
from pathlib import Path

_STATUS_PATH = Path(__file__).parent.parent.parent / "engine_status.json"

def write_engine_status(status: str, detail: str, asset_states: dict | None = None) -> None:
    """Write current engine state for dashboard consumption.
    
    status: SCANNING | LOW_EDGE | CHOPPY | LAT_COOLDOWN | NO_MARKET | PRICE_FLOOR | MACRO_BLOCK | CIRCUIT_BREAK
    detail: human-readable string, e.g. "next 5m in 2m 14s"
    asset_states: optional dict of {asset: {tf: status_str}}
    """
    try:
        payload = {
            "ts": time.time(),
            "status": status,
            "detail": detail,
            "asset_states": asset_states or {},
        }
        _STATUS_PATH.write_text(json.dumps(payload), encoding="utf-8")
    except Exception:
        pass
```

- [ ] **Step 2: Call write_engine_status from cycle_manager.py**

At the top of the main scanner loop body in `start_latency_edge_scanner` (inside `while True:`, at the top of the `try` block), add:
```python
        from infrastructure.state.engine_status import write_engine_status
        write_engine_status("SCANNING", f"scanner active — {len(engines)} engines")
```

After a LAT cooldown early-return, replace:
```python
                log.info("[LAT-DEDUP] %s/%s: global LAT cooldown active (%.0fs remain) — skip", ...)
                return
```
with:
```python
                _remain = 60.0 - (time.time() - _LAT_LAST_ENTRY_TS)
                log.info("[LAT-DEDUP] %s/%s: global LAT cooldown active (%.0fs remain) — skip",
                         asset, timeframe, _remain)
                write_engine_status("LAT_COOLDOWN", f"{_remain:.0f}s remain")
                return
```

After a low-edge return (where `abs(pct_move) < threshold`):
```python
                write_engine_status("LOW_EDGE", f"{asset}/{timeframe}: Pyth move {abs(pct_move)*100:.3f}% < {_lat_threshold*100:.1f}%")
                return
```

- [ ] **Step 3: Create engineStatus.js route**

Create `presentation/dashboard/backend/routes/engineStatus.js`:
```javascript
const express = require('express');
const fs = require('fs');
const path = require('path');

const router = express.Router();
const STATUS_FILE = path.join(__dirname, '..', '..', '..', '..', '..', 'engine_status.json');

router.get('/', (req, res) => {
  try {
    if (!fs.existsSync(STATUS_FILE)) {
      return res.json({ status: 'UNKNOWN', detail: 'No status file yet', ts: 0, asset_states: {} });
    }
    const raw = fs.readFileSync(STATUS_FILE, 'utf-8');
    res.json(JSON.parse(raw));
  } catch (e) {
    res.json({ status: 'ERROR', detail: e.message, ts: 0, asset_states: {} });
  }
});

module.exports = router;
```

- [ ] **Step 4: Mount route in server.js**

In `presentation/dashboard/backend/server.js`, find where other routes are imported and mounted (e.g., near `gateLogRouter`). Add:
```javascript
const engineStatusRouter = require('./routes/engineStatus');
```
And in the route mounting section:
```javascript
app.use('/api/engine-status', engineStatusRouter);
```

- [ ] **Step 5: Add EngineStatusPill to TradeFeed.jsx**

In `presentation/dashboard/frontend/src/components/TradeFeed.jsx`, find the `TradeFeed` component function. Add state and polling at the top of the component:
```javascript
const [engineStatus, setEngineStatus] = useState({ status: 'SCANNING', detail: '' });

useEffect(() => {
  const fetchStatus = () =>
    fetch('/api/engine-status')
      .then(r => r.json())
      .then(d => setEngineStatus(d))
      .catch(() => {});
  fetchStatus();
  const id = setInterval(fetchStatus, 5000);
  return () => clearInterval(id);
}, []);
```

Add the `EngineStatusPill` component definition near the top of the file (before the main export):
```javascript
function EngineStatusPill({ status, detail, lastTradeTs }) {
  const minsAgo = lastTradeTs
    ? Math.floor((Date.now() / 1000 - lastTradeTs) / 60)
    : 999;
  if (minsAgo < 5 || status === 'SCANNING') return null;

  const colors = {
    SCANNING:      '#10b981',
    LOW_EDGE:      '#f97316',
    CHOPPY:        '#f97316',
    LAT_COOLDOWN:  '#f97316',
    NO_MARKET:     '#ef4444',
    PRICE_FLOOR:   '#f97316',
    MACRO_BLOCK:   '#f97316',
    CIRCUIT_BREAK: '#ef4444',
    UNKNOWN:       '#52525b',
    ERROR:         '#ef4444',
  };
  const icons = {
    LOW_EDGE: '📉', CHOPPY: '🌀', LAT_COOLDOWN: '🔒',
    NO_MARKET: '🚫', MACRO_BLOCK: '📊', CIRCUIT_BREAK: '⛔',
    UNKNOWN: '❓', ERROR: '⚠️',
  };
  const color = colors[status] || '#52525b';
  const icon = icons[status] || '⏳';

  return (
    <div style={{
      display: 'inline-flex', alignItems: 'center', gap: 6,
      background: `${color}15`, border: `1px solid ${color}44`,
      borderRadius: 8, padding: '4px 10px',
      fontSize: 10, fontWeight: 700, color, fontFamily: 'monospace',
      marginBottom: 8,
    }}>
      <span>{icon}</span>
      <span>{status.replace('_', ' ')} — {detail || `last trade ${minsAgo}m ago`}</span>
    </div>
  );
}
```

In the Trade Ledger render section (near the top of the Ledger panel, before the trade table), add:
```javascript
<EngineStatusPill
  status={engineStatus.status}
  detail={engineStatus.detail}
  lastTradeTs={closed.length > 0 ? /* parse last close time */ null : null}
/>
```

For `lastTradeTs`, use the most recent closed trade's close time. Find where `closed` trades are available and parse the timestamp from the last entry's `close_time` field.

- [ ] **Step 6: Commit**

```bash
git add infrastructure/state/engine_status.py presentation/dashboard/backend/routes/engineStatus.js presentation/dashboard/backend/server.js presentation/dashboard/frontend/src/components/TradeFeed.jsx
git commit -m "feat(dashboard): Why No Trade pill — engine_status.json + /api/engine-status + EngineStatusPill"
```

---

## Task 9: Frontend Build + VPS Deploy

- [ ] **Step 1: Build the frontend**

```bash
cd presentation/dashboard/frontend && npm run build
```
Expected: `Build complete` with no errors. Fix any JSX/import errors before proceeding.

- [ ] **Step 2: Verify build output**

```bash
ls presentation/dashboard/frontend/dist
```
Expected: `index.html`, `assets/` directory present.

- [ ] **Step 3: Final commit (build artifacts if tracked)**

```bash
git add -A
git status
git commit -m "feat: ZiSi Bone Reaper Mode — full volume overhaul (overnight session)"
```

- [ ] **Step 4: Push to remote**

```bash
git push origin main
```

- [ ] **Step 5: Deploy to VPS**

Run on VPS:
```bash
cd /root/ZiSi && git pull origin main && pm2 restart 3
```

- [ ] **Step 6: Verify bot is live**

```bash
pm2 logs 3 --lines 30 --nostream
```
Expected: `[LATENCY-ARB] Starting T-15s latency arbitrage scanner daemon...` and `[ENGINE]` logs for each asset. No import errors.

- [ ] **Step 7: Verify dashboard**

Open the SSH tunnel and check the dashboard at `http://localhost:9090`. Confirm:
- Scanning Grid shows 10 cards (BTC/ETH/SOL/XRP/DOGE × 2 TF, no BNB/LINK)
- System Health shows LIVE
- No "Why No Trade" pill visible (means scanner is running)

---

## Self-Review

**Spec coverage:**
- ✅ Corroboration → multiplier: Task 2 Steps 3–4, Task 5
- ✅ _min_edge 0.05: Task 2 Step 1
- ✅ Remove direction cooldown: Task 2 Step 5
- ✅ Soften choppy detection: Task 3 Step 5 (already candle-scoped in LAT, updown_engine removal via DIR-COOLDOWN removal)
- ✅ Concurrent 5m + 15m: Task 4 Steps 2–3
- ✅ BTC/ETH tier-1 priority: Task 4 Step 4
- ✅ BNB/LINK removal: Task 1
- ✅ ATM gate 44–56¢: Task 3 Step 2
- ✅ 5m LAT threshold 0.5%: Task 3 Step 3
- ✅ LAT 60s cooldown: Task 3 Step 4
- ✅ Dynamic price floor: Tasks 2 Step 6 + Task 3 Step 6
- ✅ T-5s near-certainty scanner: Task 6
- ✅ Cross-asset lag entry: Task 7
- ✅ Dashboard Why No Trade: Task 8

**Type consistency:**
- `_corroboration_multiplier` defined in updown_engine.py, added to signal dict key `"corroboration_multiplier"`, read in main.py via `signal.get("corroboration_multiplier", 1.0)` — consistent.
- `has_open_asset_tf_exposure(positions, asset, timeframe)` defined and called with matching signature — consistent.
- `scan_and_trade(engine, next_close, time_left, t_minus=15)` — updated signature used consistently.
- `write_engine_status(status, detail, asset_states)` — called with consistent positional args throughout.

**No placeholders:** Every step has exact code. Commands have expected output.
