# 🧬 ZiSi v2 — Autonomous Prediction Market Trading Workstation
## Powered by the ZiSi Edge Architecture

ZiSi v2 is an institutional-grade, high-frequency, multi-source quantitative trading workstation designed for high-velocity predictive arbitrage on **Polymarket** and **Kalshi**. 

Equipped with the **ZiSi Edge Architecture**, this workstation operates fully autonomously 24/7, employing a multi-layered matrix of 11 mathematical advancements that optimize entry timing, execute predictive hedging, dynamically size bets based on edge, manage portfolio heat, and calibrate optimal exits.

```
                      ┌────────────────────────────────────────┐
                      │   News Harvest (11 channels, 24/7)     │
                      │   CoinTelegraph · Decrypt · Reddit...  │
                      └───────────────────┬────────────────────┘
                                          ▼
                      ┌────────────────────────────────────────┐
                      │  Vol Surface (E) & Whale Tracker (J)   │
                      │  Funding Rates · Open Interest · Flow  │
                      └───────────────────┬────────────────────┘
                                          ▼
                      ┌────────────────────────────────────────┐
                      │  Regime-Shift (A) & Confluence (G)     │
                      │  TRENDING · MEAN_REVERT · COMPRESS...  │
                      │  1m / 5m / 15m / 1h Timeframe Engine   │
                      └───────────────────┬────────────────────┘
                                          ▼
                      ┌────────────────────────────────────────┐
                      │   Adaptive Kelly (D) & Anti-Fragile(M) │
                      │   Half-Kelly · Correlation Heat (L)    │
                      └───────────────────┬────────────────────┘
                                          ▼
                      ┌────────────────────────────────────────┐
                      │        Active Order Execution          │
                      │  Polymarket UP/DOWN · Kalshi Events    │
                      │  Dual-Arbitrage · Private RPC Bundles  │
                      └───────────────────┬────────────────────┘
                                          ▼
                      ┌────────────────────────────────────────┐
                      │      RL Exit (I) & ML Retrain (H)      │
                      │  Q-Learning Exits · PyTorch LSTM       │
                      └────────────────────────────────────────┘
```

---

## 🏗️ Technical Architecture & Project Structure

The codebase has been refactored into a clean, modular structure. All scratchscripts, obsolete archives, and non-essential documentation files have been consolidated into dedicated folders to maintain a highly professional repository layout:

```
ZiSi_Bot/
├── app/                        # Bootstrapping and orchestration
│   ├── main.py                 # Core asyncio loop (staggered 15s checks)
│   ├── sovereign_runner.py     # Independent daemon lifecycler
│   └── telegram_bot.py         # Chat ops command & control interface
├── core/                       # Quantitative decision engine
│   ├── engine/                 # Market classifiers, order executors, and cycle control
│   │   ├── confluence_engine.py  # Asynchronous multi-timeframe confluent scanner
│   │   ├── cross_asset_propagator.py # Lead-lag velocity cascade propagator
│   │   ├── edge_orchestrator.py  # Master integration & context builder
│   │   ├── liquidity_heatmap.py  # L2 book stop-hunt & magnet cluster mapper
│   │   ├── polytope_solver.py    # Simplex & Bregman KL projection solver
│   │   ├── regime_detector.py    # 4-state advanced market regime classifier
│   │   └── updown_engine.py      # Per-asset async trading engine & signal generator
│   ├── ml/                     # Machine learning models, outcome loggers, and pipelines
│   │   ├── ai_injector.py        # In-memory LSTM model inference wrapper
│   │   ├── ml_pipeline.py        # Ensemble logistic regression retraining engine
│   │   ├── rl_exit_optimizer.py  # Tabular Q-learning reinforcement exit trainer
│   │   └── training_pipeline.py  # PyTorch LSTM network training pipeline
│   ├── risk/                   # Gating chains and position sizing models
│   │   ├── antifragile.py        # Streak-based dynamic aggression multiplier
│   │   ├── portfolio_heat.py     # Correlation-based position sizer dampener
│   │   └── position_sizer.py     # Math-optimal half-Kelly adaptive position sizer
│   └── shared/                 # Config loaders, telemetry, and common utilities
├── infrastructure/             # Native API connectors and state managers
│   ├── exchange/               # Kalshi & Polymarket orderbook REST connectors
│   │   └── trader.py           # Execution ledger and atomic transaction builder
│   ├── state/                  # Heartbeat watchdogs and local database state files
│   │   └── balance_history.py  # Equity tracking and chronological balance snapshots
│   └── websocket/              # High-frequency WS ingest feeds
├── presentation/               # Multi-platform visual console
│   └── dashboard/              # Bento-style administrative control deck
│       ├── backend/            # Express.js API gateway + process controller
│       └── frontend/           # Premium Vite + React Obsidian-Gold HUD
├── tests/                      # Unified unit and integration test suite
│   ├── test_edge_integration.py # Integration validation for all new Edge modules
│   └── test_*.py               # Component-level verification tests
├── miscellaneous/              # Consolidation folder for design, prompt, logo, and utility files
└── scratch/                    # Temporary developer debug scripts
```

---

## 🧬 The 11 Mathematical Edge Advancements

ZiSi v2 implements 11 specialized algorithmic models, completely integrated into the core pipeline:

### 📊 1. [A] Enhanced Regime-Shift Detector (`core/engine/regime_detector.py`)
Classifies the market in real-time into one of four statistical states:
*   `TRENDING`: Strong directional price velocity (lower entry hurdles, trailing stops, aggressive sizing).
*   `MEAN_REVERTING`: Oscillating price action (tighter entry hurdles, fixed target exits, standard sizing).
*   `VOLATILE_CHAOS`: Unpredictable high-velocity swings (extremely tight hurdles, immediate stops, minimal sizing).
*   `COMPRESSION`: Low-volatility range squeeze (reduced entry hurdles, breakout-hold exits, moderate sizing).
*   *Parameters*: Utilizes Average True Range (ATR) percentiles, Bollinger Band Width compression, Order Book Imbalance (OBI), and volume profiles.

### 📈 2. [B] Cross-Asset Signal Propagation (`core/engine/cross_asset_propagator.py`)
Exploits inter-asset lead-lag dynamics by tracking BTC price velocity. When BTC moves $>0.15\%$ in $<30$ seconds, the module calculates the Pearson correlation coefficient with lagging alts (ETH, SOL, XRP) and issues pre-emptive directional entries *before* the altcoin's own indicators register.

### 📐 3. [D] Adaptive Kelly Sizing (`core/risk/position_sizer.py`)
Computes the mathematically optimal bet allocation using the **half-Kelly criterion**:
$$f^* = \frac{bp - q}{2b}$$
where $p$ is the dynamic win probability (calibrated via Multi-TF confluence, derivatives sentiment, and historical performance), and $b$ is the current Polymarket payout ratio. It enforces a strict $0.50$ floor, $5.00$ ceiling, and caps risk at $5.0\%$ of total bankroll.

### 🌊 4. [E] Volatility Surface Analysis (`core/engine/volatility_surface.py`)
Integrates derivatives-market funding rates and Open Interest (OI) changes. Positive funding rate extremes ($>0.05\%$ per 8h) act as contrarian indicators, while rising OI confirms trend momentum, modifying quant confidence scores dynamically.

### 🗺️ 5. [F] Liquidity Heatmap / Stop-Hunt Detection (`core/engine/liquidity_heatmap.py`)
Scans the L2 order book to locate depth clusters (volumes $>3\times$ average). It uses these clusters to:
*   Identify price levels acting as short-term magnets.
*   Place smart stop-losses safely behind order walls.
*   Detect typical stop-hunt setups, skipping high-risk entry zones.

### ⏱️ 6. [G] Multi-Timeframe Confluence Engine (`core/engine/confluence_engine.py`)
Asynchronously queries Binance endpoints to compute RSI and Momentum signals across 1m, 5m, 15m, and 1h intervals. Generates a Confluence Score (0 to 4) representing timeframe alignment, which feeds directly into the Kelly win-rate model.

### 🧠 7. [H] Trained LSTM/Transformer Predictor (`core/ml/training_pipeline.py`)
Upgrades the machine learning layer to train a PyTorch LSTM model on historical candle data annotated with the active regime label. The trained model acts as a high-conviction veto or confidence booster.

### 🎮 8. [I] Reinforcement Learning Exit Optimizer (`core/ml/rl_exit_optimizer.py`)
Implements a tabular Q-learning model that learns optimal exit timing based on time elapsed, current P&L, market regime, and momentum direction, dynamically adjusting standard targets.

### 🐳 9. [J] On-Chain Whale Tracking (`core/engine/whale_tracker.py`)
Tracks large-size market orders ($>10\times$ median trade volume) to evaluate whale buyer/seller pressure ratios, generating signal multipliers to verify institutional momentum.

### 🌡️ 10. [L] Portfolio Heat Management (`core/risk/portfolio_heat.py`)
Aggregates rolling correlation scores between open positions and dampens new bet sizes proportionally when correlated directional exposure builds up, preventing black-swan account drawdowns.

### 🛡️ 11. [M] Anti-Fragile Recovery System (`core/risk/antifragile.py`)
Implements a streak-based aggression sizer. A 5-trade winning streak unlocks a $1.2\times$ sizing multiplier for compounding. A 3-trade losing streak triggers a $0.6\times$ defensive size reduction, and drawdowns $>10.0\%$ restrict sizes to $0.3\times$ to guarantee account preservation.

---

## 🔒 Verification & Test Success

ZiSi v2 is fully verified. To run the unified unit and integration test suite, run:

```powershell
python -m unittest discover tests
```

Output:
```text
Ran 44 tests in 2.508s

OK
```

The test suite includes a new comprehensive integration test (`tests/test_edge_integration.py`) verifying that the `UpDownEngine` successfully coordinates with the `EdgeOrchestrator`, queries derivatives sentiment, adjusts sizes via `PositionSizer`, and feeds trade outcomes back into the `AntifragileSystem`.

---

## 🖥️ Obsidian-Gold UI Dashboard Build

The premium Bento-style React control deck has been fully updated. The **RegimeRadarHUD** component renders all 4 Edge Architecture market regimes, accompanied by golden gradient intensity gauges representing real-time volatility.

To build the React production assets and launch the unified workstation:

```powershell
# Compile the React frontend assets
npm run build

# Start the Node.js API server, watchdog, and Python bot process
npm start
```

Navigate to **`http://localhost:5000`** in your browser to access the active workstations console.

---

## 🛡️ License & Attributions
**ZiSi v2** is a proprietary high-frequency predictive arbitrage engine. Developed with deep mathematical rigor and state-of-the-art AI-assisted pairs coding.
