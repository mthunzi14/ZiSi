# ZiSi — Autonomous Paper Trading Bot

ZiSi is a fully autonomous paper trading bot for [Polymarket](https://polymarket.com) binary prediction markets. It trades crypto Up/Down markets — 5-minute and 15-minute binary outcomes on BTC, ETH, SOL, XRP, and DOGE.

Built from the ground up over several months, ZiSi combines real-time oracle pricing, multi-timeframe signal analysis, adaptive position sizing, and a full Bloomberg-style monitoring dashboard.

---

## Architecture

```
app/
  main.py                  — Asyncio entry point, trade execution, risk gates
core/
  engine/
    updown_engine.py       — Primary signal engine (FV signal, confluence cascade)
    cycle_manager.py       — Candle-boundary LAT scanner daemon
    session_governor.py    — Cross-asset trade slot coordination
    confluence_engine.py   — Multi-timeframe RSI/momentum confluence scoring
    regime_filter.py       — Market regime detection (Trending / Mean-Rev / Chaos)
  risk/
    risk_manager.py        — Kelly sizing, daily loss gate, entry price gate
  pyth_oracle_service.py   — Real-time Pyth oracle price feed
infrastructure/
  websocket/               — Polymarket L2 CLOB WebSocket gateway
  exchange/                — Order placement, positions state
  state/                   — Account state, engine status, balance history
presentation/
  dashboard/
    backend/               — Express.js API server (Node.js)
    frontend/              — React + Recharts Bloomberg terminal UI
miscellaneous/
  clean_slate.py           — Session archive + state reset utility
```

---

## Trade Types

ZiSi executes four distinct trade types, each tracked separately:

| Type | Label | Description |
|------|-------|-------------|
| Fair Value | `FV` | Enters when the Pyth oracle price diverges from the Polymarket contract's implied fair value |
| Latency Arbitrage | `LAT` | Fires at T-15s or T-5s before candle close when Pyth shows a clear directional edge |
| Reversal Snipe | `REV` | Counter-trend entry on exhausted momentum |
| Signal | `SIG` | Full 18-layer confluence cascade: RSI, OFI, FinBERT, Markov chain, multi-TF alignment |

---

## Signal Engine

### Fair Value (FV) Signal
Compares the real-time Pyth oracle price against the current Polymarket contract price. When the market's implied probability diverges from Pyth's directional signal by at least 5c, a fair-value entry is triggered.

**FV protection gates:**
- Macro-aware edge penalty: when 5+/8 prior candles oppose the FV direction, the edge bar raises to 18c or 25c
- Cross-TF conflict: when the 15m candle contradicts the 5m FV signal, edge bar raises
- Price floor: entries below 15c contract price are blocked unless the Pyth move is >= 0.4%

### Latency Arbitrage (LAT) Scanner
A background daemon fires at two candle-close windows:
- **T-15s**: Standard scan — Pyth divergence >= 0.3% (15m) or >= 0.5% (5m)
- **T-5s**: Near-certainty scan — candle direction is locked in, Pyth freshness < 3s required

LAT includes a 60-second global cooldown to prevent simultaneous multi-asset false-signal entries.

### Confluence Cascade (SIG)
18 signals evaluated and scored:
- RSI (1m, 5m, 15m, 1h)
- Order Flow Imbalance (OFI)
- FinBERT sentiment (crypto news)
- Markov chain state transitions
- Volume relative to 14-period average
- Multi-timeframe momentum alignment

Minimum score of 6/18 required to fire a SIG trade.

---

## Risk Management

**Position sizing:** Adaptive Kelly Criterion — scales with signal confidence, current balance, and session multiplier.

**Gates (in order of evaluation):**
1. Regime filter — VOLATILE CHAOS reduces sizing 70%, blocks SIG entirely
2. Timing gate — entries only within the first 15s of a candle (engine) or T-15s/T-5s windows (LAT)
3. Fair value edge gate — minimum FV edge required (5c base, raised by macro conflict)
4. Macro gate — blocks directional trades when 6+/8 candles strongly oppose signal direction
5. Corroboration — peer asset agreement adjusts sizing (1.3x with peer, 0.7x without)
6. Spread gate — maximum 15% bid-ask spread enforced
7. ATM gate — LAT entries blocked in 44-56c zone (coin-flip territory)
8. Price floor — dynamic: weak signals below 15c contract price blocked
9. Session governor — concurrent position limits, BTC/ETH tier-1 priority
10. Daily loss circuit breaker — configurable halt threshold

**Concurrent position support:** BTC/ETH 5m and 15m markets run simultaneously. BTC and ETH are always allocated a slot ahead of lower-tier assets.

**Cross-asset lag entry:** When BTC fires a strong directional signal (>= 0.5% Pyth move) and ETH hasn't priced in the move yet, ETH is automatically entered in BTC's direction.

---

## Dashboard

Bloomberg-style monitoring terminal built in React + Recharts, served via Express.

**Real-time panels:**
- **Trade Ledger** — full trade history with entry/exit/hold/reason, session analytics, asset heatmap, "Why No Trade" status pill
- **Portfolio Performance** — equity curve, win rate chart, per-type breakdown (LAT/FV/SIG/REV)
- **System Health** — heartbeat, uptime, open positions, circuit breaker status, session KPIs
- **Scanning Grid** — per-asset/timeframe cards with live Pyth oracle price, candle countdown timer, unrealized P&L
- **Gate Event Log** — real-time stream of gate blocks with reason codes
- **Session Analytics** — peak P&L, max drawdown, LAT/FV/SIG equity sparklines

**Dashboard API** (port 5000):
```
GET /api/positions       — all active + closed trades
GET /api/state           — account balance, engine heartbeat
GET /api/engine-status   — current scanner state (why no trade)
GET /api/gate-log        — recent gate blocking events
GET /api/asset-macro     — per-asset 8-candle macro direction
GET /api/bot-logs        — live log tail (?lines=N&filter=keyword)
```

---

## Infrastructure

- **VPS:** Ubuntu, Python 3.13, Node.js v20, PM2 process manager
- **Oracle:** Pyth Network (real-time price feeds, < 2s latency)
- **L2 Books:** Polymarket CLOB WebSocket gateway (live bid-ask, no phantom entries)
- **Regime detection:** ATR-based volatility classification updated each candle
- **Session management:** Archive system preserves full session history; clean slate resets all state files

---

## Session Workflow

```bash
# Deploy updates
cd /root/ZiSi && git pull origin main && pm2 restart 3

# Clean slate — archive session + reset balance
python3 miscellaneous/clean_slate.py --force --balance 50 && pm2 restart 3

# View live logs
pm2 logs 3 --lines 100
```

---

## Performance Targets

| Metric | Target |
|--------|--------|
| Trades per day | >= 15 |
| Win rate | 65-75% |
| Profit factor | > 1.5 |
| Max daily drawdown | < 15% |

---

## Status

Active paper trading. Targeting transition to live capital after consistent multi-session win rate >= 65% is demonstrated across diverse market conditions.

---

*Built by Mthunzi Sibiya*
