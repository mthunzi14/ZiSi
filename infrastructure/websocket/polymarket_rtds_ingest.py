"""
polymarket_rtds_ingest.py - Real-Time Polymarket RTDS Chainlink Feed Ingest.
Streams real-time updates for crypto_prices_chainlink over a public connection,
caching prices and tracking candle boundary opens.
"""

import asyncio
import logging
import json
import time
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

    def start(self):
        if not self.running:
            self.running = True
            self._task = asyncio.create_task(self._socket_loop())
            log.info("[RTDS-WS] Ingest daemon started -> %s", self.ws_url)

    def stop(self):
        self.running = False
        if self._task:
            self._task.cancel()
            log.info("[RTDS-WS] Ingest daemon stopped.")

    async def _socket_loop(self):
        while self.running:
            try:
                log.info("[RTDS-WS] Connecting to %s...", self.ws_url)
                connector = aiohttp.TCPConnector(enable_cleanup_closed=True)
                async with aiohttp.ClientSession(connector=connector) as session:
                    async with session.ws_connect(self.ws_url, heartbeat=10.0) as ws:
                        log.info("[RTDS-WS] Connected to Polymarket RTDS")
                        
                        # Subscribe to crypto_prices_chainlink
                        sub_msg = {
                            "action": "subscribe",
                            "subscriptions": [
                                {
                                    "topic": "crypto_prices_chainlink",
                                    "type": "*",
                                    "filters": ""
                                }
                            ]
                        }
                        await ws.send_json(sub_msg)
                        log.info("[RTDS-WS] Subscribed to topic: crypto_prices_chainlink")
                        
                        # Start periodic PING sender task
                        ping_task = asyncio.create_task(self._ping_sender(ws))
                        
                        try:
                            async for msg in ws:
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
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error("[RTDS-WS] WebSocket connection exception: %r — reconnecting in 3s", e)
                await asyncio.sleep(3)

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

    async def _process_message(self, data: dict):
        topic = data.get("topic")
        if topic != "crypto_prices_chainlink":
            return
            
        payload = data.get("payload", {})
        symbol = payload.get("symbol", "").lower()
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
