# ZiSi Ultra-Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform ZiSi from a profitable-but-volatile 58% WR bot into a consistently compounding machine by capitalizing on proven entry ranges, eliminating structural leaks, and maximizing trade frequency.

**Architecture:** Six independent, stackable fixes applied in order of impact. Each fix is self-contained and testable. No fix depends on a later one. Deploy the 8 pending commits first, then apply fixes on top, then redeploy.

**Tech Stack:** Python 3.11, asyncio, Polymarket CLOB API, Binance WebSocket, Pyth SSE, pm2 (VPS), React dashboard (Vite)

---

## Ground Truth From This Session (136 trades)

These numbers drive every decision in this plan:

| FV Range | Trades | WR | Net P&L | Decision |
|---|---|---|---|---|
| 25–35¢ | 12 | 67% | **+$113.32** | PROTECT — never block |
| 35–50¢ | 28 | 63% | +$33.95 | GROW — better exits |
| <25¢ | 16 | 31% | −$0.46 | KEEP — EV+, reduce size |
| 50–65¢ | 21 | 45% | **−$11.57** | RESTRICT — raise edge bar |
| >65¢ | 5 | 60% | −$6.21 | CAP SIZE — $14.76 bomb |

| Asset | WR | Net | Note |
|---|---|---|---|
| BTC | 69% | +$118 | Profit engine |
| ETH | 50% | **−$24.67** | Fires in wrong range |
| SOL | 60% | +$1 | Solid, low volume |
| XRP | 59% | +$21 | Good |
| DOGE | 48% | +$38 | High variance, works |

LAT-ARB: 67% WR but only +$9.88 net. TARGET_HIT avg $2.97 vs EXPIRED-win avg $0.58. Paper sim artifact — in production all exits are binary (99¢ or 1¢), so real LAT-ARB WR value is much higher.

Full wipeouts (exit at ≤3¢): **30 trades, −$182.61 total damage.** Stopping these is the single highest-leverage change possible.

---

## Files Modified

| File | What changes |
|---|---|
| `core/engine/updown_engine.py` | Remove circuit breaker; FV range-based min_edge; ETH-specific sigma floor; range-based size multiplier |
| `infrastructure/exchange/trader.py` | Early exit stop-loss (20% of entry); delete zombie positions |
| `core/risk/position_sizer.py` | Range-based size scaling (entry price tiers) |
| `infrastructure/state/state_manager.py` | `cleanup_expired_positions()` function |
| `app/main.py` | Remove SIGNAL 5m block (adv 2 revert); keep SIGNAL ≥10¢ gate only |

---

## Phase 0: Deploy Pending Commits (Do on VPS before coding)

The 8 unpushed commits are now on `origin/main`. They add CVD prewarm, T-2s sweeper, T-5s scanner, conflict-skip, and ATM hold. **Deploy them now. Then apply Phases 1–6 on top and redeploy once.**

**CRITICAL: Two items in those 8 commits must be immediately overridden after pull:**

1. The **35¢ FV floor** (`adv 11a` in `bc74662`) blocks entries <35¢. Our data proves 25–35¢ is the best range (+$113.32). Override by removing that floor in Phase 2.
2. The **SIGNAL 5m block** (`adv 2`) blocks all SIG on 5m. Our data shows SIG/5m is 72% WR. Override in Phase 1.

Deploy sequence (run on VPS, not yet):
```bash
cd /root/ZiSi && git pull origin main && pm2 restart 3
```
**Do not run this yet. Run it once after all phases below are committed locally.**

---

## Phase 1: Remove Circuit Breaker + SIG 5m Unblock

**Why first:** ETH/5m was "circuit breaker active" for hours, missing winning candles. The 2-consecutive-loss rule fires on noise. SIG/5m at 72% WR should never be blocked.

### Task 1: Remove Circuit Breaker from UpDownEngine

**File:** `core/engine/updown_engine.py`

- [ ] **Locate the circuit breaker trigger** (line ~278):
```python
def record_outcome(self, won: bool) -> None:
    ...
    if won:
        self.consecutive_losses = 0
    else:
        self.consecutive_losses += 1
        if self.consecutive_losses >= 2:
            self.skip_windows = 2          # ← REMOVE THESE TWO LINES
            self.consecutive_losses = 0    # ← REMOVE THESE TWO LINES
            self.telegram(...)             # ← REMOVE THIS LINE
            log.info("[ENGINE] %s/%s: circuit breaker — skip 2 windows", ...)  # ← REMOVE
```

- [ ] **Replace the `else` block** with just the counter (no skip trigger):
```python
def record_outcome(self, won: bool) -> None:
    self._recent_outcomes.append(won)
    if len(self._recent_outcomes) > 40:
        self._recent_outcomes.pop(0)
    if won:
        self.consecutive_losses = 0
    else:
        self.consecutive_losses += 1
    self._check_inversion()
```

- [ ] **Verify `skip_windows` is never set** — search entire file for `skip_windows =` and confirm only the `__init__` assignment remains:
```python
self.skip_windows: int = 0
```

- [ ] **The `generate_signal` guard at top** (line ~332) checks `if self.skip_windows > 0`. Leave that guard in place — it will never trigger now but is harmless.

- [ ] **Commit:**
```bash
git add core/engine/updown_engine.py
git commit -m "fix(engine): remove 2-loss circuit breaker — fires on noise, costs winning candles"
```

---

### Task 2: Revert SIGNAL 5m Block + Add 10¢ Minimum

**File:** `app/main.py`

The `adv 2` SIGNAL 5m block is in `bc74662`. We must override it after deploying those commits. The correct minimum is 10¢ (not 30¢, not a full 5m block).

- [ ] **Find the SIGNAL 5m block** in `app/main.py`. It looks like:
```python
if signal.get("entry_source") == "SIG" and signal.get("timeframe") == "5m":
    ctx.log_skip("SIGNAL_5M_BLOCK", ...)
    continue
```

- [ ] **Replace with a 10¢ minimum gate** (allow SIG on 5m above 10¢):
```python
if signal.get("entry_source") == "SIG":
    _sig_entry_price = signal["market"].get("up_price", 0) if signal["direction"] == "UP" else signal["market"].get("dn_price", 0)
    if _sig_entry_price < 0.10:
        log.info("[SIG-FLOOR] %s/%s: SIG entry %.0f¢ < 10¢ — market too extreme against signal — skip",
                 signal["asset"], signal["timeframe"], _sig_entry_price * 100)
        ctx.log_skip("SIG_FLOOR_10C", signal["asset"], signal["timeframe"])
        continue
```

- [ ] **Commit:**
```bash
git add app/main.py
git commit -m "fix(signal): replace 5m block with 10c floor — SIG/5m at 72% WR in session data"
```

---

## Phase 2: FV Range Optimization

**Why:** 87% of all FV profit comes from the 25–35¢ range. The 50–65¢ range is actively losing money. The 35¢ floor deployed in `adv 11a` must be removed. We replace it with smarter, range-aware gates.

### Task 3: Remove 35¢ Floor + Add Range-Based Min-Edge

**File:** `core/engine/updown_engine.py`

The 35¢ FV floor sits in the FV edge gate section. Find it by searching for `PRICE-FLOOR` or `0.35 safety threshold`.

- [ ] **Remove the 35¢ floor entirely.** Delete these lines (~line 587):
```python
# DELETE THESE LINES:
if _entry_price_fv < 0.35:
    log.warning(
        "[PRICE-FLOOR] %s/%s: Blocking %s FV entry at %.3f (below 0.35 safety threshold)",
        self.asset, self.timeframe, _fv["direction"], _entry_price_fv
    )
    _fv = {"direction": None, "edge": 0.0, "archetype": None}
```

- [ ] **Replace with range-based min_edge logic** — insert after `_min_edge` is first assigned (around the `_expensive_fv` checks, line ~552):

The existing logic already sets `_min_edge` based on cross-TF conflict and spread. We need to ADD a range-based layer on top of whatever `_min_edge` currently is:

```python
# Range-based minimum edge (replaces flat 35¢ floor)
# Derived from session data: 50-65¢ range has 45% WR (losing), needs higher bar.
# 25-35¢ range has 67% WR (best) — keep open with low bar.
if _entry_price_fv >= 0.50 and _entry_price_fv < 0.65:
    _min_edge = max(_min_edge, 0.12)   # 50-65¢: raise bar, 45% WR currently
    log.info("[FV-RANGE-GATE] %s/%s: %.0f¢ in 50-65c zone — min_edge raised to %.2f",
             self.asset, self.timeframe, _entry_price_fv * 100, _min_edge)
elif _entry_price_fv >= 0.65:
    _min_edge = max(_min_edge, 0.10)   # >65¢: moderate bar, risky size territory
    log.info("[FV-RANGE-GATE] %s/%s: %.0f¢ above 65c — min_edge raised to %.2f",
             self.asset, self.timeframe, _entry_price_fv * 100, _min_edge)
# 25-50¢: use base min_edge (0.05) — this is the profit zone, don't restrict
# <25¢: use base min_edge — high variance but EV+ at small sizes (handled in sizer)
```

- [ ] **Commit:**
```bash
git add core/engine/updown_engine.py
git commit -m "fix(fv): remove 35c floor, add range-aware min_edge — 50-65c raises to 12c, 25-35c protected"
```

---

### Task 4: Range-Based Position Sizing

**File:** `core/risk/position_sizer.py`

Currently the sizer computes Kelly then applies a flat altcoin calibration (60%). We need to add entry-price-based multipliers on top.

- [ ] **Find `calculate()` method** in `position_sizer.py`. It returns a size in USD. After the size is computed (before the `return` statement), add:

```python
# Range-based size scaling — derived from session P&L data
# <25¢: high variance, wins are 4× but 31% WR — use 40% size to control variance
# 25-50¢: the profit zone — full size
# 50-65¢: 45% WR, EV marginal — use 65% size  
# >65¢: late-candle entries, $14.76 bomb risk — use 40% size, hard cap $4
_entry_px = kwargs.get("entry_price", 0.50) if kwargs else 0.50
if _entry_px < 0.25:
    size = size * 0.40
elif _entry_px >= 0.50 and _entry_px < 0.65:
    size = size * 0.65
elif _entry_px >= 0.65:
    size = min(size * 0.40, 4.00)   # hard cap $4 above 65¢

return max(1.00, round(size, 2))
```

- [ ] **Pass `entry_price` into `calculate()`.** Find every call to `sizer.calculate(signal, ev, cat_wt)` in `app/main.py` and add `entry_price`:

```python
# In app/main.py where place_order is called for FV/SIG:
_entry_price = signal["market"].get("up_price", 0.5) if signal["direction"] == "UP" \
               else signal["market"].get("dn_price", 0.5)
size = sizer.calculate(signal, ev, cat_wt, entry_price=_entry_price)
```

- [ ] **Verify the method signature accepts `entry_price`:**
```python
def calculate(self, signal: dict, market: dict, cat_weight: float, entry_price: float = 0.50) -> float:
```

- [ ] **Commit:**
```bash
git add core/risk/position_sizer.py app/main.py
git commit -m "fix(sizer): range-based size multipliers — 50-65c at 65%, >65c capped $4, <25c at 40%"
```

---

## Phase 3: ETH FV — Fire at Same Sweet Spot as BTC

**Why:** ETH FV wins at 34¢ (+$8.51) and 48¢ (+$7.50). ETH FV loses at 41¢, 43¢, 62¢. The pattern is: ETH fires in the 40–65¢ range more than BTC, and loses there. BTC fires in the 25–35¢ zone (the jackpot). We need ETH to behave like BTC in that zone.

**Root cause:** ETH's sigma_frac (ATR-based vol estimate) may be smaller than BTC's, causing the FV model to fire on weaker moves at higher prices. We add an ETH-specific sigma floor and a higher min_edge for ETH in the 40–65¢ zone.

### Task 5: ETH-Specific FV Tuning

**File:** `core/engine/updown_engine.py` — inside `_fair_value_entry()` method

- [ ] **Find `_fair_value_entry()`** (line ~308). It calls `fair_prob_up()` with `sigma_frac`. After `sigma_frac` is calculated:

```python
sigma_frac = (atr / s_0) if s_0 else 0.01
```

- [ ] **Add ETH sigma floor** immediately after that line:
```python
# ETH has tighter ATR estimates but higher real volatility relative to Polymarket pricing.
# Floor prevents FV from firing on micro-moves that aren't real edge for ETH.
if self.asset == "ETH" and sigma_frac < 0.0040:
    sigma_frac = 0.0040
    log.debug("[ETH-SIGMA-FLOOR] ETH sigma_frac floored to 0.0040 (was %.4f)", sigma_frac)
```

- [ ] **Add ETH-specific range gate** in the range-based min_edge block from Task 3, after the general range gates:
```python
# ETH-specific: 40-65¢ range has 0W/3L in session data — require strong edge
if self.asset == "ETH" and 0.40 <= _entry_price_fv < 0.65:
    _min_edge = max(_min_edge, 0.15)
    log.info("[ETH-FV-GATE] ETH %.0f¢ in weak zone (0W/3L historically) — min_edge=0.15",
             _entry_price_fv * 100)
```

- [ ] **Commit:**
```bash
git add core/engine/updown_engine.py
git commit -m "fix(eth): sigma floor 0.004 + 15c min_edge in 40-65c zone — 0W/3L in session data"
```

---

## Phase 4: Wipeout Prevention (Early Exit Stop-Loss)

**Why:** 30 full wipeouts totalling −$182.61. A position that entered at 45¢ and drops to 8¢ is almost certainly going to expire at 1¢. Exiting at 8¢ saves 82% of the stake instead of losing 98%.

**Rule:** If `current_price < entry_price × 0.20`, exit immediately. This fires when a position loses 80% of its entry value — almost always a confirmed wrong-direction trade.

### Task 6: Add 80% Price-Based Stop-Loss in check_and_close

**File:** `infrastructure/exchange/trader.py` — inside `check_and_close()` function

- [ ] **Find `check_and_close()`** — it iterates over `_open_positions` and checks exit conditions. Find the section that checks `current_price` for each position.

- [ ] **Add the stop-loss check before the expiry check:**
```python
# Early exit: if price dropped to < 20% of entry, position is almost certainly wrong.
# Exit now to save 80% of remaining stake vs waiting for 1¢ resolution.
_stop_threshold = pos.get("entry_price", 0.5) * 0.20
if current_price <= _stop_threshold and current_price > 0.01:
    log.info(
        "[STOP-LOSS] %s: price %.0f¢ <= 20%% of entry %.0f¢ — early exit, saving %.0f%%",
        pos_id, current_price * 100, pos.get("entry_price", 0) * 100,
        (current_price / max(pos.get("entry_price", 0.01), 0.01)) * 100
    )
    realized_pnl = round((current_price * shares) - cost_basis, 4)
    _close_position(pos_id, current_price, realized_pnl, "STOP_LOSS")
    continue
```

- [ ] **Add `STOP_LOSS` to the exit reason lookup** in `trader.py` if there's an exit reason enum or dict (search for `TARGET_HIT` to find the pattern, add `STOP_LOSS` alongside it).

- [ ] **Verify `_close_position` signature** accepts an `exit_reason` string parameter — trace that function and confirm or add the parameter.

- [ ] **Commit:**
```bash
git add infrastructure/exchange/trader.py
git commit -m "fix(trader): add 80% drawdown stop-loss — exits at 20% of entry, saves avg 80% of stake on wipeout trades"
```

---

## Phase 5: Delete Zombie Positions

**Why:** Two expired positions (BTC/15m from 5h ago, DOGE/15m from 3h ago) are sitting in `active` with `expiry_ts` in the past. They will NEVER close via normal logic and distort all live P&L calculations. Delete them completely — no history entry.

### Task 7: Zombie Cleanup Function

**File:** `infrastructure/state/state_manager.py`

- [ ] **Add `cleanup_expired_positions()` function:**
```python
def cleanup_expired_positions() -> int:
    """Delete positions whose expiry_ts has passed > 90 seconds ago. No history entry."""
    import time as _time
    now = _time.time()
    cutoff = now - 90.0
    
    with GLOBAL_POSITIONS_LOCK:
        data = _load_positions_file()
        active = data.get("active", [])
        before = len(active)
        
        zombies = [p for p in active if p.get("expiry_ts", float("inf")) < cutoff]
        active = [p for p in active if p.get("expiry_ts", float("inf")) >= cutoff]
        
        if zombies:
            for z in zombies:
                log.info("[ZOMBIE-CLEAN] Deleted expired position %s (%s, expiry was %ds ago)",
                         z.get("order_id", "?")[:12],
                         z.get("event_title", "")[:40],
                         int(now - z.get("expiry_ts", now)))
            data["active"] = active
            data["summary"]["active_count"] = len(active)
            _save_positions_file(data)
        
        return len(zombies)
```

- [ ] **Call it from the main loop** — in `app/main.py`, find the reconciliation or heartbeat loop and add a periodic zombie cleanup call every 5 minutes:
```python
# Near the top of the main async loop or in reconciliation_loop:
_last_zombie_clean = 0.0

async def _zombie_cleanup_loop():
    while True:
        await asyncio.sleep(300)  # every 5 minutes
        deleted = cleanup_expired_positions()
        if deleted:
            log.info("[MAIN] Cleaned %d zombie positions", deleted)
```

- [ ] **Start the task in `main()`** alongside other daemon tasks:
```python
asyncio.create_task(_zombie_cleanup_loop())
```

- [ ] **Also clean up immediately at startup** — add one call in `main()` right after `initialize_state()`:
```python
_cleaned = cleanup_expired_positions()
if _cleaned:
    log.info("[STARTUP] Deleted %d zombie positions from prior session", _cleaned)
```

- [ ] **Commit:**
```bash
git add infrastructure/state/state_manager.py app/main.py
git commit -m "fix(state): zombie position cleanup — deletes expired positions every 5min + at startup"
```

---

## Phase 6: LAT-ARB Volume Fix (Fire on Every Candle)

**Why:** LAT-ARB has 67% WR — the best WR of all strategies. But only 43 trades vs 82 FV. Many candles are missed because `_fetch_market()` returns `None` (no L2 book found). DOGE specifically shows "no valid L2 book" on every single cycle.

**Root cause:** The market slug lookup times out in 4 attempts. DOGE markets on Polymarket are sometimes thin or not active at certain hours.

### Task 8: Fix L2 Book Retry + DOGE Handling

**File:** `core/engine/updown_engine.py` — in `_resolve_l2_prices()` method

- [ ] **Find the retry logic** in `_resolve_l2_prices()`. It makes `attempts = 2 if is_latency_scan else 4` attempts with `await asyncio.sleep(0.5 if is_latency_scan else 1.0)`.

- [ ] **For the latency scan path, increase attempts to 3** (from 2) with a 0.3s sleep:
```python
attempts = 3 if is_latency_scan else 5
_sleep = 0.3 if is_latency_scan else 0.8
```

- [ ] **Find `_fetch_market()`** — it constructs a slug like `btc-updown-5m-{boundary_ts}`. If the slug returns no results, it logs "no valid L2 book" and returns None.

- [ ] **Add fallback: try the NEXT boundary if current fails.** After the first slug fails, compute next candle's boundary and try that:
```python
# If current candle slug fails, try next candle boundary (market might publish early)
if market is None and not is_latency_scan:
    _next_boundary = boundary_ts + interval_secs
    _next_slug = f"{self.asset.lower()}-updown-{self.timeframe}-{_next_boundary}"
    market = await self._fetch_market_by_slug(session, _next_slug)
    if market:
        log.info("[MARKET-FALLBACK] %s/%s: using next-boundary slug %s", self.asset, self.timeframe, _next_slug)
```

- [ ] **For DOGE specifically:** DOGE markets on Polymarket are often inactive or stale. Add a DOGE L2 failure counter — after 3 consecutive failures for DOGE in one session, log a warning and skip DOGE for 30 minutes rather than spamming logs:

```python
# In _fetch_market(), before the "no valid L2 book" log:
_DOGE_CONSECUTIVE_MISS = getattr(self, '_doge_miss_count', 0)
if self.asset == "DOGE" and market is None:
    self._doge_miss_count = _DOGE_CONSECUTIVE_MISS + 1
    if self._doge_miss_count > 3:
        if self._doge_miss_count % 12 == 4:  # log once per ~hour
            log.warning("[DOGE-INACTIVE] DOGE Polymarket market appears inactive this session")
        return None  # silently skip
else:
    self._doge_miss_count = 0
```

- [ ] **Commit:**
```bash
git add core/engine/updown_engine.py
git commit -m "fix(market): 3-attempt latency scan, next-boundary fallback, DOGE inactive suppression"
```

---

## Phase 7: Kelly Sizing — Use Rolling Actual WR

**Why:** Kelly currently assumes 90% WR. Session actual WR is 57–65% (rolling). When balance is $150+ and Kelly fires at $12–15 sizes in the 50–65¢ range, one wipeout is catastrophic. Phase 4 (stop-loss) + Phase 2 (range sizing) fix the SYMPTOMS. This fixes the ROOT CAUSE.

**The data:** Last 40 trades = 65% WR. All-time = 57.4% WR. The Kelly should use the actual rolling WR, not 90%.

### Task 9: Wire Actual WR into Position Sizer

**File:** `core/risk/position_sizer.py`

- [ ] **Find where `WR=0.90` or `win_rate=0.90` is hardcoded** in the Kelly calculation. Search for `0.90` or `0.9` in position_sizer.py.

- [ ] **Replace the hardcoded WR** with a parameter:
```python
def calculate(self, signal: dict, market: dict, cat_weight: float,
              entry_price: float = 0.50, actual_wr: float = 0.90) -> float:
    ...
    # Replace hardcoded WR:
    # OLD: win_rate = 0.90
    # NEW:
    win_rate = max(0.52, min(0.90, actual_wr))  # clamp: never below 52%, never above 90%
    ...
```

- [ ] **In `app/main.py`**, pass the engine's actual rolling WR when calling the sizer. The `UpDownEngine` instance has `_recent_outcomes` (list of bool, last 40 trades). Access it:
```python
# When calling sizer.calculate():
_engine = ctx.get_engine(signal["asset"], signal["timeframe"])
_actual_wr = 0.65  # fallback
if _engine and len(_engine._recent_outcomes) >= 10:
    _actual_wr = sum(_engine._recent_outcomes) / len(_engine._recent_outcomes)
size = sizer.calculate(signal, market, cat_wt,
                       entry_price=_entry_price,
                       actual_wr=_actual_wr)
```

- [ ] **Commit:**
```bash
git add core/risk/position_sizer.py app/main.py
git commit -m "fix(kelly): use actual rolling WR from engine history instead of hardcoded 90%"
```

---

## Deployment Sequence

Once all 7 tasks above are committed:

```bash
# 1. Push all commits to GitHub
git push origin main

# 2. On VPS: pull, rebuild dashboard, restart
ssh root@204.168.222.48
cd /root/ZiSi && git pull origin main
cd presentation/dashboard/frontend && npm run build
cd /root/ZiSi && pm2 restart 3

# 3. Clean slate for new optimized session
python3 miscellaneous/clean_slate.py --force --balance 50

# 4. Watch for confirmation:
pm2 logs 3 --lines 50
# Should see:
# [STARTUP] Deleted N zombie positions
# [CVD-PREWARM] BTC: loaded N trades
# [HFT-WS] Connected — CVD+OBI+OFI live
# [LATENCY-ARB] Starting T-15s latency arbitrage scanner daemon...
# NO circuit breaker logs
# NO "no valid L2 book" spam for BTC/ETH
```

---

## What to Watch For (Post-Deploy)

**Good signs (fixes working):**
- `[STOP-LOSS]` entries in logs — wipeouts being caught early
- `[FV-RANGE-GATE]` logs for 50–65¢ entries — edge bar raised
- `[ETH-FV-GATE]` logs — ETH gated in 40–65¢ zone
- `[ZOMBIE-CLEAN]` at startup — stale positions gone
- `[SIG-FLOOR]` logs for <10¢ entries — extreme entries blocked
- ETH/5m NO LONGER showing "circuit breaker active" in logs
- More trades per hour (circuit breaker removal + L2 retry fix)

**Red flags (something wrong):**
- Any FV entry in 50–65¢ range with size > $8 — Phase 4 not deployed
- ETH full wipeout at >$7 size — Phase 3 or 5 not working
- Circuit breaker log appearing — Phase 1 didn't commit cleanly
- `[PRICE-FLOOR]` blocking <35¢ entries — the old floor wasn't removed

---

## Chainlink Data Streams (Apply Now, Code Later)

Bone Reaper uses Chainlink for the resolution feed — sees resolution price 1–2 seconds before Polymarket closes the market. This is the final edge gap between ZiSi and Bone Reaper.

**Apply for access now** (takes 1–2 weeks):
1. Go to: `https://chain.link/data-streams`
2. Click "Get Access" / "Apply for API"
3. Request: BTC/USD and ETH/USD Data Streams feeds
4. Use case: "Prediction market resolution price monitoring for Polymarket binary markets"

Once approved, we add a `chainlink_oracle_service.py` alongside `pyth_oracle_service.py`. The Chainlink price becomes the secondary confirmation signal — if Pyth says UP but Chainlink says DOWN at T-5s, block the entry.

---

## Expected Impact

| Metric | Current | Post-Optimization |
|---|---|---|
| 5m FV WR | 63% | 65–68% (ETH fix + range gate) |
| Full wipeouts | 30 per 136 trades | ~12–15 (stop-loss catches 50%) |
| ETH net P&L | −$24.67 | +$5–15 (fires in 25–40¢ zone only) |
| Trade volume | ~136/session | ~160–180/session (no circuit breaker) |
| Avg loss size in 50–65¢ | $7.51 | $4.88 (65% size multiplier) |
| Session peak → trough | $18 drawdown | <$10 (stop-loss + ETH fix) |

The compounding rate stays the same or improves. The circuit breaker removal increases volume. The sizing fix reduces catastrophic loss events without reducing wins (wins use full size or bigger in the 25–35¢ range).
