"""
spot_websocket_ingest.py - Real-Time Binance Spot WebSocket Ingestion & OFI Calculation Engine.
Uses existing project-approved aiohttp for zero external dependency websocket execution.
"""
import asyncio
import logging
import json
import aiohttp
from typing import Dict, Optional, Tuple

log = logging.getLogger("zisi.hft.ws")

# Global thread-safe/async-safe memory structure for tracking real-time order books
# key: SYMBOL -> value: { bid_price, bid_qty, ask_price, ask_qty, ofi_value, last_update }
_market_books: Dict[str, dict] = {}
_market_books_lock = asyncio.Lock()

async def get_current_ofi(symbol: str) -> float:
    """Return the current Order Flow Imbalance value for a given symbol."""
    async with _market_books_lock:
        book = _market_books.get(symbol.upper())
        if book:
            return book.get("ofi_value", 0.0)
    return 0.0

async def get_book_details(symbol: str) -> Optional[Tuple[float, float, float]]:
    """Return (mid_price, spread, ofi_value) for a given symbol."""
    async with _market_books_lock:
        book = _market_books.get(symbol.upper())
        if book and book["bid_price"] > 0 and book["ask_price"] > 0:
            mid = (book["bid_price"] + book["ask_price"]) / 2
            spread = book["ask_price"] - book["bid_price"]
            return mid, spread, book["ofi_value"]
    return None

class BinanceWebSocketIngest:
    """
    Asynchronous daemon that connects to Binance Spot WebSocket stream,
    calculates real-time Order Flow Imbalance, and populates shared memory.
    """
    def __init__(self, symbols: list):
        self.symbols = [s.upper() for s in symbols]
        self.running = False
        self._task: Optional[asyncio.Task] = None

    def start(self):
        """Boot the background thread/task."""
        if not self.running:
            self.running = True
            self._task = asyncio.create_task(self._socket_loop())
            log.info("[HFT-WS] Ingest daemon started for symbols: %s", self.symbols)

    def stop(self):
        self.running = False
        if self._task:
            self._task.cancel()
            log.info("[HFT-WS] Ingest daemon stopped.")

    async def _socket_loop(self):
        """Core WebSocket connection loop with automatic robust reconnects."""
        # Convert symbols to stream paths (e.g. btcusdt@bookTicker)
        streams = "/".join([f"{s.lower()}usdt@bookTicker" for s in self.symbols])
        url = f"wss://stream.binance.com:9443/ws/{streams}"

        while self.running:
            try:
                log.info("[HFT-WS] Connecting to Binance WebSocket: %s", url)
                connector = aiohttp.TCPConnector(enable_cleanup_closed=True)
                async with aiohttp.ClientSession(connector=connector) as session:
                    async with session.ws_connect(url, heartbeat=10.0) as ws:
                        log.info("[HFT-WS] WebSocket connected successfully! Processing tick feeds...")
                        
                        # Initialize states in memory
                        async with _market_books_lock:
                            for s in self.symbols:
                                if s not in _market_books:
                                    _market_books[s] = {
                                        "bid_price": 0.0, "bid_qty": 0.0,
                                        "ask_price": 0.0, "ask_qty": 0.0,
                                        "ofi_value": 0.0
                                    }

                        async for msg in ws:
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                data = json.loads(msg.data)
                                await self._process_tick(data)
                            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                break
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error("[HFT-WS] WebSocket connection exception: %r. Reconnecting in 5s...", e)
                await asyncio.sleep(5)

    async def _process_tick(self, tick: dict):
        """
        Process incoming bookTicker tick and update real-time Order Flow Imbalance.
        Stream structure: { 'u': update_id, 's': symbol (e.g. 'BTCUSDT'), 
                           'b': best_bid, 'B': best_bid_qty, 'a': best_ask, 'A': best_ask_qty }
        """
        raw_symbol = tick.get("s", "")
        if not raw_symbol.endswith("USDT"):
            return
        
        symbol = raw_symbol.replace("USDT", "").upper()
        if symbol not in self.symbols:
            return

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
            # Bid side change
            if new_bid > old_bid:
                delta_v_bid = new_bid_qty
            elif new_bid == old_bid:
                delta_v_bid = new_bid_qty - old_bid_qty
            else:
                delta_v_bid = 0.0

            # Ask side change
            if new_ask < old_ask:
                delta_v_ask = new_ask_qty
            elif new_ask == old_ask:
                delta_v_ask = new_ask_qty - old_ask_qty
            else:
                delta_v_ask = 0.0

            # Normalize OFI as a dimensionless percentage imbalance between -1.0 and +1.0
            total_volume = delta_v_bid + delta_v_ask
            ofi = (delta_v_bid - delta_v_ask) / total_volume if total_volume > 0 else 0.0
            
            # Smooth the OFI value using an EMA to prevent high-frequency noise spikes
            alpha = 0.20
            smoothed_ofi = (alpha * ofi) + ((1.0 - alpha) * old["ofi_value"])

            # Update memory state
            _market_books[symbol] = {
                "bid_price": new_bid,
                "bid_qty": new_bid_qty,
                "ask_price": new_ask,
                "ask_qty": new_ask_qty,
                "ofi_value": round(smoothed_ofi, 4)
            }

if __name__ == "__main__":
    # Test script: Connect and display real-time OFI ticks for 5 seconds
    logging.basicConfig(level=logging.INFO)
    async def test():
        ingest = BinanceWebSocketIngest(symbols=["BTC", "ETH"])
        ingest.start()
        for i in range(5):
            await asyncio.sleep(1)
            btc_ofi = await get_current_ofi("BTC")
            eth_ofi = await get_current_ofi("ETH")
            print(f"[{i+1}s] Live OFI Metrics | BTC: {btc_ofi:+.4f} | ETH: {eth_ofi:+.4f}")
        ingest.stop()
        await asyncio.sleep(0.5)

    asyncio.run(test())
