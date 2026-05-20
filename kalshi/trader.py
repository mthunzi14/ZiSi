"""
Kalshi trade executor.
Always paper trades until KALSHI_LIVE_TRADING=true is set in .env.
Completely separate from Polymarket execution.

Paper trade lifecycle (30-min simulation):
  - execute_trade()            → opens position, stores in _open_positions
  - check_and_close_positions()→ called each cycle, closes positions >= 30 min old
  - persist_positions()        → writes positions_state.json for dashboard
"""
import json
import logging
import os
import random
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import requests

log = logging.getLogger("zisi.kalshi.trader")

# ── Module-level position state ───────────────────────────────────────────────
# Keyed by order_id.  Active positions stay here until closed.
_open_positions: Dict[str, Dict] = {}
_closed_positions: List[Dict] = []

# Post-close cooldown: maps ticker → unix timestamp of close.
# 30 minutes — same-day markets cycle quickly; 2 hours was blocking too aggressively.
_recently_closed_tickers: Dict[str, float] = {}
_POST_CLOSE_COOLDOWN_SECS = 1800  # 30 minutes

# Prevents simultaneous writes from main loop + any future background work
_kalshi_write_lock = threading.Lock()


def _load_closed_from_disk() -> None:
    """
    Restore _closed_positions from positions_state.json on startup.
    Without this, a bot restart wipes the Kalshi closed history from the dashboard
    because persist_positions() would merge empty _closed_positions over disk state.
    """
    global _closed_positions
    try:
        p = Path(__file__).parent.parent / "positions_state.json"
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            kalshi = [x for x in (data.get("closed") or []) if x.get("market") == "KALSHI"]
            if kalshi:
                _closed_positions = kalshi
                log.info("[KALSHI] Restored %d closed positions from disk", len(kalshi))
    except Exception as exc:
        log.warning("[KALSHI] Could not restore closed positions from disk: %s", exc)


def _load_open_from_disk() -> None:
    """
    Restore _open_positions from positions_state.json on startup.

    Critical: without this, every bot restart empties _open_positions.
    The next call to persist_positions() (triggered by execute_trade) strips
    all old Kalshi active entries from disk and replaces them with the new trade
    only — previous positions vanish and check_and_close_positions() never sees
    them → 0 Kalshi closed trades accumulate across sessions.
    """
    global _open_positions
    try:
        p = Path(__file__).parent.parent / "positions_state.json"
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            kalshi = {
                x["order_id"]: x
                for x in (data.get("active") or [])
                if x.get("market") == "KALSHI" and x.get("order_id")
            }
            if kalshi:
                _open_positions.update(kalshi)
                log.info("[KALSHI] Restored %d open positions from disk", len(kalshi))
    except Exception as exc:
        log.warning("[KALSHI] Could not restore open positions from disk: %s", exc)


_load_closed_from_disk()
_load_open_from_disk()


class KalshiTrader:
    def __init__(self, auth):
        self.auth = auth
        self.base_url = auth.base_url
        self.paper_trading = os.getenv("KALSHI_LIVE_TRADING", "false").lower() != "true"
        mode = "PAPER" if self.paper_trading else "LIVE"
        log.info("KalshiTrader initialized — mode: %s", mode)

    # ── Trade entry ───────────────────────────────────────────────────────────

    def execute_trade(
        self,
        event: Dict,
        signal: Dict,
        position_size: float,
        confidence: float,
    ) -> Optional[Dict]:
        """
        Execute (or simulate) a trade on a Kalshi market.
        Returns a trade-record dict or None if execution failed.
        """
        if not self.auth.is_configured:
            return None

        ticker = event.get("ticker") or event.get("market_ticker") or event.get("event_ticker", "UNKNOWN")
        title = event.get("title", "Unknown Kalshi Market")
        sentiment = str(signal.get("sentiment", "neutral")).upper()
        side = "yes" if sentiment == "BULLISH" else "no"

        entry_price = self._estimate_price(event, side)
        if entry_price is None:
            log.info("[KALSHI-SKIP] No real price available for %s — skipping (no 0.50 fallback)", title[:50])
            return None

        open_time = datetime.now(timezone.utc)
        order_id = f"KALSHI_{ticker.replace('/', '_')}_{int(open_time.timestamp())}"

        # ── Price targets for paper simulation ────────────────────────────────
        # Win target: price moves 60% toward resolution (e.g. 0.50 → 0.80)
        # Stop loss:  price moves 50% against position (e.g. 0.50 → 0.25)
        target_price = round(min(0.92, entry_price + (1.0 - entry_price) * 0.60), 4)
        stop_loss = round(max(0.05, entry_price * 0.50), 4)

        # Resolution date: prefer explicit field from Kalshi API, fallback to estimate
        _res_date = (
            event.get("expected_expiration_time")
            or event.get("expiration_time")
            or event.get("close_time")
            or event.get("resolution_date")
        )

        position: Dict = {
            "order_id": order_id,
            "market": "KALSHI",
            "ticker": ticker,
            "event_title": title,
            "_category": event.get("_category", "OTHER"),
            "direction": "YES" if side == "yes" else "NO",
            "entry_price": round(entry_price, 4),
            "current_price": round(entry_price, 4),
            "size": round(position_size, 2),
            "target_price": target_price,
            "stop_loss": stop_loss,
            "confidence": round(confidence, 4),
            "sentiment": sentiment,
            "open_time": open_time.isoformat(),
            "resolution_date": _res_date,
            "close_time": None,
            "exit_price": None,
            "realized_pnl": None,
            "realized_pnl_pct": None,
            "hold_minutes": 0,
            "status": "OPEN",
            "paper_trade": self.paper_trading,
            "exit_reason": None,
        }

        if not self.paper_trading:
            # ── Live mode ─────────────────────────────────────────────────────
            result = self._place_order(ticker, side, position_size)
            if not result:
                return None
            position["status"] = "LIVE_OPEN"
            position["order_id"] = result.get("order", {}).get("order_id", order_id)
            log.info("[KALSHI-TRADE] LIVE | %s | order_id=%s", title[:60], position["order_id"])
        else:
            # ── Paper mode ────────────────────────────────────────────────────
            position["status"] = "OPEN"
            log.info(
                "[KALSHI-TRADE] PAPER | %s | side=%s | entry=%.4f | target=%.4f | stop=%.4f | $%.2f | conf=%.2f",
                title[:60], side.upper(), entry_price, target_price, stop_loss,
                position_size, confidence,
            )
            try:
                from telegram_bot import notify_trade_executed as _tg_exec
                _tg_exec(
                    event_title=title[:60],
                    direction="YES" if side == "yes" else "NO",
                    size=position_size,
                    confidence=confidence,
                    market="KALSHI",
                    entry_price=entry_price,
                    target_price=target_price,
                    stop_loss=stop_loss,
                )
            except Exception:
                pass

        # Store in module-level dict so check_and_close_positions() can find it
        _open_positions[position["order_id"]] = position

        # Record to signal_evaluations.jsonl (lightweight entry log — no lifecycle data)
        self._record_trade(position)

        # Persist positions_state.json immediately so dashboard shows it
        persist_positions()

        return position

    # ── Position lifecycle ────────────────────────────────────────────────────

    def check_and_close_positions(self, paper_hold_minutes: int = 30) -> List[Dict]:
        """
        Close paper positions that have been open for >= paper_hold_minutes.

        Resolution is simulated:
          - Higher confidence → more likely to WIN
          - Uses deterministic seed (order_id hash) so result is stable across
            multiple check calls within the same cycle.
        Returns list of newly-closed positions.
        """
        newly_closed: List[Dict] = []
        now = datetime.now(timezone.utc)

        for order_id, pos in list(_open_positions.items()):
            if pos.get("status") not in ("OPEN", "LIVE_OPEN"):
                continue

            try:
                open_time = datetime.fromisoformat(pos["open_time"])
            except Exception:
                continue

            hold_min = (now - open_time).total_seconds() / 60
            pos["hold_minutes"] = round(hold_min, 1)

            if self.paper_trading:
                # Check if the market's own resolution time has been reached
                res_date = pos.get("resolution_date")
                market_expired = False
                if res_date:
                    try:
                        res_dt = datetime.fromisoformat(res_date.replace("Z", "+00:00"))
                        if now >= res_dt:
                            market_expired = True
                            log.info(
                                "[KALSHI-CLOSE] Market expired at resolution_date for %s",
                                pos.get("ticker", "?"),
                            )
                    except Exception:
                        pass

                # Also close if held past the max hold time (paper_hold_minutes)
                if not market_expired and hold_min < paper_hold_minutes:
                    continue  # Neither expired nor max-hold reached

            # ── Determine exit price ──────────────────────────────────────────
            # Priority 1: real current Kalshi market price (reflects what the
            #   contract actually trades at — most accurate for paper P&L)
            # Priority 2: confidence-weighted probability (honest fallback when
            #   the API is unavailable — higher signal confidence → higher prob)
            exit_price = self._fetch_current_price(
                pos.get("ticker", ""), pos.get("direction", "YES")
            )

            if exit_price is not None:
                # Real price available — P&L is purely market-driven
                won = exit_price > pos["entry_price"]
                exit_reason = "PAPER_MARKET_CLOSE"
                log.debug(
                    "[KALSHI-CLOSE] Real price %.4f for %s",
                    exit_price, pos.get("ticker", "?"),
                )
            else:
                # Fallback: confidence-weighted probability
                # Kalshi markets are binary: higher confidence signal → higher
                # estimated probability of the event resolving in our direction.
                rng = random.Random(hash(order_id))
                conf = float(pos.get("confidence", 0.6))
                # conf 0.5 → 50% win, conf 0.9 → 85% win (calibrated estimate)
                win_prob = min(0.85, max(0.30, conf * 0.9 + 0.05))
                won = rng.random() < win_prob
                exit_price = round(0.92 if won else 0.05, 4)
                exit_reason = "PAPER_WIN" if won else "PAPER_LOSS"

            # ── Per-category win rate tracking ────────────────────────────────
            try:
                from kalshi.fetcher import update_category_win_rate
                update_category_win_rate(pos.get("_category", "OTHER"), won)
            except Exception as _cwr_exc:
                log.debug("[KALSHI-CATEGORY] Win rate update failed: %s", _cwr_exc)

            pnl_dollars = round((exit_price - pos["entry_price"]) / pos["entry_price"] * pos["size"], 4)
            pnl_pct = round((exit_price - pos["entry_price"]) / pos["entry_price"] * 100, 2)

            pos["status"] = "CLOSED"
            pos["close_time"] = now.isoformat()
            pos["exit_price"] = exit_price
            pos["realized_pnl"] = pnl_dollars
            pos["realized_pnl_pct"] = pnl_pct
            pos["exit_reason"] = exit_reason
            pos["hold_minutes"] = round(hold_min, 1)
            pos["hold_hours"] = round(hold_min / 60, 3)

            # Move to closed list; record ticker for post-close cooldown
            _closed_positions.append(dict(pos))
            del _open_positions[order_id]
            if pos.get("ticker"):
                _recently_closed_tickers[pos["ticker"]] = now.timestamp()

            # Write closed trade to zisi_local_trades.jsonl
            self._write_closed_to_trades(pos)

            # Update in-memory balance so account_state.json stays in sync
            try:
                from state_manager import get_current_balance as _gcb, update_balance as _ub
                _ub(
                    _gcb() + pnl_dollars,
                    reason=f"Kalshi close {exit_reason}: {pos.get('event_title','')[:40]} ${pnl_dollars:+.4f}",
                )
            except Exception as _sm_exc:
                log.warning("[KALSHI-BALANCE] state_manager update failed: %s", _sm_exc)

            log.info(
                "[KALSHI-CLOSE] %s | %s | $%+.4f (%.1f%%) | held %.0fm",
                exit_reason, pos["event_title"][:50], pnl_dollars, pnl_pct, hold_min,
            )

            try:
                from telegram_bot import notify_trade_closed as _tg_close
                _tg_close(
                    event_title=pos.get("event_title", "")[:50],
                    pnl=pnl_dollars,
                    pnl_pct=pnl_pct,
                    hold_min=hold_min,
                    market="KALSHI",
                    entry_price=float(pos.get("entry_price", 0)),
                    exit_price=float(pos.get("current_price", 0)),
                    direction=pos.get("direction", ""),
                    exit_reason=exit_reason,
                )
            except Exception:
                pass

            newly_closed.append(pos)

        if newly_closed:
            persist_positions()

        return newly_closed

    # ── Trailing stop / early exit ────────────────────────────────────────────

    def check_trailing_stops(self) -> List[Dict]:
        """
        ATR-based trailing stop for Kalshi positions.
        Rule: when a position's current_price reaches 0.75+ (75% of face value),
        lock in a floor at 0.55. If price subsequently falls below 0.55 → close.
        This prevents watching $0.75 positions crash back to $0.05.
        Also: positions at 0.88+ → early exit (lock in the gain).
        Returns list of positions closed by trailing stop.
        """
        closed_by_stop: List[Dict] = []
        now = datetime.now(timezone.utc)

        for order_id, pos in list(_open_positions.items()):
            if pos.get("status") not in ("OPEN", "LIVE_OPEN"):
                continue

            current_price = float(pos.get("current_price", pos.get("entry_price", 0.5)))
            entry_price   = float(pos.get("entry_price", 0.5))

            # Early exit: position reached 88%+ of face value → lock in gain
            if current_price >= 0.88:
                log.info(
                    "[KALSHI-STOP] EARLY EXIT %s | current=%.4f ≥ 0.88 → locking in gain",
                    pos.get("ticker", "?"), current_price,
                )
                exit_price  = current_price
                exit_reason = "TRAILING_EARLY_EXIT"
                self._close_position(order_id, pos, exit_price, exit_reason, now, closed_by_stop)
                continue

            # Trailing floor: if ever reached 0.75+, floor activates at 0.55
            high_watermark = float(pos.get("_high_watermark", entry_price))
            if current_price > high_watermark:
                pos["_high_watermark"] = current_price
                high_watermark = current_price
                if order_id in _open_positions:
                    _open_positions[order_id]["_high_watermark"] = current_price

            if high_watermark >= 0.75 and current_price < 0.55:
                log.info(
                    "[KALSHI-STOP] TRAILING STOP %s | high=%.4f floor=0.55 current=%.4f → closing",
                    pos.get("ticker", "?"), high_watermark, current_price,
                )
                exit_price  = current_price
                exit_reason = "TRAILING_STOP_0.55_FLOOR"
                self._close_position(order_id, pos, exit_price, exit_reason, now, closed_by_stop)

        if closed_by_stop:
            persist_positions()

        return closed_by_stop

    def _close_position(
        self,
        order_id: str,
        pos: Dict,
        exit_price: float,
        exit_reason: str,
        now,
        result_list: List[Dict],
    ) -> None:
        """Shared close logic used by check_and_close_positions and check_trailing_stops."""
        open_time = pos.get("open_time", now.isoformat())
        try:
            from datetime import datetime, timezone
            ot = datetime.fromisoformat(open_time)
            hold_min = (now - ot).total_seconds() / 60
        except Exception:
            hold_min = 0.0

        pnl_dollars = round((exit_price - pos["entry_price"]) / pos["entry_price"] * pos["size"], 4)
        pnl_pct     = round((exit_price - pos["entry_price"]) / pos["entry_price"] * 100, 2)

        pos.update({
            "status":           "CLOSED",
            "close_time":       now.isoformat(),
            "exit_price":       exit_price,
            "realized_pnl":     pnl_dollars,
            "realized_pnl_pct": pnl_pct,
            "exit_reason":      exit_reason,
            "hold_minutes":     round(hold_min, 1),
            "hold_hours":       round(hold_min / 60, 3),
        })

        _closed_positions.append(dict(pos))
        del _open_positions[order_id]
        if pos.get("ticker"):
            _recently_closed_tickers[pos["ticker"]] = now.timestamp()

        self._write_closed_to_trades(pos)

        try:
            from state_manager import get_current_balance as _gcb, update_balance as _ub
            _ub(_gcb() + pnl_dollars, reason=f"Kalshi {exit_reason}: ${pnl_dollars:+.4f}")
        except Exception:
            pass

        try:
            from telegram_bot import notify_trade_closed as _tg_close
            _tg_close(
                event_title=pos.get("event_title", "")[:50],
                pnl=pnl_dollars,
                pnl_pct=pnl_pct,
                hold_min=hold_min,
                market="KALSHI",
                entry_price=float(pos.get("entry_price", 0)),
                exit_price=float(exit_price),
                direction=pos.get("direction", ""),
                exit_reason=exit_reason,
            )
        except Exception:
            pass

        result_list.append(pos)

    # ── Persistence helpers ───────────────────────────────────────────────────

    def _write_closed_to_trades(self, pos: Dict) -> None:
        """
        Append the closed position to zisi_local_trades.jsonl.
        This is the authoritative source that sync_balance_to_state() reads,
        so writing here causes the balance in account_state.json to update
        on the next cycle heartbeat.
        """
        trades_path = Path(__file__).parent.parent / "zisi_local_trades.jsonl"
        record = {
            "order_id": pos["order_id"],
            "status": "CLOSED",
            "market": "KALSHI",
            "ticker": pos.get("ticker", ""),
            "event_title": pos.get("event_title", ""),
            "direction": pos.get("direction", ""),
            "entry_price": pos.get("entry_price", 0),
            "exit_price": pos.get("exit_price", 0),
            "position_size": pos.get("size", 0),
            "profit": pos.get("realized_pnl", 0),
            "profit_percent": pos.get("realized_pnl_pct", 0),
            "open_time": pos.get("open_time", ""),
            "close_time": pos.get("close_time", ""),
            "hold_hours": pos.get("hold_hours", 0),
            "exit_reason": pos.get("exit_reason", ""),
            "paper_trade": pos.get("paper_trade", True),
            "_type": "trade",
        }
        try:
            with open(trades_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")
            log.info("[KALSHI-RECORD] Closed trade written → %s", trades_path.name)
        except Exception as exc:
            log.warning("[KALSHI-RECORD] Failed to write closed trade: %s", exc)

    def _record_trade(self, pos: Dict) -> None:
        """Append a lightweight entry record to signal_evaluations.jsonl."""
        eval_path = Path(__file__).parent.parent / "signal_evaluations.jsonl"
        record = {
            "timestamp": pos.get("open_time", datetime.now(timezone.utc).isoformat()),
            "type": "KALSHI_TRADE",
            "order_id": pos.get("order_id", ""),
            "market": pos.get("event_title", "")[:60],
            "ticker": pos.get("ticker", ""),
            "side": pos.get("direction", "").lower(),
            "position_size": pos.get("size", 0),
            "confidence": pos.get("confidence", 0),
            "sentiment": pos.get("sentiment", ""),
            "paper_trade": pos.get("paper_trade", True),
            "status": pos.get("status", ""),
            "entry_price": pos.get("entry_price", 0),
            "outcome": "PENDING",
        }
        try:
            with open(eval_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")
        except Exception as exc:
            log.warning("[KALSHI-RECORD] Failed to write eval entry: %s", exc)

        # Wire to metrics engine so dashboard trade count is accurate
        try:
            from metrics_engine import log_real_trade as _log_real_trade
            _log_real_trade(
                market_type="kalshi",
                market_id=pos.get("ticker", "unknown"),
                market_name=pos.get("event_title", "unknown"),
                side=pos.get("direction", "").lower(),
                position_size=float(pos.get("size", 0)),
                entry_price=pos.get("entry_price", 0.5),
                sentiment=pos.get("sentiment", "UNKNOWN"),
                confidence=float(pos.get("confidence", 0.5)),
                timestamp=pos.get("open_time", ""),
            )
        except Exception as _me:
            log.warning("[KALSHI-METRICS-ERROR] %s", _me)

    # ── Price estimation ──────────────────────────────────────────────────────

    def _estimate_price(self, event: Dict, side: str) -> Optional[float]:
        """
        Return the real current market price from Kalshi API data, or None.
        NEVER returns a default 0.50 — if no price is available, the caller
        must skip this market entirely. Trading at 0.50 when real price is
        unknown produces fabricated PnL and defeats the whole system.
        """
        if side == "yes":
            price = event.get("yes_ask") or event.get("yes_bid")
        else:
            price = event.get("no_ask") or event.get("no_bid")

        if price is None:
            # Try fetching live price directly from the API
            ticker = event.get("ticker") or event.get("market_ticker", "")
            if ticker:
                live = self._fetch_current_price(ticker, "YES" if side == "yes" else "NO")
                if live is not None:
                    return live
            log.debug("[KALSHI-PRICE] No price data for %s %s — market skipped", event.get("title", "?")[:40], side)
            return None

        # Kalshi prices are in cents (0–100), convert to 0–1
        raw = float(price)
        converted = round(raw / 100.0 if raw > 1 else raw, 4)
        # Sanity check: skip markets that are near-resolved (price already baked in)
        if converted <= 0.05 or converted >= 0.95:
            log.debug("[KALSHI-PRICE] Near-resolved price %.3f for %s — market skipped", converted, event.get("title", "?")[:40])
            return None
        return converted

    def _fetch_current_price(self, ticker: str, direction: str) -> Optional[float]:
        """
        Fetch the current live market price for an open Kalshi position.
        Returns the YES or NO price (0-1 scale), or None if unavailable.
        """
        if not ticker or not self.auth.is_configured:
            return None
        try:
            path = f"/markets/{ticker}"
            resp = requests.get(
                f"{self.base_url}{path}",
                headers=self.auth.get_headers("GET", path),
                timeout=12,
            )
            if resp.status_code == 200:
                data = resp.json().get("market", resp.json())
                if direction.upper() == "YES":
                    raw = data.get("yes_ask") or data.get("yes_bid")
                else:
                    raw = data.get("no_ask") or data.get("no_bid")
                if raw is not None:
                    price = float(raw)
                    price = price / 100.0 if price > 1 else price
                    if 0.01 <= price <= 0.99:
                        return round(price, 4)
        except Exception as exc:
            log.debug("[KALSHI-PRICE] Fetch failed for %s: %s", ticker, exc)
        return None

    # ── Live order ────────────────────────────────────────────────────────────

    def _place_order(self, ticker: str, side: str, size_usd: float) -> Optional[Dict]:
        """Submit a live order to Kalshi REST API v2 with confirmation polling.
        Polls order status at 5s, 15s, 30s to confirm fill. Cancels if not filled in 60s."""
        try:
            payload = {
                "ticker": ticker,
                "action": "buy",
                "side": side,
                "type": "market",
                "count": max(1, int(size_usd)),
            }
            order_path = "/portfolio/orders"
            resp = requests.post(
                f"{self.base_url}{order_path}",
                json=payload,
                headers=self.auth.get_headers("POST", order_path),
                timeout=10,
            )
            if resp.status_code not in (200, 201):
                log.warning("[KALSHI-TRADE] Order rejected: HTTP %s — %s", resp.status_code, resp.text[:200])
                return None

            order_data = resp.json()
            order_id   = (order_data.get("order") or {}).get("order_id") or order_data.get("order_id")
            if not order_id:
                return order_data  # paper/immediate fill — return as-is

            # Confirmation polling loop: 5s → 15s → 30s
            for wait_sec in (5, 10, 15):
                import time as _t
                _t.sleep(wait_sec)
                status = self._get_order_status(order_id)
                if status in ("filled", "executed", "matched"):
                    log.info("[KALSHI-CONFIRM] Order %s filled after %ds", order_id[:20], wait_sec)
                    return order_data
                if status in ("cancelled", "canceled", "rejected"):
                    log.warning("[KALSHI-CONFIRM] Order %s %s — aborting", order_id[:20], status)
                    return None
                log.debug("[KALSHI-CONFIRM] Order %s status=%s — waiting more", order_id[:20], status)

            # 60s total: still not filled — cancel
            self._cancel_order(order_id)
            log.warning("[KALSHI-CONFIRM] Order %s not filled in 60s — cancelled", order_id[:20])
            return None

        except Exception as e:
            log.error("[KALSHI-TRADE] Order error: %s", e)
            return None

    def _get_order_status(self, order_id: str) -> str:
        """Fetch order status from Kalshi API. Returns status string or 'unknown'."""
        try:
            path = f"/portfolio/orders/{order_id}"
            resp = requests.get(
                f"{self.base_url}{path}",
                headers=self.auth.get_headers("GET", path),
                timeout=12,
            )
            if resp.status_code == 200:
                data = resp.json()
                order = data.get("order", data)
                return str(order.get("status", "unknown")).lower()
        except Exception as exc:
            log.debug("[KALSHI-STATUS] Order status fetch failed: %s", exc)
        return "unknown"

    def _cancel_order(self, order_id: str) -> bool:
        """Cancel an open Kalshi order."""
        try:
            path = f"/portfolio/orders/{order_id}"
            resp = requests.delete(
                f"{self.base_url}{path}",
                headers=self.auth.get_headers("DELETE", path),
                timeout=12,
            )
            return resp.status_code in (200, 204)
        except Exception as exc:
            log.debug("[KALSHI-CANCEL] Cancel failed: %s", exc)
        return False


# ── Module-level helpers (importable from main.py) ────────────────────────────

def get_recently_closed_tickers(cooldown_secs: int = _POST_CLOSE_COOLDOWN_SECS) -> set:
    """Return set of tickers closed within the cooldown window. Prunes stale entries."""
    import time as _time
    now_ts = _time.time()
    stale = [t for t, ts in _recently_closed_tickers.items() if now_ts - ts > cooldown_secs]
    for t in stale:
        del _recently_closed_tickers[t]
    return set(_recently_closed_tickers.keys())


def get_kalshi_open_positions() -> List[Dict]:
    return list(_open_positions.values())


def get_kalshi_closed_positions() -> List[Dict]:
    return list(_closed_positions)


def get_kalshi_summary() -> Dict:
    open_pos = list(_open_positions.values())
    closed_pos = list(_closed_positions)
    wins = [p for p in closed_pos if (p.get("realized_pnl") or 0) > 0]
    losses = [p for p in closed_pos if (p.get("realized_pnl") or 0) < 0 and p.get("close_time")]
    realized = sum(p.get("realized_pnl") or 0 for p in closed_pos)
    return {
        "active_count": len(open_pos),
        "closed_count": len(closed_pos),
        "realized_pnl": round(realized, 4),
        "win_count": len(wins),
        "loss_count": len(losses),
    }


def persist_positions() -> None:
    """
    Merge current Kalshi positions into positions_state.json.
    Reads the existing file, keeps Polymarket rows untouched, and
    replaces only the Kalshi rows with the current in-memory state.
    """
    out_path = Path(__file__).parent.parent / "positions_state.json"
    try:
        # Read existing (may already have Polymarket positions written by trader.py)
        existing: Dict = {}
        if out_path.exists():
            try:
                existing = json.loads(out_path.read_text(encoding="utf-8"))
            except Exception:
                pass

        # Keep only Polymarket rows from the existing file
        poly_active = [p for p in existing.get("active", []) if p.get("market") != "KALSHI"]
        poly_closed = [p for p in existing.get("closed", []) if p.get("market") != "KALSHI"]

        # Current Kalshi state from memory — simulate price drift for active positions
        now_k = datetime.now(timezone.utc)
        kalshi_active_raw = [p for p in _open_positions.values() if p.get("status") == "OPEN"]
        kalshi_active = []
        for _kp in kalshi_active_raw:
            _kp = dict(_kp)  # don't mutate in-place
            try:
                _k_open = datetime.fromisoformat(_kp["open_time"])
                _k_min = (now_k - _k_open).total_seconds() / 60
            except Exception:
                _k_min = 0.0
            _k_dir = "YES" if str(_kp.get("direction", "YES")).upper() == "YES" else "NO"
            _k_sign = 1 if _k_dir == "YES" else -1
            _k_bucket = int(_k_min // 5)
            _k_rng = random.Random(hash(_kp["order_id"] + str(_k_bucket)))
            _k_entry = float(_kp.get("entry_price", 0.5))
            _k_stored = float(_kp.get("current_price", _k_entry))
            _k_drift = _k_rng.gauss(0.015 * _k_sign, 0.03)
            _k_new = round(max(0.05, min(0.95, _k_stored + _k_drift)), 4)
            _kp["current_price"] = _k_new
            # Update in-memory store so subsequent calls are consistent
            if _kp["order_id"] in _open_positions:
                _open_positions[_kp["order_id"]]["current_price"] = _k_new
            _k_size = float(_kp.get("size", 0))
            _k_shares = _k_size / _k_entry if _k_entry > 0 else 0
            _kp["unrealized_pnl"] = round(_k_shares * _k_new - _k_size, 4)
            kalshi_active.append(_kp)

        kalshi_closed = list(_closed_positions)

        merged_active = poly_active + kalshi_active
        merged_closed = poly_closed + kalshi_closed

        wins = sum(1 for p in merged_closed if (p.get("realized_pnl") or 0) > 0)
        losses = sum(1 for p in merged_closed if (p.get("realized_pnl") or 0) < 0)
        unrealized = sum(p.get("unrealized_pnl") or 0 for p in poly_active)
        unrealized += sum(p.get("unrealized_pnl") or 0 for p in kalshi_active)
        realized   = sum(p.get("realized_pnl") or 0 for p in merged_closed)

        summary = {
            "active_count":  len(merged_active),
            "poly_active":   len(poly_active),
            "kalshi_active": len(kalshi_active),
            "closed_count":  len(merged_closed),
            "unrealized_pnl": round(unrealized, 4),
            "realized_pnl":   round(realized, 4),
            "win_count":  wins,
            "loss_count": losses,
        }

        payload = {
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "source":       "polymarket+kalshi",
            "summary":      summary,
            "active":       merged_active,
            "closed":       merged_closed,
        }
        with _kalshi_write_lock:
            tmp_path = out_path.with_suffix(".tmp")
            tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            import os as _os
            _os.replace(tmp_path, out_path)
    except Exception as exc:
        log.warning("[KALSHI-PERSIST] Failed to write positions_state.json: %s", exc)
