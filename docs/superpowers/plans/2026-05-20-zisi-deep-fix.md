# ZiSi Deep Fix — Session 9 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix all 7 critical bugs causing $0.5000 entry prices, $0.00 P&L false-losses, 0 Kalshi trades, stale unrealized P&L, wrong direction labels, and incorrect position sizing — preceded by a clean slate reset to $100.

**Architecture:** Execute in strict group order (1→7). Groups 2–4 all touch `trader.py` and are batched into one commit. Group 5 is self-contained in `kalshi/matcher.py`. Group 6 is purely the Node.js SSE layer. Group 7 finishes position sizing in `trader.py`.

**Tech Stack:** Python 3.11 (bot), Node.js 18 (dashboard backend), Polymarket CLOB REST API, Kalshi REST API v2

**Success Criteria:**
- Entry prices: real CLOB bid/ask mid (never $0.5000 fallback)
- Zero-P&L trades: counted as BREAKEVEN, not LOSS
- Kalshi trades: ≥3 per cycle (target 20+ combined Poly+Kalshi per cycle)
- Win rate target: 85%+ through signal quality gates
- Unrealized P&L: refreshes every 5s via SSE live fetch
- Direction on Up/Down markets: UP/DOWN (not YES/NO)
- Balance: $100.00 clean slate

---

## File Map

| File | Groups | Changes |
|------|--------|---------|
| `trader.py` | 2, 3, 4, 7 | execute_trade_smart live price, loss fix, UP/DOWN direction, shares-first |
| `data_fetcher.py` | 2 | widen price sanitization threshold 0.90→0.97 |
| `kalshi/matcher.py` | 5 | MACRO_WHITELIST, CRYPTO_TO_MACRO, neutral price-range, threshold 0.45 |
| `kalshi/trader.py` | 3 | loss_count `<= 0` → `< 0` in persist_positions |
| `dashboard/backend/routes/events.js` | 6 | live CLOB price fetch in 5s SSE sync |

---

## Task 1: Clean Slate

**Files:** `positions_state.json`, `account_state.json`, `markov_state.json`, `runtime_tracking.json`, `zisi_local_trades.jsonl`

- [ ] **Step 1: Run clean slate with nuke + $100 reset**

```bash
cd C:\Users\mthun\Downloads\ZiSi_Bot
python clean_slate.py --force --nuke --balance 100
```

Expected output ends with: `[OK] CLEAN SLATE COMPLETE` and `Balance: $100.00`

- [ ] **Step 2: Delete additional stale state files**

```bash
del markov_state.json 2>nul
del runtime_tracking.json 2>nul
del rapid_fire_queue.json 2>nul
del shadow_state.json 2>nul
```

- [ ] **Step 3: Verify clean state**

```bash
python -c "import json; d=json.load(open('account_state.json')); assert d['balance']==100.0 and d['pnl']==0.0, d"
python -c "import json; d=json.load(open('positions_state.json')); assert d['summary']['closed_count']==0, d"
```

Expected: no assertion errors.

---

## Task 2: Fix Entry Prices (trader.py + data_fetcher.py)

**Files:**
- Modify: `trader.py` — `execute_trade_smart()` lines 391–530
- Modify: `data_fetcher.py` — market parsing lines 898–918

- [ ] **Step 1: Widen price sanitization threshold in data_fetcher.py**

In `data_fetcher.py`, find the two sanitization blocks around line 900–910 and change `0.90`/`0.10` thresholds to `0.97`/`0.03`:

```python
# OLD (around line 901):
if _yes_price is not None and (_yes_price >= 0.90 or _yes_price <= 0.10):
    _yes_price = None

# NEW:
if _yes_price is not None and (_yes_price >= 0.97 or _yes_price <= 0.03):
    _yes_price = None
```

```python
# OLD (around line 905):
if _last is not None and (_last >= 0.90 or _last <= 0.10):
    _last = None

# NEW:
if _last is not None and (_last >= 0.97 or _last <= 0.03):
    _last = None
```

```python
# OLD (around line 909):
if _mkt_price >= 0.90 or _mkt_price <= 0.10:
    _mkt_price = 0.5  # final safety cap

# NEW:
if _mkt_price >= 0.97 or _mkt_price <= 0.03:
    _mkt_price = 0.5  # final safety cap — only truly resolved markets
```

- [ ] **Step 2: Fix execute_trade_smart to fetch live CLOB price at entry**

In `trader.py`, replace the `mid_price` computation block inside `execute_trade_smart()`:

```python
# OLD (around lines 436-442):
    bid = float(polymarket_event.get("bid", 0))
    ask = float(polymarket_event.get("ask", 0))
    if bid > 0 and ask > 0:
        mid_price = (bid + ask) / 2
    else:
        mid_price = float(market.get("price", 0.5))

# NEW — fetch live CLOB price for the specific market token:
    clob_market_id = market.get("conditionId") or market.get("id", "")
    mid_price = None
    if clob_market_id:
        try:
            from data_fetcher import get_event_current_price as _gcp
            _pd = _gcp(clob_market_id)
            if _pd and isinstance(_pd.get("price"), (int, float)):
                _p = float(_pd["price"])
                if 0.03 < _p < 0.97:
                    _bid = float(_pd.get("bid", _p - 0.01))
                    _ask = float(_pd.get("ask", _p + 0.01))
                    mid_price = round((_bid + _ask) / 2, 4)
                    log.info("[SMART-EXEC] Live CLOB price %.4f for %s", mid_price, clob_market_id[:20])
        except Exception as _pe:
            log.debug("[SMART-EXEC] CLOB price fetch failed: %s", _pe)
    if mid_price is None:
        mid_price = float(market.get("price", 0.5))
        if mid_price <= 0.03 or mid_price >= 0.97:
            log.warning("[SMART-EXEC] No valid price for %s (%.4f) — skipping trade", clob_market_id[:20], mid_price)
            return None
        log.debug("[SMART-EXEC] Falling back to event price %.4f", mid_price)
```

---

## Task 3: Fix Zero-P&L Counted as LOSS

**Files:**
- Modify: `trader.py` — `persist_positions()` line ~1087, `execute_exit()` line ~879
- Modify: `kalshi/trader.py` — `persist_positions()` line ~776

- [ ] **Step 1: Fix loss_count in trader.py persist_positions**

```python
# OLD (trader.py ~line 1087):
"loss_count":     sum(1 for p in merged_closed if (p.get("realized_pnl") or 0) <= 0),

# NEW:
"loss_count":     sum(1 for p in merged_closed if (p.get("realized_pnl") or 0) < 0),
```

- [ ] **Step 2: Fix WIN/LOSS outcome label in execute_exit**

```python
# OLD (trader.py ~line 879):
outcome = "✅ WIN" if profit > 0 else "❌ LOSS"

# NEW:
if profit > 0:
    outcome = "✅ WIN"
elif profit == 0:
    outcome = "⚖️ BREAKEVEN"
else:
    outcome = "❌ LOSS"
```

- [ ] **Step 3: Fix log label — remove PAPER-AUTO-EXIT prefix**

```python
# OLD (trader.py ~line 698):
log.info(
    "[PAPER-AUTO-EXIT] %s closed after %.1fm | exit=%.4f | pnl=$%+.2f | reason=TIME_EXPIRED",
    ...
)

# NEW:
log.info(
    "[TIME-EXIT] %s closed after %.1fm | exit=%.4f | pnl=$%+.2f | reason=%s",
    order_id, age_minutes, exit_price, result["profit"], "MARKET_EXPIRED",
)
```

- [ ] **Step 4: Fix loss_count in kalshi/trader.py persist_positions**

```python
# OLD (kalshi/trader.py ~line 776):
losses = sum(1 for p in merged_closed if (p.get("realized_pnl") or 0) < 0)
# (verify it's already < 0 — if it shows <= 0, fix it)
```

---

## Task 4: Fix Direction Labels YES/NO → UP/DOWN

**Files:**
- Modify: `trader.py` — `execute_trade_smart()` line ~413

- [ ] **Step 1: Detect updown market and set UP/DOWN direction**

```python
# OLD (trader.py ~line 413):
    sentiment = signal_data.get("sentiment", "neutral")
    direction = "YES" if sentiment == "bullish" else "NO"

# NEW:
    sentiment = signal_data.get("sentiment", "neutral")
    _ev_title_lower = (polymarket_event.get("title", "") or "").lower()
    _is_updown = "up or down" in _ev_title_lower or "updown" in _ev_title_lower
    if _is_updown:
        direction = "UP" if sentiment == "bullish" else "DOWN"
    else:
        direction = "YES" if sentiment == "bullish" else "NO"
```

---

## Task 5: Fix Kalshi 0 Trades

**Files:**
- Modify: `kalshi/matcher.py` — `CRYPTO_TO_MACRO`, `_MACRO_WHITELIST`, `_EXPLICIT_FINANCE_TERMS`, `match_price_range_markets()`, `match_with_category_filter()`

- [ ] **Step 1: Add "price range" to whitelist and finance terms**

```python
# In _MACRO_WHITELIST frozenset, add:
    "price range", "bitcoin price", "ethereum price", "btc price", "eth price",

# In _EXPLICIT_FINANCE_TERMS frozenset, add:
    "price range", "bitcoin price", "ethereum price",
```

- [ ] **Step 2: Add price_range implication to CRYPTO_TO_MACRO**

In `CRYPTO_TO_MACRO`, add `"price range"` and direct coin phrases to BTC and ETH entries:

```python
# In BTC_BULLISH, BTC_BEARISH, BTC_NEUTRAL — add to start of list:
    "bitcoin price", "btc price", "price range",

# In ETH_BULLISH, ETH_BEARISH, ETH_NEUTRAL — add to start of list:
    "ethereum price", "eth price", "price range",

# In CRYPTO_BULLISH, CRYPTO_BEARISH, CRYPTO_NEUTRAL — add:
    "price range",
```

- [ ] **Step 3: Enable match_price_range_markets for NEUTRAL signals**

```python
# OLD (match_price_range_markets ~line 333):
    if sentiment not in ("BULLISH", "BEARISH"):
        return []

# NEW:
    if sentiment not in ("BULLISH", "BEARISH", "NEUTRAL"):
        return []

# And for NEUTRAL, pick brackets nearest current price (±5%):
    if sentiment == "NEUTRAL":
        # Neutral: pick brackets whose midpoint is within 5% of current price
        scored = []
        for ev in range_events:
            lo, hi = _parse_range(ev.get("title", ""))
            if lo is None:
                continue
            midpoint = (lo + hi) / 2.0
            gap_pct = abs(midpoint - current_price) / current_price
            if gap_pct <= 0.05:  # within 5% of current price
                score = round(0.55 + (0.05 - gap_pct) * 5, 4)  # closer = higher score
                scored.append((min(score, 0.75), ev))
        scored.sort(key=lambda x: -x[0])
        return [
            {"event": ev, "confidence": round(sc, 4), "matched_implication": "price_range_neutral", "market": "KALSHI"}
            for sc, ev in scored[:3]
        ]
```

- [ ] **Step 4: Lower confidence threshold to 0.45**

```python
# OLD (match_with_category_filter signature):
    def match_with_category_filter(self, signal, kalshi_events, confidence_threshold=0.6):

# NEW:
    def match_with_category_filter(self, signal, kalshi_events, confidence_threshold=0.45):
```

Also fix the call to `match_signal_to_events` inside `match_with_category_filter`:
```python
# Pass the lowered threshold through:
    matches = self.match_signal_to_events(signal, filtered_events, confidence_threshold)
```
(already passes it — just verify no hardcoded 0.6 inside `match_signal_to_events`)

- [ ] **Step 5: Verify match_signal_to_events confidence check uses parameter**

In `match_signal_to_events` (~line 272):
```python
# Confirm this reads `confidence_threshold` not a hardcoded value:
            if trade_conf >= confidence_threshold:
```

---

## Task 6: Live Unrealized P&L via SSE

**Files:**
- Modify: `dashboard/backend/routes/events.js` — 5s sync interval + live price fetch

- [ ] **Step 1: Add live price fetch helper and cache to events.js**

Add after the `_clients` declaration (around line 21):

```javascript
// Live price cache: market_id → {price, ts}
const _priceCache = new Map();
const PRICE_CACHE_TTL_MS = 3000; // 3s TTL — one fresh fetch per 5s tick

async function _fetchClobPrice(marketId) {
  if (!marketId) return null;
  const cached = _priceCache.get(marketId);
  if (cached && Date.now() - cached.ts < PRICE_CACHE_TTL_MS) return cached.price;
  try {
    const r = await fetch(`https://clob.polymarket.com/markets/${marketId}`, {
      signal: AbortSignal.timeout(2000),
    });
    if (!r.ok) return null;
    const d = await r.json();
    const tokens = d.tokens || [];
    const tok = tokens.find(t => (t.outcome || '').toUpperCase() === 'YES') || tokens[0] || {};
    const bid = parseFloat(d.bestBid ?? tok.price ?? 0);
    const ask = parseFloat(d.bestAsk ?? tok.price ?? 0);
    const price = bid > 0 && ask > 0 ? (bid + ask) / 2 : parseFloat(tok.price ?? 0);
    if (price > 0.02 && price < 0.98) {
      _priceCache.set(marketId, { price: Math.round(price * 10000) / 10000, ts: Date.now() });
      return _priceCache.get(marketId).price;
    }
  } catch (_) {}
  return null;
}
```

- [ ] **Step 2: Replace the 5s sync interval to enrich active positions with live prices**

```javascript
// OLD 5s interval (around line 161):
setInterval(() => {
  const positions = _safeRead(POSITIONS_FILE);
  broadcastEvent('balance_update', _buildBalancePayload());
  _lastBalance = _balanceFromPositions() ?? STARTING_BALANCE;

  if (positions) {
    const summary = positions.summary || {};
    broadcastEvent('positions_snapshot', {
      active_count:   (positions.active  || []).length,
      closed_count:   (positions.closed  || []).length,
      win_count:      summary.win_count   || 0,
      loss_count:     summary.loss_count  || 0,
      realized_pnl:   summary.realized_pnl  || 0,
      unrealized_pnl: summary.unrealized_pnl || 0,
    });
    _lastActiveKeys = _positionKeys(positions.active);
    _lastClosedKeys = _positionKeys(positions.closed);
  }
}, 5_000);

// NEW — async interval with live CLOB enrichment:
setInterval(async () => {
  const positions = _safeRead(POSITIONS_FILE);
  broadcastEvent('balance_update', _buildBalancePayload());
  _lastBalance = _balanceFromPositions() ?? STARTING_BALANCE;

  if (positions) {
    const activeArr = positions.active || [];
    const closedArr = positions.closed || [];

    // Enrich active Polymarket positions with live CLOB prices
    let liveUnrealized = 0;
    const enrichedActive = await Promise.all(activeArr.map(async (pos) => {
      if (pos.market !== 'POLYMARKET') {
        liveUnrealized += parseFloat(pos.unrealized_pnl || 0);
        return pos;
      }
      const marketId = pos.market_id || pos.conditionId || pos.order_id;
      const livePrice = await _fetchClobPrice(marketId);
      if (livePrice != null) {
        const shares = parseFloat(pos.shares || pos.shares_acquired || 0);
        const cost = parseFloat(pos.size || pos.amount_spent || 0);
        const unrealizedPnl = Math.round((shares * livePrice - cost) * 100) / 100;
        liveUnrealized += unrealizedPnl;
        return { ...pos, current_price: livePrice, unrealized_pnl: unrealizedPnl };
      }
      liveUnrealized += parseFloat(pos.unrealized_pnl || 0);
      return pos;
    }));

    const summary = positions.summary || {};
    broadcastEvent('positions_snapshot', {
      active:         enrichedActive,
      active_count:   enrichedActive.length,
      closed_count:   closedArr.length,
      win_count:      summary.win_count  || 0,
      loss_count:     summary.loss_count || 0,
      realized_pnl:   summary.realized_pnl  || 0,
      unrealized_pnl: liveUnrealized,
    });
    _lastActiveKeys = _positionKeys(activeArr);
    _lastClosedKeys = _positionKeys(closedArr);
  }
}, 5_000);
```

---

## Task 7: Shares-First Position Sizing

**Files:**
- Modify: `trader.py` — `place_order()` lines ~288–292

- [ ] **Step 1: Change place_order to compute shares first**

```python
# OLD (trader.py ~line 290):
    shares = round(amount_dollars / entry_price, 4) if entry_price > 0 else 0

# NEW — shares-first (pbot pattern, avoids USD→shares rounding drift):
    # Polymarket uses whole shares; round to nearest integer, min 1
    shares = max(1, round(amount_dollars / entry_price)) if entry_price > 0 else 1
    # Actual cost derived from shares (not the other way around)
    actual_cost = round(shares * entry_price, 4)
```

Then update the `order` dict to use `actual_cost` instead of `amount_dollars` for `amount_spent`:

```python
# In the paper_trading branch order dict (around line 304):
        order = {
            "order_id": order_id,
            "event_id": event_id,
            "market_id": market_id,
            "event_title": _display_title,
            "direction": direction,
            "amount_spent": actual_cost,      # ← was: amount_dollars
            "shares_acquired": shares,
            "entry_price": entry_price,
            "timestamp": timestamp,
            "status": "FILLED",
            **({"expiry_ts": expiry_ts} if expiry_ts else {}),
        }
```

---

## Task 8: Update Success Criteria in Spec

**Files:**
- Modify: `docs/superpowers/specs/2026-05-20-zisi-deep-fix-design.md`

- [ ] **Step 1: Add trade volume and win rate rows to the success criteria table**

Add to the `## Success Criteria` table:

```markdown
| Trades per cycle (combined) | Sporadic / 0 Kalshi | ≥20 Poly+Kalshi combined |
| Win rate target | 33% (current) | ≥85% via signal quality gates |
```

---

## Task 9: Commit All Changes

- [ ] **Step 1: Stage and commit**

```bash
git add trader.py data_fetcher.py kalshi/matcher.py kalshi/trader.py dashboard/backend/routes/events.js docs/superpowers/specs/2026-05-20-zisi-deep-fix-design.md
git commit -m "fix: Session 9 deep fix — live CLOB prices, UP/DOWN labels, Kalshi pipeline, live P&L, shares-first sizing, breakeven classification"
```

---

## Self-Review Checklist

- [x] Spec Group 1 (clean slate) → Task 1
- [x] Spec Group 2 (entry prices) → Task 2
- [x] Spec Group 3 (zero-P&L) → Task 3
- [x] Spec Group 4 (direction labels) → Task 4
- [x] Spec Group 5 (Kalshi 0 trades) → Task 5
- [x] Spec Group 6 (live P&L) → Task 6
- [x] Spec Group 7 (shares-first) → Task 7
- [x] Success criteria update → Task 8
- [x] No TBD/TODO placeholders
- [x] All method names consistent across tasks
- [x] `actual_cost` introduced in Task 7 used correctly in `amount_spent`
