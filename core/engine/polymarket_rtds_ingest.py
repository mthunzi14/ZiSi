"""
polymarket_rtds_ingest.py - Real-Time Polymarket RTDS Chainlink Feed Ingest.
Streams real-time updates for crypto_prices_chainlink over a public connection,
caching prices and tracking candle boundary opens.
"""

import asyncio
import logging
import json
import time
import os
import aiohttp
from typing import Dict, Optional, Tuple

log = logging.getLogger("zisi.rtds.ws")

# Global pricing cache
_chainlink_prices: Dict[str, dict] = {}
# candle opens cache: (asset, interval) -> {candle_start_timestamp: open_price}
_chainlink_candle_opens: Dict[Tuple[str, int], Dict[int, float]] = {}

_price_lock = asyncio.Lock()


async def get_chainlink_price(asset: str) -> Optional[Tuple[float, float]]:
    """Return the latest Chainlink price and its local receipt timestamp."""
    async with _price_lock:
        data = _chainlink_prices.get(asset.upper())
        if data:
            return data["price"], data["timestamp"]
    return None


async def get_chainlink_price_age(asset: str) -> float:
    """Return age in seconds of the latest Chainlink price."""
    async with _price_lock:
        data = _chainlink_prices.get(asset.upper())
        if data:
            return time.time() - data["timestamp"]
    return 999.0


async def get_chainlink_candle_open(asset: str, interval_sec: int, candle_start: int) -> Optional[float]:
    """Return the recorded Chainlink price at the candle open."""
    async with _price_lock:
        opens = _chainlink_candle_opens.get((asset.upper(), interval_sec))
        if opens:
            return opens.get(candle_start)
    return None


class PolymarketRTDSIngest:
    """
    Polymarket Real-Time Data Socket (RTDS) Ingest.
    Streams public crypto_prices_chainlink without authentication.
    """
    def __init__(self, ws_url: str = "wss://ws-live-data.polymarket.com"):
        self.ws_url = ws_url
        self.running = False
        self._task: Optional[asyncio.Task] = None
        self._disk_task: Optional[asyncio.Task] = None
        self.last_msg_ts = time.time()

    def start(self):
        if not self.running:
            self.running = True
            self._task = asyncio.create_task(self._socket_loop())
            self._disk_task = asyncio.create_task(self._write_cache_to_disk_loop())
            self._binance_task = asyncio.create_task(self._binance_poll_loop())
            log.info("[RTDS-WS] Ingest daemon started -> %s", self.ws_url)

    def stop(self):
        self.running = False
        if self._task:
            self._task.cancel()
        if hasattr(self, "_disk_task") and self._disk_task:
            self._disk_task.cancel()
        if hasattr(self, "_binance_task") and self._binance_task:
            self._binance_task.cancel()
        log.info("[RTDS-WS] Ingest daemon stopped.")

    async def _binance_poll_loop(self):
        """Poll Binance REST every 3 seconds as price backstop — ensures cards stay green even without RTDS."""
        while self.running:
            await asyncio.sleep(3)
            await self._refresh_from_binance()

    async def _write_cache_to_disk_loop(self):
        """Periodically dump the global price cache to a JSON file for the Node backend to ingest."""
        while self.running:
            try:
                base_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data")
                async with _price_lock:
                    cache_copy = dict(_chainlink_prices)
                
                cl_temp = os.path.join(base_dir, "chainlink_prices.json.tmp")
                cl_target = os.path.join(base_dir, "chainlink_prices.json")
                with open(cl_temp, "w") as f:
                    json.dump(cache_copy, f, indent=2)
                os.replace(cl_temp, cl_target)
                
                pyth_temp = os.path.join(base_dir, "pyth_prices.json.tmp")
                pyth_target = os.path.join(base_dir, "pyth_prices.json")
                with open(pyth_temp, "w") as f:
                    json.dump(cache_copy, f, indent=2)
                os.replace(pyth_temp, pyth_target)
            except Exception as e:
                log.debug("[RTDS-WS] Failed to dump prices to disk: %s", e)
            await asyncio.sleep(0.5)

    async def _connection_watchdog(self, ws):
        """Hard 15-second timeout circuit breaker to handle silent connection drops."""
        try:
            while self.running and not ws.closed:
                await asyncio.sleep(1.0)
                if time.time() - self.last_msg_ts > 180.0:
                    log.warning("[RTDS-WS] Watchdog: 180s timeout reached with no messages — triggering reconnect.")
                    await ws.close()
                    break
        except asyncio.CancelledError:
            pass

    async def _socket_loop(self):
        backoff = 10.0  # start at 10s, not 3s — avoids Cloudflare rate-limit hammering
        connected_once = False
        while self.running:
            try:
                log.info("[RTDS-WS] Connecting to %s...", self.ws_url)
                connector = aiohttp.TCPConnector(enable_cleanup_closed=True)
                async with aiohttp.ClientSession(connector=connector) as session:
                    async with session.ws_connect(self.ws_url, heartbeat=10.0) as ws:
                        log.info("[RTDS-WS] Connected to Polymarket RTDS")
                        backoff = 10.0  # reset on successful connect
                        connected_once = True
                        self.last_msg_ts = time.time()

                        subscriptions = []
                        for asset in ["BTC", "ETH", "SOL", "XRP", "DOGE"]:
                            subscriptions.append({
                                "topic": "crypto_prices_chainlink",
                                "type": "update",
                                "filters": json.dumps({"symbol": f"{asset.lower()}/usd"})
                            })

                        sub_msg = {
                            "action": "subscribe",
                            "subscriptions": subscriptions
                        }
                        await ws.send_json(sub_msg)
                        log.info("[RTDS-WS] Subscribed to topics: crypto_prices_chainlink and crypto_prices (BTCUSDT)")

                        ping_task = asyncio.create_task(self._ping_sender(ws))
                        watchdog_task = asyncio.create_task(self._connection_watchdog(ws))

                        try:
                            async for msg in ws:
                                self.last_msg_ts = time.time()
                                if msg.type == aiohttp.WSMsgType.TEXT:
                                    if msg.data == "PONG":
                                        continue
                                    try:
                                        envelope = json.loads(msg.data)
                                        await self._process_message(envelope)
                                    except json.JSONDecodeError:
                                        pass
                                    except Exception as e:
                                        log.error("[RTDS-WS] Error processing message: %s", e)
                                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                    log.warning("[RTDS-WS] Connection closed/error: %s", msg.data)
                                    break
                        finally:
                            ping_task.cancel()
                            watchdog_task.cancel()
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error("[RTDS-WS] WebSocket connection exception: %r — reconnecting in %.0fs", e, backoff)
                # Exponential backoff: 10s → 20s → 40s → 80s → capped at 120s
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 120.0)
                # While backing off, keep chainlink_prices.json fresh via Binance REST
                await self._refresh_from_binance()

    async def _ping_sender(self, ws):
        """Send PING text frame every 5 seconds to keep connection alive."""
        try:
            while self.running:
                await asyncio.sleep(5)
                if not ws.closed:
                    await ws.send_str("PING")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.warning("[RTDS-WS] Failed to send PING: %s", e)

    async def _refresh_from_binance(self):
        """Fetch spot prices from Binance REST as fallback when RTDS WS is down."""
        SYMBOLS = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT", "XRP": "XRPUSDT", "DOGE": "DOGEUSDT"}
        try:
            connector = aiohttp.TCPConnector(enable_cleanup_closed=True)
            async with aiohttp.ClientSession(connector=connector) as session:
                url = "https://api.binance.com/api/v3/ticker/price?symbols=" + \
                      "[" + ",".join(f'%22{v}%22' for v in SYMBOLS.values()) + "]"
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                    if resp.status != 200:
                        return
                    data = await resp.json()
                    now = time.time()
                    sym_to_asset = {v: k for k, v in SYMBOLS.items()}
                    async with _price_lock:
                        for item in data:
                            asset = sym_to_asset.get(item.get("symbol"))
                            if not asset:
                                continue
                            price = float(item.get("price", 0))
                            if price > 0:
                                _chainlink_prices[asset] = {"price": price, "timestamp": now}
                    log.info("[RTDS-WS] Binance REST fallback: updated %d prices", len(data))
        except Exception as e:
            log.debug("[RTDS-WS] Binance fallback failed: %s", e)

    async def _process_message(self, data: dict):
        topic = data.get("topic")
        # Accept both crypto_prices_chainlink and crypto_prices (since Chainlink updates return topic: crypto_prices)
        if topic not in ("crypto_prices_chainlink", "crypto_prices"):
            return
            
        payload = data.get("payload", {})
        symbol = payload.get("symbol", "").upper()
        # Slash guard: only process slash-separated symbols (Chainlink feeds like BTC/USD)
        # to avoid mixing/overwriting cache with Binance data (which has no slash)
        if not symbol or "/" not in symbol:
            return
            
        asset = symbol.split("/")[0].upper()
            
        value = float(payload.get("value", 0.0))
        
        if value <= 0:
            return
            
        now = time.time()
        
        async with _price_lock:
            # Store price
            _chainlink_prices[asset] = {
                "price": value,
                "timestamp": now
            }
            
            # Record candle opens
            for interval in (300, 900, 3600):
                candle_start = int(now // interval) * interval
                key = (asset, interval)
                if key not in _chainlink_candle_opens:
                    _chainlink_candle_opens[key] = {}
                
                # Record the first tick of the candle
                if candle_start not in _chainlink_candle_opens[key]:
                    _chainlink_candle_opens[key][candle_start] = value
                    
                    # Memory management: prune old entries
                    old_keys = [k for k in _chainlink_candle_opens[key] if k < candle_start - 3600 * 24]
                    for k in old_keys:
                        _chainlink_candle_opens[key].pop(k)


polymarket_rtds_ingest = PolymarketRTDSIngest()
