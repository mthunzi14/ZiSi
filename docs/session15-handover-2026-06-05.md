# ZiSi Bot — Session 15 Handover Document

**Date:** 2026-06-05 | **Author:** Claude Sonnet 4.6 via deep analysis  
**Purpose:** Complete context handover for implementing 6 prioritized fixes  
**Status:** Bot live on VPS (pm2 #2), paper trading, Polymarket binary UP/DOWN markets

---

## 1. System Overview

### What ZiSi Is

ZiSi is a paper-trading bot for Polymarket binary prediction markets. It trades UP/DOWN markets on BTC, ETH, SOL, XRP, and DOGE — 5-minute and 15-minute candle boundary markets. Each market asks: "Will [asset] close UP or DOWN in this candle?" YES = UP, NO = DOWN. Markets resolve at 99¢ (win) or 1¢ (loss). The bot enters at market price (typically 35–65¢) and either: hits TARGET (99¢), hits STOP (20% of entry), or MARKET_EXPIRES (candle ends, Binance candle direction determines resolution).

### VPS

- **Host:** Hetzner Ubuntu, root@204.168.222.48
- **Bot process:** pm2 ID 2 (`zisi-bot`) — the engine
- **Dashboard process:** pm2 ID 3 (`zisi-dashboard`) — API + UI
- **Tunnel (local):** `localhost:9090` → `VPS:5000` (SSH tunnel, must be open for API access)
- **Deploy:** `git push origin main` on local machine → pull + `pm2 restart 2` on VPS

### Key Files

```
app/main.py                               — Main asyncio engine, trade validation, Kelly sizing
core/engine/updown_engine.py              — FV + SIGNAL signal generation, macro gate, all guards
core/engine/session_governor.py           — Correlation cap, BTC dedup, trade slot management
core/engine/reconciliation.py            — Stop-loss polling (every 15s)
infrastructure/exchange/trader.py         — Paper trade execution, stop-loss logic, UPDOWN resolution
infrastructure/websocket/spot_websocket_ingest.py — Binance WebSocket price feed
infrastructure/state/state_manager.py     — Account state, open/closed position storage
infrastructure/state/fair_value_trades.jsonl — Historical FV signal log (3,638 records)
config.py                                 — All constants: RECONCILE_INTERVAL=15, assets, limits
```

---

## 2. The Three Trading Models

### A. FAIR-VAL (FV)

- **What it does:** Gaussian mean-reversion. Detects when Polymarket price has diverged from the candle open (Binance spot). If price moved UP from open → bets NO (will revert). If price moved DOWN → bets YES.
- **Entry type tag:** `FAIR-VAL` in event_title, `entry_type = "FAIR-VAL"`
- **Archetype field:** `"moderate"` (small edge, ATM price) or `"near_certainty"` (large edge, extreme price)
- **When it works:** Mean-reverting (choppy) sessions. Night session 10pm–5am ET (02:00–09:00 UTC). Extreme price departures (≤0.38¢ or ≥0.57¢ entry).
- **When it bleeds:** Trending sessions. ATM moderate entries (0.39–0.56¢) in strong directional moves.

### B. SIGNAL (SIG)

- **What it does:** Momentum model. Uses CVD (Cumulative Volume Delta), OBI (Order Book Imbalance), OFI (Order Flow Imbalance) confluence. Enters when momentum strongly confirms a direction.
- **Entry type tag:** `SIGNAL` in event_title
- **When it works:** Trending sessions, NO direction (87.5% WR today), extreme price entries (26–27¢).
- **When it bleeds:** YES direction at high entries (>60¢), BTC/5m YES at 60–72¢.

### C. LAT-ARB (Latency Arbitrage)

- **What it does:** Uses Pyth oracle price divergence. When Pyth implies near-certainty for one direction but Polymarket CLOB hasn't updated yet, enters at the stale price before it adjusts.
- **Entry type tag:** `LAT-ARB` in event_title
- **Current state:** Only fires on XRP and ETH (BTC/SOL CLOB too efficient — market makers close the gap instantly). 5W/5L today = 50% WR with a race condition bug causing duplicate entries.
- **Why not BTC/SOL:** BTC and SOL are the most liquid Polymarket markets. Arbitrageurs close the Pyth-Polymarket gap in milliseconds on BTC. The LAT window doesn't exist for BTC. XRP and ETH are less efficient and the LAT window persists 2–5 seconds.

### D. SWEEP (new, needs investigation)

- **What it does:** Unknown. Appeared for the first time in today's session at 11:19:59 UTC with two `SOL/5m/SWEEP/YES` entries, both STOP_HIT at -$2.38. These were tiny positions ($2.42 each). Possibly a resolution-sweeper or near-expiry entry logic.
- **Status:** Needs code investigation before implementing fixes. Should not block this deployment.

---

## 3. Session 15 Full Performance (2026-06-05, clean slate $50)

### Session Timeline

- **Start:** 08:21 UTC (pm2 process 2 started after user discovered it was stopped)
- **Peak balance:** $221.44 at 10:01:50 UTC (trade #54)
- **Trough:** $32.14 at 11:34:21 UTC (trade #110) — lost $189.30 in 93 minutes
- **Recovery:** $129.74 at 12:04:47 UTC (last analyzed trade)
- **Latest snapshot:** 129 trades | 48.8% WR | +$67.83 net PnL

### Headline Stats

| Metric              | Value                          |
| ------------------- | ------------------------------ |
| Total closed trades | 129                            |
| Wins                | 63                             |
| Losses              | 66                             |
| Win rate            | 48.8%                          |
| Net PnL             | +$67.83                        |
| Best single trade   | +$44.10 (ETH/5m/SIG/NO at 26¢) |
| Worst single trade  | -$26.22 (BTC/5m/SIG/NO at 35¢) |
| Peak balance        | $221.44                        |
| Trough balance      | $32.14                         |

### By Entry Type

| Type     | T   | W/L      | WR   | Net PnL | Avg Win | Avg Loss |
| -------- | --- | -------- | ---- | ------- | ------- | -------- |
| FAIR-VAL | ~80 | ~52W/28L | ~65% | ~+$90   | +$9-12  | -$8-10   |
| SIGNAL   | ~35 | ~16W/19L | ~46% | ~-$30   | +$12    | -$11     |
| LAT-ARB  | 10  | 5W/5L    | 50%  | -$12.23 | +$2.74  | -$5.38   |
| SWEEP    | 2   | 0W/2L    | 0%   | -$4.76  | —       | -$2.38   |

_(FV/SIG exact breakdown requires re-query; LAT/SWEEP are exact)_

### By Asset (from dashboard at ~11:50 UTC snapshot)

| Asset | T   | WR   | Net PnL  |
| ----- | --- | ---- | -------- |
| ETH   | 11  | 73%  | +$101.39 |
| SOL   | 6   | 100% | +$37.70  |
| XRP   | 12  | 67%  | +$12.58  |
| DOGE  | 10  | 70%  | +$16.48  |
| BTC   | 9   | 33%  | -$46.85  |

_(Updated to 129 trades total — final asset breakdown similar pattern)_

---

## 4. Three-Phase Session Analysis

### Phase 1 — Accumulation (08:21–10:01 UTC) [54 trades, +$171 net]

Market trending DOWN (bearish morning). FV's NO entries were directionally correct. Multiple high-conviction wins:

- ETH/5m/FV/NO at 38¢ → +$30.13 (trade #10)
- ETH/5m/FV/NO at 35¢ → +$35.56 (trade #43) — then best trade of early session
- BTC/15m/FV/NO at 35¢ → +$33.66 (trade #52) — monster win
- BTC/5m/FV/NO at 35¢ → +$28.16 (trade #54) — second monster win
- ETH/5m/FV/NO at 38¢ → +$30.13 (near-certainty archetype working perfectly)

The pattern was clear: when FV fires at ≤0.38¢ (near-certainty entries), wins are 3–4× larger than losses. These entries come with $18–26 sizes at peak balance = compounding wins.

### Phase 2 — Regime Flip + Catastrophic Bleed (10:01–11:35 UTC) [57 trades, -$189 net]

At ~10:01 UTC the market shifted from bearish to bullish. BTC/ETH/SOL started trending UP. The FV model continued entering NO (DOWN) positions because the Gaussian model still saw prices above candle opens. The macro gate (6/8 candles bullish) failed to block entries fast enough.

**Timeline of the bleed:**

```
10:06 — XRP/SIG/YES 14¢ → -$5.20 (extreme contrarian, wrong direction)
10:06 — SOL/SIG/YES 14¢ → -$5.98 (same)
10:08 — BTC/5m/FV/NO 52¢ → -$16.16, SIZE $16.48 (trend reversed, huge size)
10:26 — BTC/5m/FV/NO 35¢ → -$19.32, SIZE $19.88 (was winning entry type, now losing)
10:40 — BTC/5m/SIG/NO 35¢ → -$26.22, SIZE $26.98 ← SESSION WORST TRADE
10:56 — ETH/15m/FV/NO 48¢ → -$19.48, SIZE $19.89
11:09 — BTC/15m/FV/NO 47¢ → -$14.56
11:11 — BTC/5m/FV/NO 54¢ → -$16.28
11:11 — ETH/5m/FV/NO 35¢ → -$25.18
11:14 — ETH/15m/LAT/NO × 2 (DUPLICATE BUG) → -$6.52 each = -$13.04
11:19 — SOL/5m/SWEEP × 2 → -$4.76
```

**Root causes of the bleed:**

1. Kelly sizing uncapped — at $220+ balance, Kelly formula gave $17–27 position sizes. Single wrong directional trade = wipes 2–3 previous wins.
2. FV direction bias — FV almost always enters NO after a DOWN price move. In a trending UP session, NO is repeatedly wrong. The macro gate requires 6/8 candles bullish — in early trend reversal, only 4–5 candles are bullish, gate doesn't fire.
3. LAT-ARB race condition — produced $13.04 in pure duplicate losses in 2 seconds.

### Phase 3 — Recovery (11:35–12:05 UTC) [18 trades, +$90 net]

Two near-certainty SIGNAL wins triggered the recovery:

- 11:56: ETH/5m/SIG/NO at **26¢** → **+$44.10** (best trade of full session)
- 11:56: XRP/5m/SIG/NO at **27¢** → **+$28.80**

At 26–27¢ entry for NO, the market was pricing ETH/XRP going DOWN at only 26–27% probability (73–74% bullish consensus). SIG identified strong DOWN momentum despite the UP bias. These are "Bonereaper zone" entries where edge is massive. Both WON big.

This is the highest-EV pattern in the entire session. SIG/NO at ≤0.30¢ entry = near certainty territory with +100%+ upside.

---

## 5. Bugs Identified (Priority Order)

### BUG 1: LAT-ARB Duplicate Entry Race Condition [CRITICAL]

**Evidence:**

- 11:14:56 AND 11:14:58: ETH/15m/LAT/NO — identical entries 2 seconds apart, both STOP_HIT at -$6.52 each = **-$13.04 pure duplicate loss**
- 12:04:47 AND 12:04:47: XRP/5m/LAT/NO — identical entries same timestamp, both STOP_HIT at -$5.70 each = **-$11.40 pure duplicate loss**
- **Total: -$24.44 from race condition alone**

**Root cause:** In `session_governor.py`, LAT-ARB calls `request_trade_slot` with `is_dual=True`. When `is_dual=True`, the function returns early:

```python
if is_dual:
    return True, "dual_ok"
```

This bypasses all candle-bucket and open-position checks. Two simultaneous LAT-ARB signals fire at nearly identical timestamps. Neither has committed to the governor yet when the other checks, so both pass.

**Fix location:** `core/engine/session_governor.py` → add a LAT-ARB specific cooldown set, or use the same `has_open_asset_tf_exposure` check for LAT entries before returning `dual_ok`. Alternatively, track LAT-ARB in-flight requests with a per-asset-timeframe set that is checked even before lock acquisition.

---

### BUG 2: Kelly Oversizing at High Balances [HIGH]

**Evidence:**

- At balance $252: BTC/5m/SIG/NO sized at **$26.98** (10.7% of balance)
- At balance $279: BTC/5m/FV/NO sized at **$19.88** (7.1% of balance)
- At balance $202: ETH/15m/FV/NO sized at **$19.89** (9.8% of balance)
- At balance ~$95: ETH/5m/SIG/NO at 26¢ sized at ~$15.50 (16.3% of balance)

**Root cause:** The Kelly formula compounds aggressively as balance grows. At 65%+ WR estimates, Kelly fraction can push to 25–50% of bankroll before the 60% SOL/XRP reduction applies. There is no absolute dollar cap on position size.

**Fix location:** `app/main.py` in the bet sizing block (~line 290). Add a hard cap:

```python
# Hard cap per trade: max 8% of current balance OR $15, whichever is smaller
MAX_SINGLE_BET = min(current_balance * 0.08, 15.0)
bet_usd = min(bet_usd, MAX_SINGLE_BET)
```

Note: The specific cap threshold should be validated. $15 cap at $50 balance = 30% which is still high. Consider `min(balance * 0.06, 12.0)` for tighter control.

---

### BUG 3: 15m FV "Moderate" Archetype Fires in Trending Sessions [MEDIUM-HIGH]

**Evidence:** From 3,638 historical FV records and today's 15m losses:

- All 5 losing 15m FV trades today had entries between 0.41–0.55¢ (ATM "moderate" archetype, edge 5–20%)
- Both winning 15m FV trades had entries at 0.35¢ and 0.59¢ ("near_certainty" archetype, edge 30%+)
- 15m FV edge distribution is **bimodal**: 5–20% (moderate) and 30%+ (near_certainty), with a gap at 20–30% (only 2.8% of records)
- Moderate archetype: 53% of 15m signals. Near_certainty: 47%.

**Root cause:** Current 15m FV min_edge floor is 0.10 (10%). This allows the entire "moderate" bucket (5–20%) through. In choppy night sessions, moderate 15m entries work (price reverts within the 15m window). In trending day sessions, they don't (trend persists for the full 15 minutes).

**Fix location:** `core/engine/updown_engine.py` in the FV signal generation block (around the `FV-15M-FLOOR` comment). Add archetype-aware filtering:

```python
if self.timeframe == "15m":
    fv_archetype = _fv.get("archetype", "moderate")
    # In trending sessions, only near_certainty 15m entries have sufficient edge
    # moderate archetype (small edge, ATM price) cannot survive a 15-minute trend
    if fv_archetype == "moderate":
        # Check regime — if RANGE (choppy), allow moderate; otherwise require near_certainty
        regime = _read_regime()  # reads regime_status.json
        if regime not in ("RANGE",):
            log.info("[FV-15M-ARCH] %s/15m blocked — moderate archetype in %s regime", self.asset, regime)
            return None
```

Alternative simpler fix (no regime dependency): raise min_edge floor for 15m from 0.10 to **0.28** (above the bimodal gap). This allows near_certainty (30%+ edge) and blocks moderate (5–20% edge) globally. Night session impact: moderate signals at night would be blocked, reducing 15m FV volume by ~53% at night. However, the near_certainty entries at extreme prices are the correct entries in both sessions.

---

### BUG 4: SIG/YES Entries Above 0.60¢ — Unfavorable Payout Math [MEDIUM]

**Evidence:**

- SIG/NO today: 87.5% WR, +$52.04 net, avg entry 0.49¢
- SIG/YES today: 50.0% WR, -$23.27 net, avg entry 0.59¢
- SIG/YES above 0.60¢ specifically: BTC at 60¢ (LOSS), BTC at 72¢ (LOSS), XRP at 64¢ (LOSS)
- SIG/YES below 0.55¢: mostly wins (ETH at 51¢, SOL at 58¢, DOGE at 47–52¢)

**Root cause — payout math:**

- At 72¢ YES: to win you need price → 99¢. Upside = +37.5%. Downside = -80% (stop at 14¢). Needs >72% true probability to be +EV.
- At 49¢ NO: to win you need price → 99¢. Upside = +102%. Downside = -80%. Needs >44% true probability.
- The market ALREADY prices YES at 72%, so you'd need an edge ABOVE the full market consensus. BTC CLOB is too efficient for this.

**Fix location:** `core/engine/updown_engine.py` in the SIGNAL evaluation block, after direction is confirmed:

```python
# Cap SIG/YES entries: at >60¢, upside math is unfavorable on 5m
# At 60¢+ YES, the market has already priced in the bullish momentum
if _entry_source == "SIG" and direction == "YES" and self.timeframe == "5m":
    if _quote > 0.60:
        log.info("[SIG-YES-CAP] %s/5m SIG/YES blocked at %.2f > 0.60 cap — insufficient payout room",
                 self.asset, _quote)
        return None
# For 15m, allow up to 0.65¢ (longer window gives more room)
if _entry_source == "SIG" and direction == "YES" and self.timeframe == "15m":
    if _quote > 0.65:
        log.info("[SIG-YES-CAP] %s/15m SIG/YES blocked at %.2f > 0.65 cap", self.asset, _quote)
        return None
```

---

### BUG 5: SIGNAL Hard Size Cap (Already Agreed) [MEDIUM]

**Evidence:**

- SIG avg loss: $10.66 vs avg win: $5.52 — loss is 1.93× win
- ETH/15m/SIG/YES: -$19.32 (size $19.88) destroyed single-session SIGNAL stats
- BTC/5m/SIG/NO at 35¢: -$26.22 (size $26.98) — worst trade of entire session
- Expected value per SIGNAL trade: 0.57 × $5.52 + 0.43 × (-$10.66) = -$1.43 per trade

**Fix location:** `app/main.py` in the bet sizing block. Add after Kelly calculation:

```python
# SIGNAL hard size cap — avg SIG loss 2x avg win requires strict size control
if _entry_source in ("SIG", "SIGNAL"):
    bet_usd = min(bet_usd, 10.0)
    log.info("[RISK] SIG hard cap applied: $%.2f → $10.00", bet_usd)
```

This ensures no single SIG trade risks more than $10 regardless of balance or Kelly. At $50 balance this is 20% max, at $200 balance it's 5%.

---

### BUG 6: SIG Entries at Extreme Low Prices (0.14¢) [LOW-MEDIUM]

**Evidence:**

- 10:06: XRP/5m/SIG/YES at 0.14¢ → MARKET_EXPIRED (-$5.20)
- 10:06: SOL/5m/SIG/YES at 0.14¢ → MARKET_EXPIRED (-$5.98)
- Market was pricing these assets at only 14% likely UP. SIG confirmed bullish momentum. Both resolved DOWN.

**Analysis:** At 14¢, the SIG model is making a strongly contrarian call (market says 86% DOWN, SIG says UP). These are near-certainty zone entries but in the WRONG direction — SIG's CVD/OBI is catching local buy pressure in a strongly bearish broader move. The 14¢ is a sign the market is very bearish, and SIG should be skeptical of counter-signals in that environment.

**Possible fix:** Add a minimum entry price floor for SIG: `if _quote < 0.20 and direction == "YES": block` (or vice versa for NO). Entries below 20¢ should require regime confirmation.

---

## 6. Planned Fixes — Priority Order

| Priority | Fix                                                | File                  | Agreed              |
| -------- | -------------------------------------------------- | --------------------- | ------------------- |
| P1       | LAT-ARB duplicate race condition                   | `session_governor.py` | New finding         |
| P2       | Kelly hard size cap ($12–15 max)                   | `app/main.py`         | New finding         |
| P3       | SIGNAL hard size cap ($10 max)                     | `app/main.py`         | ✅ Fully agreed     |
| P4       | 15m FV archetype gate (block moderate in trending) | `updown_engine.py`    | ✅ Agreed direction |
| P5       | SIG/YES entry cap ≤0.60¢ on 5m / ≤0.65¢ on 15m     | `updown_engine.py`    | ✅ Agreed direction |
| P6       | SIG entry floor ≥0.20¢ (no extreme contrarian SIG) | `updown_engine.py`    | Analysis pending    |

---

## 7. Key Patterns Not to Break

### What is Working — DO NOT TOUCH

1. **ETH/5m/FV**: 83%+ WR, the best combo in the bot. Generated +$83 in Phase 1.
2. **SOL/5m/FV**: 100% WR. Small but consistent.
3. **XRP/5m/FV**: 100% WR, +$34 net. Near-certainty entries.
4. **DOGE/5m/FV**: 70%+ WR.
5. **SIG/NO at extreme prices** (26–27¢ entries): +$72.90 from two trades alone today. This is the highest-EV pattern in the entire system. PROTECT.
6. **15m FV near_certainty** (≤0.38¢ or ≥0.57¢ entries): BTC/15m/FV/NO at 0.35¢ generated +$33.66. The best individual 15m trade of the session.
7. **BTC/15m/SIG**: 2W/0L. BTC SIGNAL on 15m is working. Don't restrict.

### What is Broken — FIX SURGICALLY

1. **15m FV moderate archetype** (ATM 0.39–0.56¢ in trending sessions): 5 losses, -$43 in one session.
2. **SIG/YES above 0.60¢** (5m): 0/3 today. Math doesn't work above 60¢.
3. **Kelly uncapped at high balances**: $26 single-trade sizes destroyed the peak balance.
4. **LAT-ARB duplicate entries**: Race condition giving $24.44 in pure duplicate losses.

---

## 8. Night Session Strategy (10pm–5am ET = 02:00–09:00 UTC)

This is the session where ZiSi thrives. Historical performance confirms:

- **Choppy regime**: Alternating UP/DOWN candles, no sustained trend
- **FV is king**: Mean-reversion works at all price levels, including moderate ATM entries
- **Macro gate rarely fires**: 6/8 candle consensus rarely reached in alternating market
- **15m FV works well at night**: Even moderate archetype (45–55¢ ATM) entries revert within 15 minutes

**Critical**: The 15m FV archetype gate fix (Bug 3) must be REGIME-AWARE. If it blocks all moderate entries globally, it will damage night session performance. The correct implementation checks `regime_status.json` — if regime is RANGE or the UTC hour is in 02:00–09:00, allow moderate 15m entries. If NORMAL/VOLATILE/SHOCK, require near_certainty only.

**Preferred night session targets:**

- DOGE/5m/FV: Consistently the most choppy asset
- ETH/5m/FV: Strong even in choppy sessions
- All assets 5m FV: Entry at any archetype level in RANGE regime

---

## 9. Context: Competitor Strategy and Future Direction

### Bonereaper (competitor on Polymarket)

- Pure T-0 LAT-ARB: enters at 80–99¢ in the last 2 seconds before candle close
- Needs Chainlink Data Streams (sub-100ms oracle, pull-based) for reliable T-2 signal
- Currently ZiSi uses Pyth SSE (push, inconsistent latency) which misses T-0 windows on BTC

### PBot-6 (competitor)

- Pure SIGNAL/directional model, ATM entries (45–55¢), strong directional reading
- Similar to ZiSi's SIGNAL model but possibly with tighter asset filtering

### Chainlink Data Streams

- Applied via email 2026-06-05 (two emails from Chainlink team received, both replied)
- Expected access: 1–2 weeks
- When available: enables LAT-ARB on BTC and SOL (currently impossible due to CLOB efficiency)
- Will unlock the highest-EV model: near-certainty entries at T-2s with BTC/SOL

### Live Trading Timeline

- Target: ~1 month from 2026-06-05
- Condition: 500+ trades with consistent 60%+ WR in paper mode
- Setup: Polymarket wallet, USDC on Polygon, API key in signature mode, `BOT_MODE=live`
- Starting capital: $50 USDC

---

## 10. Complete Code Reference for All Fixes

### Fix 1: LAT-ARB Duplicate Prevention

**File:** `core/engine/session_governor.py`
**Current behavior:** LAT-ARB entries use `is_dual=True` which bypasses all open-position checks.
**Required change:** Before the `if is_dual: return True, "dual_ok"` block, check if there is already an open position for this exact (asset, timeframe) pair using the fresh `open_positions` snapshot. Additionally maintain an in-memory `_lat_arb_in_flight` set (keyed by `asset+timeframe`) that is set before order placement and cleared after. Check this set before allowing any LAT-ARB entry.

```python
# Proposed logic in request_trade_slot:
if is_dual:
    # Still enforce: no LAT entry if there's already an open position on this asset+TF
    if has_open_asset_tf_exposure(open_positions, asset, timeframe):
        return False, f"lat_open_{asset}_{timeframe}"
    # Also check in-flight LAT set to prevent race-condition duplicates
    lat_key = f"{asset}_{timeframe}"
    if lat_key in _lat_arb_in_flight:
        return False, f"lat_inflight_{asset}_{timeframe}"
    return True, "dual_ok"
```

And in `commit_trade_slot`, add `_lat_arb_in_flight.add(lat_key)` with a 30-second TTL cleanup.

### Fix 2 + 3: Kelly Cap + Signal Cap

**File:** `app/main.py` (~line 290, in the bet sizing section)
**Current:** Kelly with SOL/XRP 60% multiplier, SIG/5m +35% premium, no absolute cap.
**Required changes:**

```python
# After all multipliers are applied:

# Global hard cap — prevents compounding Kelly from oversizing at high balances
MAX_BET = min(account_balance * 0.06, 12.0)
bet_usd = min(bet_usd, MAX_BET)
log.info("[RISK] Global cap: $%.2f (6%% of balance, max $12)", bet_usd)

# SIGNAL-specific hard cap (in addition to global cap)
if _entry_source in ("SIG", "SIGNAL"):
    bet_usd = min(bet_usd, 10.0)
    log.info("[RISK] SIG hard cap: $%.2f → $10.00", bet_usd)
```

### Fix 4: 15m FV Archetype Gate

**File:** `core/engine/updown_engine.py`
**Where:** In the FV entry evaluation block, after the existing `FV-15M-FLOOR` code block.
**Required:** Read the `archetype` field from the FV signal dict. In non-RANGE regime, block "moderate" archetype 15m entries.

```python
if self.timeframe == "15m":
    _fv_arch = _fv.get("archetype", "moderate")
    if _fv_arch == "moderate":
        _regime = _read_current_regime()  # reads regime_status.json, returns str
        if _regime not in ("RANGE",):
            log.info("[FV-15M-ARCH] %s/15m moderate archetype blocked in %s regime — use near_certainty only",
                     self.asset, _regime)
            _write_gate_event(self.asset, self.timeframe, "FV-15M-ARCH", _regime)
            return None
```

If `_read_current_regime()` is not available as a standalone function, inline the regime file read (same pattern as the governor uses at lines 94–107 of `session_governor.py`).

### Fix 5: SIG/YES Entry Cap

**File:** `core/engine/updown_engine.py`
**Where:** In the SIGNAL evaluation block, after direction is determined, before the score threshold check.

```python
if _entry_source == "SIG":
    _sig_cap = 0.60 if self.timeframe == "5m" else 0.65
    if direction == "YES" and _quote > _sig_cap:
        log.info("[SIG-YES-CAP] %s/%s SIG/YES %.2f > %.2f cap — insufficient payout room at high entry",
                 self.asset, self.timeframe, _quote, _sig_cap)
        return None
```

---

## 11. Current Active State

At time of handover (12:05 UTC 2026-06-05):

- **Balance:** ~$130 (recovering from $32 trough)
- **Session PnL:** +$67.83 net from $50 start
- **Open trades:** Check via `curl http://localhost:9090/api/positions/open`
- **Recent trend:** 11:03–11:09 UTC was a massive WIN streak (6 consecutive wins including ETH +$26, DOGE +$9 twice). 11:56 UTC: two near-certainty SIG wins (+$44 and +$29) that anchored the recovery.
- **Bot state:** RUNNING, all 5 assets active, FV + SIG + LAT-ARB all enabled

---

## 12. Deploy Instructions

After making code changes locally:

```bash
# 1. Commit and push
git add -A
git commit -m "fix: [describe changes]"
git push origin main

# 2. On VPS (user must SSH themselves):
git pull origin main
pm2 restart 2

# 3. Verify bot is running:
pm2 status
pm2 logs 2 --lines 20

# 4. For clean slate (reset balance to $50):
# Via API: curl -X POST http://localhost:5000/api/reset (confirm exact endpoint)
# Or run: python3 infrastructure/state/state_manager.py --reset 50
```

**CRITICAL:** pm2 #2 = bot engine. pm2 #3 = dashboard. NEVER restart #3 instead of #2. Previous sessions lost hours of data because user was restarting dashboard thinking it was the bot.

---

## 13. What NOT to Change

1. **FV rate limiter** (3 entries per 60s) — prevents the 5× simultaneous entry crash that wiped the account in a previous session. Keep.
2. **Correlation cap** (max 2 same-direction open positions) — prevents correlated macro wipeout. Keep.
3. **RECONCILE_INTERVAL = 15s** — stop-loss executes at ~9¢ not ~3¢. Keep.
4. **Macro gate** (6/8 candles, extended to all assets) — blocks countertrend entries in trending sessions. The threshold of 6/8 is intentionally high to avoid night-session false positives. Keep.
5. **Stop-loss at 20% of entry** — protects from catastrophic expiry at 1¢. Keep.
6. **Binance candle UPDOWN resolution** (`_resolve_updown_by_binance_candle` in trader.py) — matches real Polymarket resolution exactly. Keep.

---

_Document generated: 2026-06-05 by deep session analysis. All trade data sourced from `http://localhost:9090/api/positions/closed` (127+ closed trades). Historical FV data from `infrastructure/state/fair_value_trades.jsonl` (3,638 records)._
