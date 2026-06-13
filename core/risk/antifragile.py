"""
antifragile.py – Anti-Fragile Recovery System for ZiSi Bot.

Automatically adjusts trading aggression based on recent performance
so the bot trades smaller during drawdowns and compounds gains during
winning streaks.

Aggression tiers:
  Winning streak  (last 5 trades positive)       → 1.20×
  Normal                                         → 1.00×
  Losing streak   (last 3 trades negative)       → 0.60×
  Heavy drawdown  (>10 % of peak portfolio)      → 0.30×

Recovery ramp:
  After a drawdown, aggression rebuilds gradually at +0.10 per
  consecutive winning trade until it reaches normal (1.0).

State is persisted to ``antifragile_state.json`` in the project root
so the bot survives restarts without losing drawdown context.
"""

import json
import logging
import os
import time
from collections import deque
from pathlib import Path
from typing import Optional

log = logging.getLogger("zisi.antifragile")

# ── File paths ────────────────────────────────────────────────────────────────

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_STATE_PATH = _PROJECT_ROOT / "antifragile_state.json"
_POSITIONS_PATH = _PROJECT_ROOT / "data" / "positions_state.json"

# ── Tier constants ────────────────────────────────────────────────────────────

_WIN_STREAK_LEN = 5       # consecutive wins → enter win streak
_LOSE_STREAK_LEN = 3      # consecutive losses → enter losing streak
_DRAWDOWN_PCT = 0.10      # portfolio peak drawdown threshold
_RECOVERY_STEP = 0.10     # aggression gain per win during recovery
_MAX_HISTORY = 50         # keep last N trade results in memory

_MULT_WIN_STREAK = 1.20
_MULT_NORMAL = 1.00
_MULT_LOSE_STREAK = 0.60
_MULT_HEAVY_DRAWDOWN = 0.30
_MULT_MIN = _MULT_HEAVY_DRAWDOWN
_MULT_MAX = _MULT_WIN_STREAK


class AntifragileRecovery:
    """Adaptive aggression system that scales position sizing
    based on recent P&L performance.

    Usage::

        af = AntifragileRecovery()
        mult = af.get_aggression_multiplier()
        # … size position using mult …
        af.record_trade_result(pnl=1.23, portfolio_value=105.0)
    """

    def __init__(self) -> None:
        self._trade_history: deque[float] = deque(maxlen=_MAX_HISTORY)
        self._peak_portfolio: float = 0.0
        self._current_portfolio: float = 0.0
        self._aggression: float = _MULT_NORMAL
        self._in_recovery: bool = False
        self._tier: str = "NORMAL"
        self._consecutive_wins: int = 0
        self._consecutive_losses: int = 0

        # Bootstrap from persisted state + positions_state.json
        self._load_state()
        self._bootstrap_from_positions()
        log.info(
            "[Antifragile] initialised — aggression=%.2f tier=%s "
            "peak=$%.2f portfolio=$%.2f history=%d",
            self._aggression, self._tier, self._peak_portfolio,
            self._current_portfolio, len(self._trade_history),
        )

    # ── Public API ────────────────────────────────────────────────────────

    def get_aggression_multiplier(self) -> float:
        """Return the current aggression multiplier (clamped to
        [0.30, 1.20]).  Downstream position sizing should multiply
        the base bet size by this value."""
        return round(self._aggression, 4)

    def record_trade_result(self, pnl: float, portfolio_value: float) -> None:
        """Record a completed trade's P&L and update aggression.

        Args:
            pnl: Realised P&L of the trade (positive = win).
            portfolio_value: Account balance after the trade settled.
        """
        self._trade_history.append(pnl)
        self._current_portfolio = portfolio_value

        # Track peak portfolio for drawdown detection
        if portfolio_value > self._peak_portfolio:
            self._peak_portfolio = portfolio_value

        # Update consecutive streaks
        if pnl > 0:
            self._consecutive_wins += 1
            self._consecutive_losses = 0
        elif pnl < 0:
            self._consecutive_losses += 1
            self._consecutive_wins = 0
        # pnl == 0 → break-even, do not alter streaks

        self._evaluate_tier()
        self._persist_state()

        log.info(
            "[Antifragile] trade recorded pnl=$%.2f → tier=%s "
            "aggression=%.2f streak W%d/L%d recovery=%s",
            pnl, self._tier, self._aggression,
            self._consecutive_wins, self._consecutive_losses,
            self._in_recovery,
        )

    def get_status(self) -> dict:
        """Return a comprehensive status dict for dashboards."""
        drawdown_pct = self._current_drawdown_pct()
        return {
            "aggression_multiplier": round(self._aggression, 4),
            "tier": self._tier,
            "in_recovery": self._in_recovery,
            "consecutive_wins": self._consecutive_wins,
            "consecutive_losses": self._consecutive_losses,
            "peak_portfolio": round(self._peak_portfolio, 2),
            "current_portfolio": round(self._current_portfolio, 2),
            "drawdown_pct": round(drawdown_pct * 100, 2),
            "trade_history_len": len(self._trade_history),
            "last_5_pnl": [round(p, 2) for p in list(self._trade_history)[-5:]],
        }

    # ── Tier evaluation ───────────────────────────────────────────────────

    def _evaluate_tier(self) -> None:
        """Determine the current tier and set aggression accordingly."""
        dd = self._current_drawdown_pct()

        # Priority 1: Heavy drawdown overrides everything
        if dd >= _DRAWDOWN_PCT:
            self._tier = "HEAVY_DRAWDOWN"
            self._aggression = _MULT_HEAVY_DRAWDOWN
            self._in_recovery = True
            return

        # Priority 2: Losing streak
        if self._consecutive_losses >= _LOSE_STREAK_LEN:
            self._tier = "LOSING_STREAK"
            self._aggression = _MULT_LOSE_STREAK
            self._in_recovery = True
            return

        # If we are in recovery, ramp up gradually on wins
        if self._in_recovery:
            if self._consecutive_wins > 0:
                # Ramp: start from current aggression, add step per win
                self._aggression = min(
                    _MULT_NORMAL,
                    self._aggression + _RECOVERY_STEP,
                )
                if self._aggression >= _MULT_NORMAL:
                    self._in_recovery = False
                    self._tier = "NORMAL"
                    self._aggression = _MULT_NORMAL
                else:
                    self._tier = "RECOVERING"
            return  # don't upgrade to win-streak while recovering

        # Priority 3: Winning streak (only when not recovering)
        if self._consecutive_wins >= _WIN_STREAK_LEN:
            self._tier = "WINNING_STREAK"
            self._aggression = _MULT_WIN_STREAK
            return

        # Default
        self._tier = "NORMAL"
        self._aggression = _MULT_NORMAL

    def _current_drawdown_pct(self) -> float:
        """Return current drawdown as a fraction of peak portfolio."""
        if self._peak_portfolio <= 0:
            return 0.0
        return max(
            0.0,
            (self._peak_portfolio - self._current_portfolio) / self._peak_portfolio,
        )

    # ── Bootstrap from positions_state.json ───────────────────────────────

    def _bootstrap_from_positions(self) -> None:
        """Read closed trades from positions_state.json to initialise
        trade history and streak counters if we have no persisted state."""
        if self._trade_history:
            # Already populated from persisted state — skip
            return

        try:
            from core.engine.state_manager import GLOBAL_POSITIONS_LOCK

            if not _POSITIONS_PATH.exists():
                log.debug("[Antifragile] positions_state.json not found — skipping bootstrap")
                return

            with GLOBAL_POSITIONS_LOCK:
                data = json.loads(_POSITIONS_PATH.read_text(encoding="utf-8"))

            closed: list[dict] = data.get("closed", [])
            if not closed:
                return

            # Populate trade history (oldest → newest as stored in file)
            for trade in closed[-_MAX_HISTORY:]:
                pnl = float(trade.get("realized_pnl", 0) or 0)
                self._trade_history.append(pnl)

            # Derive portfolio value from summary
            summary = data.get("summary", {})
            realized = float(summary.get("realized_pnl", 0) or 0)
            starting_balance = 100.0  # default per state_manager
            self._current_portfolio = round(starting_balance + realized, 2)
            self._peak_portfolio = max(self._peak_portfolio, self._current_portfolio)

            # Rebuild streaks from history
            self._rebuild_streaks()
            self._evaluate_tier()
            self._persist_state()

            log.info(
                "[Antifragile] bootstrapped from %d closed trades → "
                "tier=%s aggression=%.2f",
                len(closed), self._tier, self._aggression,
            )
        except Exception as exc:
            log.warning("[Antifragile] bootstrap failed (non-fatal): %s", exc)

    def _rebuild_streaks(self) -> None:
        """Walk trade history tail to rebuild consecutive win/loss counts."""
        self._consecutive_wins = 0
        self._consecutive_losses = 0

        for pnl in reversed(self._trade_history):
            if pnl > 0:
                if self._consecutive_losses > 0:
                    break  # streak broken
                self._consecutive_wins += 1
            elif pnl < 0:
                if self._consecutive_wins > 0:
                    break
                self._consecutive_losses += 1
            else:
                break  # break-even breaks streak

    # ── Persistence ───────────────────────────────────────────────────────

    def _persist_state(self) -> None:
        """Write current state to antifragile_state.json."""
        try:
            payload = {
                "aggression": self._aggression,
                "tier": self._tier,
                "in_recovery": self._in_recovery,
                "consecutive_wins": self._consecutive_wins,
                "consecutive_losses": self._consecutive_losses,
                "peak_portfolio": self._peak_portfolio,
                "current_portfolio": self._current_portfolio,
                "trade_history": list(self._trade_history),
                "last_updated": time.time(),
            }
            with open(_STATE_PATH, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2)
        except OSError as exc:
            log.warning("[Antifragile] state write failed: %s", exc)

    def _load_state(self) -> None:
        """Restore state from antifragile_state.json if it exists."""
        if not _STATE_PATH.exists():
            return
        try:
            data = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
            self._aggression = float(data.get("aggression", _MULT_NORMAL))
            self._tier = str(data.get("tier", "NORMAL"))
            self._in_recovery = bool(data.get("in_recovery", False))
            self._consecutive_wins = int(data.get("consecutive_wins", 0))
            self._consecutive_losses = int(data.get("consecutive_losses", 0))
            self._peak_portfolio = float(data.get("peak_portfolio", 0.0))
            self._current_portfolio = float(data.get("current_portfolio", 0.0))

            history = data.get("trade_history", [])
            for pnl in history[-_MAX_HISTORY:]:
                self._trade_history.append(float(pnl))

            log.info(
                "[Antifragile] restored state — tier=%s aggression=%.2f "
                "history=%d trades",
                self._tier, self._aggression, len(self._trade_history),
            )
        except Exception as exc:
            log.warning("[Antifragile] state load failed (starting fresh): %s", exc)
