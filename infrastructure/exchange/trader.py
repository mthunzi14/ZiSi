"""
trader.py - ZiSi Bot Order Execution
Places, monitors, and closes positions on Polymarket via the CLOB API.
Paper-trading mode simulates fills without touching real funds.

Silent Fill Reconciliation (0x_Punisher pattern):
  After ANY API timeout or ambiguous response, poll the order status endpoint
  directly rather than trusting the original response.  A background thread
  runs a full reconciliation pass every 30 s so the bot's in-memory view of
  open positions is always consistent with what is actually on-chain.
"""

import json
import logging
import random
import threading
import time
import uuid
from datetime import datetime, timezone, timedelta  # timedelta used for memory pruning
from pathlib import Path
from typing import Optional

import requests

import kalshi_python
from kalshi_python.models import CreateOrderRequest
import tempfile
import os

from config import load_config
from infrastructure.state.state_manager import get_current_balance, update_balance, GLOBAL_POSITIONS_LOCK

log = logging.getLogger("zisi.trader")

# In-memory store for open positions (paper trading + live fallback cache)
_open_positions: dict[str, dict] = {}

# ── Reconciliation state ──────────────────────────────────────────────────────
# Tracks orders whose fill status is uncertain (timeout / unclear API response).
# Reconciliation loop resolves these before allowing new trades on the same market.
_pending_reconcile: dict[str, dict] = {}   # order_id → {market_id, placed_at, amount}
_reconcile_lock    = threading.Lock()
_reconcile_thread: Optional[threading.Thread] = None
_reconcile_stop    = threading.Event()

# Prevents simultaneous writes to positions_state.json from the main thread
# and any background thread (e.g. reconciliation, future async work).
# GLOBAL_POSITIONS_LOCK is imported from state_manager.py


def _get_config() -> dict:
    return load_config()


def _calculate_exit_targets_fallback(entry_price: float, amount_spent: float) -> tuple[Optional[float], Optional[float]]:
    try:
        from core.risk.risk_manager import calculate_exit_targets
        res = calculate_exit_targets(entry_price, amount_spent)
        return res.get("target_price"), res.get("stop_loss")
    except Exception as e:
        log.warning("[TRADER] Could not compute dynamic exit targets: %s", e)
        cfg = _get_config()
        tp = round(entry_price * cfg.get("POSITION_TARGET_MULTIPLIER", 1.50), 4)
        sl = round(entry_price * cfg.get("POSITION_STOP_LOSS_MULTIPLIER", 0.85), 4)
        return tp, sl


# ---------------------------------------------------------------------------
# Silent Fill Reconciliation  (0x_Punisher pattern)
# ---------------------------------------------------------------------------

def _poll_order_status_live(order_id: str) -> str:
    """
    Directly query the CLOB API for order status.
    Returns 'FILLED', 'PENDING', 'CANCELLED', 'PARTIALLY_FILLED', or 'UNKNOWN'.
    Never raises — on any failure returns 'UNKNOWN'.
    """
    cfg = _get_config()
    clob_url = cfg["POLYMARKET_CLOB_API_URL"].rstrip("/")
    try:
        resp = _retry_request("GET", f"{clob_url}/orders/{order_id}")
        if resp is None:
            return "UNKNOWN"
        return resp.json().get("status", "UNKNOWN").upper()
    except Exception as exc:
        log.warning("[RECONCILE] Status poll failed for %s: %s", order_id, exc)
        return "UNKNOWN"


def _reconcile_pending_orders() -> None:
    """
    Resolve all orders in _pending_reconcile by polling each one directly.
    Called by the background thread and also synchronously after any timeout.

    Logic per pending order:
      • FILLED          → move to _open_positions so exits can fire correctly
      • CANCELLED       → remove from pending; no further action needed
      • PARTIALLY_FILLED→ log a warning and leave pending for next pass
      • UNKNOWN         → leave pending; will retry next cycle
      • PENDING (stale) → if >5 min old and still pending, cancel and remove

    THREADING FIX: The lock is held only to take a snapshot and to commit results.
    All blocking network I/O (_poll_order_status_live, _retry_request) runs outside
    the lock so that _register_pending_order and get_pending_reconcile_count never
    block waiting for HTTP retries to complete.
    """
    # ── Step 1: Snapshot inside the lock (fast, no I/O) ──────────────────────
    with _reconcile_lock:
        if not _pending_reconcile:
            return
        pending_copy = dict(_pending_reconcile)   # shallow copy — safe to iterate outside lock

    # ── Step 2: All network I/O outside the lock ──────────────────────────────
    cfg = _get_config()
    now = datetime.now(timezone.utc)
    resolved_ids: list[str] = []
    reconstructed: dict[str, dict] = {}  # order_id → position dict to add to _open_positions

    for order_id, meta in pending_copy.items():
        status = _poll_order_status_live(order_id)    # blocking HTTP — outside lock
        age_s  = (now - meta["placed_at"]).total_seconds()

        log.info(
            "[RECONCILE] order=%s | status=%s | age=%.0fs | market=%s",
            order_id, status, age_s, meta.get("market_id", "?"),
        )

        if status == "FILLED":
            resolved_ids.append(order_id)
            price  = meta.get("entry_price", 0.5)
            amount = meta.get("amount", 0.0)
            tp, sl = _calculate_exit_targets_fallback(price, amount)
            reconstructed[order_id] = {
                "order_id":        order_id,
                "event_id":        meta.get("event_id", ""),
                "market_id":       meta.get("market_id", ""),
                "direction":       meta.get("direction", "YES"),
                "amount_spent":    amount,
                "shares_acquired": round(amount / price, 4) if price > 0 else 0,
                "entry_price":     price,
                "timestamp":       meta["placed_at"].isoformat(),
                "status":          "FILLED",
                "target_price":    tp,
                "stop_loss":       sl,
                "open_time":       meta["placed_at"],
                "reconciled":      True,
            }

        elif status == "CANCELLED":
            log.info("[RECONCILE] Order %s was cancelled — removing", order_id)
            resolved_ids.append(order_id)

        elif status == "PENDING" and age_s > 300:
            log.warning(
                "[RECONCILE] Stale PENDING order %s (%.0fs old) — attempting cancel",
                order_id, age_s,
            )
            try:
                clob_url = cfg["POLYMARKET_CLOB_API_URL"].rstrip("/")
                _retry_request("DELETE", f"{clob_url}/orders/{order_id}")   # I/O outside lock
            except Exception:
                pass
            resolved_ids.append(order_id)

        elif status in ("PARTIALLY_FILLED",):
            log.warning("[RECONCILE] %s partially filled — monitoring", order_id)
            # Leave in pending; checked again next cycle

    # ── Step 3: Commit results inside the lock (fast, no I/O) ─────────────────
    if resolved_ids or reconstructed:
        with _reconcile_lock:
            for order_id in resolved_ids:
                _pending_reconcile.pop(order_id, None)

        # Write silent-fill positions outside the reconcile lock (uses GLOBAL_POSITIONS_LOCK)
        for order_id, pos_dict in reconstructed.items():
            if order_id not in _open_positions:
                log.warning("[RECONCILE] Silent fill detected! Reconstructing position for %s", order_id)
                _open_positions[order_id] = pos_dict
        if reconstructed:
            persist_positions()


def _register_pending_order(
    order_id: str,
    market_id: str,
    event_id: str,
    direction: str,
    amount: float,
    entry_price: float,
) -> None:
    """Mark an order for reconciliation (call when fill status is ambiguous)."""
    with _reconcile_lock:
        _pending_reconcile[order_id] = {
            "market_id":   market_id,
            "event_id":    event_id,
            "direction":   direction,
            "amount":      amount,
            "entry_price": entry_price,
            "placed_at":   datetime.now(timezone.utc),
        }
    log.info("[RECONCILE] Registered order %s for reconciliation", order_id)


def _reconciliation_loop() -> None:
    """
    Background daemon thread: run _reconcile_pending_orders() every 30 s.
    Runs until _reconcile_stop is set (called by stop_reconciliation_loop()).
    """
    log.info("[RECONCILE] Background reconciliation loop started (30s interval)")
    while not _reconcile_stop.is_set():
        try:
            _reconcile_pending_orders()
        except Exception as exc:
            log.error("[RECONCILE] Unexpected error in reconciliation loop: %s", exc)
        _reconcile_stop.wait(timeout=30)
    log.info("[RECONCILE] Background reconciliation loop stopped")


def start_reconciliation_loop() -> None:
    """
    Start the background reconciliation thread if not already running.
    Call once from main.py during bot startup.
    Safe to call multiple times — idempotent.
    """
    global _reconcile_thread
    cfg = _get_config()
    if cfg.get("BOT_MODE") == "paper_trading":
        log.info("[RECONCILE] Paper trading mode — reconciliation loop not needed")
        return

    if _reconcile_thread and _reconcile_thread.is_alive():
        return

    _reconcile_stop.clear()
    _reconcile_thread = threading.Thread(
        target=_reconciliation_loop,
        name="zisi-reconcile",
        daemon=True,
    )
    _reconcile_thread.start()
    log.info("[RECONCILE] Background thread started: %s", _reconcile_thread.name)


def stop_reconciliation_loop() -> None:
    """Signal the background reconciliation thread to stop gracefully."""
    _reconcile_stop.set()
    if _reconcile_thread:
        _reconcile_thread.join(timeout=5)
    log.info("[RECONCILE] Reconciliation loop stopped")


def get_pending_reconcile_count() -> int:
    """Return the number of orders currently awaiting reconciliation."""
    with _reconcile_lock:
        return len(_pending_reconcile)


def _retry_request(
    method: str,
    url: str,
    json_body: dict | None = None,
    params: dict | None = None,
    headers: dict | None = None,
) -> Optional[requests.Response]:
    cfg = _get_config()
    retries = cfg["API_RETRY_COUNT"]
    backoff = cfg["API_RETRY_BACKOFF_SECONDS"]
    timeout = cfg["API_TIMEOUT_SECONDS"]

    for attempt in range(1, retries + 1):
        try:
            resp = requests.request(
                method, url,
                json=json_body,
                params=params,
                headers=headers,
                timeout=timeout,
            )
            resp.raise_for_status()
            return resp
        except requests.RequestException as exc:
            log.warning("Attempt %d/%d for %s: %s", attempt, retries, url, exc)
            if attempt < retries:
                time.sleep(backoff * attempt)

    log.error("All %d attempts failed for %s", retries, url)
    return None


# ---------------------------------------------------------------------------
# Order placement
# ---------------------------------------------------------------------------

def place_order(
    event_id: str,
    market_id: str,
    amount_dollars: float,
    direction: str,
    entry_price: float,
    event_title: str = "",
    expiry_ts: int = 0,
    market: str = "POLYMARKET",
) -> Optional[dict]:
    """
    Place a BUY order for the given Polymarket market.

    In paper_trading mode, the order is simulated locally.
    In live_trading mode, the order is sent to the CLOB API.

    Args:
        event_id:       Polymarket event identifier.
        market_id:      Specific YES/NO market identifier.
        amount_dollars: Dollar amount to spend.
        direction:      "YES" or "NO".
        entry_price:    Limit price (0–1).
        event_title:    Human-readable event title (for display/logging).
    Returns:
        Order dict on success, None on failure.
    """
    cfg = _get_config()
    mode = cfg["BOT_MODE"]

    # Shares-first sizing (ZiSi sovereign pattern): avoids USD→shares rounding drift at low prices.
    # Polymarket uses whole shares — round to nearest integer, minimum 1.
    shares = max(1, round(amount_dollars / entry_price)) if entry_price > 0 else 1
    actual_cost = round(shares * entry_price, 4)  # true cost derived from share count
    order_id = f"zisi_{uuid.uuid4().hex[:12]}"
    timestamp = datetime.now(timezone.utc).isoformat()

    if not event_title:
        log.warning("[TRADE] Missing event_title for %s — will display as [%s]", order_id, event_id[:16])
    _display_title = event_title if event_title else f"[{event_id[:30]}]"

    # Live Kalshi order placement
    if market == "KALSHI" and mode != "paper_trading":
        log.info("[TRADE] Executing live Kalshi order for %s", order_id)

        kalshi_api_key = cfg.get("KALSHI_API_KEY")
        kalshi_priv_key = cfg.get("KALSHI_PRIVATE_KEY")

        if not kalshi_api_key or not kalshi_priv_key:
            log.error("[TRADE] Missing KALSHI_API_KEY or KALSHI_PRIVATE_KEY for live execution!")
            return None

        try:
            # SECURITY FIX: Inject private key in-memory via Configuration.private_key_pem.
            # Previously used tempfile.mkstemp() which wrote the raw PEM to disk — exposing
            # the key to other local processes and unprivileged filesystem readers.
            kalshi_cfg = kalshi_python.Configuration()
            kalshi_cfg.host = "https://api.elections.kalshi.com/trade-api/v2"
            kalshi_cfg.api_key_id = kalshi_api_key
            kalshi_cfg.private_key_pem = kalshi_priv_key.replace('\\n', '\n')

            kalshi_api_client = kalshi_python.ApiClient(configuration=kalshi_cfg)
            portfolio_api = kalshi_python.PortfolioApi(kalshi_api_client)

            kalshi_side = "yes" if direction.upper() == "YES" else "no"
            price_cents = int(round(entry_price * 100))

            # Kalshi API uses 'count' as integer number of contracts
            count = int(shares)

            create_order_req = CreateOrderRequest(
                ticker=market_id,
                action="buy",
                side=kalshi_side,
                count=count,
                type="limit",
                client_order_id=order_id
            )

            if kalshi_side == "yes":
                create_order_req.yes_price = price_cents
            else:
                create_order_req.no_price = price_cents

            resp = portfolio_api.create_order(create_order_req)
            api_status = resp.order.status if resp.order else "PENDING"

            order = {
                "order_id": order_id,
                "event_id": event_id,
                "market_id": market_id,
                "event_title": _display_title,
                "direction": direction,
                "amount_spent": actual_cost,
                "shares_acquired": shares,
                "entry_price": entry_price,
                "timestamp": timestamp,
                "status": api_status.upper(),
                "market": "KALSHI",
                **({"expiry_ts": expiry_ts} if expiry_ts else {}),
            }

            tp, sl = _calculate_exit_targets_fallback(entry_price, actual_cost)
            _open_positions[order_id] = {
                **order,
                "target_price": tp,
                "stop_loss": sl,
                "open_time": datetime.now(timezone.utc),
            }
            persist_positions()
            log.info("Kalshi Order placed: %s status=%s", order["order_id"], order["status"])
            return order

        except Exception as e:
            log.error("[TRADE] Kalshi order failed: %s", e)
            return None

    if mode == "paper_trading":
        log.info(
            "[PAPER] BUY %s | %d shares @ %.4f = $%.4f | %s",
            direction, shares, entry_price, actual_cost,
            _display_title[:55],
        )
        order = {
            "order_id": order_id,
            "event_id": event_id,
            "market_id": market_id,
            "event_title": _display_title,
            "direction": direction,
            "amount_spent": actual_cost,
            "shares_acquired": shares,
            "entry_price": entry_price,
            "timestamp": timestamp,
            "status": "FILLED",
            "market": market,
            **({"expiry_ts": expiry_ts} if expiry_ts else {}),
        }
        tp, sl = _calculate_exit_targets_fallback(entry_price, actual_cost)
        _open_positions[order_id] = {
            **order,
            "target_price": tp,
            "stop_loss": sl,
            "open_time": datetime.now(timezone.utc),
        }
        persist_positions()
        return order

    # Live order
    clob_url = cfg["POLYMARKET_CLOB_API_URL"].rstrip("/")
    payload = {
        "market_id": market_id,
        "side": "BUY",
        "amount": amount_dollars,
        "price_limit": entry_price,
        "order_type": "GTE",
    }

    resp = _retry_request("POST", f"{clob_url}/orders", json_body=payload)
    if resp is None:
        # API timeout / no response — we don't know if the order landed.
        # Register for reconciliation immediately so the loop can sort it out.
        log.error(
            "[TRADE] Order placement timed out for market %s — "
            "registering for reconciliation (0x_Punisher pattern)", market_id,
        )
        _register_pending_order(order_id, market_id, event_id, direction, amount_dollars, entry_price)
        return None

    data = resp.json()
    resolved_id = data.get("id", order_id)
    api_status  = data.get("status", "PENDING").upper()

    order = {
        "order_id":        resolved_id,
        "event_id":        event_id,
        "market_id":       market_id,
        "direction":       direction,
        "amount_spent":    amount_dollars,
        "shares_acquired": shares,
        "entry_price":     entry_price,
        "timestamp":       timestamp,
        "status":          api_status,
        "market":          market,
    }

    if api_status in ("PENDING", "PARTIALLY_FILLED"):
        # Status is ambiguous — poll once immediately before trusting it
        log.info("[TRADE] Status=%s for %s — polling to verify fill", api_status, resolved_id)
        verified_status = _poll_order_status_live(resolved_id)
        order["status"] = verified_status

        if verified_status not in ("FILLED",):
            # Still not confirmed — register for background reconciliation
            _register_pending_order(
                resolved_id, market_id, event_id, direction, amount_dollars, entry_price,
            )

    tp, sl = _calculate_exit_targets_fallback(entry_price, amount_dollars)
    _open_positions[order["order_id"]] = {
        **order,
        "event_title":  event_title or event_id,
        "target_price": tp,
        "stop_loss":    sl,
        "open_time":    datetime.now(timezone.utc),
    }
    persist_positions()
    log.info("Order placed: %s status=%s", order["order_id"], order["status"])
    return order


# ---------------------------------------------------------------------------
# Order / position queries
# ---------------------------------------------------------------------------

def execute_trade_smart(
    polymarket_event: dict,
    signal_data: dict,
    account_balance: float,
    position_size: float,
) -> Optional[dict]:
    """
    Smart order execution: limit at mid-price (30s wait) → chase market (15s) → market order.

    In paper trading mode, delegates directly to place_order.
    In live mode, attempts sequential limit orders before falling back to market.

    Args:
        polymarket_event: Polymarket event dict with markets, bid/ask data.
        signal_data:      Sentiment signal (used for direction).
        account_balance:  Current account balance (unused directly, for future sizing).
        position_size:    Dollar amount to place.
    Returns:
        Order dict on success, None on failure.
    """
    cfg = _get_config()

    sentiment = signal_data.get("sentiment", "neutral")
    _ev_title_lower = (polymarket_event.get("title", "") or "").lower()
    _is_updown = "up or down" in _ev_title_lower or "updown" in _ev_title_lower
    if _is_updown:
        direction = "UP" if sentiment == "bullish" else "DOWN"
    else:
        direction = "YES" if sentiment == "bullish" else "NO"

    markets = polymarket_event.get("markets", [])
    if direction == "YES":
        market = next(
            (m for m in markets if "YES" in str(m.get("outcomeLabel", "")).upper()),
            markets[0] if markets else None,
        )
    else:
        market = next(
            (m for m in markets if "NO" in str(m.get("outcomeLabel", "")).upper()),
            markets[1] if len(markets) > 1 else (markets[0] if markets else None),
        )

    if not market:
        log.warning("[SMART-EXEC] No market found for direction %s", direction)
        return None

    market_id = market["id"]
    event_id = polymarket_event["id"]

    # Fetch live CLOB price — use YES token ID when available (long decimal string),
    # fall back to conditionId / market id (hex) which may also work on CLOB.
    _clob_tokens = market.get("clobTokenIds") or []
    _token_objects = market.get("tokens") or []
    _is_bearish_sentiment = (sentiment or "neutral").lower() == "bearish"

    if _clob_tokens:
        # Prefer the YES token (index 0) for price; derive NO price below if needed
        clob_market_id = _clob_tokens[0]
    elif _token_objects:
        _yes_tok = next((t for t in _token_objects if t.get("outcome", "").upper() == "YES"), _token_objects[0] if _token_objects else None)
        clob_market_id = (_yes_tok or {}).get("token_id", "") or market.get("conditionId") or market.get("id", "")
    else:
        clob_market_id = market.get("conditionId") or market.get("id", "")

    mid_price = None
    if clob_market_id:
        try:
            from infrastructure.websocket.extraterrestrial_ws_gateway import polymarket_l2_gateway
            # Try fetching from ultra-fast L2 memory cache first
            cached_mid, _ = polymarket_l2_gateway.get_price(clob_market_id)
            if cached_mid is not None:
                # Direct hit in L2 cache
                yes_mid = cached_mid
                mid_price = round(1.0 - yes_mid, 4) if _is_bearish_sentiment else yes_mid
                log.info("[SMART-EXEC] L2 WS Cache HIT! Price %.4f (YES=%.4f %s) for %s",
                         mid_price, yes_mid, "NO" if _is_bearish_sentiment else "YES", str(clob_market_id)[:24])
            else:
                # Fallback to REST if not subscribed yet
                from infrastructure.exchange.data_fetcher import get_event_current_price as _gcp
                _pd = _gcp(clob_market_id)
                if _pd and isinstance(_pd.get("price"), (int, float)):
                    _p = float(_pd["price"])
                    if 0.03 < _p < 0.97:
                        _bid = float(_pd.get("bid", _p - 0.01))
                        _ask = float(_pd.get("ask", _p + 0.01))
                        yes_mid = round((_bid + _ask) / 2, 4)
                        mid_price = round(1.0 - yes_mid, 4) if _is_bearish_sentiment else yes_mid
                        log.info("[SMART-EXEC] REST Fallback price %.4f (YES=%.4f %s) for %s",
                                 mid_price, yes_mid, "NO" if _is_bearish_sentiment else "YES", str(clob_market_id)[:24])
        except Exception as _pe:
            log.debug("[SMART-EXEC] L2/REST price fetch failed: %s", _pe)
    if mid_price is None:
        mid_price = float(market.get("price", 0.5))
        if mid_price <= 0.03 or mid_price >= 0.97:
            log.warning("[SMART-EXEC] No valid price for %s (%.4f) — skipping", str(clob_market_id)[:24], mid_price)
            return None
        log.debug("[SMART-EXEC] Using event price fallback %.4f", mid_price)

    _ev_title = polymarket_event.get("title") or polymarket_event.get("question") or event_id
    if cfg["BOT_MODE"] == "paper_trading":
        log.info("[SMART-EXEC] Paper mode — delegating to place_order at %.4f", mid_price)
        return place_order(
            event_id=event_id,
            market_id=market_id,
            amount_dollars=position_size,
            direction=direction,
            entry_price=mid_price,
            event_title=_ev_title,
        )

    # Live mode: attempt limit orders before market fallback
    clob_url = cfg["POLYMARKET_CLOB_API_URL"].rstrip("/")

    # ATTEMPT 1: limit at mid-price, wait 30s
    log.info("[SMART-EXEC] Limit order at mid-price %.4f (30s wait)", mid_price)
    payload_1 = {"market_id": market_id, "side": "BUY", "amount": position_size, "price_limit": mid_price, "order_type": "GTE"}
    resp_1 = _retry_request("POST", f"{clob_url}/orders", json_body=payload_1)

    if resp_1 is None:
        # Timeout on placement — register and abort; reconcile loop will recover
        tmp_id = f"zisi_{uuid.uuid4().hex[:12]}"
        log.warning("[SMART-EXEC] Attempt-1 timed out — registering %s for reconciliation", tmp_id)
        _register_pending_order(tmp_id, market_id, event_id, direction, position_size, mid_price)
        return None

    data_1    = resp_1.json()
    order_id1 = data_1.get("id", "")

    if data_1.get("status", "").upper() == "FILLED":
        log.info("[SMART-EXEC] Limit filled immediately at %.4f", mid_price)
        return _build_order_dict(data_1, event_id, market_id, direction, position_size, mid_price)

    # Wait, then poll status directly (not via a second request that can also timeout)
    time.sleep(30)
    verified_status1 = _poll_order_status_live(order_id1) if order_id1 else "UNKNOWN"
    if verified_status1 == "FILLED":
        log.info("[SMART-EXEC] Limit confirmed filled after 30s at %.4f", mid_price)
        return _build_order_dict(data_1, event_id, market_id, direction, position_size, mid_price)

    # Cancel unfilled limit before chasing to avoid double-fill
    if order_id1:
        try:
            _retry_request("DELETE", f"{clob_url}/orders/{order_id1}")
            log.info("[SMART-EXEC] Cancelled unfilled limit %s before chase", order_id1)
        except Exception:
            pass

    # ATTEMPT 2: chase market at +1% above mid, wait 15s
    chase_price = round(mid_price * 1.01, 6)
    log.info("[SMART-EXEC] Chasing market at %.4f (15s wait)", chase_price)
    payload_2 = {"market_id": market_id, "side": "BUY", "amount": position_size, "price_limit": chase_price, "order_type": "GTE"}
    resp_2 = _retry_request("POST", f"{clob_url}/orders", json_body=payload_2)

    if resp_2 is None:
        tmp_id2 = f"zisi_{uuid.uuid4().hex[:12]}"
        log.warning("[SMART-EXEC] Attempt-2 timed out — registering %s for reconciliation", tmp_id2)
        _register_pending_order(tmp_id2, market_id, event_id, direction, position_size, chase_price)
        return None

    data_2    = resp_2.json()
    order_id2 = data_2.get("id", "")

    time.sleep(15)
    verified_status2 = _poll_order_status_live(order_id2) if order_id2 else "UNKNOWN"
    if verified_status2 == "FILLED":
        log.info("[SMART-EXEC] Chase limit confirmed filled at %.4f", chase_price)
        return _build_order_dict(data_2, event_id, market_id, direction, position_size, chase_price)

    # Cancel unfilled chase before market fallback
    if order_id2:
        try:
            _retry_request("DELETE", f"{clob_url}/orders/{order_id2}")
            log.info("[SMART-EXEC] Cancelled unfilled chase %s before market fallback", order_id2)
        except Exception:
            pass

    # FALLBACK: market order (last resort)
    log.info("[SMART-EXEC] No limit fills — executing market order at %.4f", chase_price)
    return place_order(
        event_id=event_id,
        market_id=market_id,
        amount_dollars=position_size,
        direction=direction,
        entry_price=chase_price,
        event_title=_ev_title,
    )


def _build_order_dict(api_data: dict, event_id: str, market_id: str, direction: str, amount: float, price: float) -> dict:
    """Build a normalized order dict from CLOB API response."""
    from datetime import datetime, timezone
    shares = round(amount / price, 4) if price > 0 else 0
    order_id = api_data.get("id", f"zisi_{uuid.uuid4().hex[:12]}")
    return {
        "order_id": order_id,
        "event_id": event_id,
        "market_id": market_id,
        "direction": direction,
        "amount_spent": amount,
        "shares_acquired": shares,
        "entry_price": price,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": "FILLED",
    }


def check_order_status(order_id: str) -> str:
    """
    Return the fill status of an order.

    Returns one of: 'FILLED', 'PENDING', 'CANCELLED', 'PARTIALLY_FILLED', 'UNKNOWN'
    """
    cfg = _get_config()

    if cfg["BOT_MODE"] == "paper_trading":
        pos = _open_positions.get(order_id)
        return pos["status"] if pos else "UNKNOWN"

    clob_url = cfg["POLYMARKET_CLOB_API_URL"].rstrip("/")
    resp = _retry_request("GET", f"{clob_url}/orders/{order_id}")
    if resp is None:
        return "UNKNOWN"

    data = resp.json()
    return data.get("status", "UNKNOWN").upper()


def get_current_position(order_id: str) -> Optional[dict]:
    """
    Return current position details including unrealised P&L.

    In paper mode, current_price is approximated from the stored entry price
    (the main loop updates this via check_exit_condition).
    """
    cfg = _get_config()
    pos = _open_positions.get(order_id)

    if pos is None:
        log.warning("No position found for order_id %s", order_id)
        return None

    current_price = pos.get("current_price", pos["entry_price"])
    shares = pos["shares_acquired"]
    current_value = round(shares * current_price, 2)
    entry_value = pos["amount_spent"]
    unrealised_pnl = round(current_value - entry_value, 2)
    unrealised_pct = round((unrealised_pnl / entry_value) * 100, 2) if entry_value else 0

    return {
        "order_id": order_id,
        "market_id": pos["market_id"],
        "shares_held": shares,
        "entry_price": pos["entry_price"],
        "current_price": current_price,
        "current_value": current_value,
        "unrealized_pnl": unrealised_pnl,
        "unrealized_pnl_percent": unrealised_pct,
    }


def count_open_trades() -> int:
    """Return the number of currently open positions."""
    return len([p for p in _open_positions.values() if p.get("status") not in ("CLOSED", "CANCELLED")])


def has_open_position(order_id: str) -> bool:
    """True if order_id is tracked in the current session's open positions."""
    return order_id in _open_positions


def get_all_open_trades() -> list[dict]:
    """Return all currently open position dicts (enriched with targets)."""
    return [
        p for p in _open_positions.values()
        if p.get("status") not in ("CLOSED", "CANCELLED")
    ]


def check_and_close_paper_trades(max_hold_minutes: int = 240) -> list[dict]:
    """
    Paper-trading only: auto-close positions older than max_hold_minutes.
    Simulates a 60/40 win/loss split: +10% gain or -5% loss on position value.
    Returns a list of exit result dicts for each trade closed.
    """
    cfg = _get_config()
    if cfg["BOT_MODE"] != "paper_trading":
        return []

    now = datetime.now(timezone.utc)
    closed = []

    for order_id, pos in list(_open_positions.items()):
        if pos.get("status") in ("CLOSED", "CANCELLED"):
            continue

        open_time: datetime = pos.get("open_time", now)
        age_minutes = (now - open_time).total_seconds() / 60

        # Derive the correct market window from the event_title TF tag [5m], [15m], etc.
        # hold_minutes stored on the position is the age at entry (0.0) — not the window.
        _ev_title = (pos.get("event_title") or "").upper()
        is_updown = "UPDOWN" in _ev_title or "UP OR DOWN" in _ev_title
        if is_updown:
            import re as _re
            _tf_match = _re.search(r'\[(\d+)M\]', _ev_title)
            effective_max_minutes = int(_tf_match.group(1)) if _tf_match else 5
        else:
            effective_max_minutes = max_hold_minutes

        entry_price = pos["entry_price"]
        _is_short_tf = "5M" in _ev_title or "15M" in _ev_title or "UPDOWN" in _ev_title

        target_price = pos.get("target_price")
        if _is_short_tf:
            target_price = 0.88
        elif not target_price or target_price <= 0:
            target_price = round(entry_price * cfg.get("POSITION_TARGET_MULTIPLIER", 1.50), 4)

        stop_loss = pos.get("stop_loss")
        if _is_short_tf:
            stop_loss = -1.0
        elif not stop_loss or stop_loss <= 0:
            stop_loss = round(entry_price * cfg.get("POSITION_STOP_LOSS_MULTIPLIER", 0.50), 4)

        # ── Live exit price — NO simulation, all markets use real CLOB/Gamma data ──
        exit_price = None
        _market_id = pos.get("market_id") or pos.get("conditionId")

        # Try live L2 WS Cache first, then fall back to REST
        if _market_id:
            try:
                from infrastructure.websocket.extraterrestrial_ws_gateway import polymarket_l2_gateway
                mid_val, _ = polymarket_l2_gateway.get_price(_market_id)
                if mid_val is not None and 0.01 <= mid_val <= 0.99:
                    exit_price = round(mid_val, 4)
                    log.info("[LIVE-EXIT] L2 WS Cache price %.4f for %s", exit_price, order_id)
            except Exception:
                pass

            if exit_price is None:
                try:
                    from infrastructure.exchange.data_fetcher import get_event_current_price as _gcp
                    _pd = _gcp(_market_id)
                    if _pd and isinstance(_pd.get("price"), (int, float)):
                        _real = float(_pd["price"])
                        # Accept any live price; if near 0/1 it is likely resolved
                        if 0.01 <= _real <= 0.99:
                            exit_price = round(_real, 4)
                            log.info("[LIVE-EXIT] CLOB REST price %.4f for %s", exit_price, order_id)
                        elif _real < 0.01:
                            exit_price = 0.01
                        else:
                            exit_price = 0.99
                except Exception as _ce:
                    log.debug("[LIVE-EXIT] CLOB fetch failed for %s: %s", order_id, _ce)

        # If price fetch failed or market is at extreme, check resolution
        if exit_price is None or exit_price <= 0.03 or exit_price >= 0.97:
            try:
                from infrastructure.exchange.data_fetcher import fetch_market_resolution as _fmr
                _outcome = _fmr(_market_id) if _market_id else None
                if _outcome in ("YES", "UP"):
                    exit_price = 0.01 if pos.get("direction", "YES").upper() in ("NO", "DOWN") else 0.99
                    log.info("[LIVE-EXIT] Resolved %s → %.2f for %s", _outcome, exit_price, order_id)
                elif _outcome in ("NO", "DOWN"):
                    exit_price = 0.99 if pos.get("direction", "YES").upper() in ("NO", "DOWN") else 0.01
                    log.info("[LIVE-EXIT] Resolved %s → %.2f for %s", _outcome, exit_price, order_id)
            except Exception as _re:
                log.debug("[LIVE-EXIT] Resolution check failed for %s: %s", order_id, _re)

        # Last resort: use stored current_price (honest — no fabrication)
        if exit_price is None:
            _stored = float(pos.get("current_price", entry_price))
            _dir = pos.get("direction", "YES").upper()
            exit_price = round(_stored, 4)
            log.info("[LIVE-EXIT] Using stored price %.4f for %s (live fetch unavailable)", exit_price, order_id)

        # Evaluate exit triggers
        is_expired = age_minutes >= effective_max_minutes
        is_target_hit = exit_price >= target_price
        is_stop_hit = exit_price <= stop_loss if not _is_short_tf else False

        if not (is_expired or is_target_hit or is_stop_hit):
            # Update local current_price in memory and continue
            pos["current_price"] = exit_price
            continue

        # Determine reason and exit type (Standard vs Netting Merge)
        if is_target_hit:
            exit_reason = "TARGET_HIT"
            # Competitor Blueprint: Delta-neutral opposite-leg purchase simulation
            opposite_cost = round(1.0 - exit_price, 4)
            log.info(
                "[NETTING-EXIT] %s TARGET HIT! Buying opposite outcome at %.2fc (YES: %.2fc + NO: %.2fc = %.2fc) to lock in profit risk-free",
                order_id, opposite_cost * 100, exit_price * 100, opposite_cost * 100, (entry_price + opposite_cost) * 100
            )
        elif is_stop_hit:
            exit_reason = "STOP_HIT"
            opposite_cost = round(1.0 - exit_price, 4)
            log.info(
                "[NETTING-EXIT] %s STOP LOSS HIT! Buying opposite outcome at %.2fc to hedge downside and merge to cash",
                order_id, opposite_cost * 100
            )
        else:
            exit_reason = "MARKET_EXPIRED"

        result = execute_exit(order_id, exit_price, exit_reason=exit_reason)
        if result:
            log.info(
                "[TIME-EXIT] %s closed after %.1fm | exit=%.4f | pnl=$%+.2f | reason=%s",
                order_id, age_minutes, exit_price, result["profit"], exit_reason,
            )
            closed.append({"order_id": order_id, **result})

    if closed:
        persist_positions()

    return closed


def update_trade_record(order_id: str, exit_data: dict) -> None:
    """Merge exit details into the cached position record."""
    if order_id in _open_positions:
        _open_positions[order_id].update(exit_data)
        _open_positions[order_id]["status"] = "CLOSED"


def attach_exit_targets(order_id: str, target_price: float, stop_loss: float) -> None:
    """Store target and stop-loss prices on an open position."""
    if order_id in _open_positions:
        _open_positions[order_id]["target_price"] = target_price
        _open_positions[order_id]["stop_loss"] = stop_loss


# ---------------------------------------------------------------------------
# Exit logic
# ---------------------------------------------------------------------------

def check_exit_condition(
    order_id: str,
    target_price: float,
    stop_loss: float,
    max_hold_hours: int,
) -> dict:
    """
    Evaluate whether a position should be closed.

    Three exit triggers:
        1. current_price >= target_price  → TARGET_HIT
        2. current_price <= stop_loss     → STOP_HIT
        3. Time held >= max_hold_hours    → TIME_EXPIRED

    For paper trading, a simulated price drift is applied based on time held
    to exercise all three code paths during testing.

    Returns:
        Dict with should_exit (bool), exit_reason, current_price, pnl, pnl_percent.
    """
    cfg = _get_config()
    pos = _open_positions.get(order_id)

    if pos is None:
        return {"should_exit": False, "exit_reason": "NOT_FOUND", "current_price": 0, "pnl": 0, "pnl_percent": 0}

    entry_price = pos["entry_price"]
    open_time: datetime = pos.get("open_time", datetime.now(timezone.utc))
    hours_held = (datetime.now(timezone.utc) - open_time).total_seconds() / 3600

    # Fetch live price from CLOB for ALL modes — no simulation
    _market_id = pos.get("market_id") or pos.get("conditionId")
    current_price = pos.get("current_price", entry_price)
    if _market_id:
        _price_fetched = False
        # 1. Try ultra-fast L2 WS Cache
        try:
            from infrastructure.websocket.extraterrestrial_ws_gateway import polymarket_l2_gateway
            mid_val, _ = polymarket_l2_gateway.get_price(_market_id)
            if mid_val is not None and 0.01 <= mid_val <= 0.99:
                current_price = round(mid_val, 4)
                _price_fetched = True
                log.debug("[CHECK-EXIT] L2 WS Cache HIT for %s: %.4f", order_id, current_price)
        except Exception:
            pass

        # 2. Try REST Gamma API if Cache missed
        if not _price_fetched:
            try:
                from infrastructure.exchange.data_fetcher import get_event_current_price
                price_data = get_event_current_price(_market_id)
                if price_data and isinstance(price_data.get("price"), (int, float)):
                    _live = float(price_data["price"])
                    if 0.01 <= _live <= 0.99:
                        current_price = round(_live, 4)
                        _price_fetched = True
                        log.debug("[CHECK-EXIT] Gamma REST fallback hit for %s: %.4f", order_id, current_price)
            except Exception:
                pass

        if _price_fetched:
            _open_positions[order_id]["current_price"] = current_price

    shares = pos["shares_acquired"]
    entry_value = pos["amount_spent"]
    current_value = shares * current_price
    pnl = round(current_value - entry_value, 2)
    pnl_pct = round((pnl / entry_value) * 100, 2) if entry_value else 0

    should_exit = False
    reason = "NONE"

    # For short-term binary option contracts, set a deep emergency stop (-1.0) to let them mature
    _ev_title = (pos.get("event_title") or "").upper()
    _is_short_tf = "5M" in _ev_title or "15M" in _ev_title or "UPDOWN" in _ev_title
    effective_stop_loss = -1.0 if _is_short_tf else stop_loss
    effective_target_price = 0.88 if _is_short_tf else target_price

    if current_price >= effective_target_price:
        should_exit = True
        reason = "TARGET_HIT"
    elif current_price <= effective_stop_loss if not _is_short_tf else False:
        should_exit = True
        reason = "STOP_HIT"
    elif hours_held >= max_hold_hours:
        should_exit = True
        reason = "TIME_EXPIRED"

    if should_exit:
        log.info(
            "Exit condition: %s | order=%s | price=%.4f | pnl=$%.2f (%.2f%%)",
            reason, order_id, current_price, pnl, pnl_pct,
        )

    return {
        "should_exit": should_exit,
        "exit_reason": reason,
        "current_price": current_price,
        "pnl": pnl,
        "pnl_percent": pnl_pct,
    }


def execute_exit(order_id: str, current_price: float, exit_reason: str = "UNKNOWN") -> Optional[dict]:
    """
    Close a position at the given price.

    In paper mode, the exit is recorded locally.
    In live mode, a SELL order is sent to the CLOB API.

    Args:
        exit_reason: One of TARGET_HIT, STOP_HIT, TIME_EXPIRED, RESOLUTION_PROXIMITY, SIGNAL_FLIP.
                     Stored on the position record for ML labelling and audit.

    Returns:
        Exit summary dict, or None on failure.
    """
    cfg = _get_config()
    pos = _open_positions.get(order_id)

    if pos is None:
        log.debug("Cannot exit: order %s not found in open positions (likely pre-restart ghost)", order_id)
        return None

    shares = pos["shares_acquired"]
    entry_value = pos["amount_spent"]
    exit_value = round(shares * current_price, 2)
    profit = round(exit_value - entry_value, 2)
    profit_pct = round((profit / entry_value) * 100, 2) if entry_value else 0

    open_time: datetime = pos.get("open_time", datetime.now(timezone.utc))
    hold_hours = round((datetime.now(timezone.utc) - open_time).total_seconds() / 3600, 2)
    exit_timestamp = datetime.now(timezone.utc).isoformat()

    # Readable close log — shows asset, direction, result, PnL
    title = pos.get("event_title", "")
    import re as _re
    _asset_tag = _re.search(r'\[(BTC|ETH|SOL|XRP)\]', title)
    _tf_tag    = _re.search(r'\[(5m|15m)\]', title)
    _asset = _asset_tag.group(1) if _asset_tag else (
        'BTC' if 'bitcoin' in title.lower() else
        'ETH' if 'ethereum' in title.lower() else
        'SOL' if 'solana' in title.lower() else
        'XRP' if 'xrp' in title.lower() else '?'
    )
    _tf    = _tf_tag.group(1) if _tf_tag else '?'
    _dir   = 'UP' if pos.get('direction') in ('YES', 'UP') else 'DOWN'
    _result = 'WIN' if profit > 0 else 'LOSS' if profit < 0 else 'EVEN'
    _hold_m = round(hold_hours * 60)
    _hold_s = f"{_hold_m}m" if _hold_m < 60 else f"{_hold_m // 60}h {_hold_m % 60}m"

    log.info(
        "[TRADE CLOSED] %s/%s %s | %s | entry=%.0f¢ exit=%.0f¢ | pnl=%+.2f$ (%.1f%%) | %s | held=%s",
        _asset, _tf, _dir, _result,
        pos.get("entry_price", 0) * 100, current_price * 100,
        profit, profit_pct, exit_reason, _hold_s,
    )

    if cfg["BOT_MODE"] == "paper_trading":
        pass  # log above replaces the generic SELL line
    else:
        if pos.get("market", "POLYMARKET") == "POLYMARKET":
            clob_url = cfg["POLYMARKET_CLOB_API_URL"].rstrip("/")
            payload = {
                "market_id": pos["market_id"],
                "side": "SELL",
                "amount": shares,
                "price_limit": current_price,
            }
            resp = _retry_request("POST", f"{clob_url}/orders", json_body=payload)
            if resp is None:
                log.error("Exit order failed for %s — position still open", order_id)
                return None
        else:
            log.info("[PAPER] Exit order for %s (%s) simulated successfully", order_id, pos.get("market"))

    exit_data = {
        "exit_price": current_price,
        "shares_sold": shares,
        "exit_value": exit_value,
        "entry_value": entry_value,
        "profit": profit,
        "profit_percent": profit_pct,
        "hold_duration": hold_hours,
        "exit_timestamp": exit_timestamp,
        "exit_reason": exit_reason,
        "status": "FILLED",
    }

    title_short = (pos.get("event_title") or order_id)[:50]

    update_trade_record(order_id, exit_data)
    persist_positions()

    # Balance is already updated by persist_positions() above — just read it back
    new_balance = get_current_balance()
    try:
        update_balance(new_balance, reason=f"Trade {order_id} closed with ${profit:+.2f}")
    except Exception as exc:
        log.error("Failed to update balance after trade %s: %s", order_id, exc)

    if profit > 0:
        outcome = "✅ WIN"
    elif profit == 0:
        outcome = "⚖️ BREAKEVEN"
    else:
        outcome = "❌ LOSS"
    log.info(
        "[EXIT] %s | %s | %s @ %.4f | pnl=$%+.2f | bal=$%.2f",
        outcome, title_short, exit_reason, current_price, profit, new_balance,
    )

    # Circuit breaker + inversion feedback per asset/timeframe engine
    try:
        from core.engine.updown_engine import notify_trade_outcome
        notify_trade_outcome(pos.get("event_title") or "", profit > 0)
    except Exception as exc:
        log.debug("[EXIT] Engine outcome notify failed: %s", exc)

    # Feed outcome to Edge Orchestrator (for anti-fragile recovery)
    try:
        from core.engine.edge_orchestrator import edge_orchestrator
        edge_orchestrator.record_trade_outcome(profit, new_balance)
    except Exception as exc:
        log.debug("[EXIT] EdgeOrchestrator outcome notify failed: %s", exc)

    # Feed every closed trade into the ML pipeline immediately
    try:
        from core.ml.ml_pipeline import link_trade_outcomes as _ml_link
        _ml_link()
    except Exception:
        pass

    # Record UP/DOWN outcome into Markov tracker for statistical edge learning
    try:
        _direction = (pos.get("direction") or "").upper()
        _title = (pos.get("event_title") or pos.get("event_id") or "").upper()
        _coin = "BTC"
        if "ETH" in _title or "ETHEREUM" in _title:
            _coin = "ETH"
        elif "SOL" in _title or "SOLANA" in _title:
            _coin = "SOL"
        elif "XRP" in _title or "RIPPLE" in _title:
            _coin = "XRP"
        if _direction in ("UP", "YES") or "UP" in _title:
            _markov_outcome = "UP" if profit > 0 else "DOWN"
        elif _direction in ("DOWN", "NO") or "DOWN" in _title:
            _markov_outcome = "DOWN" if profit > 0 else "UP"
        else:
            _markov_outcome = None
        if _markov_outcome and any(kw in _title for kw in ("BTC", "BITCOIN", "ETH", "ETHEREUM", "SOL", "SOLANA", "XRP", "CRYPTO")):
            from markov_tracker import tracker as _markov
            _markov.record(_coin, _markov_outcome)
    except Exception:
        pass

    return exit_data


# ---------------------------------------------------------------------------
# Position persistence & reporting
# ---------------------------------------------------------------------------

def get_closed_positions() -> list[dict]:
    """Return all closed/cancelled positions from the in-memory store."""
    return [p for p in _open_positions.values() if p.get("status") in ("CLOSED", "CANCELLED")]


def get_position_summary() -> dict:
    """Return a compact summary dict suitable for console logging."""
    now = datetime.now(timezone.utc)
    open_pos  = get_all_open_trades()
    closed_pos = get_closed_positions()

    unrealized = 0.0
    for pos in open_pos:
        entry_price   = pos.get("entry_price", 0.0)
        current_price = pos.get("current_price", entry_price)
        shares = pos.get("shares_acquired", 0.0)
        size   = pos.get("amount_spent", 0.0)
        unrealized += (shares * current_price) - size

    realized = sum(float(p.get("profit", 0.0) or 0) for p in closed_pos)
    wins      = sum(1 for p in closed_pos if float(p.get("profit", 0.0) or 0) > 0)

    return {
        "active":          len(open_pos),
        "closed":          len(closed_pos),
        "unrealized_pnl":  round(unrealized, 2),
        "realized_pnl":    round(realized, 2),
        "wins":            wins,
        "losses":          len(closed_pos) - wins,
    }


def persist_positions() -> None:
    """
    Write current open and closed Polymarket positions to positions_state.json.
    Called automatically after every open/close so the dashboard always has
    fresh data without polling the Python process.
    """
    now = datetime.now(timezone.utc)
    active: list[dict] = []
    closed: list[dict] = []

    for order_id, pos in _open_positions.items():
        status      = pos.get("status", "UNKNOWN")
        entry_price = pos.get("entry_price", 0.0)
        size        = pos.get("amount_spent", 0.0)
        shares      = pos.get("shares_acquired", 0.0)
        open_time   = pos.get("open_time", now)
        hold_min    = round((now - open_time).total_seconds() / 60, 1) if isinstance(open_time, datetime) else 0
        title       = pos.get("event_title") or pos.get("event_id", pos.get("market_id", "Unknown"))

        if status in ("CLOSED", "CANCELLED"):
            closed.append({
                "order_id":         order_id,
                "market":           pos.get("market", "POLYMARKET"),
                "market_id":        pos.get("market_id", ""),
                "event_title":      title,
                "direction":        pos.get("direction", "?"),
                "entry_price":      round(entry_price, 4),
                "exit_price":       round(pos.get("exit_price", 0.0), 4),
                "size":             round(size, 2),
                "realized_pnl":     round(float(pos.get("profit", 0.0) or 0), 2),
                "realized_pnl_pct": round(float(pos.get("profit_percent", 0.0) or 0), 2),
                "exit_reason":      pos.get("exit_reason", status),
                "hold_hours":       round(float(pos.get("hold_duration", hold_min / 60) or 0), 2),
                "entry_time":       open_time.isoformat() if isinstance(open_time, datetime) else str(open_time),
                "exit_time":        pos.get("exit_timestamp", ""),
                "expiry_ts":        pos.get("expiry_ts", 0),
            })
        else:
            current_price = pos.get("current_price", entry_price)
            unrealized    = round((shares * current_price) - size, 2)
            active.append({
                "order_id":       order_id,
                "market":         pos.get("market", "POLYMARKET"),
                "market_id":      pos.get("market_id", ""),
                "event_title":    title,
                "direction":      pos.get("direction", "?"),
                "entry_price":    round(entry_price, 4),
                "current_price":  round(current_price, 4),
                "size":           round(size, 2),
                "shares":         round(shares, 4),
                "entry_time":     open_time.isoformat() if isinstance(open_time, datetime) else str(open_time),
                "hold_minutes":   hold_min,
                "unrealized_pnl": unrealized,
                "target_price":   pos.get("target_price"),
                "stop_loss":      pos.get("stop_loss"),
                "status":         status,
                "expiry_ts":      pos.get("expiry_ts", 0),
            })

    # Newest closed trades first
    closed.sort(key=lambda p: p.get("exit_time", ""), reverse=True)

    # ── Merge with existing positions file ─────────────────────────────────────
    # On restart _open_positions starts empty — closed trades would be lost.
    # We preserve them by:
    #   1. Reading Kalshi rows from the file (written by kalshi/trader.py)
    #   2. Reading Polymarket CLOSED rows that aren't in the current in-memory set
    #   3. Loading any JSONL closed trades that are in neither (full history recovery)
    out_path = Path(__file__).parent / "positions_state.json"
    kalshi_active: list[dict] = []
    kalshi_closed: list[dict] = []
    existing_poly_closed: list[dict] = []
    try:
        if out_path.exists():
            existing = json.loads(out_path.read_text(encoding="utf-8"))
            kalshi_active = [p for p in existing.get("active", []) if p.get("market") == "KALSHI"]
            kalshi_closed = [p for p in existing.get("closed", []) if p.get("market") == "KALSHI"]
            in_mem_ids = {p["order_id"] for p in closed}
            existing_poly_closed = [
                p for p in existing.get("closed", [])
                if p.get("market") == "POLYMARKET" and p.get("order_id") not in in_mem_ids
            ]
    except Exception:
        pass

    # Also recover any JSONL-only closed trades not already represented above
    _jsonl_path = Path(__file__).parent / "zisi_local_trades.jsonl"
    jsonl_closed: list[dict] = []
    try:
        if _jsonl_path.exists():
            known_ids = {p["order_id"] for p in closed} | {p.get("order_id") for p in existing_poly_closed}
            for line in _jsonl_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    oid = entry.get("order_id", "")
                    if (entry.get("status", "").upper() == "CLOSED" and oid and oid not in known_ids):
                        known_ids.add(oid)
                        jsonl_closed.append({
                            "order_id":         oid,
                            "market":           "POLYMARKET",
                            "event_title":      entry.get("event_title", ""),
                            "direction":        entry.get("direction", "?"),
                            "entry_price":      round(float(entry.get("entry_price", 0)), 4),
                            "exit_price":       round(float(entry.get("exit_price", 0)), 4),
                            "size":             round(float(entry.get("amount_spent", entry.get("position_size", 0))), 2),
                            "realized_pnl":     round(float(entry.get("profit", 0) or 0), 2),
                            "realized_pnl_pct": round(float(entry.get("profit_percent", 0) or 0), 2),
                            "exit_reason":      entry.get("exit_reason", "CLOSED"),
                            "hold_hours":       round(float(entry.get("hold_duration", 0) or 0), 2),
                            "entry_time":       entry.get("timestamp", ""),
                            "exit_time":        entry.get("exit_timestamp", ""),
                        })
                except Exception:
                    pass
    except Exception:
        pass

    merged_active = active + kalshi_active
    # Merge order: in-memory first (most recent), then file-preserved, then JSONL history
    merged_closed = closed + existing_poly_closed + jsonl_closed + kalshi_closed
    # Sort newest-first by exit_time
    merged_closed.sort(key=lambda p: p.get("exit_time", p.get("exit_timestamp", "")), reverse=True)

    summary = {
        "active_count":  len(merged_active),
        "poly_active":   len(active),
        "kalshi_active": len(kalshi_active),
        "closed_count":  len(merged_closed),
        "unrealized_pnl": round(sum(p.get("unrealized_pnl", 0) for p in active), 2),
        "realized_pnl":   round(
            sum(p.get("realized_pnl", 0) for p in merged_closed), 2
        ),
        "win_count":      sum(1 for p in merged_closed if (p.get("realized_pnl") or 0) > 0),
        "loss_count":     sum(1 for p in merged_closed if (p.get("realized_pnl") or 0) < 0),
    }

    data = {
        "last_updated": now.isoformat(),
        "source":       "polymarket+kalshi",
        "summary":      summary,
        "active":       merged_active,
        "closed":       merged_closed,
    }

    with GLOBAL_POSITIONS_LOCK:
        try:
            # Cap closed list to 300 most recent to prevent unbounded file growth
            data["closed"] = data["closed"][:300]
            # Atomic write: write to .tmp then os.replace so the dashboard never
            # reads a partially-written file (which causes the JSON parse error).
            tmp_path = out_path.with_suffix(".tmp")
            tmp_path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
            import os as _os
            _os.replace(tmp_path, out_path)
        except Exception as exc:
            log.warning("[POSITIONS] Failed to persist: %s", exc)

    # ── Memory pruning ────────────────────────────────────────────────────────
    # Remove CLOSED entries older than 2 h from _open_positions to prevent
    # unbounded memory growth on long overnight runs.
    _cutoff = datetime.now(timezone.utc) - timedelta(hours=2)
    _to_prune: list[str] = []
    for _oid, _p in _open_positions.items():
        if _p.get("status") not in ("CLOSED", "CANCELLED"):
            continue
        _ts = _p.get("exit_timestamp") or _p.get("timestamp", "")
        if not _ts:
            continue
        try:
            _close_dt = datetime.fromisoformat(_ts.replace("Z", "+00:00"))
            if _close_dt < _cutoff:
                _to_prune.append(_oid)
        except Exception:
            pass
    for _oid in _to_prune:
        _open_positions.pop(_oid, None)
    if _to_prune:
        log.debug("[MEMORY] Pruned %d stale CLOSED positions from memory", len(_to_prune))


# ---------------------------------------------------------------------------
# Trailing Stop Escalator — ratchets stop-loss up as profit builds
# ---------------------------------------------------------------------------

def escalate_trailing_stops() -> int:
    """
    For every open position with a target_price and stop_loss set,
    ratchet the stop-loss upward as unrealized P&L accumulates.

    Escalation ladder (measured as % of distance from entry to target):
      ≥ 50% of target reached  →  move stop to breakeven (entry price)
      ≥ 75% of target reached  →  move stop to lock in 40% of target profit
      ≥ 90% of target reached  →  move stop to lock in 70% of target profit

    This converts a potential win→loss reversal into a guaranteed profit once
    the position is well in-the-money.  Returns count of stops updated.
    """
    updated = 0

    for order_id, pos in list(_open_positions.items()):
        if pos.get("status") in ("CLOSED", "CANCELLED"):
            continue

        # Skip trailing stop escalation for short timeframe contracts
        _ev_title = (pos.get("event_title") or "").upper()
        _is_short_tf = "5M" in _ev_title or "15M" in _ev_title or "UPDOWN" in _ev_title
        if _is_short_tf:
            continue

        entry  = float(pos.get("entry_price", 0) or 0)
        target = pos.get("target_price")
        stop   = pos.get("stop_loss")
        current = float(pos.get("current_price", entry) or entry)

        if not target or not stop or entry <= 0:
            continue

        target = float(target)
        stop   = float(stop)

        target_dist = target - entry
        if target_dist <= 0:
            continue  # inverted or zero-range target — skip

        progress = (current - entry) / target_dist   # 0 = at entry, 1 = at target

        new_stop = stop
        if progress >= 0.90:
            # Lock in 70% of the full profit
            new_stop = max(stop, round(entry + 0.70 * target_dist, 4))
        elif progress >= 0.75:
            # Lock in 40% of the full profit
            new_stop = max(stop, round(entry + 0.40 * target_dist, 4))
        elif progress >= 0.50:
            # Move stop to breakeven
            new_stop = max(stop, round(entry, 4))

        if new_stop > stop:
            pos["stop_loss"] = new_stop
            log.info(
                "[TRAIL] %s | progress=%.0f%% | stop %.4f → %.4f (locked)",
                order_id, progress * 100, stop, new_stop,
            )
            updated += 1

    if updated:
        persist_positions()

    return updated


# ---------------------------------------------------------------------------
# Live price refresh for open paper positions
# ---------------------------------------------------------------------------

def refresh_open_position_prices() -> int:
    """
    Fetch fresh Polymarket CLOB prices for every open paper position and update
    current_price in the in-memory store.  Called once per cycle from main.py.

    This is what makes unrealized P&L accurate on the dashboard — without it,
    current_price never moves from its initial entry value.

    Returns the number of positions that had their price successfully updated.
    """
    from infrastructure.exchange.data_fetcher import get_event_current_price as _gcp

    updated = 0
    for order_id, pos in list(_open_positions.items()):
        if pos.get("status") in ("CLOSED", "CANCELLED"):
            continue

        market_id = pos.get("market_id") or pos.get("conditionId")
        if not market_id:
            continue

        try:
            price_data = _gcp(market_id)
            if price_data and isinstance(price_data.get("price"), (int, float)):
                new_price = float(price_data["price"])
                if 0.01 <= new_price <= 0.99:   # reject resolved/invalid prices
                    old_price = pos.get("current_price", pos.get("entry_price", 0.5))
                    pos["current_price"] = round(new_price, 4)
                    updated += 1
                    log.debug(
                        "[PRICE-REFRESH] %s: %.4f → %.4f (Δ%+.4f)",
                        order_id, old_price, new_price, new_price - old_price,
                    )
        except Exception as exc:
            log.debug("[PRICE-REFRESH] Failed for %s: %s", order_id, exc)

    # No simulation — all price refreshes use live CLOB data regardless of mode

    if updated:
        persist_positions()
        log.info("[PRICE-REFRESH] Updated %d open Polymarket position price(s)", updated)
    return updated


def _recover_active_positions_from_disk() -> None:
    """
    On startup/import, load any existing active Polymarket positions
    from positions_state.json back into the in-memory _open_positions store
    so the bot doesn't orphan/abandon them upon restart.
    """
    out_path = Path(__file__).parent / "positions_state.json"
    if not out_path.exists():
        return
    try:
        data = json.loads(out_path.read_text(encoding="utf-8"))
        active = data.get("active", [])
        loaded = 0
        for pos in active:
            if pos.get("market") == "POLYMARKET":
                order_id = pos.get("order_id")
                if order_id and order_id not in _open_positions:
                    try:
                        open_time = datetime.fromisoformat(pos["entry_time"])
                    except Exception:
                        open_time = datetime.now(timezone.utc)
                    
                    _open_positions[order_id] = {
                        "order_id": order_id,
                        "event_id": pos.get("event_id", ""),
                        "market_id": pos.get("market_id", ""),
                        "event_title": pos.get("event_title", ""),
                        "direction": pos.get("direction", "YES"),
                        "amount_spent": pos.get("size", 0.0),
                        "shares_acquired": pos.get("shares", 0.0),
                        "entry_price": pos.get("entry_price", 0.5),
                        "current_price": pos.get("current_price", pos.get("entry_price", 0.5)),
                        "timestamp": pos.get("entry_time", ""),
                        "status": pos.get("status", "FILLED"),
                        "market": "POLYMARKET",
                        "open_time": open_time,
                        "target_price": pos.get("target_price"),
                        "stop_loss": pos.get("stop_loss"),
                        "expiry_ts": pos.get("expiry_ts", 0),
                    }
                    loaded += 1
        if loaded:
            log.info("[RECOVERY] Reloaded %d active Polymarket position(s) from disk.", loaded)
    except Exception as exc:
        log.error("[RECOVERY] Failed to reload active positions: %s", exc)

# Execute recovery instantly upon module import
_recover_active_positions_from_disk()
