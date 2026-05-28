# ZiSi — Autonomous Prediction Market Trading Bot

> Self-learning paper-trading bot for Polymarket and Kalshi. Target: $100 → $1,000.

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)
[![Node.js 18+](https://img.shields.io/badge/node-18+-green.svg)](https://nodejs.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## What ZiSi Does

ZiSi runs a 15-minute loop that:

1. **Fetches live crypto news** (NewsAPI + Cointelegraph RSS)
2. **Scores sentiment** through a 10-level AI cascade (Claude → Gemini → Groq → Cerebras → Mistral → OpenRouter → Together → FinBERT → VADER → Keyword)
3. **Matches events** to live Polymarket + Kalshi markets
4. **Sizes positions** with Kelly Criterion (regime + drawdown + signal-type multipliers)
5. **Executes trades** in paper mode (or live CLOB)
6. **Monitors exits** at target/stop/signal-flip/max-hold thresholds
7. **Learns** — labels outcomes and trains a logistic regression confidence model after 50 trades

Additionally, a **24/7 RSI cycle** trades BTC/ETH/SOL UP/DOWN binary markets every cycle, independent of news.

A **Shadow Mule system** copy-trades two expert Polymarket wallets (Mule1, Mule2) in real-time, mirroring positions into paper trades with self-hedging dedup.

---

## Project Status

| Component | Status |
|---|---|
| News sentiment pipeline | ✅ Live |
| UP/DOWN RSI cycle (24/7) | ✅ Live |
| Polymarket event matching | ✅ Live |
| Kalshi 12-category matching | ✅ Live |
| Shadow Mule copy-trading | ✅ Live |
| React dashboard | ✅ Live |
| ML self-learning (Phase 1) | ✅ Collecting |
| ML self-learning (Phase 2) | ⏳ Auto-activates at 50 labelled trades |
| Live trading (real capital) | ⏳ After $1,000 paper target |

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
        └── Shadow Mules → Mule1 + Mule2 copy-trade
        ↓
Position Monitor  (target / stop / signal-flip / max-hold exit)
        ↓
ML Feedback  (label outcomes → train confidence model Phase 2)
```

---

## Key Features

### 10-Level Sentiment Cascade
Never blocks on a single provider. Falls through providers in order — first valid score wins. VADER keyword fallback ensures signals always complete.

### Shadow Mule System
- Polls expert wallets every 15 seconds
- Mirrors new positions into paper trades instantly
- Self-hedging dedup: if a mule enters both UP and DOWN on the same window, only one is mirrored
- If the current window has < 20s left, enters the next available window
- Toggle each mule ON/OFF from the dashboard without restarting

### 24/7 RSI UP/DOWN Cycle
Runs before every news cycle, completely independent of news sentiment. Monitors BTC, ETH, SOL 1-min candles. 3 windows × 3 coins = up to 9 trades per cycle.

### ML Self-Learning
- Phase 1: Raw AI confidence scores (Gemini deflated 0.65× while collecting data)
- Phase 2: Trained logistic regression model replaces raw confidence after 50 labelled outcomes

### React Dashboard
Live-refreshing dashboard at `http://localhost:5000` with balance, P&L, win rate, equity curve, position list, ML calibration progress, and signal analytics.

---

## Quick Start

### Prerequisites
- Python 3.11+
- Node.js 18+
- API keys (at minimum: one AI provider + NewsAPI)

### 1. Install

```bash
pip install -r requirements.txt
pip install vaderSentiment
cd dashboard/backend && npm install && cd ../..
```

### 2. Configure `.env`

```env
KALSHI_API_KEY=your_key_id
NEWSAPI_KEY=your_key
ANTHROPIC_API_KEY=your_key
GEMINI_API_KEY=your_key
GROQ_API_KEY=your_key
TELEGRAM_BOT_TOKEN=your_token
TELEGRAM_CHAT_ID=your_chat_id
BOT_MODE=paper_trading
ACCOUNT_BALANCE=100
```

### 3. Start

```bash
cd dashboard/backend && npm start
```

Open **http://localhost:5000** — the dashboard auto-spawns `python main.py`.

---

## Repository Structure

```
ZiSi_Bot/
├── main.py                     Orchestration + 15-min cycle loop
├── trader.py                   Polymarket paper/live execution
├── updown_trader.py            24/7 RSI UP/DOWN cycle
├── shadow_mode.py              Mule copy-trade engine
├── sentiment_analyzer.py       10-level AI sentiment cascade
├── event_matcher.py            Polymarket event matching
├── signal_router.py            Poly vs Kalshi routing
├── risk_manager.py             Kelly sizing + gate chain
├── ml_pipeline.py              Outcome labeller + trainer
├── state_manager.py            Account state persistence
├── health_monitor.py           Background health checks
├── clean_slate.py              State reset utility
├── kalshi/                     Kalshi auth, fetcher, matcher, trader
└── dashboard/
    ├── backend/server.js       Express API + process manager
    └── frontend/src/           React dashboard (Vite)
```

---

## Contributing

This is an active solo project. PRs welcome for:
- New sentiment providers
- Additional market matching strategies  
- Dashboard improvements
- Test coverage

Please open an issue before starting large features.

---

## Roadmap

- [ ] Multi-timeframe RSI confluence (1m + 3m + 5m)
- [ ] Volume confirmation gate
- [ ] WebSocket real-time dashboard (replace polling)
- [ ] `/performance` and `/mule` Telegram commands
- [ ] Win rate by time-of-day analytics
- [ ] Auto-disable mule if 30-day win rate < 40%
- [ ] Live trading mode (post $1,000 paper target)

---

## License

MIT
