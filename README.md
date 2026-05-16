# ZiSi — Autonomous Prediction Market Trading Bot

ZiSi is a self-learning, multi-source, AI-driven paper-trading bot for **Polymarket** and **Kalshi**. It harvests news from 10+ sources in real-time, scores sentiment through a 10-level AI cascade, matches markets with Kelly-sized positions, and learns from every trade outcome — autonomously.

**Current stats (paper mode):** 72.9% win rate · $1,318 realized P&L · 680 closed trades · ML Phase 2 active

---

## Architecture

```
News Sources (11 channels, zero dead zones)
  Primary:  NewsAPI · CoinTelegraph · Decrypt · CryptoSlate RSS
  Free ext: CryptoPanic · Reddit (4 subreddits) · Google News RSS · CoinDesk RSS
        ↓
Rapid-Fire Scanner (background, 90s interval)
  Detects breaking news → queues immediate Kalshi cycle without waiting 15 min
        ↓
Sentiment Cascade (10 levels, auto-fallback)
  Claude → Gemini Flash → Groq Llama → Cerebras → Mistral → OpenRouter
        → Together AI → FinBERT → VADER → Keyword
        ↓
Signal Classification  (TYPE_A_HIGH / TYPE_A_LOW / TYPE_B_HIGH / TYPE_B_LOW)
        ↓
5 Einstein Advancements (size multipliers, stacked)
  D: Fear & Greed Index (Alternative.me)
  E: Asymmetric Directional Kelly (per YES/NO win rate)
  F: UTC Hour Edge Multiplier (self-learned)
  G: Rolling Coin Signal Quality Decay (regime detection)
  H: Polymarket Volume Surge Detector (smart money signal)
        ↓
Gate Chain  (liquidity → spread → price → confluence → MTF → EV → routing)
        ↓
Kelly Sizing  (regime + drawdown + signal-type + Einstein multipliers, capped 2.8×)
        ↓
Execution
  ├── Polymarket UP/DOWN  (RSI + momentum, 24/7, 3 windows/coin/cycle)
  └── Kalshi macro events (15-min cycle + immediate rapid-fire trigger)
        ↓
Position Monitor  (target / stop / signal-flip / max-hold exit)
        ↓
ML Feedback Loop
  Outcome labeller → 695 labelled trades → Phase 2 logistic regression active
```

News cycles run every **15 minutes** (96/day). UP/DOWN RSI trades run every cycle, 24/7. The rapid-fire scanner fires extra Kalshi cycles within 90 seconds of breaking news.

---

## Quick Start

### Prerequisites
- Python 3.11+
- Node.js 18+

### 1. Install dependencies

```bash
pip install -r requirements.txt
pip install vaderSentiment          # local fallback — no API key
cd dashboard/backend && npm install && cd ../..
```

### 2. Configure `.env`

```env
# Core trading
KALSHI_API_KEY=your_key_id
KALSHI_PRIVATE_KEY="-----BEGIN RSA PRIVATE KEY-----\n...\n-----END RSA PRIVATE KEY-----"
NEWSAPI_KEY=your_key                 # optional — RSS sources work without it

# AI Sentiment chain (first valid key wins — all have free tiers)
ANTHROPIC_API_KEY=your_key           # Claude — highest quality
GEMINI_API_KEY=your_key             # Free 1,500 req/day
GROQ_API_KEY=your_key               # Free 14,400 req/day
CEREBRAS_API_KEY=your_key           # Free tier
MISTRAL_API_KEY=your_key            # Free tier
OPENROUTER_API_KEY=your_key         # Free open-source models
TOGETHER_API_KEY=your_key           # $25 free credits

# Telegram
TELEGRAM_BOT_TOKEN=your_token
TELEGRAM_CHAT_ID=your_chat_id

# Bot settings
BOT_MODE=paper_trading              # paper_trading | live_trading
ACCOUNT_BALANCE=100
RISK_PER_TRADE_PERCENT=2
SIGNAL_THRESHOLD=6
```

### 3. Launch

```bash
cd dashboard/backend
npm start
```

Dashboard → **http://localhost:5000**

The dashboard auto-spawns `python main.py`. Bot output streams live to the terminal.

---

## Features

### News Intelligence — 11 Sources

| Source | Type | Key Required |
|---|---|---|
| CoinTelegraph | RSS | No |
| CoinDesk | RSS | No |
| Decrypt | RSS | No |
| CryptoSlate | RSS | No |
| CryptoPanic | Free API | No |
| Reddit r/CryptoCurrency | JSON API | No |
| Reddit r/Bitcoin | JSON API | No |
| Reddit r/ethereum | JSON API | No |
| Google News (BTC/ETH/Macro) | RSS | No |
| NewsAPI | REST | Optional |
| Binance Funding Rate | REST | No |

### Rapid-Fire Breaking News Scanner

A background daemon thread runs every **90 seconds** harvesting from all free sources. When a headline matches 2+ extreme-confidence keywords (ETF approval, crash, hack, record high, etc.) for a tracked coin, it queues an immediate Kalshi execution cycle — **ZiSi reacts to breaking news within 90 seconds** instead of waiting up to 15 minutes.

### UP/DOWN RSI Cycle (24/7)

Monitors BTC, ETH, SOL via Binance 1-min candles. Trades up to 3 windows per coin per cycle using RSI + momentum signals. Runs uninterrupted through overnight dead windows.

### Sentiment Cascade — 10 Levels

```
P1  Claude Sonnet      — premium, highest quality
P2  Gemini 2.0 Flash   — free, 1,500/day, 1h backoff on quota
P3  Groq Llama-70B     — free, 14,400/day
P4  Cerebras Llama-70B — free tier
P5  Mistral Small      — free tier
P6  OpenRouter (Llama/Qwen/Phi — free models)
P7  Together Llama-70B — $25 free credits
P8  FinBERT            — local, financial-domain BERT
P9  VADER              — local, no install beyond pip
P10 Keyword fallback   — zero latency, always available
```

### ML Self-Learning (Phase 2 Active)

- **Phase 1** (< 50 labelled trades): Gemini confidence × 0.65 deflation
- **Phase 2** (≥ 50): Logistic regression trained on actual trade outcomes. Blended 50% Gemini + 50% model probability. Auto-activates on startup if 50+ labelled examples exist.
- **695 labelled trades** as of May 2026. Val accuracy and ROC-AUC shown on dashboard.

### 5 Einstein Position Sizing Advancements

| Module | Signal | Effect |
|---|---|---|
| Fear & Greed | Extreme Fear + UP | 1.20× size |
| Directional Kelly | Per-direction historical WR | Up to 1.60× or down to 0.50× |
| UTC Hour Edge | Self-learned hourly WR | 1.15× / 0.85× |
| Decay Detector | Rolling 30-trade coin WR | 0.80× when WR < 48% |
| Volume Surge | vol24hr > 2× liquidity | 1.15× |

All multipliers stack, capped at **2.8× base Kelly**.

### Kalshi Macro Execution

Trades 6 event categories: politics, economics, sports, financials, crypto, technology. Paper positions close after **30 minutes** with confidence-weighted simulation. Cross-cycle dedup prevents re-entering the same market. Open positions survive bot restarts via `_load_open_from_disk()`.

---

## Dashboard

Real-time via SSE (Server-Sent Events) + 5s polling fallback.

| Component | Refresh | Description |
|---|---|---|
| Bot Status strip | 5s SSE | Balance, P&L, trades, win rate, uptime |
| Live Trade Feed | Instant SSE | Last 15 trades with flash animation |
| Positions | 5s | Open + closed with timestamps, unrealized P&L |
| Equity Curve | 15s | Balance over time |
| ML Calibration | 15s | Training samples, val accuracy, ROC-AUC, phase |
| ZiSi Performance | 15s | WR, avg win/loss, all-time stats |
| Signal Analytics | 15s | By coin, by signal strength |

### Dashboard API

| Endpoint | Description |
|---|---|
| `GET /api/positions` | Active + closed positions + summary |
| `GET /api/events` | SSE stream: balance, trades, heartbeat |
| `GET /api/health` | Full bot health + signal analytics |
| `GET /api/equity` | Balance time-series |
| `GET /api/system-health` | ML status, diagnostics |
| `GET /api/performance` | ZiSi trade performance stats |
| `POST /api/control/pause` | Pause signal processing |
| `POST /api/control/resume` | Resume signal processing |

### Telegram Commands

| Command | Description |
|---|---|
| `/status` | Live balance, P&L, win rate, open positions |
| `/pnl` | Breakdown by market (Polymarket vs Kalshi) |
| `/positions` | All open positions |
| `/performance` | By coin and direction |
| `/help` | Command list |

---

## Utilities

### Clean Slate Reset

```bash
python clean_slate.py              # interactive
python clean_slate.py --force      # skip confirmation
python clean_slate.py --balance 200
```

Resets: `positions_state.json`, `account_state.json`, `system_alerts.json`, `signal_queue.json`  
Preserves: `zisi_local_trades.jsonl`, `ml_labelled_outcomes.jsonl`, `balance_history.jsonl`

---

## Repository Structure

```
ZiSi_Bot/
├── main.py                     Main loop (15-min cycles + rapid-fire scanner)
├── trader.py                   Polymarket paper/live execution
├── updown_trader.py            24/7 RSI UP/DOWN cycle + Einstein advancements
├── rss_fetcher.py              Free multi-source news harvester (new)
├── sentiment_analyzer.py       10-level AI sentiment cascade
├── event_matcher.py            Polymarket event matching
├── signal_router.py            Platform routing (Poly vs Kalshi)
├── risk_manager.py             Kelly sizing + gate chain
├── ml_pipeline.py              Outcome labeller + logistic regression trainer
├── state_manager.py            Account balance + heartbeat persistence
├── health_monitor.py           90s background health checks
├── clean_slate.py              State reset utility
├── data_fetcher.py             Primary news + price fetching
├── kalshi/
│   ├── auth.py                 RSA-PSS signature
│   ├── fetcher.py              6-category market fetch
│   ├── matcher.py              Signal → Kalshi matching
│   └── trader.py               Execution + position lifecycle
└── dashboard/
    ├── backend/
    │   ├── server.js           Express API + bot process manager
    │   └── routes/             REST endpoints + SSE event stream
    └── frontend/src/           React dashboard (Vite)
        └── components/
            ├── BotStatus.jsx   Live balance strip
            ├── Positions.jsx   Active + closed with timestamps
            ├── LiveTradeFeed.jsx  Real-time trade feed (SSE)
            ├── EquityChart.jsx
            ├── MLStatus.jsx
            └── PerformanceCard.jsx
```

---

## GitHub Actions

Two CI workflows run on every push and pull request to `main`:

| Workflow | Trigger | What it checks |
|---|---|---|
| `python-lint.yml` | push / PR | Syntax-checks all `.py` files with `ast.parse`. Flags import errors. |
| `frontend-build.yml` | push / PR | `npm ci` + `npm run build` in `dashboard/frontend`. Catches JSX/CSS errors. |

---

## Troubleshooting

**No trades for several cycles**  
Check `[CYCLE-SUMMARY]` — shows which gate rejects most signals. Common: wide spreads, no matching events, dead-hour filter active (1–4 AM UTC).

**Kalshi not closing trades**  
Open positions are restored from disk on restart via `_load_open_from_disk()`. Hold time is 30 minutes. Check `[KALSHI-CLOSE]` in logs after 30 min.

**Dashboard shows old win rate**  
Win rate recomputes from `positions_state.json` closed array. After mule-history removal it will reflect ZiSi-only trades (72.9%).

**ML still Phase 1**  
`ensure_phase2_activated()` runs at startup. If `ml_labelled_outcomes.jsonl` has 50+ lines and `lr_model.pkl` doesn't exist, training runs immediately. Check `[ML-TRAIN]` in startup logs.

**Rapid scanner not firing**  
`rapid_fire_queue.json` is created when a spike signal is detected. Check `[RAPID]` in logs. Scanner runs every 90s — check for RSS fetch failures under `[RSS]`.

**Ctrl+C / shutdown**  
Uses `threading.Event` — responds immediately. Shadow monitor + rapid scanner stop cleanly. Give 2–3 seconds for final disk writes.
