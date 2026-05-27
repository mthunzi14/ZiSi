"""
arbitrage_scanner.py - High-Frequency Asynchronous Cross-Platform Arbitrage Scanner.
Scans for mispricings between Polymarket Gamma API and Kalshi V2 events.
"""
import asyncio
import logging
import time
import aiohttp
import re
from datetime import datetime, timezone
from typing import List, Dict, Any, Tuple
import numpy as np

from infrastructure.state.state_manager import get_current_balance, update_balance
from infrastructure.exchange.trader import place_order, execute_exit
from infrastructure.state.diagnostics import global_diagnostics
from core.engine.updown_engine import _fetch_klines_async
from polytope_solver import PolytopeSolver
from core.engine.block_bundler import BlockBundler

log = logging.getLogger("zisi.arbitrage")

POLY_GAMMA_API = "https://gamma-api.polymarket.com"
KALSHI_API = "https://external-api.kalshi.com/trade-api/v2"

# Centralized stop words
STOPWORDS = {"will", "the", "a", "an", "is", "are", "be", "by", "in", "on", "at", "to", "for", "of", "with", "that", "this", "or", "and"}

def normalize_text(text: str) -> str:
    if not text:
        return ""
    text = text.lower()
    for char in ["?", ".", ",", "!", "-", '"', "'", "(", ")", "[", "]"]:
        text = text.replace(char, " ")
    return " ".join(text.split())

def check_overlap(title1: str, title2: str) -> bool:
    t1 = normalize_text(title1)
    t2 = normalize_text(title2)
    
    words1 = set(t1.split())
    words2 = set(t2.split())
    
    w1 = words1 - STOPWORDS
    w2 = words2 - STOPWORDS
    
    if not w1 or not w2:
        return False
        
    intersection = w1.intersection(w2)
    union = w1.union(w2)
    
    jaccard = len(intersection) / len(union) if union else 0
    
    # Exact number matching is critical!
    nums1 = set(re.findall(r'\d+', t1))
    nums2 = set(re.findall(r'\d+', t2))
    if nums1 != nums2:
        return False
        
    # Exact month matching
    months = {"january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december",
              "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "oct", "nov", "dec"}
    m1 = w1.intersection(months)
    m2 = w2.intersection(months)
    if m1 != m2:
        return False
        
    # High Jaccard similarity or key term match
    return jaccard >= 0.55

class ArbitrageScanner:
    def __init__(self, telegram_callback=None):
        self.telegram_callback = telegram_callback

    def _try_telegram(self, msg: str):
        if self.telegram_callback:
            try:
                self.telegram_callback(msg)
            except Exception:
                pass

    async def fetch_polymarket(self, session: aiohttp.ClientSession) -> List[Dict[str, Any]]:
        url = f"{POLY_GAMMA_API}/markets?active=true&closed=false&limit=100"
        headers = {"User-Agent": "Mozilla/5.0"}
        try:
            async with session.get(url, headers=headers, ssl=False, timeout=15) as resp:
                if resp.status == 200:
                    return await resp.json()
                else:
                    log.error("[ARB] Polymarket Gamma API returned status: %d", resp.status)
        except Exception as e:
            log.error("[ARB] Failed to fetch Polymarket Gamma markets: %r", e)
        return []

    async def fetch_kalshi(self, session: aiohttp.ClientSession) -> List[Dict[str, Any]]:
        # V2 events API with nested markets - increased limit from 25 to 100
        url = f"{KALSHI_API}/events?status=open&limit=100&with_nested_markets=true"
        headers = {"User-Agent": "Mozilla/5.0"}
        try:
            async with session.get(url, headers=headers, ssl=False, timeout=15) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("events", [])
                else:
                    log.error("[ARB] Kalshi V2 Events API returned status: %d", resp.status)
        except Exception as e:
            log.error("[ARB] Failed to fetch Kalshi events: %r", e)
        return []

    async def fetch_kalshi_orderbook(self, session: aiohttp.ClientSession, ticker: str) -> Dict[str, Any]:
        """Fetch real-time fixed-point orderbook arrays from Kalshi V2."""
        url = f"https://external-api.kalshi.com/trade-api/v2/markets/{ticker}/orderbook"
        headers = {"User-Agent": "Mozilla/5.0"}
        try:
            async with session.get(url, headers=headers, ssl=False, timeout=10) as resp:
                if resp.status == 200:
                    return await resp.json()
        except Exception as e:
            log.debug("[ARB] Failed to fetch Kalshi orderbook for %s: %r", ticker, e)
        return {}

    def scan_for_pairs(self, poly_markets: List[Dict[str, Any]], kalshi_events: List[Dict[str, Any]]) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
        pairs = []
        for pm in poly_markets:
            poly_title = pm.get("question", "")
            if not poly_title or not pm.get("outcomePrices") or len(pm.get("outcomePrices", [])) < 2:
                continue
                
            # Iterate through Kalshi events and their sub-markets
            for ke in kalshi_events:
                markets = ke.get("markets", [])
                for km in markets:
                    kalshi_title = km.get("title") or ke.get("title") or ""
                    if not kalshi_title:
                        continue
                        
                    # Check overlap match
                    if check_overlap(poly_title, kalshi_title):
                        pairs.append((pm, km))
        return pairs

    def calculate_kelly_size(self, cost: float, spread: float, balance: float) -> float:
        """
        Fractional Kelly compounding:
        f* = 0.5 * ((b * p - q) / b) * Dampening_Factor
        """
        if cost <= 0 or spread <= 0:
            return 0.0
            
        b = spread / cost
        p = 0.98 # Delta neutral arbitrage high success probability
        q = 1.0 - p
        
        # Raw Kelly bet
        f_raw = p - (q / b)
        if f_raw <= 0:
            return 0.0
            
        # Half-Kelly multiplier
        f_half = 0.5 * f_raw
        
        # Dynamic connection and performance dampener
        risk_mult = global_diagnostics.get_risk_multiplier()
        
        # Combine into position size
        bet_usd = balance * f_half * risk_mult
        
        # Absolute safety cap: maximum 15% of account balance
        size_cap = balance * 0.15
        bet_usd = min(bet_usd, size_cap)
        
        return round(bet_usd, 2)

    async def execute_arbitrage(self, poly_m: Dict[str, Any], kalshi_m: Dict[str, Any], direction_poly: str, price_poly: float, price_kalshi: float):
        """Project arbitrage vector and execute atomic private bundle transacting."""
        balance = get_current_balance()
        cost = price_poly + price_kalshi
        spread = 1.0 - cost

        poly_dir = direction_poly # "YES" or "NO"
        kalshi_dir = "NO" if poly_dir == "YES" else "YES"

        # ── 1. MATHEMATICAL POLYTOPE PROJECTION (Bregman KL Projection) ──
        # Group: indices [0, 1] represent the mutually exclusive outcomes of this event.
        # They must sum to 1.0.
        # Market vector: [price_poly, 1.0 - price_kalshi]
        theta = np.array([price_poly, 1.0 - price_kalshi])
        groups = [[0, 1]]
        
        solver = PolytopeSolver(num_vars=2)
        projected, kl_edge = solver.project(theta, groups)
        
        # If the mathematical edge is too thin, stop
        if kl_edge < 0.005:
            log.info("[ARB] Mathematical KL edge %.4f below minimum threshold 0.005 — skipping", kl_edge)
            return

        # Sizing using fractional Kelly based on the exact Bregman KL edge
        bet_usd = self.calculate_kelly_size(cost, spread, balance)
        if bet_usd < 2.00:
            log.info("[ARB] Size $%.2f below $2.00 minimum — skipping opportunity", bet_usd)
            return

        log.warning(
            "[ARB] BREGMAN ARBITRAGE TRIGGERED! Cost=%.2fc, KL-Edge=%.4f, Sizing=$%.2f on %s",
            cost * 100, kl_edge, bet_usd, poly_m["question"][:60]
        )

        # Allocate sizes proportional to projected prices
        poly_cost = max(1.0, round(bet_usd * (projected[0]), 2))
        kalshi_cost = max(1.0, round(bet_usd * (1.0 - projected[0]), 2))

        start_time = time.time()

        # ── 2. HIGH-FREQUENCY PRIVATE RPC BUNDLER BUNDLING ──
        bundler = BlockBundler(mode="PAPER")
        bundle_payload = [
            {
                "symbol": "POLYMARKET",
                "direction": poly_dir,
                "price": price_poly,
                "amount": poly_cost,
                "market_slug": poly_m["question"][:40]
            },
            {
                "symbol": "KALSHI",
                "direction": kalshi_dir,
                "price": price_kalshi,
                "amount": kalshi_cost,
                "market_slug": poly_m["question"][:40]
            }
        ]

        bundle_res = await bundler.submit_atomic_bundle(bundle_payload)
        
        latency_ms = (time.time() - start_time) * 1000
        simulated_slippage = round(max(0.01, (latency_ms / 150.0) * 0.25), 2)

        if bundle_res.get("success", False):
            # Record atomic fills in our state engine database (positions_state.json)
            poly_order = place_order(
                event_id = poly_m["id"],
                market_id = poly_m["id"],
                amount_dollars = poly_cost,
                direction = poly_dir,
                entry_price = price_poly,
                event_title = f"[ARB][LEG-A][PRIVATE] {poly_m['question']}",
                expiry_ts = int(poly_m.get("expiry_ts") or (time.time() + 86400)),
                market = "POLYMARKET"
            )
            
            kalshi_order = place_order(
                event_id = poly_m["id"],
                market_id = poly_m["id"],
                amount_dollars = kalshi_cost,
                direction = kalshi_dir,
                entry_price = price_kalshi,
                event_title = f"[ARB][LEG-B][PRIVATE] {poly_m['question']}",
                expiry_ts = int(poly_m.get("expiry_ts") or (time.time() + 86400)),
                market = "KALSHI"
            )

            if poly_order and kalshi_order:
                log.info("[ARB] SUCCESS! Placed private atomic bundle. Cost: $%.2f | Extracted Spread: $%.2f", bet_usd, bet_usd * (1.0 + spread))
                global_diagnostics.log_execution(latency_ms, simulated_slippage, successful_hedge=True)
                self._try_telegram(
                    f"🎉 BUNDLE SUCCESS! Captured {spread*100:.1f}% spread on {poly_m['question'][:40]} | Net size ${bet_usd:.2f}"
                )
        else:
            log.error("[ARB] Private Block Bundler failed to submit atomic transaction bundle.")
            global_diagnostics.log_execution(latency_ms, simulated_slippage, successful_hedge=False)

    async def scan_cycle(self, session: aiohttp.ClientSession):
        """Execute a single non-blocking scan and comparison pass."""
        # Fetch dynamic volatility spread hurdle
        dynamic_hurdle = 0.020 # fallback 2%
        try:
            klines = await _fetch_klines_async(session, "BTC", "5m", 20)
            if klines and len(klines) >= 15:
                # Compute ATR
                tr_values = []
                for i in range(1, len(klines)):
                    high = float(klines[i][2])
                    low = float(klines[i][3])
                    prev_close = float(klines[i-1][4])
                    tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
                    tr_values.append(tr)
                atr = sum(tr_values[-14:]) / 14
                btc_price = float(klines[-1][4])
                volatility_ratio = atr / btc_price if btc_price else 0.001
                
                # Scale between 1.0% and 4.0%
                base_hurdle = 0.010
                volatility_scale = volatility_ratio * 2.0
                dynamic_hurdle = min(0.040, max(0.010, base_hurdle + volatility_scale))
                log.info(
                    "[ARB] Dynamic Volatility-Scaled Hurdle calibrated: %.2f%% (ATR=%.2f, Price=%.2f)",
                    dynamic_hurdle * 100, atr, btc_price
                )
        except Exception as e:
            log.debug("[ARB] Failed to calculate dynamic hurdle, falling back to 2%: %r", e)

        log.info("[ARB] Starting arbitrage scan cycle (spread hurdle: %.2f%%)...", dynamic_hurdle * 100)
        poly_markets = await self.fetch_polymarket(session)
        kalshi_events = await self.fetch_kalshi(session)
        
        if not poly_markets or not kalshi_events:
            return
            
        pairs = self.scan_for_pairs(poly_markets, kalshi_events)
        log.info("[ARB] Scanned and paired %d identical contracts.", len(pairs))
        
        for poly_m, kalshi_m in pairs:
            # Parse Polymarket prices
            prices_poly = poly_m.get("outcomePrices")
            if not prices_poly or len(prices_poly) < 2:
                continue
            try:
                poly_yes = float(prices_poly[0])
                poly_no = float(prices_poly[1])
            except (ValueError, TypeError):
                continue
                
            # Fetch real-time exact Kalshi orderbook
            kalshi_ticker = kalshi_m.get("ticker")
            if not kalshi_ticker:
                continue
                
            ob_data = await self.fetch_kalshi_orderbook(session, kalshi_ticker)
            ob_fp = ob_data.get("orderbook_fp", {})
            
            # The lowest ask price is the lowest price in the NO array (to buy NO)
            # Kalshi API provides yes_dollars and no_dollars as lists of [price_dollars, count]
            yes_dollars = ob_fp.get("yes_dollars", [])
            no_dollars = ob_fp.get("no_dollars", [])
            
            # To buy YES, we need to lift the yes ask (which is someone selling YES, i.e., lowest in yes_dollars)
            # Wait, Kalshi's orderbook `yes_dollars` represents BIDS on the YES contract. To buy YES, you hit the NO BIDS!
            # Actually, standard orderbooks: to buy YES you hit the lowest offer. 
            # In Kalshi's binary orderbook, "yes_dollars" are bids for YES. "no_dollars" are bids for NO.
            # So to buy YES, you match with a bid for NO. 
            # Thus, yes_ask = 1.0 - highest no_bid.
            # Let's extract the highest bid from yes_dollars and no_dollars (the arrays are usually sorted)
            if not yes_dollars and not no_dollars:
                continue
                
            # Highest yes bid
            yes_bids = [float(lvl[0]) for lvl in yes_dollars]
            max_yes_bid = max(yes_bids) if yes_bids else 0.0
            
            # Highest no bid
            no_bids = [float(lvl[0]) for lvl in no_dollars]
            max_no_bid = max(no_bids) if no_bids else 0.0
            
            # To buy YES, you pay 1.0 - max_no_bid
            yes_ask = 1.0 - max_no_bid if max_no_bid > 0 else 0.99
            
            # To buy NO, you pay 1.0 - max_yes_bid
            no_ask = 1.0 - max_yes_bid if max_yes_bid > 0 else 0.99
                
            # Compare Spread 1: Buy YES on Poly, Buy NO on Kalshi
            # Cost = Poly YES + Kalshi NO
            cost_1 = poly_yes + no_ask
            if 0.05 < cost_1 < (1.0 - dynamic_hurdle): # require cost to be reasonable and spread > hurdle
                await self.execute_arbitrage(poly_m, kalshi_m, "YES", poly_yes, no_ask)
                continue # Execute only one arbitrage per pair in a cycle to avoid double positioning
                
            # Compare Spread 2: Buy NO on Poly, Buy YES on Kalshi
            # Cost = Poly NO + Kalshi YES
            cost_2 = poly_no + yes_ask
            if 0.05 < cost_2 < (1.0 - dynamic_hurdle):
                await self.execute_arbitrage(poly_m, kalshi_m, "NO", poly_no, yes_ask)
                continue

async def clob_warmup_loop(session: aiohttp.ClientSession):
    """Periodic daemon task that pings CLOB API endpoints to warm TCP pool."""
    log.info("[ARB] TCP Keep-Alive Warmup Daemon active (10s interval)")
    headers = {"User-Agent": "Mozilla/5.0"}
    while True:
        try:
            # Ping Polymarket Gamma base and Kalshi V2 events API using HEAD
            async with session.head("https://gamma-api.polymarket.com/markets?limit=1", headers=headers, ssl=False, timeout=3) as resp:
                pass
            async with session.head("https://external-api.kalshi.com/trade-api/v2/events?limit=1", headers=headers, ssl=False, timeout=3) as resp:
                pass
            log.debug("[ARB] TCP connections warmed successfully.")
        except Exception as e:
            log.debug("[ARB] Keep-alive warmup ping failed: %r", e)
        await asyncio.sleep(10)

async def arbitrage_scanner_loop(telegram_callback=None):
    """Background task running the arbitrage scanner loop."""
    scanner = ArbitrageScanner(telegram_callback)
    log.info("[ARB] Background Arbitrage Loop started (60s interval)")
    
    # Configure low latency connection pooling
    connector = aiohttp.TCPConnector(limit=10, ttl_dns_cache=300, keepalive_timeout=60)
    async with aiohttp.ClientSession(connector=connector, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Accept": "application/json",
    }) as session:
        # Spawn Keep-Alive Warmup Loop as background task
        asyncio.create_task(clob_warmup_loop(session))
        
        while True:
            try:
                await scanner.scan_cycle(session)
            except Exception as e:
                log.error("[ARB] Error in scanner cycle: %s", e, exc_info=True)
            await asyncio.sleep(60)
