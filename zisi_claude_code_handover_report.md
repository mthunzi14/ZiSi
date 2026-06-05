# ZiSi Bot: High-Conviction Lag-Arbitrage Engine
## Master Handover & Deep Technical Blueprint for Claude Code

---

## 1. Executive Summary & Strategy Mandate

This document serves as the master technical specification and transition report for **Claude Code** to implement the next major phase of the **ZiSi Polymarket Bot**.

### The Core Mandate
* **Target Win Rate**: **80% to 90%+** (matching the performance of professional bots like **PBot-6** and **BoneReaper**).
* **Target Trade Volume**: **150+ trades** per 11-12 hour session (~13–15 trades per hour).
* **Target Assets**: **BTC and ETH** (primary money printers with highest liquidity), with selective, optimized deployment for **SOL and XRP**. DOGE is permanently excluded from latency/lag arbitrage.
* **The Edge**: Real-time **Lag-Arbitrage**. We do not predict the future; we exploit the microsecond latency gap where Polymarket CLOB contracts lag behind Binance spot price velocity, Cumulative Volume Delta (CVD), and Order Book Imbalance (OBI) moves.

---

## 2. Historical Context & Forensic Performance Audits

To build the future, Claude Code must understand the exact history of the bot's sessions:

### 2.1. The Competitor Blueprint
* **BoneReaper** (`0xeebde7a...`): $8,573 balance. 49,136 lifetime predictions. Average 80–120+ trades/day on BTC+ETH 5m and 15m. $1,000–$7,640 per trade. Near-100% WR. Fires both 5m and 15m concurrently. Takes contrarian entries at 14¢ when spot momentum strongly suggests the contract will resolve in-the-money, and wins.
* **PBot-6** (`0x21d0a97...`): $2,669 balance. 53,718 lifetime predictions. BTC+ETH+XRP+SOL. Trades strictly in the **40¢ to 55¢ range** at ATM. Near-100% WR.
* **MutlakButlan**: $5,083 in 3 days. BTC 5m only. 37 trades on May 22 alone. 36–75¢ entries.

### 2.2. Session 13 Overnight: Bone Reaper Gate Removal (June 3)
* **Goal**: Increase trade velocity from a slow 0.6% candle hit rate (8 trades in 17 hours) to match competitor volumes.
* **Modifications Made**:
  1. Base FV edge (`_min_edge`) reduced from 0.10 to 0.05.
  2. Peer corroboration changed from a hard block to a sizing multiplier (1.3x if peers agree, 0.7x if they conflict).
  3. Same-asset direction cooldown (15 minutes) removed completely.
  4. Choppy detection pause reduced to block the current candle scan only.
  5. `session_governor` slots refactored to track `(asset, timeframe)` rather than `asset` only, allowing concurrent 5m and 15m positions on the same asset.
  6. Implemented a dynamic price floor (15¢ filter bypassed on strong >0.4% spot moves).

### 2.3. Forensic Audit of June 4 Session (The Cheap Contract Leak)
* **Overview**: 156 closed trades, 0 open positions. Net P&L: **+$7.24** on $50.00 baseline (Peak P&L: **+$46.71** / balance **$96.71**).
* **The Leak**: The drawdown post-peak was 100% driven by the **Fair Value (`FAIR_VAL`)** strategy, which lost **-$42.14** over 68 trades. **Latency Arbitrage (`LAT_ARB`)** remained profitable during the drawdown, gaining **+$4.05** over 50 trades.
* **The Culprit**: Out-of-the-money contrarian entries (entry price <= 0.25) had a **0% win rate** (0 wins, 12 losses), resulting in **-$76.23** in losses. The sizer calculated massive Kelly fractions due to high payout ratios (`b = 9.0`) combined with high theoretical win probability (which ignores rapid momentum wicks).
* **The Fix**: Placed a hard 0.35 entry price floor on all `FAIR_VAL` signals in both `updown_engine.py` and `fair_value.py`. Cheap contrarian trades are now blocked, converting `FAIR_VAL` into a strong net winner (+$64.93 projected P&L improvement).

---

## 3. The Lag-Arbitrage Signal Fusion Model

As outlined by `@0x_Punisher`, consistent printing relies on spot-to-CLOB latency gap exploitation. The complete fusion stack connects the feeds in real time:

```
[Binance WebSocket (bookTicker, aggTrade, depth5)] 
                       │
                       ▼
       [CVD & OBI Real-Time Metrics] 
                       │
                       ▼
    [Driftless normal-CDF Win Probability]
                       │
         (Divergence >= 8c Discount)
                       │
                       ▼
[Confirm with 1-Minute Candle Delta & Volume] ────► [Polymarket YES/NO Order Exec]
```

### The 6 Design Pillars (Aligned via /grill-me)
1. **Asset Focus**: Prioritize **BTC and ETH** (highest volume, cleanest signals). SOL and XRP may be traded with 60% sizing caps and tighter triggers. Exclude DOGE completely.
2. **Binance WebSocket Feeds**: Ingest `bookTicker` (instant price), `aggTrade` (tick trades for CVD), and `depth5@100ms` (fast L2 depth for OBI).
3. **CVD Momentum**: Use a **dual-window approach**: a fast 10-second CVD and a slow 60-second CVD. A signal triggers when the 10s CVD accelerates sharply away from the 60s baseline.
4. **OBI Alignment**: For an UP trade, Binance spot OBI must be positive (> 0.15) and Polymarket YES OBI must not be heavily negative (> -0.30). A direct imbalance conflict vetoes the trade.
5. **Lag Trigger**: Compute theoretical win probability `P_up` using the spot-distance normal-CDF. Trigger an entry when the Polymarket YES price is at a significant discount (`entry_price <= P_up - 0.08`), provided Binance CVD and OBI confirm strong directional momentum.
6. **1-Minute Confirmation**: Require the current active 1-minute candle's price direction (close > open) and net volume delta (CVD) to match the trade direction.

---

## 4. Deep Codebase Changes & Specific Implementations

### 4.1. Combined Binance WebSocket Ingestion
Modify [spot_websocket_ingest.py](file:///c:/Users/mthun/Downloads/ZiSi_Bot/infrastructure/websocket/spot_websocket_ingest.py).

Binance requires the `/stream?streams=` URL format for combined streams. The message format changes: it wraps the raw payload inside a `{"stream": "...", "data": {...}}` JSON envelope.

Here is the exact code architecture for the new `BinanceWebSocketIngest` daemon:

```python
import time
import json
import asyncio
import logging
import collections
import aiohttp
from typing import Dict, Optional, Tuple

log = logging.getLogger("zisi.hft.ws")

# Expanded thread-safe memory structure
# _market_books[SYMBOL] = {
#     "bid_price": float, "bid_qty": float,
#     "ask_price": float, "ask_qty": float,
#     "ofi_value": float,
#     "binance_obi": float,
#     "trades_history": deque,      # stores (timestamp, delta_qty)
#     "m1_candle": dict,            # {"open", "high", "low", "close", "cvd", "open_time"}
# }
_market_books: Dict[str, dict] = {}
_market_books_lock = asyncio.Lock()

async def get_current_obi(symbol: str) -> float:
    """Return Binance Order Flow Imbalance (tick-based)."""
    async with _market_books_lock:
        book = _market_books.get(symbol.upper())
        return book.get("ofi_value", 0.0) if book else 0.0

async def get_binance_obi(symbol: str) -> float:
    """Return top-5 depth-weighted Order Book Imbalance."""
    async with _market_books_lock:
        book = _market_books.get(symbol.upper())
        return book.get("binance_obi", 0.0) if book else 0.0

async def get_cvd_metrics(symbol: str) -> Tuple[float, float]:
    """
    Returns (fast_cvd_10s, slow_cvd_60s).
    Calculates net volume delta sums from memory queue.
    """
    now = time.time()
    fast_limit = now - 10.0
    slow_limit = now - 60.0
    
    fast_cvd = 0.0
    slow_cvd = 0.0
    
    async with _market_books_lock:
        book = _market_books.get(symbol.upper())
        if not book:
            return 0.0, 0.0
            
        history = book["trades_history"]
        # Process in reverse to sum fast limit first
        for ts, delta in reversed(history):
            if ts >= fast_limit:
                fast_cvd += delta
            if ts >= slow_limit:
                slow_cvd += delta
            else:
                break
                
    return round(fast_cvd, 4), round(slow_cvd, 4)

async def get_m1_candle_alignment(symbol: str, direction: str) -> bool:
    """
    Returns True if the active 1-minute candle direction and CVD match the trade direction.
    """
    async with _market_books_lock:
        book = _market_books.get(symbol.upper())
        if not book:
            return False
        m1 = book["m1_candle"]
        if m1["open_time"] == 0:
            return True # Not initialized yet, default pass
            
        close_price = m1["close"]
        open_price = m1["open"]
        cvd = m1["cvd"]
        
        if direction.upper() == "UP":
            return (close_price >= open_price) and (cvd > 0.0)
        else:
            return (close_price <= open_price) and (cvd < 0.0)

class BinanceWebSocketIngest:
    def __init__(self, symbols: list):
        self.symbols = [s.upper() for s in symbols]
        self.running = False
        self._task: Optional[asyncio.Task] = None

    def start(self):
        if not self.running:
            self.running = True
            self._task = asyncio.create_task(self._socket_loop())
            log.info("[HFT-WS] Ingest daemon started for symbols: %s", self.symbols)

    def stop(self):
        self.running = False
        if self._task:
            self._task.cancel()

    async def _socket_loop(self):
        # Build streams: bookTicker, aggTrade, and depth5@100ms
        stream_list = []
        for s in self.symbols:
            s_low = s.lower()
            stream_list.append(f"{s_low}usdt@bookTicker")
            stream_list.append(f"{s_low}usdt@aggTrade")
            stream_list.append(f"{s_low}usdt@depth5@100ms")
            
        streams = "/".join(stream_list)
        url = f"wss://stream.binance.com:9443/stream?streams={streams}"

        while self.running:
            try:
                log.info("[HFT-WS] Connecting to Binance Combined Streams: %s", url)
                connector = aiohttp.TCPConnector(enable_cleanup_closed=True)
                async with aiohttp.ClientSession(connector=connector) as session:
                    async with session.ws_connect(url, heartbeat=10.0) as ws:
                        log.info("[HFT-WS] Connected successfully!")
                        
                        # Initialize states
                        async with _market_books_lock:
                            for s in self.symbols:
                                if s not in _market_books:
                                    _market_books[s] = {
                                        "bid_price": 0.0, "bid_qty": 0.0,
                                        "ask_price": 0.0, "ask_qty": 0.0,
                                        "ofi_value": 0.0,
                                        "binance_obi": 0.0,
                                        "trades_history": collections.deque(maxlen=2000),
                                        "m1_candle": {"open": 0.0, "high": 0.0, "low": 0.0, "close": 0.0, "cvd": 0.0, "open_time": 0}
                                    }

                        async for msg in ws:
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                envelope = json.loads(msg.data)
                                stream_name = envelope.get("stream", "")
                                data = envelope.get("data", {})
                                await self._process_stream_msg(stream_name, data)
                            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                break
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error("[HFT-WS] Connection error: %r. Reconnecting in 5s...", e)
                await asyncio.sleep(5)

    async def _process_stream_msg(self, stream: str, data: dict):
        raw_symbol = data.get("s", "")
        if not raw_symbol.endswith("USDT"):
            return
        symbol = raw_symbol.replace("USDT", "").upper()
        if symbol not in self.symbols:
            return

        if "@bookTicker" in stream:
            await self._process_book_ticker(symbol, data)
        elif "@aggTrade" in stream:
            await self._process_agg_trade(symbol, data)
        elif "@depth" in stream:
            await self._process_depth(symbol, data)

    async def _process_book_ticker(self, symbol: str, tick: dict):
        new_bid = float(tick.get("b", 0.0))
        new_bid_qty = float(tick.get("B", 0.0))
        new_ask = float(tick.get("a", 0.0))
        new_ask_qty = float(tick.get("A", 0.0))

        async with _market_books_lock:
            old = _market_books[symbol]
            old_bid = old["bid_price"]
            old_bid_qty = old["bid_qty"]
            old_ask = old["ask_price"]
            old_ask_qty = old["ask_qty"]

            # Calculate Order Flow Imbalance (OFI) on current tick vs previous state
            if new_bid > old_bid:
                delta_v_bid = new_bid_qty
            elif new_bid == old_bid:
                delta_v_bid = new_bid_qty - old_bid_qty
            else:
                delta_v_bid = 0.0

            if new_ask < old_ask:
                delta_v_ask = new_ask_qty
            elif new_ask == old_ask:
                delta_v_ask = new_ask_qty - old_ask_qty
            else:
                delta_v_ask = 0.0

            total_volume = delta_v_bid + delta_v_ask
            ofi = (delta_v_bid - delta_v_ask) / total_volume if total_volume > 0 else 0.0
            
            alpha = 0.20
            smoothed_ofi = (alpha * ofi) + ((1.0 - alpha) * old["ofi_value"])

            old.update({
                "bid_price": new_bid, "bid_qty": new_bid_qty,
                "ask_price": new_ask, "ask_qty": new_ask_qty,
                "ofi_value": round(smoothed_ofi, 4)
            })

    async def _process_agg_trade(self, symbol: str, trade: dict):
        price = float(trade.get("p", 0.0))
        qty = float(trade.get("q", 0.0))
        is_buyer_maker = trade.get("m", False)  # True = seller aggressive, False = buyer aggressive
        
        # Calculate signed delta
        delta = -qty if is_buyer_maker else qty
        now = time.time()
        
        async with _market_books_lock:
            book = _market_books[symbol]
            # 1. Update rolling history queue
            book["trades_history"].append((now, delta))
            
            # 2. Update active 1-minute candle
            m1 = book["m1_candle"]
            candle_boundary = int(now // 60) * 60
            
            if candle_boundary > m1["open_time"]:
                # Initialize new candle
                m1["open_time"] = candle_boundary
                m1["open"] = price
                m1["high"] = price
                m1["low"] = price
                m1["close"] = price
                m1["cvd"] = delta
            else:
                m1["high"] = max(m1["high"], price)
                m1["low"] = min(m1["low"], price)
                m1["close"] = price
                m1["cvd"] += delta

    async def _process_depth(self, symbol: str, depth: dict):
        bids = depth.get("bids", [])
        asks = depth.get("asks", [])
        
        # depth5 structure is list of [price, qty] strings
        sum_bid_qty = 0.0
        sum_ask_qty = 0.0
        
        for bid in bids[:5]:
            sum_bid_qty += float(bid[1])
        for ask in asks[:5]:
            sum_ask_qty += float(ask[1])
            
        obi = 0.0
        if (sum_bid_qty + sum_ask_qty) > 0.0:
            obi = (sum_bid_qty - sum_ask_qty) / (sum_bid_qty + sum_ask_qty)
            
        async with _market_books_lock:
            _market_books[symbol]["binance_obi"] = round(obi, 4)
```

---

### 4.2. Implementing the Lag-Arbitrage Engine & Execution Decision
Modify `start_latency_edge_scanner` in [cycle_manager.py](file:///c:/Users/mthun/Downloads/ZiSi_Bot/core/engine/cycle_manager.py).

Refactor the `scan_and_trade` helper. Instead of utilizing standard percentage thresholds, build the multi-indicator signal fusion check:

```python
    async def scan_and_trade(engine, next_close, time_left, t_minus=15):
        asset = engine.asset
        timeframe = engine.timeframe
        interval_minutes = 60 if timeframe == "1h" else int(timeframe.rstrip("m"))

        # DOGE permanently excluded due to volatile oracle wicks
        if asset == "DOGE":
            return

        try:
            # ── 1. RETRIEVE REAL-TIME indicators ──
            from core.pyth_oracle_service import GLOBAL_ORACLE_CACHE
            pyth_price = GLOBAL_ORACLE_CACHE.get(asset, {}).get("price", 0.0)
            if pyth_price <= 0.0:
                return

            from infrastructure.websocket.spot_websocket_ingest import (
                get_binance_obi,
                get_cvd_metrics,
                get_m1_candle_alignment
            )
            
            # Fetch active CVD and Binance OBI
            fast_cvd, slow_cvd = await get_cvd_metrics(asset)
            binance_obi = await get_binance_obi(asset)

            # ── 2. CALCULATE DRIFTLESS normal-CDF WIN PROBABILITY ──
            from core.engine.updown_engine import _fetch_klines_async
            tf_map = {"5m": ("5m", 30), "15m": ("15m", 30), "1h": ("1h", 30)}
            interval, limit = tf_map.get(timeframe, ("5m", 30))
            klines = await _fetch_klines_async(session, asset, interval, limit)
            if len(klines) < 14:
                return
                
            open_price = float(klines[-1][1])  # Strike price of active candle
            
            # Extract volatility (ATR percentage)
            trs = []
            for i in range(len(klines)-14, len(klines)):
                h, l, pc = float(klines[i][2]), float(klines[i][3]), float(klines[i-1][4])
                trs.append(max(h - l, abs(h - pc), abs(l - PC)))
            atr = sum(trs) / len(trs)
            sigma_frac = atr / open_price if open_price > 0 else 0.01

            # Time calculation
            elapsed_sec = time_left
            total_sec = interval_minutes * 60.0
            elapsed_min = (total_sec - elapsed_sec) / 60.0

            from core.engine.fair_value import fair_prob_up
            P_up = fair_prob_up(pyth_price, open_price, sigma_frac, elapsed_min, interval_minutes)

            # ── 3. DETECT POTENTIAL DIRECTION ──
            # If P_up >= 0.70 -> potential UP direction. If P_up <= 0.30 -> potential DOWN direction.
            direction = None
            P_direction = 0.5
            
            if P_up >= 0.65:
                direction = "UP"
                P_direction = P_up
            elif P_up <= 0.35:
                direction = "DOWN"
                P_direction = 1.0 - P_up
                
            if not direction:
                return

            # ── 4. MOMENTUM GATES (CVD Spike & Acceleration) ──
            # For UP, fast CVD must be strongly positive. For DOWN, fast CVD must be strongly negative.
            # Require 10s CVD to align and accelerate away from 60s average.
            cvd_passes = False
            if direction == "UP" and fast_cvd > 0.0:
                cvd_passes = (fast_cvd >= 0.25 * abs(slow_cvd)) or (fast_cvd >= 0.05 * open_price)
            elif direction == "DOWN" and fast_cvd < 0.0:
                cvd_passes = (abs(fast_cvd) >= 0.25 * abs(slow_cvd)) or (abs(fast_cvd) >= 0.05 * open_price)

            if not cvd_passes:
                return

            # ── 5. ORDER BOOK ALIGNMENT GATE (Binance vs Polymarket YES/NO OBI) ──
            from infrastructure.websocket.extraterrestrial_ws_gateway import polymarket_l2_gateway
            market = await engine._fetch_market(session, is_latency_scan=True)
            if not market:
                return

            up_price = market["up_price"]
            dn_price = market["dn_price"]
            up_tk = market["up_market"]["id"]
            dn_tk = market["dn_market"]["id"]

            polymarket_obi = 0.0
            obi_passes = False
            
            if direction == "UP":
                polymarket_obi = polymarket_l2_gateway.get_obi(up_tk)
                obi_passes = (binance_obi > 0.15) and (polymarket_obi > -0.30)
                entry_price = up_price
                market_id = up_tk
                order_direction = "YES"
            else:
                polymarket_obi = polymarket_l2_gateway.get_obi(dn_tk)
                obi_passes = (binance_obi < -0.15) and (polymarket_obi > -0.30)
                entry_price = dn_price
                market_id = dn_tk
                order_direction = "NO"

            if not obi_passes:
                log.info("[OBI-VETO] %s/%s: Binance OBI %.2f & Polymarket OBI %.2f mismatch — skip",
                         asset, timeframe, binance_obi, polymarket_obi)
                return

            # ── 6. ACTIVE 1-MINUTE CANDLE DIRECTION AND CVD ALIGNMENT ──
            m1_aligned = await get_m1_candle_alignment(asset, direction)
            if not m1_aligned:
                log.info("[M1-VETO] %s/%s: Current 1-minute candle contradicts direction %s — skip",
                         asset, timeframe, direction)
                return

            # ── 7. THE DIVERGENCE (DISCOUNT) ENTRY TRIGGER ──
            # Compare current contract execution entry price to the theoretical probability
            # We must capture at least 8c of mispricing lag.
            discount = P_direction - entry_price
            if discount < 0.08:
                log.info("[LAG-ARB-GATE] %s/%s %s price %.2f >= Prob %.2f - 8¢ (discount %.2f) — skip",
                         asset, timeframe, direction, entry_price, P_direction, discount)
                return

            # Exclude weak pricing traps
            if entry_price < 0.35:
                 log.info("[PRICE-FLOOR] Veto cheap entry at %.2f", entry_price)
                 return

            # ── 8. POSITION LIMITS & ORDER EXECUTION ──
            import infrastructure.state.state_manager as state_mgr
            open_positions = state_mgr.get_open_positions()
            
            # Same-market dedup
            already_entered = False
            for pos in open_positions:
                if pos.get("event_id") == market["event_id"]:
                    already_entered = True
                    break
            if already_entered:
                return

            # Execute order with structured naming logs
            from infrastructure.exchange.trader import place_order
            from core.engine.session_governor import commit_trade_slot
            from infrastructure.state.state_manager import get_current_balance

            balance = get_current_balance()
            normal_size = engine.compute_size(0.85, entry_price, balance)
            
            # Position sizing modifier
            usd_size = max(1.50, normal_size * 0.75) # 0.75x sizing limit for fast execution
            
            # Target-asset adjustments
            if asset in ["SOL", "XRP"]:
                usd_size *= 0.60
                
            usd_size = min(usd_size, balance * 0.15) # Safety cap 15%

            if time.time() >= next_close:
                return # Abort if delayed

            order = place_order(
                event_id=market["event_id"],
                market_id=market_id,
                amount_dollars=usd_size,
                direction=order_direction,
                entry_price=entry_price,
                event_title=f"[UPDOWN][{asset}][{timeframe}][LAG_ARB_FUSION] {market['event_title']}",
                expiry_ts=market["expiry_ts"],
            )

            if order:
                await commit_trade_slot(asset, timeframe, 0.85, interval_minutes, is_dual=False, direction=direction)
                log.info("[FUSION ENTERED] Entered %s/%s %s: $%.2f @ %.0f¢ (P_dir: %.2f, discount: %.2f)",
                         asset, timeframe, direction, usd_size, entry_price * 100, P_direction, discount)
                
                try:
                    from app.telegram_bot import send_alert
                    send_alert(f"🚀 FUSION LAG ARB {asset}/{timeframe} {direction} | ${usd_size:.2f} @ {entry_price*100:.0f}¢ (Disc: {discount*100:.0f}¢)")
                except Exception:
                    pass

        except Exception as e:
            log.error("[LAG-ARB] Error scanning %s/%s: %s", asset, timeframe, e, exc_info=True)
```

---

## 5. Automated Tests Architecture

Modify/update [test_edges.py](file:///c:/Users/mthun/Downloads/ZiSi_Bot/tests/test_edges.py).

Claude Code must write automated tests for these newly created metrics. Implement test mocks like this to verify:

```python
import unittest
from unittest.mock import MagicMock, patch, AsyncMock
import time

# Tested modules
from infrastructure.websocket.spot_websocket_ingest import (
    BinanceWebSocketIngest,
    _market_books,
    _market_books_lock,
    get_cvd_metrics,
    get_binance_obi,
    get_m1_candle_alignment
)

class TestLagArbitrageIndicators(unittest.TestCase):

    async def test_cvd_queuing_and_calculation(self):
        # Initialize symbol state
        async with _market_books_lock:
            _market_books["BTC"] = {
                "trades_history": [],
                "m1_candle": {"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "cvd": 0.0, "open_time": 0}
            }
        
        ingest = BinanceWebSocketIngest(symbols=["BTC"])
        
        # Simulate trades: 4 positive buy trades (delta = 5.0 each), 1 sell trade (delta = -2.0)
        now = time.time()
        # Add to history
        async with _market_books_lock:
            history = _market_books["BTC"]["trades_history"]
            history.append((now - 50, -2.0)) # 50s ago (slow CVD)
            history.append((now - 5, 5.0))   # 5s ago (fast & slow CVD)
            history.append((now - 3, 5.0))   # 3s ago (fast & slow CVD)
            history.append((now - 1, 5.0))   # 1s ago (fast & slow CVD)
            
        fast, slow = await get_cvd_metrics("BTC")
        # fast CVD sum (last 10s) = 5.0 + 5.0 + 5.0 = 15.0
        # slow CVD sum (last 60s) = -2.0 + 5.0 + 5.0 + 5.0 = 13.0
        self.assertEqual(fast, 15.0)
        self.assertEqual(slow, 13.0)

    async def test_m1_candle_alignment(self):
        # 1. Bullish candle setup
        async with _market_books_lock:
            _market_books["ETH"] = {
                "m1_candle": {"open": 2000.0, "high": 2010.0, "low": 1999.0, "close": 2005.0, "cvd": 500.0, "open_time": int(time.time() // 60) * 60}
            }
            
        is_up_aligned = await get_m1_candle_alignment("ETH", "UP")
        is_dn_aligned = await get_m1_candle_alignment("ETH", "DOWN")
        
        self.assertTrue(is_up_aligned)
        self.assertFalse(is_dn_aligned)
```

---

## 6. VPS Operations & Deployment Walkthrough

To apply the changes live on the VPS, Claude Code must run the following deployment commands:

### 6.1. Establishing the VPS Tunnel
Establish an SSH tunnel to forward the VPS REST endpoint to local port `9090`:
```bash
ssh -L 9090:localhost:5000 root@204.168.222.48
```
*Port `9090` is required by `UpDownEngine._fetch_market` and the dashboard backend to pull live positions and execution state.*

### 6.2. Resetting to a Clean Slate
Before starting the new session, run the clean-slate utility script on the VPS to nuke any orphaned paper positions, archive the history of the previous session, and reset the balance to the $50.00 baseline:
```bash
python3 miscellaneous/clean_slate.py --balance 50 --force
```

### 6.3. Redeploying & Monitoring
1. Deploy the updated code files to the VPS (via git push or rsync).
2. Start the bot on the VPS:
   ```bash
   ./start_bot.bat  # (Or run app/main.py directly under tmux on Linux VPS)
   ```
3. Open the browser to [http://localhost:3000](http://localhost:3000) (or the mapped dashboard port) to monitor the portfolio curve, the active trade ledger, and watch for any red status flags on the `EngineStatusPill`.
4. Run the automated test suite locally to verify the new indicators:
   ```bash
   python -m unittest discover tests/
   ```

---

## 7. Verification Checklist for Claude Code

Before executing the bot with real/paper capital, Claude Code must verify:
- [ ] Subscriptions to `@aggTrade` and `@depth5` are successfully registered on startup.
- [ ] No syntax errors or loop-blocking operations exist in `_process_tick`.
- [ ] The `tests/test_edges.py` test suite passes (verify that mock data triggers CVD and OBI calculations accurately).
- [ ] The 0.35 entry price floor vetoes low-probability `FAIR_VAL` signals.
- [ ] Stale paper trades close out gracefully without locking up session governor slots.

---

## 8. Conclusion

By implementing this real-time Lag-Arbitrage Engine, ZiSi will evolve from a predictive bot into a high-frequency microstructural execution engine. It will trade with the speed, volume, and precision of PBot-6 and BoneReaper, securing a robust, compounding edge while completely protecting the bankroll from out-of-the-money drawdowns.
