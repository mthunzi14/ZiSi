import asyncio
import logging
import json
import time
import aiohttp
from typing import Dict, Optional, Callable

log = logging.getLogger("zisi.extraterrestrial.ws")

class ExtraterrestrialWSGateway:
    """
    Polymarket CLOB WebSocket Gateway (Real L2 Orderbook Subscriptions).
    Maintains an in-memory ultra-fast L2 cache for instant price execution without REST latency.
    """
    def __init__(self, feed_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"):
        self.feed_url = feed_url
        self.subscriptions = set()
        
        # In-memory L2 orderbook cache: token_id -> {"bid": float, "ask": float, "ts": float}
        self.l2_cache: Dict[str, dict] = {}
        
        self.is_active = False
        self._session = None
        self._ws = None

    async def start_gateway(self, on_tick_callback: Optional[Callable[[str, float, float], None]] = None):
        self.is_active = True
        self._on_tick = on_tick_callback
        asyncio.create_task(self._ws_loop())
        log.info(f"[GOD-WS] Booting Extraterrestrial L2 Gateway -> {self.feed_url}")

    def subscribe(self, token_id: str):
        if token_id not in self.subscriptions:
            self.subscriptions.add(token_id)
            if self._ws and not self._ws.closed:
                asyncio.create_task(self._send_sub(token_id))
            log.info(f"[GOD-WS] Subscribed to L2 Feed: {token_id}")

    async def _send_sub(self, token_id: str):
        try:
            msg = {
                "type": "subscribe",
                "assets_ids": [token_id],
                "custom_feature_enabled": True
            }
            await self._ws.send_json(msg)
        except Exception as e:
            log.error(f"[GOD-WS] Failed to send subscription: {e}")

    def get_price(self, token_id: str) -> tuple[Optional[float], Optional[float]]:
        """Returns (mid_price, spread). Returns None, None if no data yet."""
        data = self.l2_cache.get(token_id)
        if not data:
            return None, None
        
        b = data.get("bid", 0.0)
        a = data.get("ask", 0.0)
        if b > 0 and a > 0:
            return round((b + a) / 2, 4), round(a - b, 4)
        return a or b or None, None

    async def _ws_loop(self):
        while self.is_active:
            try:
                self._session = aiohttp.ClientSession(headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                })
                async with self._session.ws_connect(self.feed_url, heartbeat=10.0) as ws:
                    self._ws = ws
                    log.info("[GOD-WS] Connected to Polymarket CLOB WebSocket")
                    
                    if self.subscriptions:
                        msg = {
                            "type": "subscribe",
                            "assets_ids": list(self.subscriptions),
                            "custom_feature_enabled": True
                        }
                        await ws.send_json(msg)
                    
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            try:
                                data = json.loads(msg.data)
                                if isinstance(data, list):
                                    for item in data:
                                        self._process_message(item)
                                else:
                                    self._process_message(data)
                            except json.JSONDecodeError:
                                pass
                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            break
            except Exception as e:
                log.error(f"[GOD-WS] Connection error: {e}")
            
            if self._session:
                await self._session.close()
                
            if self.is_active:
                log.warning("[GOD-WS] Disconnected. Reconnecting in 3 seconds...")
                await asyncio.sleep(3)

    def _process_message(self, data: dict):
        asset_id = data.get("asset_id") or data.get("token_id")
        if not asset_id:
            return

        bids = data.get("bids", [])
        asks = data.get("asks", [])
        
        if "price" in data and not bids and not asks:
            p = float(data["price"])
            self.l2_cache[asset_id] = {"bid": p, "ask": p, "ts": time.time()}
            return
            
        bb = float(bids[0].get("price", 0)) if bids else self.l2_cache.get(asset_id, {}).get("bid", 0.0)
        ba = float(asks[0].get("price", 0)) if asks else self.l2_cache.get(asset_id, {}).get("ask", 0.0)
        
        self.l2_cache[asset_id] = {"bid": bb, "ask": ba, "ts": time.time()}
        
        if self._on_tick:
            mid, _ = self.get_price(asset_id)
            if mid:
                self._on_tick(asset_id, time.time(), mid)

polymarket_l2_gateway = ExtraterrestrialWSGateway()
