"""
spot_websocket_ingest.py - Real-Time Binance Spot WebSocket Ingestion.

Ingests three stream types per symbol for the lag-arbitrage engine:
  @bookTicker      → best bid/ask OFI (existing)
  @aggTrade        → trade-level CVD (new: dual 10s/60s windows)
  @depth5@100ms    → depth-weighted OBI (new)

Public API
----------
get_current_ofi(symbol)           -> float          (tick-level OFI, legacy)
get_book_details(symbol)          -> (mid, spread, ofi) | None
get_binance_obi(symbol)           -> float          (-1.0 to +1.0)
get_cvd_metrics(symbol)           -> (fast_10s, slow_60s)
get_m1_candle_alignment(symbol, direction) -> bool
"""
import asyncio
import logging
import json
import time
import aiohttp
from typing import Dict, Optional, Tuple

log = logging.getLogger("zisi.hft.ws")

_market_books: Dict[str, dict] = {}
_market_books_lock = asyncio.Lock()
_price_move_events = asyncio.Queue(maxsize=50)

async def pre_warm_cvd(symbols, session):
    now = time.time()
    cutoff = now - 62.0
    for sym in symbols:
        s = sym.upper()
        try:
            url = "https://api.binance.com/api/v3/aggTrades?symbol=" + s + "USDT&limit=500"
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5.0)) as resp:
                if resp.status != 200:
                    continue
                trades = await resp.json()
            async with _market_books_lock:
                if s not in _market_books:
                    _market_books[s] = {
                        "bid_price": 0.0, "bid_qty": 0.0, "ask_price": 0.0, "ask_qty": 0.0,
                        "ofi_value": 0.0, "binance_obi": 0.0, "trades_history": [],
                        "m1_candle": {"open": 0.0, "high": 0.0, "low": 0.0, "close": 0.0, "cvd": 0.0, "open_time": 0.0},
                    }
                book = _market_books[s]
                for t in trades:
                    ts = float(t["T"]) / 1000.0
                    if ts < cutoff:
                        continue
                    qty = float(t["q"])
                    delta = -qty if t.get("m", False) else qty
                    book["trades_history"].append((ts, delta))
                book["trades_history"].sort(key=lambda x: x[0])
            log.info("[CVD-PREWARM] %s: loaded %d trades from REST", s, sum(1 for tr in _market_books[s]["trades_history"] if tr[0] >= cutoff))
        except Exception as e:
            log.warning("[CVD-PREWARM] %s failed: %s", s, e)


async def get_validated_price(symbol, pyth_price):
    async with _market_books_lock:
        book = _market_books.get(symbol.upper())
        if not book or book["bid_price"] <= 0 or book["ask_price"] <= 0:
            return pyth_price
        binance_mid = (book["bid_price"] + book["ask_price"]) / 2.0
    if pyth_price <= 0:
        return binance_mid
    divergence = abs(binance_mid - pyth_price) / max(pyth_price, 1e-9)
    if divergence > 0.002:
        log.info("[PRICE-XVAL] %s: Pyth=%.4f Binance=%.4f diverge=%.3f%% using Binance", symbol, pyth_price, binance_mid, divergence*100)
        return binance_mid
    return pyth_price



# ---------------------------------------------------------------------------
# Public accessors
# ---------------------------------------------------------------------------

async def get_current_ofi(symbol: str) -> float:
    async with _market_books_lock:
        book = _market_books.get(symbol.upper())
        return book.get("ofi_value", 0.0) if book else 0.0


async def get_book_details(symbol: str) -> Optional[Tuple[float, float, float]]:
    async with _market_books_lock:
        book = _market_books.get(symbol.upper())
        if book and book["bid_price"] > 0 and book["ask_price"] > 0:
            mid = (book["bid_price"] + book["ask_price"]) / 2
            spread = book["ask_price"] - book["bid_price"]
            return mid, spread, book["ofi_value"]
    return None


async def get_binance_obi(symbol: str) -> float:
    """Depth-weighted OBI from @depth5@100ms: (bid_vol - ask_vol) / total, EMA-smoothed."""
    async with _market_books_lock:
        book = _market_books.get(symbol.upper())
        return book.get("binance_obi", 0.0) if book else 0.0


async def get_cvd_metrics(symbol: str) -> Tuple[float, float]:
    """Return (fast_cvd_10s, slow_cvd_60s) cumulative volume deltas.
    Positive = net buy pressure, negative = net sell pressure."""
    now = time.time()
    async with _market_books_lock:
        book = _market_books.get(symbol.upper())
        if not book:
            return 0.0, 0.0
        trades = list(book.get("trades_history", []))  # snapshot under lock

    fast = sum(d for ts, d in trades if now - ts <= 10.0)
    slow = sum(d for ts, d in trades if now - ts <= 60.0)
    return fast, slow


async def get_m1_candle_alignment(symbol: str, direction: str) -> bool:
    """True if the active 1-minute candle close direction AND CVD match `direction`."""
    async with _market_books_lock:
        book = _market_books.get(symbol.upper())
        if not book:
            return False
        m1 = dict(book.get("m1_candle", {}))

    if m1.get("open", 0.0) <= 0:
        return False  # no data yet — don't block

    if direction == "UP":
        return m1["close"] >= m1["open"] and m1.get("cvd", 0.0) > 0
    return m1["close"] <= m1["open"] and m1.get("cvd", 0.0) < 0


def _has_cvd_data(symbol: str) -> bool:
    """Non-async check: True if aggTrade history exists (ingest is live)."""
    book = _market_books.get(symbol.upper(), {})
    return len(book.get("trades_history", [])) > 0


async def get_book_age(symbol: str) -> float:
    """Return the time in seconds since the last bookTicker update for this symbol."""
    async with _market_books_lock:
        book = _market_books.get(symbol.upper())
        return time.time() - book.get("timestamp", 0.0) if book else 999.0


# ---------------------------------------------------------------------------
# Ingest daemon
# ---------------------------------------------------------------------------

class BinanceWebSocketIngest:
    """Async daemon connecting to Binance combined stream for CVD + OBI + OFI."""

    def __init__(self, symbols: list):
        self.symbols = [s.upper() for s in symbols]
        self.running = False
        self._task: Optional[asyncio.Task] = None

    def start(self):
        if not self.running:
            self.running = True
            self._task = asyncio.create_task(self._socket_loop())
            log.info("[HFT-WS] Ingest daemon started for %s", self.symbols)

    def stop(self):
        self.running = False
        if self._task:
            self._task.cancel()
            log.info("[HFT-WS] Ingest daemon stopped.")

    async def _socket_loop(self):
        # Build combined stream — 3 feeds per symbol
        parts = []
        for s in self.symbols:
            sl = s.lower() + "usdt"
            parts.append(f"{sl}@bookTicker")
            parts.append(f"{sl}@aggTrade")
            parts.append(f"{sl}@depth5@100ms")

        url = f"wss://stream.binance.com:9443/stream?streams={'/'.join(parts)}"

        while self.running:
            try:
                log.info("[HFT-WS] Connecting: %d streams for %d symbols", len(parts), len(self.symbols))
                connector = aiohttp.TCPConnector(enable_cleanup_closed=True)
                async with aiohttp.ClientSession(connector=connector) as session:
                    async with session.ws_connect(url, heartbeat=10.0) as ws:
                        log.info("[HFT-WS] Connected — CVD+OBI+OFI live")

                        async with _market_books_lock:
                            for s in self.symbols:
                                if s not in _market_books:
                                    _market_books[s] = {
                                        "bid_price": 0.0, "bid_qty": 0.0,
                                        "ask_price": 0.0, "ask_qty": 0.0,
                                        "ofi_value": 0.0,
                                        "binance_obi": 0.0,
                                        "trades_history": [],
                                        "timestamp": 0.0,
                                        "m1_candle": {
                                            "open": 0.0, "high": 0.0, "low": 0.0,
                                            "close": 0.0, "cvd": 0.0, "open_time": 0.0,
                                        },
                                    }

                        async for msg in ws:
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                envelope = json.loads(msg.data)
                                # Combined stream format: {"stream": "...", "data": {...}}
                                data = envelope.get("data", envelope)
                                stream = envelope.get("stream", "")
                                await self._process_tick(data, stream)
                            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                break
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error("[HFT-WS] Exception: %r — reconnecting in 5s", e)
                await asyncio.sleep(5)

    async def _process_tick(self, data: dict, stream: str):
        raw_symbol = data.get("s", "")
        if not raw_symbol:
            # depth5 snapshots don't include 's' — infer from stream name
            if "@" in stream:
                raw_symbol = stream.split("@")[0].upper()
        if not raw_symbol:
            return

        raw_upper = raw_symbol.upper()
        symbol = raw_upper.replace("USDT", "") if raw_upper.endswith("USDT") else raw_upper
        if symbol not in self.symbols:
            return

        event_type = data.get("e", "")

        if event_type == "bookTicker" or "@bookTicker" in stream:
            await self._handle_book_ticker(symbol, data)
        elif event_type == "aggTrade" or "@aggTrade" in stream:
            await self._handle_agg_trade(symbol, data)
        elif "@depth" in stream or "bids" in data:
            await self._handle_depth(symbol, data)

    async def _handle_book_ticker(self, symbol: str, tick: dict):
        new_bid = float(tick.get("b", 0.0))
        new_bid_qty = float(tick.get("B", 0.0))
        new_ask = float(tick.get("a", 0.0))
        new_ask_qty = float(tick.get("A", 0.0))

        old_bid = 0.0
        old_ask = 0.0

        async with _market_books_lock:
            old = _market_books.get(symbol)
            if old:
                old_bid = old.get("bid_price", 0.0)
                old_bid_qty = old.get("bid_qty", 0.0)
                old_ask = old.get("ask_price", 0.0)
                old_ask_qty = old.get("ask_qty", 0.0)

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
                old["ofi_value"] = round(alpha * ofi + (1.0 - alpha) * old["ofi_value"], 4)
                old["bid_price"] = new_bid
                old["bid_qty"] = new_bid_qty
                old["ask_price"] = new_ask
                old["ask_qty"] = new_ask_qty
                old["timestamp"] = time.time()

        if new_bid > 0 and old_bid > 0:
            _pct = (new_bid - old_bid) / old_bid
            if abs(_pct) >= 0.002:
                _dir = "UP" if _pct > 0 else "DOWN"
                try:
                    _price_move_events.put_nowait({"asset": symbol, "direction": _dir, "pct_move": _pct})
                except asyncio.QueueFull:
                    pass

    async def _handle_agg_trade(self, symbol: str, tick: dict):
        price = float(tick.get("p", 0.0))
        qty = float(tick.get("q", 0.0))
        is_buyer_maker = tick.get("m", False)  # True = market sell
        delta = -qty if is_buyer_maker else qty
        now = time.time()

        async with _market_books_lock:
            book = _market_books.get(symbol)
            if not book:
                return
            book["trades_history"].append((now, delta))
            # Prune > 60s (max window we need)
            cutoff = now - 61.0
            book["trades_history"] = [(ts, d) for ts, d in book["trades_history"] if ts >= cutoff]

            # Update rolling 1-minute candle
            candle_open = float(int(now // 60) * 60)
            m1 = book["m1_candle"]
            if candle_open > m1["open_time"]:
                m1["open_time"] = candle_open
                m1["open"] = price
                m1["high"] = price
                m1["low"] = price
                m1["close"] = price
                m1["cvd"] = delta
            else:
                if price > m1["high"]:
                    m1["high"] = price
                if price < m1["low"]:
                    m1["low"] = price
                m1["close"] = price
                m1["cvd"] += delta

    async def _handle_depth(self, symbol: str, data: dict):
        bids = data.get("bids", data.get("b", []))[:5]
        asks = data.get("asks", data.get("a", []))[:5]
        if not bids or not asks:
            return

        bid_vol = sum(float(b[1]) for b in bids)
        ask_vol = sum(float(a[1]) for a in asks)
        total = bid_vol + ask_vol
        if total <= 0:
            return

        raw_obi = (bid_vol - ask_vol) / total
        async with _market_books_lock:
            book = _market_books.get(symbol)
            if book:
                alpha = 0.30
                book["binance_obi"] = round(alpha * raw_obi + (1.0 - alpha) * book["binance_obi"], 4)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    async def _test():
        ingest = BinanceWebSocketIngest(symbols=["BTC", "ETH"])
        ingest.start()
        for i in range(10):
            await asyncio.sleep(1)
            btc_ofi = await get_current_ofi("BTC")
            btc_obi = await get_binance_obi("BTC")
            fast, slow = await get_cvd_metrics("BTC")
            m1_up = await get_m1_candle_alignment("BTC", "UP")
            print(f"[{i+1}s] BTC | OFI={btc_ofi:+.4f} OBI={btc_obi:+.4f} "
                  f"CVD fast={fast:+.2f} slow={slow:+.2f} | 1m UP={m1_up}")
        ingest.stop()
        await asyncio.sleep(0.5)

    asyncio.run(_test())
