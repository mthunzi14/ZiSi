# ZiSi Deep Fix — Session 9 Design Spec

**Date:** 2026-05-20  
**Status:** Approved  
**Goal:** Fix all critical bugs causing $0.5000 entry prices, $0.00 P&L false-losses, 0 Kalshi trades, stale unrealized P&L, wrong direction labels, and incorrect position sizing. Precede all fixes with a clean slate.

---

## Problem Summary

From 21 closed trades observed:
- 10 of 21 trades show `$0.00 P&L` and are counted as **LOSS**
- All entries are at exactly `$0.5000` — not real market prices
- Kalshi has **0 trades** despite 166 matched signals (`166P + 0K`)
- Dashboard unrealized P&L never moves (stale file read, not live)
- Direction shows `YES`/`NO` on "Up or Down" markets instead of `UP`/`DOWN`
- Balance and state files carry stale history

---

## Root Causes

### RC1 — Entry Price Always $0.5000
**File:** `data_fetcher.py:898–918`, `trader.py:436–453`

`data_fetcher.py` sanitizes `lastTradePrice` and `outcomePrices[0]` to `None` when ≥0.90 or ≤0.10. The fallback `float(mkt.get("price") or 0.5)` returns 0.5 when `price` is absent. In `execute_trade_smart`, event-level `bid`/`ask` fields don't exist on the event object from `fetch_polymarket_events`, so `mid_price` always falls back to `market.get("price", 0.5)` = 0.5.

### RC2 — $0.00 P&L Counted as LOSS
**File:** `trader.py:1087`, `kalshi/trader.py:776`

Loss counter uses `<= 0` not `< 0`. Zero-P&L trades (entry == exit == 0.5000) are miscounted as losses. The zero exit price is itself caused by RC1 (live CLOB fetch fails because `market_id` is sometimes the Gamma ID not the CLOB token ID).

### RC3 — EXIT_REASON Label "PAPER_AUTO_EXIT"
**File:** `trader.py:698`

`TIME_EXPIRED` exits log as `[PAPER-AUTO-EXIT]` which is meaningless in a live-simulation context. Exit reason should be `MARKET_EXPIRED` or `TIME_EXPIRED` only — no "PAPER" prefix on displayed labels.

### RC4 — YES/NO Instead of UP/DOWN
**File:** `trader.py:971–985` (`persist_positions`)

Direction is stored and displayed as `YES`/`NO` for all markets including "Up or Down" markets where `YES` = price went UP and `NO` = price went DOWN. The dashboard shows the raw value without translation.

### RC5 — Kalshi 0 Trades
**File:** `kalshi/matcher.py:196–304`, `kalshi/matcher.py:327–417`

Chain of failures:
1. `match_signal_to_events` requires `best_score >= 1.0` meaning a word from the `implications[]` list must appear verbatim in the Kalshi event title
2. For BTC/ETH price-range markets (e.g. `"Bitcoin price range on May 20, $103k-$104k?"`), the implications list contains `"fomc"`, `"federal funds"`, `"rate cut"` etc. — none of which appear in price-range titles
3. `match_price_range_markets` (the correct code path for these markets) is only called for `BULLISH`/`BEARISH` sentiments, not `NEUTRAL`
4. Confidence threshold on Kalshi is 0.6 — too high for macro-correlated signals
5. Signal confidence comes in on a 0–10 scale and gets divided by 10, producing values like 0.57 that barely reach 0.6

### RC6 — Stale Unrealized P&L
**File:** `dashboard/backend/routes/events.js`

The SSE endpoint reads `positions_state.json` and broadcasts it every 5s. `positions_state.json` is only updated when `refresh_open_position_prices()` runs inside the main bot loop cycle (every ~5–10 min). Active positions show `current_price` = `entry_price` for the entire hold duration.

### RC7 — Position Sizing USD→Shares Rounding
**File:** `trader.py:290`, `execute_trade_smart:447`

`shares = round(amount_dollars / entry_price, 4)` introduces rounding drift at low prices. Polymarket works in whole shares. Should round to integer shares first, then derive actual cost.

---

## Fix Plan (7 Groups)

### Group 1 — Clean Slate (Execute First)

**What:** Archive all state files, reset balance to $100.

**Files touched:**
- Create `clean_slate.py` execution (already exists — run it)
- `account_state.json` → reset `balance=100`, `pnl=0`, `starting_balance=100`
- `positions_state.json` → reset to empty active/closed arrays
- `zisi_local_trades.jsonl` → archive and truncate
- `signal_evaluations.jsonl` → archive and truncate
- `markov_state.json` → archive and delete
- `runtime_tracking.json` → delete (will be recreated on next start)

**Implementation:** Run existing `clean_slate.py` with archive flag. Verify all state files show $100 balance and 0 trades before proceeding.

---

### Group 2 — Fix Entry Prices (RC1)

**What:** Replace the 0.5 fallback with a live CLOB price fetch at order placement time.

**Files:** `trader.py`, `data_fetcher.py`

**`trader.py` — `execute_trade_smart()`:**
```python
# After selecting market_id, fetch real live CLOB price
from data_fetcher import get_event_current_price
price_data = get_event_current_price(market_id)
if price_data and 0.02 < price_data.get("price", 0) < 0.98:
    bid = price_data.get("bid", price_data["price"] - 0.01)
    ask = price_data.get("ask", price_data["price"] + 0.01)
    mid_price = round((bid + ask) / 2, 4)
else:
    mid_price = float(market.get("price", 0.5))
    if mid_price <= 0.02 or mid_price >= 0.98:
        log.warning("[SMART-EXEC] No valid price for %s — skipping", market_id)
        return None  # refuse to trade at unknown price
```

**`data_fetcher.py` — market parsing:**
- Widen sanitization threshold from `>= 0.90 / <= 0.10` to `>= 0.97 / <= 0.03`
- Only reject prices that are essentially already resolved; keep lopsided-but-active prices (e.g. 0.85 YES = real market price, not sanitize-worthy)

**`get_event_current_price()` — CLOB token ID fix:**
- The CLOB API uses `token_id` (from `mkt.conditionId` or `mkt.id`) not the Gamma event ID
- In `execute_trade_smart`, use `market.get("conditionId") or market.get("id")` as the market_id for CLOB calls
- Polymarket CLOB endpoint: `GET /markets/{token_id}` — already implemented, just needs the right ID

---

### Group 3 — Fix Zero-PnL = LOSS (RC2)

**What:** Change all `<= 0` loss comparisons to `< 0`. Introduce `BREAKEVEN` as a distinct outcome.

**Files:** `trader.py`, `kalshi/trader.py`

**`trader.py:1087`:**
```python
"loss_count": sum(1 for p in merged_closed if (p.get("realized_pnl") or 0) < 0),
```

**`trader.py:879` — `execute_exit()`:**
```python
outcome = "✅ WIN" if profit > 0 else ("⚖️ BREAKEVEN" if profit == 0 else "❌ LOSS")
```

**`kalshi/trader.py:776`:**
```python
losses = sum(1 for p in merged_closed if (p.get("realized_pnl") or 0) < 0),
```

**`kalshi/trader.py:713` — `get_kalshi_summary()`:**
```python
losses = [p for p in closed_pos if (p.get("realized_pnl") or 0) < 0 and p.get("close_time")]
```
(already `< 0` here — just verify)

**Exit reason label cleanup:**
- Change `"PAPER_WIN"` / `"PAPER_LOSS"` exit reasons → `"MARKET_WIN"` / `"MARKET_LOSS"`
- Log line at `trader.py:698`: remove `[PAPER-AUTO-EXIT]` prefix → use `[TIME-EXIT]`

---

### Group 4 — Fix Direction Labels (RC4)

**What:** For "Up or Down" markets, store and display UP/DOWN not YES/NO.

**Files:** `trader.py` (`persist_positions`), `execute_trade_smart`

**`execute_trade_smart()` — at direction assignment:**
```python
_is_updown_market = "up or down" in _ev_title.lower() or "updown" in _ev_title.lower()
if _is_updown_market:
    direction = "UP" if sentiment == "bullish" else "DOWN"
else:
    direction = "YES" if sentiment == "bullish" else "NO"
```

This propagates correctly through `place_order` → `_open_positions` → `persist_positions` → dashboard without any further translation needed.

**`kalshi/trader.py` — `execute_trade()`:**
Already stores `"YES"` / `"NO"`. Kalshi markets are genuinely YES/NO binary, so leave as-is.

---

### Group 5 — Fix Kalshi 0 Trades (RC5)

**What:** Unblock the Kalshi trade pipeline at every choke point.

**Files:** `kalshi/matcher.py`, `markets_orchestrator.py`

**`kalshi/matcher.py` — `match_with_category_filter()`:**
- Lower `confidence_threshold` parameter default: `0.6 → 0.45`
- Call `match_price_range_markets` for ALL sentiments (remove `if sentiment not in ("BULLISH", "BEARISH")` guard)

**`kalshi/matcher.py` — `CRYPTO_TO_MACRO` dict:**
- Add `"price range"` and `"bitcoin price"` and `"ethereum price"` as implications for BTC/ETH signals so they pass `match_signal_to_events`

**`kalshi/matcher.py` — `_is_macro_eligible()`:**
- Add `"price range"` to `_MACRO_WHITELIST` frozenset
- Add `"price range"` to `_EXPLICIT_FINANCE_TERMS` frozenset

**`kalshi/matcher.py` — `match_price_range_markets()`:**
- Call for `NEUTRAL` sentiment too (currently skips neutral)
- For neutral: pick brackets nearest to current price (±2% band)

**`markets_orchestrator.py`:**
- Verify `MAX_KALSHI_TRADES_PER_CYCLE = 30` is per-cycle not per-signal (it is — just confirm)
- Ensure the confidence normalization (÷10 for 10-scale signals) happens before the threshold check, not after

---

### Group 6 — Live Unrealized P&L (RC6)

**What:** SSE endpoint fetches fresh CLOB prices inline instead of reading stale file.

**Files:** `dashboard/backend/routes/events.js`

**Architecture change:**
- In the SSE broadcast function, after reading `positions_state.json`, for each active Polymarket position: call `https://clob.polymarket.com/markets/{market_id}` to get `bestBid`/`bestAsk`/`lastTradePrice`
- Compute `current_price = (bestBid + bestAsk) / 2`
- Recompute `unrealized_pnl = shares * current_price - cost`
- This runs every 5s (SSE tick) — keep per-request timeout at 2s; on timeout use file value
- For Kalshi: call Kalshi REST `/markets/{ticker}` similarly
- Cache results with a 3s TTL so a 5s SSE tick hits the API once then serves the cache on the next tick — prevents fanning out 10 simultaneous CLOB calls per tick

**Implementation note:** Use Node.js `fetch` (native in Node 18+). The backend `server.js` already runs on Node — no new dependencies needed.

---

### Group 7 — Shares-First Position Sizing (RC7)

**What:** Compute shares as integer first, then derive cost.

**Files:** `trader.py` (`place_order`, `execute_trade_smart`)

**`place_order()`:**
```python
# Shares-first: avoids USD→shares rounding drift (pbot pattern)
shares = max(1, round(amount_dollars / entry_price))   # integer shares
actual_cost = round(shares * entry_price, 4)           # true cost
```
Use `actual_cost` as `amount_spent`, `shares` as `shares_acquired`.

**`execute_trade_smart()`:** pass `position_size` (USD) unchanged; `place_order` handles the shares calculation.

---

## Data Flow After Fixes

```
Signal (bullish/bearish) 
  → execute_trade_smart()
      → select market (YES token for UP, NO token for DOWN)
      → CLOB API: GET /markets/{token_id}  ← NEW: real bid/ask
      → mid_price = (bid + ask) / 2        ← real entry price
      → direction = UP / DOWN              ← NEW: for updown markets
      → shares = round(USD / price)        ← shares-first
      → place_order() → _open_positions    ← stored with real price
  → persist_positions() → positions_state.json

Dashboard SSE (every 5s):
  → read positions_state.json
  → for each open position: CLOB API live price  ← NEW: fresh P&L
  → broadcast with real current_price + unrealized_pnl

Kalshi pipeline:
  → KalshiEventMatcher.match_with_category_filter(threshold=0.45)
      → price_range_markets matched for all sentiments  ← NEW
      → "price range" in MACRO_WHITELIST                ← NEW
      → diverse[:8] returned → execute_trade()
      → real entry price from yes_ask/no_ask
```

---

## Success Criteria

| Metric | Before | After |
|--------|--------|-------|
| Entry prices | Always $0.5000 | Real CLOB bid/ask mid |
| Zero-P&L trades | Counted as LOSS | Counted as BREAKEVEN (not loss) |
| Kalshi trades per cycle | 0 | ≥3 per cycle target |
| Trades per cycle (combined) | Sporadic / 0 Kalshi | ≥20 Poly+Kalshi combined |
| Win rate target | 33% (current) | ≥85% via signal quality + price-range gates |
| Unrealized P&L refresh | Every 5–10 min | Every 5s via SSE live fetch |
| Direction on Up/Down | YES/NO | UP/DOWN |
| Balance at start | $109.22 | $100.00 (clean slate) |

---

## Files Modified

| File | Change Group |
|------|-------------|
| `trader.py` | 2, 3, 4, 7 |
| `data_fetcher.py` | 2 |
| `kalshi/matcher.py` | 5 |
| `kalshi/trader.py` | 3 |
| `markets_orchestrator.py` | 5 |
| `dashboard/backend/routes/events.js` | 6 |
| State files (clean) | 1 |

---

## Out of Scope

- Live trading mode (stays paper)
- ML pipeline changes
- New data sources
- Telegram bot changes
- Project folder restructuring (separate task)
