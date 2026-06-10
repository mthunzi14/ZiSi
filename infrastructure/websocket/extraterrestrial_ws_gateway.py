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
                "type": "market",
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

    async def _ping_loop(self, ws) -> None:
        """Resend subscribe message every 60s as keepalive.
        Polymarket's CLOB WS has an ~80s server-side idle timeout and ignores
        WebSocket-protocol PINGs and unknown message types. Re-subscribing is
        the only guaranteed way to reset the idle timer."""
        try:
            while not ws.closed:
                await asyncio.sleep(45)
                if not ws.closed and self.subscriptions:
                    await ws.send_json({
                        "type": "market",
                        "assets_ids": list(self.subscriptions),
                        "custom_feature_enabled": True,
                    })
        except Exception:
            pass

    async def _ws_loop(self):
        while self.is_active:
            try:
                self._session = aiohttp.ClientSession(headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                })
                # No heartbeat= param — Polymarket CLOB doesn't respond to WS-level PING frames,
                # causing aiohttp to drop the connection. We use application-level JSON pings instead.
                async with self._session.ws_connect(self.feed_url) as ws:
                    self._ws = ws
                    log.info("[GOD-WS] Connected to Polymarket CLOB WebSocket")

                    if self.subscriptions:
                        msg = {
                            "type": "market",
                            "assets_ids": list(self.subscriptions),
                            "custom_feature_enabled": True
                        }
                        await ws.send_json(msg)

                    ping_task = asyncio.create_task(self._ping_loop(ws))
                    try:
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
                    finally:
                        ping_task.cancel()
            except Exception as e:
                log.error(f"[GOD-WS] Connection error: {e}")

            if self._session:
                await self._session.close()

            if self.is_active:
                log.warning("[GOD-WS] Disconnected. Reconnecting in 3 seconds...")
                await asyncio.sleep(3)

    def get_obi(self, token_id: str) -> float:
        """Returns the computed top-5 OBI for the token_id."""
        data = self.l2_cache.get(token_id)
        if not data:
            return 0.0
        return data.get("obi", 0.0)

    def _update_cache_bid_ask(self, asset_id: str, bid: float, ask: float):
        entry = self.l2_cache.get(asset_id, {
            "bid": 0.0, "ask": 0.0, "ts": 0.0, "bids": [], "asks": [], "obi": 0.0
        })
        entry["bid"] = bid
        entry["ask"] = ask
        entry["ts"] = time.time()
        self.l2_cache[asset_id] = entry
        if self._on_tick:
            mid = (bid + ask) / 2
            self._on_tick(asset_id, entry["ts"], mid)

    def _process_message(self, data: dict):
        event_type = data.get("event_type", "")

        # price_change: most frequent event — asset_id is nested inside price_changes[]
        if event_type == "price_change":
            for change in data.get("price_changes", []):
                aid = change.get("asset_id")
                if not aid:
                    continue
                best_bid = change.get("best_bid")
                best_ask = change.get("best_ask")
                if best_bid is not None and best_ask is not None:
                    try:
                        self._update_cache_bid_ask(aid, float(best_bid), float(best_ask))
                    except (ValueError, TypeError):
                        pass
            return

        asset_id = data.get("asset_id") or data.get("token_id")
        if not asset_id:
            return

        # best_bid_ask: direct best_bid/best_ask fields at top level
        if event_type == "best_bid_ask":
            best_bid = data.get("best_bid")
            best_ask = data.get("best_ask")
            if best_bid is not None and best_ask is not None:
                try:
                    self._update_cache_bid_ask(asset_id, float(best_bid), float(best_ask))
                except (ValueError, TypeError):
                    pass
            return

        bids = data.get("bids", [])
        asks = data.get("asks", [])

        # last_trade_price: single price field, no book levels
        if "price" in data and not bids and not asks:
            try:
                p = float(data["price"])
                self.l2_cache[asset_id] = {
                    "bid": p, "ask": p, "ts": time.time(), "bids": [], "asks": [], "obi": 0.0
                }
            except (ValueError, TypeError):
                pass
            return

        # book: full snapshot with bids/asks arrays
        cache_entry = self.l2_cache.get(asset_id, {
            "bid": 0.0, "ask": 0.0, "ts": 0.0, "bids": [], "asks": [], "obi": 0.0
        })

        if bids:
            cache_entry["bids"] = bids
        if asks:
            cache_entry["asks"] = asks

        bb = max([float(b.get("price", 0)) for b in cache_entry["bids"]]) if cache_entry["bids"] else cache_entry.get("bid", 0.0)
        ba = min([float(a.get("price", 0)) for a in cache_entry["asks"]]) if cache_entry["asks"] else cache_entry.get("ask", 0.0)

        sum_bid_qty = sum(float(b.get("size") or b.get("qty") or b.get("amount") or 0.0) for b in cache_entry["bids"][:5])
        sum_ask_qty = sum(float(a.get("size") or a.get("qty") or a.get("amount") or 0.0) for a in cache_entry["asks"][:5])

        obi = 0.0
        if (sum_bid_qty + sum_ask_qty) > 0.0:
            obi = (sum_bid_qty - sum_ask_qty) / (sum_bid_qty + sum_ask_qty)

        cache_entry.update({"bid": bb, "ask": ba, "ts": time.time(), "obi": obi})
        self.l2_cache[asset_id] = cache_entry

        if self._on_tick:
            mid, _ = self.get_price(asset_id)
            if mid:
                self._on_tick(asset_id, time.time(), mid)

polymarket_l2_gateway = ExtraterrestrialWSGateway()
