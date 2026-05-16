# ZiSi — Autonomous Prediction Market Trading Bot

ZiSi is a self-learning paper-trading bot for Polymarket and Kalshi. It collects live crypto news, scores sentiment with a cascade of AI providers, matches markets, sizes positions with Kelly Criterion, shadow-copies expert wallets, and exits automatically. Target: grow $100 → $1,000 in paper mode, then go live.

---

## Architecture

```
News (NewsAPI + Cointelegraph RSS)
        ↓
Sentiment (Claude → Gemini → Groq → Cerebras → Mistral → OpenRouter → Together → VADER)
        ↓
Signal Classification  (TYPE_A_HIGH / B_HIGH etc.)
        ↓
Event Matching  (Polymarket smart-match + Kalshi 12-category)
        ↓
Gate Chain  (liquidity → spread → price → confluence → MTF → routing)
        ↓
Kelly Sizing  (regime + drawdown + signal-type multipliers)
        ↓
Order Placement  (paper sim or live CLOB)
        │
        ├── Own signals → ZiSi UP/DOWN trades (RSI + momentum, 24/7)
        └── Shadow Mules → Mule1 (PBot6) + Mule2 (Wallet2) copy-trade
        ↓
Position Monitor  (target / stop / signal-flip / max-hold exit)
        ↓
ML Feedback  (label outcomes → train confidence model Phase 2)
```

Cycles run every **15 minutes** (96/day). UP/DOWN RSI trades run every cycle, 24/7.

---

## Quick Start

### Prerequisites
- Python 3.11+
- Node.js 18+
- API keys (see `.env` setup below)

### 1. Install dependencies

```bash
pip install -r requirements.txt
pip install vaderSentiment  # local fallback sentiment
cd dashboard/backend && npm install && cd ../..
```

### 2. Configure `.env`

```env
# Core trading APIs
KALSHI_API_KEY=your_key_id
KALSHI_PRIVATE_KEY="-----BEGIN RSA PRIVATE KEY-----\n...\n-----END RSA PRIVATE KEY-----"
NEWSAPI_KEY=your_key

# AI Sentiment chain (use as many as you have — first valid key wins)
ANTHROPIC_API_KEY=your_key     # Claude — best quality
GEMINI_API_KEY=your_key        # Free 1,500/day
GROQ_API_KEY=your_key          # Free 14,400/day
CEREBRAS_API_KEY=your_key      # Free tier
MISTRAL_API_KEY=your_key       # Free tier
OPENROUTER_API_KEY=your_key    # Free models
TOGETHER_API_KEY=your_key      # $25 free credits

# Telegram alerts
TELEGRAM_BOT_TOKEN=your_token
TELEGRAM_CHAT_ID=your_chat_id

# Bot settings
BOT_MODE=paper_trading         # paper_trading | live_trading
ACCOUNT_BALANCE=100
RISK_PER_TRADE_PERCENT=2
SIGNAL_THRESHOLD=6
```

### 3. Start

```bash
cd dashboard/backend
npm start
```

Dashboard → **http://localhost:5000**

The dashboard auto-spawns `python main.py`. Bot output streams to the terminal.

---

## Features

### ZiSi Own Trades
- **UP/DOWN RSI cycle**: Monitors BTC, ETH, SOL Binance 1-min candles for RSI + momentum signals. Trades 3 windows per coin per cycle. Runs 24/7, unaffected by overnight dead-window.
- **News sentiment trades**: Polymarket and Kalshi event matching from AI-scored news.

### Shadow Mule System
Copy-trades two expert wallets (Mule1 = PBot6, Mule2 = Wallet2) via Polymarket positions API:
- Detects new positions every 15 seconds
- If current window has < 20s left, enters **next available window** (same coin/direction)
- Self-hedging dedup: if Mule2 enters both UP and DOWN on same window, only mirrors one
- Per-mule toggle from dashboard without restarting the bot

### Sentiment Cascade (10 levels)
Priority: Claude → Gemini Flash → Groq Llama → Cerebras → Mistral → OpenRouter → Together → FinBERT → VADER → Keyword

### ML Self-Learning
Phase 1: Gemini confidence deflated 0.65× while collecting 50 labelled outcomes.
Phase 2 (auto-activates at 50): Trained logistic regression replaces raw Gemini confidence.

---

## Dashboard

| Component | Refresh | Description |
|---|---|---|
| Bot Status strip | 5s | Balance, P&L, trades, win rate, uptime |
| Mule controls | 5s | Toggle Mule1/Mule2 shadow copy-trading live |
| Positions | 10s | Open + closed with unrealized P&L |
| Equity Curve | 15s | Balance over time |
| ML Calibration | 15s | Training progress, val accuracy, ROC-AUC |
| Signal Analytics | 15s | By coin, by signal strength |

### Dashboard API

| Endpoint | Description |
|---|---|
| `GET /api/positions` | Active + closed positions + summary |
| `GET /api/health` | Full bot health, metrics, signal analytics |
| `GET /api/equity` | Balance time-series |
| `GET /api/system-health` | ML status, diagnostics |
| `GET /api/control/mules` | Mule enabled/disabled status |
| `POST /api/control/mule/:id/enable` | Enable mule (id = mule1 \| mule2) |
| `POST /api/control/mule/:id/disable` | Disable mule |
| `POST /api/control/pause` | Pause signal processing |
| `POST /api/control/resume` | Resume signal processing |

---

## Clean Slate Reset

```bash
python clean_slate.py              # interactive
python clean_slate.py --force      # non-interactive
python clean_slate.py --balance 200  # different starting balance
```

Resets: `positions_state.json`, `account_state.json`, `system_alerts.json`, `signal_queue.json`
Preserves: `zisi_local_trades.jsonl`, `ml_labelled_outcomes.jsonl`, `balance_history.jsonl`

---

## Configuration

| Variable | Default | Effect |
|---|---|---|
| `ACCOUNT_BALANCE` | 100 | Starting bankroll |
| `RISK_PER_TRADE_PERCENT` | 2 | Max % of balance per trade |
| `SIGNAL_THRESHOLD` | 6 | Min confidence (0–10) to process |
| `BOT_MODE` | paper_trading | `paper_trading` or `live_trading` |
| `MIN_EVENT_LIQUIDITY_USD` | 1000 | Min Polymarket market liquidity |
| `MAX_SIMULTANEOUS_TRADES` | 100 | Open position cap |

---

## Log Reference

```
[PAPER]          Paper trade filled (1 line per trade)
[EXIT] ✅ WIN    Trade closed with profit
[EXIT] ❌ LOSS   Trade closed with loss
[UPDOWN]         UP/DOWN RSI cycle result
[SHADOW] ✅      Shadow trade opened / mirrored / next-window
[SHADOW] ✅ WIN  Shadow trade resolved
[CYCLE-SUMMARY]  End-of-cycle: signals=N placed=M skipped=K
[HEALTH]         Background health check
[RECOVERY]       Startup reconciliation
Heartbeat →      Balance + PnL at cycle end
```

---

## Files

```
ZiSi_Bot/
├── main.py                     15-min cycle loop + orchestration
├── trader.py                   Polymarket paper/live execution
├── updown_trader.py            24/7 RSI + momentum UP/DOWN cycle
├── shadow_mode.py              Mule copy-trade engine
├── sentiment_analyzer.py       10-level AI sentiment cascade
├── event_matcher.py            Polymarket event matching
├── signal_router.py            Platform routing (Poly vs Kalshi)
├── risk_manager.py             Kelly sizing + gate chain
├── ml_pipeline.py              Self-learning outcome labeller + trainer
├── state_manager.py            Account balance + heartbeat persistence
├── health_monitor.py           90s background health checks
├── clean_slate.py              State reset utility
├── kalshi/
│   ├── auth.py                 RSA-PSS signature
│   ├── fetcher.py              12-category market fetch
│   ├── matcher.py              Signal → Kalshi matching
│   └── trader.py               Kalshi execution
└── dashboard/
    ├── backend/server.js       Express API + process manager
    └── frontend/src/           React dashboard (Vite)
```

---

## Troubleshooting

**No trades for several cycles**
Check `[CYCLE-SUMMARY]` — it shows which gate rejects most signals. Common: wide spreads, no matching events.

**UP/DOWN trades not executing**
The RSI cycle runs at `:00` and `:30` minute marks. Check `[UPDOWN]` in logs for RSI values and market availability.

**Shadow mule not trading**
Check `shadow_state.json` exists. Toggle the mule OFF then ON from the dashboard to reset. Verify target wallets have active UP/DOWN positions.

**Dashboard shows 0 closed trades**
This means no trades have resolved yet in this session. Shadow trades resolve within 5–15 min after expiry. ZiSi's own UP/DOWN trades resolve at 30-min hold time.

**Balance not updating**
Balance updates on each trade close. The heartbeat (every cycle) recomputes from the in-memory balance — if you see a discrepancy, wait for the next cycle.

**Ctrl+C / shutdown**
The bot responds immediately to Ctrl+C (uses threading.Event, not time.sleep). Shadow monitor stops cleanly. Give it 2–3 seconds to finish the current write.
