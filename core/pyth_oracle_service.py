"""
pyth_oracle_service.py - Institutional-Grade Server-Sent Events (SSE) Price Streaming Service.
Streams real-time price updates from Pyth Hermes over a persistent connection to bypass
REST rate limits, maintaining a sub-100ms global price cache for ZiSi.
"""

import asyncio
import aiohttp
import json
import logging
import time
import os
from typing import Dict, Optional, Any

log = logging.getLogger("zisi.pyth.service")

# ── Pyth Hermes Streams & Mappings ─────────────────────────────────────────────
HERMES_STREAM_URL = "https://hermes.pyth.network/v2/updates/price/stream"

PYTH_FEED_IDS = {
    "BTC": "0xe62df6c8b4a85fe1a67db44dc12de5db330f7ac66b72dc658afedf0f4a415b43",
    "ETH": "0xff61491a931112ddf1bd8147cd1b641375f79f5825126d665480874634fd0ace",
    "SOL": "0xef0d8b6fda2ceba41da15d4095d1da392a0d2f8ed0c6c7bc0f4cfac8c280b56d",
    "XRP": "0xec5d399846a9209f3fe5881d70aae9268c94339ff9817e8d18ff19fa05eea1c8",
    "ADA": "0x2a01deaec9e51a579277b34b122399984d0bbf57e2458a7e42fecd2829867a0d",
    "LINK": "0x8ac0c70fff57e9aefdf5edf44b51d62c2d433653cbb2cf5cc06bb115af04d221",
    "DOGE": "0xdcef50dd0a4cd2dcc17e45df1676dcb336a11a61c69df7a0299b0150c672d25c",
    "AVAX": "0x93da3352f9f1d105fdfe4971cfa80e9dd777bfc5d0f683ebb6e1294b92137bb7",
    "SUI": "0x23d7315113f5b1d3ba7a83604c44b94d79f4fd69af77f804fc7f920a6dc65744",
    "HYPE": "0x4279e31cc369bbcc2faf022b382b080e32a8e689ff20fbc530d2a603eb6cd98b",
    "BNB": "0x2f95862b045670cd22bee3114c39763a4a08beeb663b145d283c31d7d1101c4f"
}

REV_FEED_IDS = {v: k for k, v in PYTH_FEED_IDS.items()}

# ── Global In-Memory Oracle Cache (Sub-millisecond access for the bot) ───────
GLOBAL_ORACLE_CACHE: Dict[str, Dict[str, Any]] = {
    symbol: {"price": 0.0, "timestamp": 0, "conf": 0.0} for symbol in PYTH_FEED_IDS
}


class PythOracleService:
    def __init__(self):
        self.is_running = False
        self._session: Optional[aiohttp.ClientSession] = None
        self._main_task: Optional[asyncio.Task] = None

    async def start(self):
        """Start the background SSE persistent streaming loop."""
        self.is_running = True
        self._session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=5, keepalive_timeout=60))
        self._main_task = asyncio.create_task(self._stream_loop())
        self._disk_task = asyncio.create_task(self._write_cache_to_disk_loop())
        log.info("[PYTH] Persistent Oracle Pricing Daemon initiated.")

    async def stop(self):
        """Stop the background streaming loop cleanly."""
        self.is_running = False
        if self._main_task:
            self._main_task.cancel()
        if hasattr(self, "_disk_task") and self._disk_task:
            self._disk_task.cancel()
        if self._session:
            await self._session.close()
        log.info("[PYTH] Persistent Oracle Pricing Daemon stopped.")

    async def _write_cache_to_disk_loop(self):
         """Periodically dump the global price cache to a JSON file for the Node backend to ingest."""
         while self.is_running:
             try:
                 # Write to a temp file and replace atomically to prevent race conditions during Node reads
                 temp_filename = "pyth_prices.json.tmp"
                 with open(temp_filename, "w") as f:
                     json.dump(GLOBAL_ORACLE_CACHE, f, indent=2)
                 os.replace(temp_filename, "pyth_prices.json")
             except Exception as e:
                 log.debug("[PYTH] Failed to dump prices to disk: %s", e)
             await asyncio.sleep(0.5)  # Update every 500ms for low latency!

    async def _stream_loop(self):
        """
        Persistent Server-Sent Events (SSE) streaming daemon.
        Bypasses HTTP polling limits entirely by keeping a single long-lived TCP socket open.
        """
        params = [("ids[]", feed_id) for feed_id in PYTH_FEED_IDS.values()]
        headers = {"Accept": "text/event-stream", "User-Agent": "ZiSi-Bot/2.0"}

        while self.is_running:
            try:
                # Use total=None timeout for persistent SSE EventStreams to prevent aiohttp 30s auto-closure timeouts
                timeout = aiohttp.ClientTimeout(total=None, sock_connect=15, sock_read=45)
                async with self._session.get(HERMES_STREAM_URL, params=params, headers=headers, timeout=timeout) as resp:
                    if resp.status == 200:
                        log.info("[PYTH] SSE Price Stream connected successfully! Ingesting updates...")
                        
                        # Process SSE stream line-by-line
                        async for line in resp.content:
                            if not self.is_running:
                                break
                                
                            decoded_line = line.decode('utf-8').strip()
                            if decoded_line.startswith("data:"):
                                try:
                                    json_str = decoded_line[5:].strip()
                                    data = json.loads(json_str)
                                    parsed = data.get("parsed", [])
                                    
                                    for update in parsed:
                                        feed_id = update.get("id")
                                        if feed_id and not feed_id.startswith("0x"):
                                            feed_id = "0x" + feed_id
                                            
                                        symbol = REV_FEED_IDS.get(feed_id)
                                        if not symbol:
                                            continue
                                            
                                        price_data = update.get("price", {})
                                        raw_p = float(price_data.get("price", 0))
                                        expo = int(price_data.get("expo", 0))
                                        conf = float(price_data.get("conf", 0)) * (10 ** expo)
                                        
                                        float_price = raw_p * (10 ** expo)
                                        
                                        # Update global in-memory cache instantly
                                        GLOBAL_ORACLE_CACHE[symbol] = {
                                            "price": round(float_price, 6),
                                            "timestamp": time.time(),
                                            "conf": round(conf, 6)
                                        }
                                except Exception as e:
                                    log.debug("[PYTH] Failed to parse stream data packet: %r", e)
                    else:
                        log.error("[PYTH] SSE Price Stream returned status: %d — reconnecting in 5s...", resp.status)
                        await asyncio.sleep(5)
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error("[PYTH] SSE Stream error: %r — attempting reconnection in 5s...", e)
                await asyncio.sleep(5)


# ── Asynchronous Execution Example (Manual SSE Sandbox Test) ──────────────────
async def test_stream():
    print("Testing Pyth Hermes Server-Sent Events (SSE) Price Stream...")
    service = PythOracleService()
    
    # Run the streaming service in the background
    await service.start()
    
    # Monitor the global cache in-memory updating every second
    for i in range(10):
        await asyncio.sleep(1)
        print(f"\nTick {i+1} — Real-time Prices in Global Cache:")
        print("-" * 40)
        for symbol, data in GLOBAL_ORACLE_CACHE.items():
            print(f"  {symbol:<6} : ${data['price']:,.4f} | Conf: {data['conf']:.4f} | Age: {int(time.time()) - data['timestamp']}s")
        print("-" * 40)
        
    await service.stop()

if __name__ == "__main__":
    # Configure logging to show connection events
    logging.basicConfig(level=logging.INFO)
    asyncio.run(test_stream())
