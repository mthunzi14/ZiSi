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

# Prevents simultaneous writes from main loop + any future background work
_kalshi_write_lock = threading.Lock()


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
        open_time = datetime.now(timezone.utc)
        order_id = f"KALSHI_{ticker.replace('/', '_')}_{int(open_time.timestamp())}"

        # ── Price targets for paper simulation ────────────────────────────────
        # Win target: price moves 60% toward resolution (e.g. 0.50 → 0.80)
        # Stop loss:  price moves 50% against position (e.g. 0.50 → 0.25)
        target_price = round(min(0.92, entry_price + (1.0 - entry_price) * 0.60), 4)
        stop_loss = round(max(0.05, entry_price * 0.50), 4)

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

        # Store in module-level dict so check_and_close_positions() can find it
        _open_positions[position["order_id"]] = position

        # Record to signal_evaluations.jsonl (lightweight entry log — no lifecycle data)
        self._record_trade(position)

        # Persist positions_state.json immediately so dashboard shows it
        persist_positions()

        return position

    # ── Position lifecycle ────────────────────────────────────────────────────

    def check_and_close_positions(self, paper_hold_minutes: int = 240) -> List[Dict]:
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

            if self.paper_trading and hold_min < paper_hold_minutes:
                continue  # Not time yet

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

            # Move to closed list
            _closed_positions.append(dict(pos))
            del _open_positions[order_id]

            # Write closed trade to zisi_local_trades.jsonl so balance is updated
            self._write_closed_to_trades(pos)

            log.info(
                "[KALSHI-CLOSE] %s | %s | $%+.4f (%.1f%%) | held %.0fm",
                exit_reason, pos["event_title"][:50], pnl_dollars, pnl_pct, hold_min,
            )
            newly_closed.append(pos)

        if newly_closed:
            persist_positions()

        return newly_closed

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

    def _estimate_price(self, event: Dict, side: str) -> float:
        """Return the current market price, or 0.50 as fallback."""
        if side == "yes":
            price = event.get("yes_ask") or event.get("yes_bid")
        else:
            price = event.get("no_ask") or event.get("no_bid")
        if price is not None:
            # Kalshi prices are in cents (0–100), convert to 0–1
            raw = float(price)
            return round(raw / 100.0 if raw > 1 else raw, 4)
        return 0.50

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
                timeout=6,
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
        """Submit a live order to Kalshi REST API v2."""
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
            if resp.status_code in (200, 201):
                return resp.json()
            log.warning("[KALSHI-TRADE] Order rejected: HTTP %s — %s", resp.status_code, resp.text[:200])
            return None
        except Exception as e:
            log.error("[KALSHI-TRADE] Order error: %s", e)
            return None


# ── Module-level helpers (importable from main.py) ────────────────────────────

def get_kalshi_open_positions() -> List[Dict]:
    return list(_open_positions.values())


def get_kalshi_closed_positions() -> List[Dict]:
    return list(_closed_positions)


def get_kalshi_summary() -> Dict:
    open_pos = list(_open_positions.values())
    closed_pos = list(_closed_positions)
    wins = [p for p in closed_pos if (p.get("realized_pnl") or 0) > 0]
    losses = [p for p in closed_pos if (p.get("realized_pnl") or 0) <= 0 and p.get("close_time")]
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

        # Current Kalshi state from memory
        kalshi_active = [p for p in _open_positions.values() if p.get("status") == "OPEN"]
        kalshi_closed = list(_closed_positions)

        merged_active = poly_active + kalshi_active
        merged_closed = poly_closed + kalshi_closed

        wins = sum(1 for p in merged_closed if (p.get("realized_pnl") or 0) > 0)
        unrealized = sum(p.get("unrealized_pnl") or 0 for p in poly_active)
        realized   = sum(p.get("realized_pnl") or 0 for p in merged_closed)

        summary = {
            "active_count":  len(merged_active),
            "poly_active":   len(poly_active),
            "kalshi_active": len(kalshi_active),
            "closed_count":  len(merged_closed),
            "unrealized_pnl": round(unrealized, 4),
            "realized_pnl":   round(realized, 4),
            "win_count":  wins,
            "loss_count": len(merged_closed) - wins,
        }

        payload = {
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "source":       "polymarket+kalshi",
            "summary":      summary,
            "active":       merged_active,
            "closed":       merged_closed,
        }
        with _kalshi_write_lock:
            out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception as exc:
        log.warning("[KALSHI-PERSIST] Failed to write positions_state.json: %s", exc)
