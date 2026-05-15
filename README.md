# ZiSi — Prediction Market Sentiment Trading Bot

ZiSi is an autonomous paper-trading bot for prediction markets (Polymarket + Kalshi). It collects live crypto news, scores sentiment with AI, matches to open markets, sizes positions with Kelly Criterion, and exits automatically on profit targets, stop-loss, or max hold time.

---

## How It Works

```
News (NewsAPI + Cointelegraph RSS)
        ↓
Sentiment Analysis  (Gemini / Anthropic / Groq — batch)
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
        ↓
Position Monitor  (target / stop / signal-flip / max-hold exit)
        ↓
ML Feedback  (label outcomes → train confidence model Phase 2)
```

Cycles run every **15 minutes** (96/day). Each cycle processes 25+ articles.

---

## Quick Start

### Prerequisites
- Python 3.11+
- Node.js 18+
- API keys (see `.env` setup below)

### 1. Install dependencies

```bash
cd C:\Users\mthun\Downloads\ZiSi_Bot
pip install -r requirements.txt
cd dashboard/backend && npm install && cd ../..
```

### 2. Configure `.env`

```env
# Kalshi (key ID + RSA private key PEM)
KALSHI_API_KEY=your_key_id_here
KALSHI_PRIVATE_KEY=-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----

# Polymarket (CLOB endpoints — defaults work without changes)
POLYMARKET_GAMMA_API_URL=https://gamma-api.polymarket.com
POLYMARKET_DATA_API_URL=https://data-api.polymarket.com
POLYMARKET_CLOB_API_URL=https://clob.polymarket.com

# AI sentiment (at least one required; Gemini flash is free)
GEMINI_API_KEY=your_key
ANTHROPIC_API_KEY=your_key    # optional fallback
GROQ_API_KEY=your_key         # optional fallback

# News
NEWSAPI_KEY=your_key

# Notifications (optional)
TELEGRAM_BOT_TOKEN=your_token
GMAIL_SENDER_EMAIL=you@gmail.com
GMAIL_APP_PASSWORD=your_app_password
GOOGLE_DRIVE_FOLDER_ID=your_folder_id
```

### 3. Start

```bash
cd dashboard/backend
npm start
```

Dashboard → **http://localhost:5000**

The dashboard spawns `python main.py` automatically. Bot output streams directly to the terminal.

---

## Clean Slate Reset

Before a fresh run, reset state files without deleting trade history:

```bash
python clean_slate.py              # interactive (confirms before wiping)
python clean_slate.py --force      # non-interactive
python clean_slate.py --balance 200  # reset with different starting balance
```

What gets reset: `positions_state.json`, `account_state.json`, `system_alerts.json`, `signal_queue.json`

What is preserved: `zisi_local_trades.jsonl`, `ml_labelled_outcomes.jsonl`, `balance_history.jsonl`, `category_win_rates.json`

---

## Configuration

Key values are in `.env` / `config.py`:

| Variable | Default | Effect |
|---|---|---|
| `ACCOUNT_BALANCE` | 100.00 | Starting bankroll |
| `RISK_PER_TRADE_PERCENT` | 2.5 | Max % of balance per trade |
| `SIGNAL_THRESHOLD` | 7 | Min Gemini confidence (0–10) to process |
| `BOT_MODE` | paper_trading | `paper_trading` or `live_trading` |
| `MIN_EVENT_LIQUIDITY_USD` | 1000 | Min Polymarket market liquidity |

Trading gates (not in `.env`, hardcoded in `main.py`):

| Gate | Threshold | Reason |
|---|---|---|
| Liquidity | ≥ $1 000 | Avoid dead markets |
| Spread | ≤ 8% | Avoid wide bid/ask |
| Price | 0.10 – 0.90 | Avoid near-resolved markets |
| Confluence | ≥ 0.50 | Require multi-source signal agreement |
| MTF | ≥ 0.33 OR conf ≥ 0.70 | Price + trend confirmation |
| Drawdown pause | ≥ 15% | Stop digging when losing |
| Drawdown halt | ≥ 20% | Hard stop — require manual restart |

---

## Log Patterns

```
[SIGNAL]         News article scored
[GATE]           Gate decision
[CYCLE-SUMMARY]  End-of-cycle: signals=N placed=M skipped=K (reason_A:x | reason_B:y)
[TRADE]          Order placement
[PAPER]          Paper-trade simulated fill
[FILLED]         Shares acquired
[EXIT]           Position closed
[KALSHI]         Kalshi activity
[ROUTING]        Platform routing decision
[SKIP]           Gate rejection reason
[CYCLE-MANAGER]  Multi-signal routing pass
[HEALTH]         90-second background health check
[RECOVERY]       Startup position reconciliation
[DIAG]           Startup diagnostic output
```

---

## Dashboard API

| Endpoint | Description |
|---|---|
| `GET /api/positions` | Active + closed positions with summary |
| `GET /api/positions/active` | Open positions only |
| `GET /api/trades` | Full trade history (from `zisi_local_trades.jsonl`) |
| `GET /api/metrics` | Daily metrics (win rate, Sharpe, drawdown) |
| `GET /api/health` | 5-point health check result |
| `GET /api/equity` | Balance-over-time equity curve |
| `GET /api/alerts` | Last 50 system alerts |
| `GET /api/signal-queue` | Last 50 signal routing decisions |
| `GET /api/system-health` | Detailed diagnostics |
| `POST /api/control/pause` | Pause signal processing |
| `POST /api/control/resume` | Resume signal processing |

---

## Files

```
ZiSi_Bot/
├── main.py                     Entry point + 15-min cycle loop
├── trader.py                   Polymarket order execution (paper + live)
├── kalshi/
│   ├── auth.py                 RSA-PSS signature auth
│   ├── fetcher.py              12-category market fetch
│   ├── matcher.py              Signal → Kalshi event matching
│   └── trader.py               Kalshi order execution
├── sentiment_analyzer.py       AI sentiment scoring (Gemini/Anthropic/Groq)
├── event_matcher.py            Polymarket event matching
├── risk_manager.py             Kelly sizing, liquidity/price gates
├── signal_router.py            Platform routing decision logic
├── cycle_manager.py            Multi-signal routing + conflict detection
├── health_monitor.py           90s background health checks
├── regime_detector.py          ATR-based market regime + Kelly multiplier
├── ml_pipeline.py              Feature collection + model training
├── clean_slate.py              State file reset utility
│
├── dashboard/
│   ├── backend/server.js       Express API + bot process manager
│   └── frontend/src/           React dashboard
│
└── State files (gitignored, runtime only):
    ├── positions_state.json    Open/closed positions
    ├── account_state.json      Balance + PnL
    ├── zisi_local_trades.jsonl Trade history (append-only)
    ├── ml_labelled_outcomes.jsonl  ML training data
    ├── system_alerts.json      Health monitor alerts
    └── signal_queue.json       Last 50 signal evaluations
```

---

## Understanding Trade Volume

ZiSi is intentionally conservative. With 25 articles per cycle, most signals are filtered out:

| Gate | Typical rejection rate |
|---|---|
| No matching Polymarket event | ~50% of signals |
| Price out of range (0.10–0.90) | ~20% |
| Spread > 8% | ~10% |
| Confluence < 0.50 | ~10% |
| Routing → Kalshi | ~5% |

**Expected trade volume**: 2–5 trades/day in normal market conditions. The `[CYCLE-SUMMARY]` log line at cycle end shows exactly where signals drop out.

---

## Phase 1 → Phase 2 Upgrade Path

| Phase | Goal | Signal source | Kelly base |
|---|---|---|---|
| Phase 1 (now) | Collect 50+ labelled outcomes | Gemini raw confidence | 0.25 deflated |
| Phase 2 | Use trained ML model | Blended (Gemini + ML) | Full Kelly |

The ML model trains automatically once 50 labelled examples accumulate. No manual action needed.

---

## Troubleshooting

**No trades for several cycles**
- Check `[CYCLE-SUMMARY]` in logs — it shows which gate is rejecting most signals
- Common cause: all Polymarket markets in the right price range already have wide spreads

**Kalshi disabled at startup**
- Ensure `.env` has both `KALSHI_API_KEY` (the key UUID) and `KALSHI_PRIVATE_KEY` (PEM format)
- Test: `python -c "from kalshi.auth import KalshiAuth; a=KalshiAuth(); print(a.is_configured, a.validate_connection())"`

**Dashboard shows 0 positions**
- Verify bot is running: check terminal output
- Confirm `positions_state.json` exists and has correct structure
- Run `python clean_slate.py --force` to reinitialize state files

**Bot keeps restarting**
- Read the Python traceback in the terminal
- Most common: missing API key, import error, or corrupted state file
