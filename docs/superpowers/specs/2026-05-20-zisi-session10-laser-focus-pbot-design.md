# ZiSi Session 10 — Laser Focus + pBot Intelligence
**Date:** 2026-05-20  
**Status:** Approved — ready for implementation  
**Branch:** main  

---

## 1. Objective

Transform ZiSi from a multi-platform signal bot (Polymarket + Kalshi + LLM news) into a precision **Polymarket Up/Down engine** modelled directly on pbot-6's $100k+ strategy. Remove everything that doesn't serve a 5-min/15-min RSI trade. Add the six intelligence layers that separate pbot-6 from losing bots.

**Targets after this session:**
- 500–750 evaluated windows/day across BTC-5m, BTC-15m, ETH-5m, SOL-5m, XRP-5m
- Win rate ≥ 62% (price gate enforces edge before entry)
- Zero Kalshi errors (module deleted entirely)
- Dashboard reflects live reality, not dead signals

---

## 2. Strategic Context — Why pbot-6 Works

From deep wallet analysis (44,751 trades, March–May 2026, $100k+ PnL):

| Observation | Implication for ZiSi |
|-------------|---------------------|
| 75% of entries are sub-50¢ | Entry price gate is mandatory — never enter expensive |
| Buys BOTH UP and DOWN simultaneously | Dual-side entry when combined cost < $0.92 |
| Fires in 10-second burst at candle boundary | Precise asyncio alignment to `closeTime` |
| Median $2–3 per trade, $20–60 on BTC high conviction | Tiered Kelly by score + price |
| 60/40 DOWN/UP bias — follows prevailing trend | Regime filter aligns direction to market conditions |
| Trades BTC 5-min AND 15-min | Two separate tasks for BTC |

From Punisher's published playbook:
- Regime filtering (weekday trend / weekend mean-reversion) is the fastest fix for a losing system
- Loss clusters are predictable — pause after 2 consecutive losses
- Latency kills bots before strategy does — pre-stage everything
- A consistent loser can be inverted; random losers cannot
- Entry price 10¢ below WR estimate = profit; above that = bleeding

---

## 3. Architecture — Surgical Removal

### 3.1 Files Deleted

| Category | Files / Directories |
|----------|-------------------|
| **Kalshi** | `kalshi/` (full directory), `markets_orchestrator.py`, `category_suspensions.json` |
| **News / LLM pipeline** | `sentiment_analyzer.py`, `data_fetcher.py`, `rss_fetcher.py`, `signal_router.py`, `event_matcher.py`, `smart_money.py`, `consensus_engine.py` |
| **External data sources** | `data_sources/` (all 14 files: audioalpha, binance_basis, btc_dominance, cryptoquant_flow, deribit_options, fred_api, funding_consensus, liq_heatmap, lunarcrush, polymarket_flow, predictit_markets, santiment, vix_monitor, pmxt/) |
| **Obsolete state** | `shadow_mode.py`, `markov_tracker.py`, `price_drift_tracker.py`, `regime_adaptive_weights.py` |
| **Dead JSON** | `shadow_state.json`, `markov_state.json`, `macro_context.json`, `rapid_fire_queue.json` |
| **Debug scripts** | `analyze_trades.py`, `debug_trading.py`, `features_scaffold.py` |

**Module count: ~30 → 14**

### 3.2 Files Kept (unchanged)

- `price_analyzer.py` — Binance RSI + momentum + volume (core signal source)
- `risk_manager.py` — Kelly fraction (gains entry price gate + exposure caps)
- `position_sizer.py` — Shares-first sizing (already fixed Session 9)
- `state_manager.py` — Balance + positions (gains reconciliation hook)
- `metrics_engine.py` — Trade recording (gains inversion monitor)
- `logger.py` — Unchanged
- `telegram_bot.py` — Gains new alert types (regime flip, inversion, circuit breaker)
- `backtester.py` — Unchanged (use to validate regime filter thresholds)
- `dashboard/backend/` — Routes updated, health endpoint unchanged

### 3.3 Files Modified

| File | Change |
|------|--------|
| `updown_trader.py` → `updown_engine.py` | Refactored into `UpDownEngine` class; regime-aware; dual-entry support |
| `main.py` | Replaced single loop with `asyncio.gather()` of 6 tasks |
| `config.py` | Remove all Kalshi/news/LLM config; add regime, time gate, price gate, inversion params |

### 3.4 Files Added (New)

| File | Purpose |
|------|---------|
| `regime_filter.py` | Weekday/weekend mode + UTC time gate |
| `reconciliation.py` | 30s background fill verification loop |
| `dashboard/frontend/src/components/CommandCentre.jsx` | Top bar panel |
| `dashboard/frontend/src/components/AssetCards.jsx` | Per-asset live cards |
| `dashboard/frontend/src/components/TradeFeed.jsx` | Scrolling trade log |
| `dashboard/frontend/src/components/WinRateChart.jsx` | Rolling 40-window WR chart |
| `dashboard/frontend/src/components/PositionMonitor.jsx` | Open positions with countdowns |
| `dashboard/frontend/src/components/SystemHealth.jsx` | Infrastructure status panel |

---

## 4. Execution Engine

### 4.1 Asyncio Task Architecture

`main.py` launches 6 concurrent tasks via `asyncio.gather()`:

```python
asyncio.gather(
    asset_loop("BTC", "5m",  offset_seconds=0),    # BTC 5-min
    asset_loop("BTC", "15m", offset_seconds=0),    # BTC 15-min (separate task)
    asset_loop("ETH", "5m",  offset_seconds=90),   # ETH 5-min
    asset_loop("SOL", "5m",  offset_seconds=180),  # SOL 5-min
    asset_loop("XRP", "5m",  offset_seconds=270),  # XRP 5-min
    reconciliation_loop(),                          # 30s background
)
```

Stagger prevents all tasks hammering Binance + Polymarket APIs simultaneously.

### 4.2 Per-Asset Loop

```python
async def asset_loop(asset: str, interval: str, offset_seconds: int):
    await asyncio.sleep(offset_seconds)
    await align_to_candle_boundary(asset, interval)   # wait for next :00/:05/:15 mark
    while True:
        if not regime_filter.time_gate_open():        # UTC 13:00–23:00 only
            await sleep_to_next_candle(interval)
            continue
        if skip_windows[asset] > 0:                   # circuit breaker active
            skip_windows[asset] -= 1
            await sleep_to_next_candle(interval)
            continue
        gate_passed = await warmup_gate(asset, interval)
        if not gate_passed:
            await sleep_to_next_candle(interval)
            continue
        signal = await evaluate_signal(asset, interval)
        if signal.valid:
            order = pre_stage_order(signal)            # build HMAC + body NOW
            await execute(order)
        await sleep_to_next_candle(interval)
```

### 4.3 Candle Boundary Alignment

- Pull `closeTime` from Binance kline WebSocket stream
- Sleep to `closeTime + 1000ms` (wait for candle to fully seal)
- All trades fire in a ≤10 second burst at the boundary — mirrors pbot-6's observed clustering

### 4.4 Warmup Gate (3-layer simplified from pbot-6's 6-layer)

| Layer | Rule | Fail action |
|-------|------|-------------|
| **Warmup** | Connect 15s before closeTime, collect ticks | — |
| **Quality gate** | Require ≥3 ticks in final 5s; no single tick jump >5¢ | Skip window |
| **Stale guard** | Drop the first tick from any new connection (always a cached snapshot) | Drop tick, use next |

Gate failure → skip window, align to next candle. Never enter on dirty data.

### 4.5 Pre-Staged Execution

Before the gate fires, build:
- HMAC signature
- Request headers
- Order body (price, side, size in shares)

At execution: clone pre-built request, fire with `TCP_NODELAY=True`. Zero serialization on hot path.

---

## 5. pBot Signal Intelligence Layer

### 5.1 Regime Filter (`regime_filter.py`)

```python
def get_regime_mode() -> Literal["TREND", "MEAN_REVERSION"]:
    return "TREND" if datetime.utcnow().weekday() < 5 else "MEAN_REVERSION"

def time_gate_open() -> bool:
    hour = datetime.utcnow().hour
    return 13 <= hour < 23    # UTC 1pm–11pm (US + EU active hours)
```

| Mode | Signal logic | Active |
|------|-------------|--------|
| `TREND` | Go WITH RSI (bullish RSI → UP, bearish RSI → DOWN) | Mon–Fri |
| `MEAN_REVERSION` | Go AGAINST RSI extremes (overbought → DOWN, oversold → UP) | Sat–Sun |

Regime mode broadcast to dashboard via SSE on every candle cycle.

### 5.2 Entry Price Gate (in `risk_manager.py`)

Punisher's rule: entry price 10¢ below estimated win rate = profit. Enforced hard:

```python
# WR estimates derived from composite score (backtested on updown_trader history):
SCORE_TO_WR = {
    (0.85, 1.00): 0.70,   # score ≥ 0.85 → est. WR 70% → max entry 60¢
    (0.75, 0.85): 0.65,   # score 0.75–0.85 → est. WR 65% → max entry 55¢
    (0.62, 0.75): 0.57,   # score 0.62–0.75 → est. WR 57% → max entry 47¢
}

def price_gate_passes(price: float, score: float) -> bool:
    est_wr = lookup_wr(score)
    return price <= (est_wr - 0.10)
```

If price gate fails → skip this window. No exceptions. No "size down to compensate" — the edge is in cheap entries, not in adjusting size on expensive ones.

### 5.3 Loss Clustering Circuit Breaker (in `updown_engine.py`)

```python
# Per-asset state (persisted to state_manager):
consecutive_losses: dict[str, int]   # resets on win
skip_windows:       dict[str, int]   # decrements each skipped window

# After trade resolves:
if trade.profit < 0:
    consecutive_losses[asset] += 1
    if consecutive_losses[asset] >= 2:
        skip_windows[asset] = 2
        consecutive_losses[asset] = 0
        telegram.send(f"⚠️ {asset}: 2 consecutive losses — pausing 2 windows")
else:
    consecutive_losses[asset] = 0
```

### 5.4 Dual-Side Entry (in `updown_engine.py`)

pbot-6 buys BOTH sides when combined cost < $1.00 (guaranteed profit after fees at < $0.92).

```python
def should_dual_enter(main_price: float, hedge_price: float) -> bool:
    return (main_price + hedge_price) < 0.92

# Sizing when dual:
main_size  = kelly_size(score, balance)        # e.g. 2% Kelly = $2.00
hedge_size = 0.25 * main_size                  # hedge = 25% of main = $0.50
# Total outlay: $2.50 — whichever side wins pays full $1/share
```

Dual positions tracked as one `DUAL` logical position in `state_manager.py`. Dashboard shows them as linked rows.

When not dual-eligible (prices sum ≥ 0.92) → single directional entry as normal.

### 5.5 Strategy Inversion Monitor (in `metrics_engine.py`)

```python
# Checked after every resolved trade, rolling 40-window per asset:
if len(recent_trades[asset]) >= 40:
    rolling_wr = wins / 40
    if rolling_wr < 0.45 and not invert_signal[asset]:
        invert_signal[asset] = True
        telegram.send(f"🔄 {asset}: WR={rolling_wr:.0%} over 40 windows — INVERTING signal")
    elif rolling_wr > 0.52 and invert_signal[asset]:
        invert_signal[asset] = False
        telegram.send(f"✅ {asset}: WR recovered to {rolling_wr:.0%} — reverting inversion")
```

When inverted: RSI bullish → DOWN entry, RSI bearish → UP entry. Auto-revert when WR recovers. **40 windows minimum** — at ~12 trades/hour for BTC-5m, this is ~3.5 hours of data before flipping.

---

## 6. Risk & Position Sizing

### 6.1 Shares-First (enforced everywhere)

```python
# Every order in the codebase must use this pattern:
shares = round(kelly_usd / price)    # decide shares first
actual_cost = shares * price          # derive USD from shares
# NEVER: shares = kelly_usd / price (rounding drift at low prices)
```

### 6.2 Tiered Kelly by Score + Price Gate

| Score | Max entry price | Kelly | Position cap |
|-------|----------------|-------|-------------|
| ≥ 0.85 | 60¢ | 4% | 15% of balance |
| 0.75–0.85 | 55¢ | 3% | 10% of balance |
| 0.62–0.75 | 47¢ | 1.5% | 5% of balance |
| < 0.62 | — | skip | — |

**Price gate failure at any tier = skip. No fallback tier.**

### 6.3 Exposure Caps

```python
MAX_OPEN_PER_ASSET = 2      # max 2 open positions per asset simultaneously
MAX_TOTAL_OPEN     = 6      # max 6 total across all assets
MAX_DAILY_LOSS     = 0.15   # halt all trading if daily drawdown hits 15% of balance
```

### 6.4 Dual-Side Sizing

```
Main bet:  Kelly × balance  (e.g. 2% × $100 = $2.00)
Hedge bet: 25% × main size  (e.g. $0.50)
Total:     $2.50
```

Both legs recorded as `position_type: "DUAL"` in state. P&L calculated as combined outcome.

---

## 7. Infrastructure Reliability

### 7.1 Silent Fill Reconciliation (`reconciliation.py`)

Runs as the 6th asyncio task. Every 30 seconds:

```python
async def reconciliation_loop():
    while True:
        await asyncio.sleep(30)
        for pos in state_manager.get_open_positions():
            api_status = await clob.get_order_status(pos.order_id)
            if api_status.filled and not state_manager.is_confirmed(pos.id):
                state_manager.force_confirm(pos)
                logger.warning(f"Ghost fill corrected: {pos.id}")
                telegram.send(f"👻 Ghost fill detected + corrected: {pos.asset}")
```

### 7.2 Timeout → Poll Pattern

Applied at every CLOB order submission:

```python
try:
    resp = await clob.place_order(order, timeout=3.0)
except asyncio.TimeoutError:
    await asyncio.sleep(1)
    status = await clob.get_order_status(order.id)
    if status.filled:
        state_manager.record_fill(order)
    # only treat as miss if confirmed NOT filled
```

### 7.3 Paper → Shadow → Live Graduation Path

| Stage | Configuration | Graduation criteria |
|-------|--------------|-------------------|
| **Paper** (now) | Simulated fills, real price data | 40+ trades, WR ≥ 62% |
| **Shadow** | Real API calls, zero-balance wallet | Backtest matches live ±3% over 100 trades |
| **Live** | Real capital, 10% of intended size first | Shadow results hold for 48h |

---

## 8. Dashboard Overhaul — Air Design System

### 8.1 Design Direction

**Theme:** Dark-mode trading terminal using Air design tokens  
**Aesthetic:** Refined utility — high contrast, clean typography, data-first, no decorative noise  
**Palette (dark-mode adaptation):**

```css
:root {
  /* Air tokens — dark mode interpretation */
  --color-bg-base:       #0a0a0a;    /* deepest background */
  --color-bg-surface:    #111111;    /* card surface */
  --color-bg-elevated:   #1a1a1a;    /* elevated card */
  --color-text-primary:  #ffffff;    /* Cloud Canvas as text */
  --color-text-secondary: #f5f5f5;  /* Vapor Gray as secondary text */
  --color-text-muted:    #6b6b6b;    /* muted labels */
  --color-accent:        #2b7fff;    /* Vivid Azure — interactive, active states */
  --color-accent-muted:  #426188;    /* Sky Blue — decorative accents */
  --color-midnight:      #1b1b1b;    /* Midnight Ink — card borders */
  --color-profit:        #00d4a3;    /* teal green for positive P&L */
  --color-loss:          #ff4d4d;    /* red for negative P&L */
  --color-neutral:       #f5f5f5;    /* neutral / open positions */

  /* Air typography */
  --font-body:      'Inter', ui-sans-serif, system-ui, sans-serif;         /* Control substitute */
  --font-heading:   'Montserrat', ui-sans-serif, system-ui, sans-serif;    /* Control TNT substitute */
  --font-display:   'Oswald', ui-sans-serif, system-ui, sans-serif;        /* Control Compressed substitute */
  --font-script:    'Dancing Script', cursive;                             /* Control Cursive */
  --font-mono:      'JetBrains Mono', 'Fira Code', ui-monospace, monospace;

  /* Air spacing */
  --spacing-4:  4px;  --spacing-8:  8px;  --spacing-12: 12px;
  --spacing-16: 16px; --spacing-20: 20px; --spacing-24: 24px;
  --spacing-32: 32px; --spacing-48: 48px;

  /* Air radii */
  --radius-inputs:  4px;
  --radius-buttons: 8px;
  --radius-cards:   14px;
}
```

**Typography:**
- Panel headings: Montserrat 500, 20px — Air "Control TNT" role
- Data labels: Inter 500, 12px — Air "Control" caption role
- Live values: JetBrains Mono — tabular numerals for prices, P&L, percentages
- Bot name "ZiSi": Oswald 900, display scale — Air "Control Compressed" role

### 8.2 Layout

Full viewport, 3-row grid:
```
┌────────────────────────────────────────────────┐
│  COMMAND CENTRE (top bar — sticky, 64px)        │
├──────────┬──────────┬──────────┬────────────────┤
│ BTC-5m   │ BTC-15m  │ ETH-5m   │ SOL / XRP      │
│ ASSET    │ ASSET    │ ASSET    │ ASSET CARDS     │
│ CARD     │ CARD     │ CARD     │ (2 stacked)     │
├──────────┴──────────┴──────────┴────────────────┤
│  TRADE FEED (left 40%)  │  WIN RATE CHART (right)│
├─────────────────────────┼────────────────────────┤
│  POSITION MONITOR       │  SYSTEM HEALTH         │
└─────────────────────────┴────────────────────────┘
```

### 8.3 Panel Specifications

#### Panel 1 — Command Centre (sticky top bar)

**File:** `CommandCentre.jsx`

Elements (left to right):
- **ZiSi** wordmark in Oswald 900 — `--color-accent` (`#2b7fff`)
- Divider `|`
- **Live balance** — monospace, green/red delta indicator
- **Daily P&L** — `+$X.XX` green or `−$X.XX` red, Montserrat 500
- **All-time P&L** — muted label + value
- **Regime badge** — `TREND` (azure outline) or `MEAN_REVERSION` (sky blue outline), `--radius-buttons` 8px
- **Time gate badge** — `ACTIVE 🟢` or `PAUSED 🔴` with live UTC clock
- **Daily loss bar** — thin progress bar, `--color-loss` fill, max = 15%

Air component rules:
- Background: `--color-bg-surface` (#111111)
- Border bottom: `1px solid rgba(255,255,255,0.06)`
- Text: `--color-text-primary`
- Badges: transparent background, `--color-accent` border, `--radius-buttons`

#### Panel 2 — Asset Cards

**File:** `AssetCards.jsx`  
5 cards in a horizontal row (BTC-5m, BTC-15m, ETH, SOL, XRP).

Each card (`--radius-cards` 14px, `--color-bg-elevated` background, `--spacing-20` padding):

```
┌─────────────────────────┐
│ BTC  5m    TREND      ↑ │  ← asset + timeframe + regime + direction
│ Score: 0.87             │
│ ━━━━━━━━━━━━━━━━━━━━━━━ │  ← composite score bar (azure fill)
│ WR: 67%  ↑  [40 trades] │  ← rolling WR + trend arrow
│ Entry gate: ≤ 58¢       │  ← max allowed entry price
│ Open: 1 pos  −$0.12 unr │  ← open positions + unrealized
│ Losses: 0  Skip: 0      │  ← circuit breaker state
│         [INVERTED]      │  ← badge only if inversion active
└─────────────────────────┘
```

- Score bar: filled left-to-right, `--color-accent` for score value, `--color-bg-surface` for remainder
- WR arrows: ↑ in `--color-profit`, ↓ in `--color-loss`
- INVERTED badge: `--color-loss` border, pulsing opacity animation

#### Panel 3 — Live Trade Feed

**File:** `TradeFeed.jsx`  
Scrolling table, last 50 trades, newest at top. New rows animate in from top (slide-down, 150ms).

Columns:
```
Time | Asset | TF | Dir   | Entry¢ | Exit¢ | Score | Type  | P&L    | Result
─────┼───────┼────┼───────┼────────┼───────┼───────┼───────┼────────┼───────
2:35 │ BTC   │ 5m │ DOWN↓ │  46¢   │ 100¢  │ 0.87  │ DUAL  │ +$1.82 │  WIN
2:30 │ ETH   │ 5m │  UP↑  │  48¢   │   0¢  │ 0.76  │ SINGL │ −$0.48 │ LOSS
```

- `WIN` rows: left border `3px solid --color-profit`
- `LOSS` rows: left border `3px solid --color-loss`
- `DUAL` type badge: `--color-accent-muted` background
- Open positions: `--color-neutral` italicised, no exit price yet
- Font: JetBrains Mono for all numeric columns, Inter for labels

#### Panel 4 — Win Rate Analytics

**File:** `WinRateChart.jsx`  
Recharts `LineChart` (already in the project's dependency tree).

- **Primary chart:** Rolling 40-window WR per asset — 5 lines, each a different Air colour
  - BTC-5m: `#2b7fff` (Vivid Azure)
  - BTC-15m: `#426188` (Sky Blue)
  - ETH: `#00d4a3` (profit teal)
  - SOL: `#f5f5f5` (Vapor Gray)
  - XRP: `#ff9500` (amber — new colour for contrast)
- **Reference lines:**
  - `y=0.45` — red dashed — inversion trigger
  - `y=0.52` — green dashed — recovery threshold
  - `y=0.62` — azure dashed — "edge confirmed" threshold
- **Inversion events:** vertical dashed line + `INVERTED` label at that x position
- **Secondary chart (below):** Entry price distribution histogram per asset
  - Bars in 5¢ buckets (0–5¢, 5–10¢, … 95–100¢)
  - Goal: see the sub-50¢ concentration build over time
  - `--color-accent` for sub-50¢ bars, `--color-loss` for above-50¢ bars

#### Panel 5 — Position Monitor

**File:** `PositionMonitor.jsx`  
Live table of all open positions. Updates via SSE stream every 2s.

Columns:
```
Asset | TF | Dir  | Type  | Entry¢ | Current¢ | Unr P&L | Candle closes in
──────┼────┼──────┼───────┼────────┼──────────┼─────────┼─────────────────
BTC   │ 5m │ DOWN │ DUAL  │  46¢   │   52¢    │ −$0.18  │ 2m 14s ⏱
BTC   │ 5m │  UP  │ DUAL  │  15¢   │   48¢    │ +$0.39  │ 2m 14s ⏱ (hedge)
ETH   │ 5m │  UP  │ SINGL │  44¢   │   61¢    │ +$0.34  │ 3m 42s ⏱
```

- DUAL pairs shown as linked rows with a connecting bracket `⌐` on the left
- Countdown timer: green when >60s remaining, amber when 15–60s, red when <15s (pulsing)
- Unrealized P&L: real-time colour — green positive, red negative

#### Panel 6 — System Health

**File:** `SystemHealth.jsx`  
Grid of status indicators (`--color-bg-elevated` card, `--radius-cards`):

```
Warmup gate pass rate    │ 94.2%        ✅
Ghost fills today        │ 0            ✅
Reconciliation loop      │ 8s ago       ✅
Timeout → poll events    │ 2 today      ⚠️
─────────────────────────┼──────────────────
BTC-5m  consecutive loss │ 0  skip: 0   ✅
BTC-15m consecutive loss │ 1  skip: 0   ⚠️
ETH-5m  consecutive loss │ 0  skip: 0   ✅
SOL-5m  consecutive loss │ 0  skip: 2   🔴 PAUSED
XRP-5m  consecutive loss │ 0  skip: 0   ✅
─────────────────────────┼──────────────────
Next candle boundaries
  BTC-5m                 │ 0m 47s
  BTC-15m                │ 4m 12s
  ETH-5m                 │ 1m 21s
  SOL-5m                 │ 2m 55s
  XRP-5m                 │ 0m 12s
```

Status icons: ✅ `--color-profit`, ⚠️ amber `#f5a623`, 🔴 `--color-loss`

### 8.4 Components to Delete

All current dashboard components are replaced:
- `BotStatus.jsx` → deleted (replaced by `CommandCentre.jsx`)
- `MissedTrades.jsx` → deleted (replaced by `TradeFeed.jsx`)
- `SignalPipeline.jsx` → deleted (Kalshi/LLM pipeline is gone)
- `CompoundingProgress.jsx` → deleted (replaced by `WinRateChart.jsx`)
- `MacroPanel.jsx` → deleted (macro data sources deleted)

### 8.5 SSE Events (Backend → Frontend)

New event types added to `dashboard/backend/routes/health.js`:

| Event | Payload | Triggers |
|-------|---------|---------|
| `trade_executed` | asset, direction, type, entry_price, score | Every new trade |
| `trade_resolved` | position_id, exit_price, pnl, result | Every resolution |
| `position_update` | all open positions + unrealized P&L | Every 2s |
| `regime_change` | mode, asset | On weekday/weekend flip |
| `circuit_breaker` | asset, skip_windows | On 2 consecutive losses |
| `inversion_toggle` | asset, inverted, rolling_wr | On inversion flip |
| `reconciliation` | ghost_fills_corrected | Every 30s |
| `candle_boundary` | asset, timeframe, seconds_to_close | Every 10s |

---

## 9. Module Inventory — Final State

```
ZiSi_Bot/
├── main.py                    # asyncio.gather() of 6 tasks
├── config.py                  # simplified: no Kalshi/LLM/news params
├── updown_engine.py           # UpDownEngine class (was updown_trader.py)
├── regime_filter.py           # NEW: weekday/weekend + time gate
├── reconciliation.py          # NEW: 30s fill verification loop
├── price_analyzer.py          # Binance RSI + momentum + volume
├── risk_manager.py            # Kelly + entry price gate + exposure caps
├── position_sizer.py          # shares-first sizing
├── state_manager.py           # balance + positions + inversion state
├── metrics_engine.py          # trade recording + inversion monitor
├── logger.py
├── telegram_bot.py            # + new alert types
├── backtester.py              # unchanged — validate thresholds
├── email_scheduler.py         # unchanged
└── dashboard/
    ├── backend/
    │   └── routes/health.js   # + new SSE event types
    └── frontend/src/
        ├── App.jsx
        └── components/
            ├── CommandCentre.jsx   # NEW
            ├── AssetCards.jsx      # NEW
            ├── TradeFeed.jsx       # NEW
            ├── WinRateChart.jsx    # NEW
            ├── PositionMonitor.jsx # NEW
            └── SystemHealth.jsx    # NEW
```

**Total: 14 Python modules + 6 new React components**

---

## 10. Config Parameters — New / Changed

```python
# config.py additions
ASSETS           = ["BTC", "ETH", "SOL", "XRP"]
TIMEFRAMES       = {"BTC": ["5m", "15m"], "ETH": ["5m"], "SOL": ["5m"], "XRP": ["5m"]}
TIME_GATE_UTC    = (13, 23)          # trade only UTC 13:00–23:00
INVERSION_WINDOW = 40                # trades before inversion check
INVERSION_TRIGGER_WR = 0.45         # flip below this
INVERSION_RECOVERY_WR = 0.52        # revert above this
DUAL_ENTRY_MAX_COMBINED = 0.92      # don't dual-enter if combined > this
CIRCUIT_BREAKER_LOSSES = 2          # consecutive losses before pause
CIRCUIT_BREAKER_SKIP   = 2          # windows to skip after trigger
MAX_DAILY_LOSS_PCT     = 0.15       # halt all trading at 15% daily drawdown
WARMUP_SECONDS         = 15         # seconds before candle close to warmup
WARMUP_MIN_TICKS       = 3          # minimum ticks required in final 5s
WARMUP_MAX_JUMP        = 0.05       # reject window if any tick jumps > 5¢
RECONCILE_INTERVAL     = 30         # seconds between reconciliation passes
```

---

## 11. Success Criteria

| Metric | Target |
|--------|--------|
| Trades evaluated per day | 500–750 (across all 6 loops) |
| Trades executed per day | 50–150 (price gate + warmup gate filtering) |
| Win rate (rolling 40) | ≥ 62% per asset |
| Average entry price | ≤ 50¢ (75%+ sub-50¢ like pbot-6) |
| Dual-entry % | ≥ 20% of trades when market conditions allow |
| Ghost fills corrected | Visible in System Health panel |
| Circuit breaker fires | Working — Telegram alert + dashboard update |
| Kalshi errors | Zero (module deleted) |
| Dashboard data staleness | ≤ 2s (SSE stream) |

---

## 12. Implementation Sequence

1. **Surgical deletion** — remove all Kalshi, news, LLM, data_sources files
2. **Refactor `updown_trader.py` → `updown_engine.py`** — `UpDownEngine` class
3. **`regime_filter.py`** — weekday/weekend mode + time gate
4. **`reconciliation.py`** — 30s asyncio loop
5. **`main.py` overhaul** — replace loop with `asyncio.gather()`
6. **`risk_manager.py`** — add entry price gate + exposure caps
7. **`metrics_engine.py`** — add inversion monitor (40-window)
8. **`config.py`** — remove dead params, add new ones
9. **Dashboard backend** — new SSE event types
10. **Dashboard frontend** — delete old components, build 6 new ones with Air tokens
11. **Backtest validation** — run `backtester.py` to verify WR-to-score mappings
12. **Paper trade run** — 48h paper trading, verify all 6 panels live-updating correctly

---

*Spec written 2026-05-20. Ready for implementation plan.*
