# ZiSi v1 — Autonomous Prediction Market Trading Workstation

ZiSi v1 is an institutional-grade, self-learning, multi-source quantitative trading suite designed for high-velocity predictive arbitrage on **Polymarket** and **Kalshi**. Combining real-time news harvesting, multi-layered AI sentiment cascades, advanced technical signal confluence check-gates, and rolling logistic regression self-learning feedback loops, ZiSi v1 operates completely autonomously 24/7.

```
                  ┌────────────────────────────────────────┐
                  │   News Harvest (11 channels, 24/7)     │
                  │   CoinTelegraph · Decrypt · Reddit...  │
                  └───────────────────┬────────────────────┘
                                      ▼
                  ┌────────────────────────────────────────┐
                  │ 10-Tier Sentiment Cascade (Claude/Gem) │
                  └───────────────────┬────────────────────┘
                                      ▼
                  ┌────────────────────────────────────────┐
                  │ Technical Confluence Gating Check-Rows │
                  │ RSI · Momentum · OFI · volume · spread │
                  └───────────────────┬────────────────────┘
                                      ▼
                  ┌────────────────────────────────────────┐
                  │ Capital Protection & Kelly Bet Sizer  │
                  │ 0.5% base size · Hard-capped $2.00 bet │
                  └───────────────────┬────────────────────┘
                                      ▼
                  ┌────────────────────────────────────────┐
                  │       Active Order Execution           │
                  │ Polymarket UP/DOWN · Kalshi Events     │
                  └───────────────────┬────────────────────┘
                                      ▼
                  ┌────────────────────────────────────────┐
                  │       ML Outcome Retraining Loop       │
                  │ Phase 2 logistic regression feedback   │
                  └────────────────────────────────────────┘
```

---

## Technical Architecture & Core Layers

ZiSi v1 has been completely refactored into a highly modular, decoupled architecture separating business logic from raw execution and presentation layers:

```
ZiSi_Bot/
├── app/                        # Bootstrapping and orchestration
│   ├── main.py                 # Core asyncio runner loop (staggered 15s checks)
│   ├── sovereign_runner.py     # Independent daemon lifecycler
│   └── telegram_bot.py         # Chat ops command & control interface
├── core/                       # Quantitative decision engine
│   ├── engine/                 # Event matchers, order executors, and cycle control
│   ├── ml/                     # Labeling models, outcome logs, and ML pipelines
│   ├── risk/                   # Gating chains and position sizing models
│   └── shared/                 # Config loaders, telemetry, and common utilities
├── infrastructure/             # Native API connectors and state managers
│   ├── exchange/               # Kalshi & Polymarket spot / orderbook feeds
│   ├── state/                  # Heartbeat watchdogs and local state files
│   └── websocket/              # Active streams and prices feeds
└── presentation/               # Multi-platform visual console
    └── dashboard/              # Bento-style administrative control deck
        ├── backend/            # Express.js API gateway + process controller
        └── frontend/           # Premium Vite + React Obsidian-Gold HUD
```

---

## 🔒 1. Capital Protection Risk Sizing Profiles

To protect the trade capital stack from extreme market drawdowns, spike liquidations, or quick succession losses, ZiSi v1 enforces a strict, mathematical risk mitigation system inside `core/risk/position_sizer.py`:

*   **0.5% Base Account Bet Sizing:** Base trade allocations are strictly locked to **0.5% of the total account balance** (e.g., exactly `$0.50` on a `$100.00` bankroll). This ensures a survival runway of over 200 consecutive losses.
*   **Hard Sizing Cap:** To completely isolate the account from sudden indicator failures or extreme volatility spikes, all position sizes are locked behind a **hard absolute limit of $2.00 per trade**. Kelly multipliers and adaptive weights can scale allocations downward, but can never breach this limit.
*   **Regime-Adaptive Kelly Modifiers:** When volatility peaks (high-volatility ATR regime), a strict **0.50x modifier** is immediately applied to all bet sizes, instantly halving risk exposure.
*   **Decay & Expiry Filters:** If an asset's rolling 10-trade win rate falls below 30%, size allocations scale down to **0.50x**. Additionally, markets expiring in less than 1 hour are penalised with a **0.30x scaling factor** to mitigate late-expiry slippage.

---

## 🛡️ 2. Self-Healing Heartbeat Watchdog

To guarantee 100% continuous uptime and bypass silent asyncio freezes, ZiSi v1 integrates an active **Self-Healing Watchdog** directly into the Node.js Express server (`presentation/dashboard/backend/server.js`):

*   **Active Uptime Scans:** The backend server queries the shared `account_state.json` file every **60 seconds**.
*   **Liveness Tracking:** The trading bot updates its timestamped liveness key (`last_updated`) at the beginning of its boot sequence and at the end of every active scanning cycle.
*   **Automatic Restarts:** If the heartbeat becomes **older than 4 minutes (240 seconds)**, the Express server flags a freeze event.
*   **Force Kill & Recover:** On Windows, the watchdog executes a force-kill chain using the system tree-killer:
    `taskkill /F /T /PID <botProcess.pid>`
    Once terminated, Node's unexpected exit hook catches the death, waits 15 seconds for socket buffers to clear, and auto-spawns a fresh Python process under `C:\Python313\python.exe` to restore active trading seamlessly.

---

## 📊 3. Institutional Bento Analytics Tab

The refactored presentation layer introduces an ultra-premium, dark-obsidian bento-style **Analytics Tab** (`presentation/dashboard/frontend/src/components/Analytics.jsx`) that extracts advanced mathematical metrics directly from the live trading states:

*   **Technical Signal Confluence Radar:** A live, comprehensive checklist displaying real-time indicator statuses (RSI, Momentum, Order Flow Imbalance, Volume Multipliers, and AI Sentiment confidence) for all **13 active asset-timeframe loops** (BTC, ETH, SOL, XRP, ADA, LINK, DOGE, AVAX, SUI).
*   **Volatility Regime Radar:** A real-time volatility tracking panel visualising whether the current market state is `NORMAL` or `TURBULENT`, highlighting active ATR percentages and the corresponding Kelly bet modifier applied to the sizer.
*   **Hourly Execution Profitability Heatmap:** A beautiful 24-box HSL-colored grid mapping trade density and win rates to their respective UTC execution hours. This helps identify profitable trading sessions and filters out dead-hour trading zones.
*   **Mathematical Risk & Expectancy Profiles:** High-fidelity metrics displaying the mathematical edge:
    *   *Profit Factor:* Gross profits divided by gross losses.
    *   *Expectancy/Bet:* Net average profit or loss generated per executed position.
    *   *Max Drawdown & Active Lose Streak:* Tracks peak-to-trough capital decline.
    *   *Risk of Ruin Profile:* A probabilistic risk metric based on win rates and sizing caps.
*   **Ensemble ML Retraining Engine:** A progress tracker monitoring the live sample gathering count, illustrating progress towards the 50-sample limit required to trigger automatic retraining of the Phase 2 logistic regression ensemble models.

---

## 🚀 Quick Start Guide

### Prerequisites
*   Windows OS (configured for PowerShell execution)
*   Python `3.13.x` (installed at `C:\Python313\python.exe`)
*   Node.js `18.x` or `20.x`

### 1. Installation

Clone the repository and install the comprehensive dependencies for both the core engine and the dashboard server:

```powershell
# Core Python libraries
pip install -r requirements.txt

# Dashboard dependencies
cd presentation/dashboard/backend
npm install
cd ../frontend
npm install
```

### 2. Configure Environment

Create a `.env` file in the root directory:

```env
# Bot Sizing & Risk Parameters
BOT_MODE=paper_trading              # paper_trading | live_trading
ACCOUNT_BALANCE=100.0
RISK_PER_TRADE_PERCENT=0.5
SIGNAL_THRESHOLD=6.0

# Kalshi & Polymarket Credentials
KALSHI_API_KEY=your_key_id
KALSHI_PRIVATE_KEY="-----BEGIN RSA PRIVATE KEY-----\n...\n-----END RSA PRIVATE KEY-----"

# Multi-Tiered AI Sentiment Keys (First active key takes precedence)
ANTHROPIC_API_KEY=your_claude_key
GEMINI_API_KEY=your_gemini_key
GROQ_API_KEY=your_groq_key
```

### 3. Build & Run

To build the production frontend assets and launch the unified trading suite:

```powershell
# In the repository root
npm run build   # Compiles frontend React bundle
npm start       # Launches Node API server, activates the watchdog, and spawns the bot
```

Navigate to **`http://localhost:5000`** in your browser to access the premium gold-obsidian ZiSi v1 console.

---

## 🛠️ Utilities

### State Resetter (`clean_slate.py`)
To clean up trading states, reset the simulation ledger, or adjust starting balances while keeping ML historical training logs intact:
```powershell
python clean_slate.py --balance 100.0 --force
```
*Resets:* `positions_state.json`, `account_state.json`, `system_alerts.json`, `signal_queue.json`  
*Preserves:* `ml_training_data.jsonl` (protects core logistic regression training logs!)

### Desktop Branding Shortcut
A rounded, custom gold-branded desktop icon is saved at `presentation/dashboard/frontend/public/zisi_desktop_icon.png`. A fast internet shortcut (`ZiSi.url`) resides directly on the Windows Desktop for instant single-click trading hub access.

---

## 🛡️ License & Attributions
**ZiSi v1** is a proprietary high-frequency predictive arbitrage engine. Developed with deep mathematical rigor and state-of-the-art AI-assisted pairs coding.
