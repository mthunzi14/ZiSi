import asyncio
import logging
import json
import time
import aiohttp
import threading
import queue
from typing import Dict, Optional, Callable

log = logging.getLogger("zisi.extraterrestrial.ws")

class ExtraterrestrialWSGateway:
    """
    Polymarket CLOB WebSocket Gateway (Real L2 Orderbook Subscriptions).
    Refactored to support a Tri-Socket Redundant Connection architecture to mitigate network jitter.
    Separates ingestion (Thread A - running 3 staggered connections) from processing (Thread B)
    via Queue to prevent skipped frames. Builds synthetic midpoint candles, implements OBI,
    10s keepalive, and connection watchdogs.
    """
    def __init__(self, feed_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"):
        self.feed_url = feed_url
        self.subscriptions = set()
        
        # In-memory L2 orderbook cache: token_id -> {"bid": float, "ask": float, "ts": float, "bids": [], "asks": [], "obi": float}
        self.l2_cache: Dict[str, dict] = {}
        
        # Synthetic midpoint candle cache: token_id -> {interval_seconds: [candles]}
        # Candle format: [open_time_ms, open, high, low, close, volume, close_time_ms]
        self.candle_cache: Dict[str, Dict[int, list]] = {}
        
        self.is_active = False
        
        # Thread-safe queue for incoming raw messages
        self.msg_queue = queue.Queue()
        
        # Active WebSockets: worker_id -> ws_client_session
        self.active_wss = {}
        
        # Timing trackers: worker_id -> last_received_timestamp
        self.last_msg_ts = {}
        self.listener_thread = None
        self.processor_task = None
        self._on_tick = None

    def start_gateway(self, on_tick_callback: Optional[Callable[[str, float, float], None]] = None):
        """Boot gateway: Spin up Thread A (Listener) and Thread B (Processor task)."""
        if self.is_active:
            return
        self.is_active = True
        self._on_tick = on_tick_callback
        
        # Thread A: Dedicated background ingestion thread (runs its own loop for all 3 workers)
        self.listener_thread = threading.Thread(target=self._run_listener_loop, daemon=True)
        self.listener_thread.start()
        
        # Thread B: Main-thread asyncio task that processes queue items sequentially
        self.processor_task = asyncio.create_task(self._processor_loop())
        log.info(f"[GOD-WS] Booted Extraterrestrial L2 Gateway (Tri-Socket Redundancy Active)")

    def _run_listener_loop(self):
        """Thread A Entry: Runs its own asyncio loop to manage the WebSocket connection."""
        loop = asyncio.new_event_loop()
        self.loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._ws_loop())
        finally:
            loop.close()

    async def _processor_loop(self):
        """Thread B Entry: Main loop pulling from the queue sequentially (no blocking)."""
        loop = asyncio.get_running_loop()
        while self.is_active:
            try:
                # Retrieve message from queue using run_in_executor to keep main event loop free
                msg = await loop.run_in_executor(None, self.msg_queue.get)
                if msg is None:  # Shutdown sentinel
                    break
                
                if isinstance(msg, list):
                    for item in msg:
                        self._process_message(item)
                else:
                    self._process_message(msg)
                
                self.msg_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error(f"[GOD-WS] Processor error: {e}")

    def subscribe(self, token_id: str):
        """Subscribe to token feed. Thread-safe."""
        if token_id not in self.subscriptions:
            self.subscriptions.add(token_id)
            if hasattr(self, "loop") and self.loop.is_running():
                # Run subscription message on Thread A loop
                asyncio.run_coroutine_threadsafe(self._broadcast_subscription(token_id), self.loop)
            log.info(f"[GOD-WS] Subscribed to L2 Feed: {token_id}")

    async def _broadcast_subscription(self, token_id: str):
        msg = {
            "type": "market",
            "assets_ids": [token_id],
            "custom_feature_enabled": True
        }
        for worker_id, ws in list(self.active_wss.items()):
            if ws and not ws.closed:
                try:
                    await ws.send_json(msg)
                    log.info(f"[GOD-WS] Worker {worker_id} sent subscription for {token_id}")
                except Exception as e:
                    log.error(f"[GOD-WS] Worker {worker_id} subscription failed: {e}")

    def get_price(self, token_id: str) -> tuple[Optional[float], Optional[float]]:
        """Returns (mid_price, spread) from in-memory cache."""
        data = self.l2_cache.get(token_id)
        if not data:
            return None, None
        
        b = data.get("bid", 0.0)
        a = data.get("ask", 0.0)
        if b > 0 and a > 0:
            return round((b + a) / 2, 4), round(a - b, 4)
        return a or b or None, None

    def get_obi(self, token_id: str) -> float:
        """Returns computed OBI from in-memory cache."""
        data = self.l2_cache.get(token_id)
        if not data:
            return 0.0
        return data.get("obi", 0.0)

    def get_klines(self, token_id: str, interval: str, limit: int = 30) -> list:
        """Get time-bucketed synthetic OHLC midpoint candles for a token ID."""
        interval_map = {"5m": 300, "15m": 900, "1h": 3600}
        sec = interval_map.get(interval, 300)
        token_candles = self.candle_cache.get(token_id, {})
        candles = token_candles.get(sec, [])
        return candles[-limit:]

    async def _ping_sender_worker(self, ws, worker_id: int):
        try:
            while self.is_active and not ws.closed:
                await asyncio.sleep(10)
                if not ws.closed:
                    await ws.send_str("PING")
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    async def _watchdog_worker(self, ws, worker_id: int):
        try:
            while self.is_active and not ws.closed:
                await asyncio.sleep(1.0)
                if time.time() - self.last_msg_ts.get(worker_id, 0.0) > 30.0:
                    log.warning(f"[GOD-WS] Worker {worker_id} Watchdog: 30s timeout reached — reconnecting.")
                    await ws.close()
                    break
        except asyncio.CancelledError:
            pass

    async def _ws_loop(self):
        """Thread A: WebSocket listener master loop. Spawns 3 staggered worker tasks."""
        workers = []
        for i in range(3):
            workers.append(asyncio.create_task(self._ws_worker(worker_id=i)))
            await asyncio.sleep(0.3) # 300ms staggered startup
        
        await asyncio.gather(*workers, return_exceptions=True)

    async def _ws_worker(self, worker_id: int):
        """Websocket connection worker representing one redundancy leg."""
        backoff = 3.0
        while self.is_active:
            session = None
            try:
                self.last_msg_ts[worker_id] = time.time()
                session = aiohttp.ClientSession(headers={
                    "User-Agent": f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) RedundantWS-{worker_id}",
                })
                async with session.ws_connect(self.feed_url) as ws:
                    self.active_wss[worker_id] = ws
                    log.info(f"[GOD-WS] Worker {worker_id} connected to Polymarket CLOB WebSocket")
                    backoff = 3.0
                    
                    if self.subscriptions:
                        msg = {
                            "type": "market",
                            "assets_ids": list(self.subscriptions),
                            "custom_feature_enabled": True
                        }
                        await ws.send_json(msg)

                    ping_task = asyncio.create_task(self._ping_sender_worker(ws, worker_id))
                    watchdog_task = asyncio.create_task(self._watchdog_worker(ws, worker_id))
                    
                    try:
                        async for msg in ws:
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                if msg.data == "PONG":
                                    continue
                                self.last_msg_ts[worker_id] = time.time()
                                try:
                                    payload = json.loads(msg.data)
                                    self.msg_queue.put(payload)
                                except json.JSONDecodeError:
                                    pass
                            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                break
                    finally:
                        ping_task.cancel()
                        watchdog_task.cancel()
            except Exception as e:
                log.warning(f"[GOD-WS] Worker {worker_id} connection exception: {e}")
            finally:
                if session:
                    await session.close()
                self.active_wss.pop(worker_id, None)

            if self.is_active:
                log.warning(f"[GOD-WS] Worker {worker_id} disconnected. Reconnecting in {backoff} seconds...")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)

    def _update_cache_bid_ask(self, asset_id: str, bid: float, ask: float):
        # First-unique deduplication gate: discard identical updates to conserve CPU & I/O
        cached = self.l2_cache.get(asset_id)
        if cached and cached.get("bid") == bid and cached.get("ask") == ask:
            return

        entry = self.l2_cache.setdefault(asset_id, {
            "bid": 0.0, "ask": 0.0, "ts": 0.0, "bids": [], "asks": [], "obi": 0.0
        })
        entry["bid"] = bid
        entry["ask"] = ask
        entry["ts"] = time.time()
        
        # Rule 2: Build time-bucketed OHLC midpoint candles
        if bid > 0 and ask > 0:
            midpoint = round((bid + ask) / 2, 4)
            self._update_midpoint_candle(asset_id, midpoint, entry["ts"])
            
        if self._on_tick:
            mid = (bid + ask) / 2
            self._on_tick(asset_id, entry["ts"], mid)

    def _update_midpoint_candle(self, token_id: str, midpoint: float, ts: float):
        self.candle_cache.setdefault(token_id, {})
        for interval in (300, 900, 3600):  # 5m, 15m, 1h
            candles = self.candle_cache[token_id].setdefault(interval, [])
            candle_start = int(ts // interval) * interval
            
            if not candles or candles[-1][0] != candle_start * 1000:
                if len(candles) >= 100:
                    candles.pop(0)
                # Candle structure: [open_time_ms, open, high, low, close, volume, close_time_ms]
                candles.append([candle_start * 1000, midpoint, midpoint, midpoint, midpoint, 0.0, (candle_start + interval) * 1000])
            else:
                candles[-1][2] = max(candles[-1][2], midpoint)  # High
                candles[-1][3] = min(candles[-1][3], midpoint)  # Low
                candles[-1][4] = midpoint  # Close

    def _process_message(self, data: dict):
        event_type = data.get("event_type", "")

        # price_change: most frequent event
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

        # best_bid_ask: direct best_bid/best_ask fields
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

        # book: full snapshot
        if event_type == "book" or bids or asks:
            # Rule 3: Best Bid at bids[-1] (absolute END), Best Ask sits at asks[0] (absolute START)
            best_bid = None
            best_ask = None
            if bids:
                try:
                    best_bid = float(bids[-1].get("price", 0))
                except (ValueError, TypeError, IndexError):
                    pass
            if asks:
                try:
                    best_ask = float(asks[0].get("price", 0))
                except (ValueError, TypeError, IndexError):
                    pass
                    
            if best_bid is not None and best_ask is not None:
                self._update_cache_bid_ask(asset_id, best_bid, best_ask)

            # Store bids/asks in cache for OBI
            cache_entry = self.l2_cache.setdefault(asset_id, {
                "bid": 0.0, "ask": 0.0, "ts": 0.0, "bids": [], "asks": [], "obi": 0.0
            })
            if bids:
                cache_entry["bids"] = bids
            if asks:
                cache_entry["asks"] = asks
                
            # Compute OBI
            sum_bid_qty = sum(float(b.get("size") or b.get("qty") or b.get("amount") or 0.0) for b in cache_entry["bids"][:5])
            sum_ask_qty = sum(float(a.get("size") or a.get("qty") or a.get("amount") or 0.0) for a in cache_entry["asks"][:5])
            obi = 0.0
            if (sum_bid_qty + sum_ask_qty) > 0.0:
                obi = (sum_bid_qty - sum_ask_qty) / (sum_bid_qty + sum_ask_qty)
            cache_entry["obi"] = obi

polymarket_l2_gateway = ExtraterrestrialWSGateway()
