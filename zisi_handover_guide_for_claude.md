# ZiSi Bot Master Handover & Knowledge Base for Claude Code

Welcome, Claude! This document compiles everything achieved, all findings compiled across our recent optimization sessions, the active system state, instructions on how to interact with the VPS (clean slate, log tailing, command execution), and the future roadmap.

---

## 1. Executive Summary & Recent Accomplishments

Over the last few sessions, we successfully upgraded the bot from **v3.0 to v4.2**, resolving critical latency, entry-timing, pricing bias, and dashboard display issues.

### 1.1 Rebuild v4.0: Resolving the Momentum Trap
- **Momentum Trap**: The `SIG-CONFIRM` gate was rejecting trades at candle open when price hadn't moved. However, the evaluation loop in `app/main.py` kept retrying, resulting in late entries 2–3 minutes into 5m candles. These late entries bought at the worst prices and resolved worthless due to mean reversion.
- **Fix**: Enforced a strict 15.0-second boundary limit inside `_evaluate_market_signals` in `app/main.py`. Any evaluation beyond 15s of the candle open is discarded to completely eliminate late entries.

### 1.2 Rebuild v4.1: Pre-fetch & Fallback Slug Alignment
- **Off-by-One Slugs**: Polymarket up/down event slugs are keyed by their *expiry* timestamp. The bot previously calculated the pre-fetch slugs using the *start* timestamp of the candle boundary. This caused it to pre-fetch already expired markets.
- **Fix**: Updated `prefetch_upcoming_market` in `core/engine/updown_engine.py` and the fallback offset loop in `_fetch_market` to target the future expiry boundary. Signal retry windows were adjusted from 15s to 30s to allow newly listed CLOB order books to populate bids/asks at candle open.

### 1.3 Rebuild v4.2: Chainlink RTDS Integration & Dashboard Oracle Swap
- **Oracle Swap**: Commented out the obsolete Pyth Hermes stream to bypass REST rate limits and basis risk. Built a public WebSocket client in [polymarket_rtds_ingest.py](file:///c:/Users/mthun/Downloads/ZiSi_Bot/infrastructure/websocket/polymarket_rtds_ingest.py) that streams the official **Chainlink pricing feed** (`crypto_prices_chainlink`) directly from Polymarket's RTDS.
- **Disk Pricing Cache**: Added a background task `_write_cache_to_disk_loop()` to dump the global Chainlink price cache to `chainlink_prices.json` and `pyth_prices.json` (for backward compatibility) every 500ms in the root folder.
- **Dashboard Routing & Frontend UI**: 
  - Updated `/api/health` in [health.js](file:///c:/Users/mthun/Downloads/ZiSi_Bot/presentation/dashboard/backend/routes/health.js) to load `chainlink_prices.json` and return `chainlinkPrices`.
  - Updated [AssetCards.jsx](file:///c:/Users/mthun/Downloads/ZiSi_Bot/presentation/dashboard/frontend/src/components/AssetCards.jsx) to consume `chainlinkPrices` and display the **"Chainlink oracle"** label instead of "Pyth oracle".

---

## 2. Compendium of Core Findings

### 2.1 Pricing Feeds and Resolution Basis Risk
- **Pyth Hermes Stream**: Found to have a persistent -3 to -5 bps basis bias compared to Binance spot closes, triggering wrong-direction trades.
- **Binance WebSocket Feed**: Used for spot tracking in our latency edge scanner and reversal sniper to match execution venue realities.
- **Chainlink RTDS**: Since Polymarket crypto markets resolve from Chainlink, this is the ultimate source of truth for resolution pricing and fair value strike calculations.

### 2.2 Proximity & Sizing Controls
- **NCS Proximity Guard**: Adjusted to `0.25 * _atr14` in `cycle_manager.py` to block snipes if the spot price is within 25% of the 14-period ATR relative to the strike, avoiding tail-loss noise.
- **Streak Reversal Sizing**: Sized down streak reversal strategies to quarter-Kelly to cap potential losses at ~$1.50 per trade.

---

## 3. Remote VPS Operations Guide

Since you run in a local sandbox, you must execute all VPS operations through the local SSH tunnel port-forwarding on port **`9090`** (which maps to VPS port `5000`).

### 3.1 Executing VPS Commands
Use the local `/api/control/exec` endpoint to execute arbitrary shell commands on the VPS:

```python
import urllib.request
import json

def run_vps_cmd(command: str):
    url = "http://127.0.0.1:9090/api/control/exec"
    payload = {"command": command}
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    with urllib.request.urlopen(req) as resp:
        res = json.loads(resp.read().decode("utf-8"))
        print(res.get("stdout", ""))
```

### 3.2 Tailing PM2 Logs
To inspect bot logs or standard output:
```python
# Tailing PM2 output
run_vps_cmd("pm2 logs zisi-dashboard --lines 50 --no-color")

# Viewing console log files
run_vps_cmd("tail -n 100 /root/ZiSi/zisi_bot_console.log")
```

### 3.3 How to Safely Execute a Clean Slate Reset

A clean slate reset archives the session, wipes logs/databases on the VPS, and resets the paper balance (typically to `$50.00`).

> [!WARNING]
> **Active Position Lock**: Never execute a clean slate if there are active trades open. Query `http://127.0.0.1:9090/api/positions` first. If `active` contains open trades, wait or warn the user.

To run the clean slate reset safely:
```python
import urllib.request
import json

# 1. Fetch positions to ensure active count is 0
req = urllib.request.urlopen("http://localhost:9090/api/positions")
positions = json.loads(req.read().decode("utf-8"))
if len(positions.get("active", [])) > 0:
    raise Exception("Cannot clean slate: Active trades are currently open!")

# 2. Trigger reset endpoint with starting balance
reset_payload = {"balance": 50}
req_reset = urllib.request.Request(
    "http://localhost:9090/api/control/reset",
    data=json.dumps(reset_payload).encode("utf-8"),
    headers={"Content-Type": "application/json"},
    method="POST"
)
try:
    with urllib.request.urlopen(req_reset, timeout=10) as resp:
        print(resp.read().decode("utf-8"))
except Exception as e:
    # Note: A socket closure/timeout is expected on success because the server restarts PM2
    print("Reset request sent (disconnect expected on success).")
```

---

## 4. Current System State

- **Starting Balance**: `$50.00`
- **Active Positions**: `0`
- **Realized PnL**: `+$5.94` (ETH/5m YES trade won at 10:35 UTC)
- **Log Errors**: Clean (No `pyth_prices.json` warnings or other errors in `zisi-dashboard-err.log`).
- **Websockets**: Connected and streaming HFT (Binance) and RTDS (Chainlink) feeds.

---

## 5. Next Steps & Future Directions
1. **Model Fine-Tuning**: Optimize the AI Injector to load pre-trained weights instead of running in observe-only mode.
2. **Exposure Cap Checks**: Add a configuration option to dynamically scale correlated asset exposure caps if market conditions are highly mean-reverting.
3. **Execution Latency**: Measure and log order execution times to optimize our Polymarket API execution paths.
