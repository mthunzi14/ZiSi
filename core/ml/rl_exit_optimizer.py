"""
rl_exit_optimizer.py — Tabular Q-Learning Exit Optimizer for ZiSi Bot.

Learns optimal exit timing (HOLD / TAKE_PROFIT / CUT_LOSS) using a
lightweight tabular Q-learning approach.  No heavy ML frameworks required —
only numpy for the Q-table.

State Space (300 discrete states):
    - Time since entry  : 5 bins  [0-1m, 1-2m, 2-3m, 3-4m, 4-5m]
    - Current P&L       : 5 bins  [deep_loss, small_loss, breakeven, small_profit, big_profit]
    - Regime            : 4 bins  [TRENDING, MEAN_REVERTING, VOLATILE_CHAOS, COMPRESSION]
    - Momentum direction: 3 bins  [against, neutral, with]
    Total = 5 × 5 × 4 × 3 = 300

Action Space:
    HOLD, TAKE_PROFIT, CUT_LOSS = 3 actions

Public API:
    get_exit_recommendation(time_in_trade, current_pnl, regime, momentum) → dict
    record_exit(state_dict, action, reward)
    get_status() → dict
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("zisi.rl_exit_optimizer")

# ── Guarded numpy import ─────────────────────────────────────────────────────
_NUMPY_AVAILABLE = False
try:
    import numpy as np
    _NUMPY_AVAILABLE = True
except ImportError:
    log.warning(
        "[RL-EXIT] numpy not installed — RL exit optimizer disabled. "
        "Install with: pip install numpy"
    )

# ── Paths ─────────────────────────────────────────────────────────────────────
_Q_TABLE_PATH = Path(__file__).resolve().parent / "q_table.npy"
_RL_STATE_PATH = Path(__file__).resolve().parent / "rl_exit_state.json"

# ── State-space dimensions ────────────────────────────────────────────────────
TIME_BINS: int = 5          # 0-1m, 1-2m, 2-3m, 3-4m, 4-5m
PNL_BINS: int = 5           # deep_loss, small_loss, breakeven, small_profit, big_profit
REGIME_BINS: int = 4         # TRENDING, MEAN_REVERTING, VOLATILE_CHAOS, COMPRESSION
MOMENTUM_BINS: int = 3       # against, neutral, with
N_STATES: int = TIME_BINS * PNL_BINS * REGIME_BINS * MOMENTUM_BINS  # 300
N_ACTIONS: int = 3           # HOLD, TAKE_PROFIT, CUT_LOSS

# ── Action names ──────────────────────────────────────────────────────────────
ACTIONS: List[str] = ["HOLD", "TAKE_PROFIT", "CUT_LOSS"]
ACTION_HOLD: int = 0
ACTION_TAKE_PROFIT: int = 1
ACTION_CUT_LOSS: int = 2

# ── Q-learning hyperparameters ────────────────────────────────────────────────
ALPHA: float = 0.1          # learning rate
GAMMA: float = 0.95         # discount factor
EPSILON: float = 0.1        # exploration rate (ε-greedy)

# ── Reward shaping ────────────────────────────────────────────────────────────
HOLD_PENALTY: float = -0.01  # small negative reward for holding

# ── Regime label mapping ─────────────────────────────────────────────────────
REGIME_LABELS: List[str] = ["TRENDING", "MEAN_REVERTING", "VOLATILE_CHAOS", "COMPRESSION"]
_REGIME_MAP: Dict[str, int] = {
    # Canonical
    "TRENDING": 0,
    "MEAN_REVERTING": 1,
    "VOLATILE_CHAOS": 2,
    "COMPRESSION": 3,
    # Common aliases from the regime_detector module
    "TREND": 0,
    "NORMAL": 0,
    "RANGE": 1,
    "MEAN_REVERSION": 1,
    "VOLATILE": 2,
    "SHOCK": 2,
    "COMPRESSED": 3,
}

# ── P&L thresholds (fraction of entry cost) ──────────────────────────────────
_PNL_EDGES: List[float] = [-0.03, -0.005, 0.005, 0.03]
# → deep_loss (< -3%), small_loss (-3% to -0.5%), breakeven (-0.5% to +0.5%),
#   small_profit (+0.5% to +3%), big_profit (> +3%)


# ══════════════════════════════════════════════════════════════════════════════
# Discretization helpers
# ══════════════════════════════════════════════════════════════════════════════

def _discretize_time(time_in_trade: float) -> int:
    """
    Map time-in-trade (seconds) to a bin index [0..4].

    Bins: 0-60s → 0, 60-120s → 1, 120-180s → 2, 180-240s → 3, 240+s → 4
    """
    minutes = max(0.0, time_in_trade) / 60.0
    return min(int(minutes), TIME_BINS - 1)


def _discretize_pnl(current_pnl: float) -> int:
    """
    Map current P&L (fractional, e.g. -0.02 = -2%) to a bin index [0..4].

    Bins: deep_loss=0, small_loss=1, breakeven=2, small_profit=3, big_profit=4
    """
    for i, edge in enumerate(_PNL_EDGES):
        if current_pnl < edge:
            return i
    return PNL_BINS - 1


def _discretize_regime(regime: str) -> int:
    """Map regime string to index [0..3]. Defaults to TRENDING (0) if unknown."""
    return _REGIME_MAP.get(str(regime).upper().strip(), 0)


def _discretize_momentum(momentum: float) -> int:
    """
    Map momentum value to directional bin [0..2].

    against=0 (momentum < -0.1), neutral=1 (-0.1 to +0.1), with=2 (> +0.1)
    """
    if momentum < -0.1:
        return 0  # against
    if momentum > 0.1:
        return 2  # with
    return 1  # neutral


def _state_to_index(
    time_bin: int,
    pnl_bin: int,
    regime_bin: int,
    momentum_bin: int,
) -> int:
    """
    Flatten the 4D state into a single integer index for the Q-table.

    Layout: time × (PNL_BINS × REGIME_BINS × MOMENTUM_BINS) + pnl × (...) + ...
    """
    idx = (
        time_bin * (PNL_BINS * REGIME_BINS * MOMENTUM_BINS)
        + pnl_bin * (REGIME_BINS * MOMENTUM_BINS)
        + regime_bin * MOMENTUM_BINS
        + momentum_bin
    )
    return max(0, min(idx, N_STATES - 1))


def _index_to_state(index: int) -> Dict[str, int]:
    """Reverse a flat index back to bin indices (for debugging)."""
    index = max(0, min(index, N_STATES - 1))
    momentum_bin = index % MOMENTUM_BINS
    index //= MOMENTUM_BINS
    regime_bin = index % REGIME_BINS
    index //= REGIME_BINS
    pnl_bin = index % PNL_BINS
    index //= PNL_BINS
    time_bin = index
    return {
        "time_bin": time_bin,
        "pnl_bin": pnl_bin,
        "regime_bin": regime_bin,
        "momentum_bin": momentum_bin,
    }


def encode_state(
    time_in_trade: float,
    current_pnl: float,
    regime: str,
    momentum: float,
) -> int:
    """
    Encode raw trade state into a discrete state index for the Q-table.

    Args:
        time_in_trade: Seconds since trade entry.
        current_pnl:   Current P&L as a fraction (e.g. 0.02 = +2%).
        regime:        Market regime string.
        momentum:      Momentum indicator value (negative = against position).

    Returns:
        Integer state index in [0, 299].
    """
    return _state_to_index(
        _discretize_time(time_in_trade),
        _discretize_pnl(current_pnl),
        _discretize_regime(regime),
        _discretize_momentum(momentum),
    )


# ══════════════════════════════════════════════════════════════════════════════
# Q-Learning Agent
# ══════════════════════════════════════════════════════════════════════════════

class RLExitOptimizer:
    """
    Tabular Q-learning agent that learns optimal exit timing for trades.

    The agent maintains a Q-table of shape (300, 3) and updates it online
    as trade exits are recorded.  Recommendations are made via ε-greedy
    policy with configurable exploration.
    """

    def __init__(
        self,
        alpha: float = ALPHA,
        gamma: float = GAMMA,
        epsilon: float = EPSILON,
    ):
        self.alpha = alpha
        self.gamma = gamma
        self.epsilon = epsilon
        self._n_updates: int = 0
        self._total_reward: float = 0.0

        if not _NUMPY_AVAILABLE:
            self._q_table = None
            log.warning("[RL-EXIT] numpy unavailable — Q-table not initialised")
            return

        # Initialise or load Q-table
        self._q_table: Optional["np.ndarray"] = np.zeros(
            (N_STATES, N_ACTIONS), dtype=np.float64
        )
        self._load()

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load(self) -> None:
        """Load Q-table and metadata from disk if available."""
        if not _NUMPY_AVAILABLE or self._q_table is None:
            return

        if _Q_TABLE_PATH.exists():
            try:
                loaded = np.load(str(_Q_TABLE_PATH))
                if loaded.shape == (N_STATES, N_ACTIONS):
                    self._q_table = loaded
                    log.info("[RL-EXIT] Q-table loaded from %s", _Q_TABLE_PATH)
                else:
                    log.warning(
                        "[RL-EXIT] Q-table shape mismatch: expected (%d,%d), got %s — reinitialising",
                        N_STATES, N_ACTIONS, loaded.shape,
                    )
            except Exception as exc:
                log.warning("[RL-EXIT] Failed to load Q-table: %s — reinitialising", exc)

        if _RL_STATE_PATH.exists():
            try:
                meta = json.loads(_RL_STATE_PATH.read_text(encoding="utf-8"))
                self._n_updates = int(meta.get("n_updates", 0))
                self._total_reward = float(meta.get("total_reward", 0.0))
                log.info(
                    "[RL-EXIT] State loaded: %d updates, total_reward=%.4f",
                    self._n_updates, self._total_reward,
                )
            except Exception:
                pass

    def _save(self) -> None:
        """Persist Q-table and metadata to disk."""
        if not _NUMPY_AVAILABLE or self._q_table is None:
            return

        try:
            _Q_TABLE_PATH.parent.mkdir(parents=True, exist_ok=True)
            np.save(str(_Q_TABLE_PATH), self._q_table)
        except Exception as exc:
            log.warning("[RL-EXIT] Failed to save Q-table: %s", exc)

        try:
            meta = {
                "n_updates": self._n_updates,
                "total_reward": round(self._total_reward, 6),
                "last_updated": datetime.now(timezone.utc).isoformat(),
                "alpha": self.alpha,
                "gamma": self.gamma,
                "epsilon": self.epsilon,
                "n_states": N_STATES,
                "n_actions": N_ACTIONS,
            }
            _RL_STATE_PATH.write_text(
                json.dumps(meta, indent=2), encoding="utf-8"
            )
        except Exception as exc:
            log.warning("[RL-EXIT] Failed to save RL state: %s", exc)

    # ── Q-learning core ───────────────────────────────────────────────────────

    def update(
        self,
        state: int,
        action: int,
        reward: float,
        next_state: int,
    ) -> None:
        """
        Perform a single Q-learning update.

        Q(s, a) ← Q(s, a) + α × [r + γ × max_a' Q(s', a') − Q(s, a)]

        Args:
            state:      Current state index [0..299].
            action:     Action taken [0=HOLD, 1=TAKE_PROFIT, 2=CUT_LOSS].
            reward:     Observed reward.
            next_state: Next state index [0..299].
        """
        if self._q_table is None:
            return

        state = max(0, min(state, N_STATES - 1))
        next_state = max(0, min(next_state, N_STATES - 1))
        action = max(0, min(action, N_ACTIONS - 1))

        current_q = self._q_table[state, action]
        max_next_q = self._q_table[next_state].max()
        td_target = reward + self.gamma * max_next_q
        self._q_table[state, action] = current_q + self.alpha * (td_target - current_q)

        self._n_updates += 1
        self._total_reward += reward

        # Persist every 10 updates to balance I/O and durability
        if self._n_updates % 10 == 0:
            self._save()

    def get_action(self, state: int) -> int:
        """
        Select an action using ε-greedy policy.

        With probability ε, choose a random action (exploration).
        Otherwise, choose the action with highest Q-value (exploitation).

        Args:
            state: Current state index [0..299].
        Returns:
            Action index [0..2].
        """
        if self._q_table is None:
            return ACTION_HOLD

        state = max(0, min(state, N_STATES - 1))

        if np.random.random() < self.epsilon:
            return int(np.random.randint(0, N_ACTIONS))

        q_values = self._q_table[state]
        return int(np.argmax(q_values))

    # ── Public API ────────────────────────────────────────────────────────────

    def get_exit_recommendation(
        self,
        time_in_trade: float,
        current_pnl: float,
        regime: str,
        momentum: float,
    ) -> Optional[Dict[str, Any]]:
        """
        Get the RL-recommended exit action for the current trade state.

        Args:
            time_in_trade: Seconds since trade entry.
            current_pnl:   Current P&L as a fraction (e.g. 0.02 = +2%).
            regime:        Market regime string (e.g. "TRENDING", "VOLATILE").
            momentum:      Momentum indicator value (negative = against position).

        Returns:
            Dict with keys:
                action     : str   — "HOLD", "TAKE_PROFIT", or "CUT_LOSS"
                confidence : float — Normalised confidence [0, 1]
                q_values   : dict  — Q-values for all actions
                state_index: int   — Discrete state index used
                state_bins : dict  — Human-readable bin breakdown
            Returns None if numpy is unavailable.
        """
        if self._q_table is None:
            log.debug("[RL-EXIT] Q-table not available — cannot recommend")
            return None

        state_idx = encode_state(time_in_trade, current_pnl, regime, momentum)
        action_idx = self.get_action(state_idx)

        q_vals = self._q_table[state_idx].copy()
        q_max = q_vals.max()
        q_min = q_vals.min()
        q_range = q_max - q_min

        # Confidence = how much the best action dominates alternatives
        if q_range > 0 and self._n_updates > 0:
            confidence = float((q_vals[action_idx] - q_min) / q_range)
        else:
            confidence = 0.0  # no learning yet → no confidence

        result: Dict[str, Any] = {
            "action": ACTIONS[action_idx],
            "confidence": round(confidence, 4),
            "q_values": {
                ACTIONS[i]: round(float(q_vals[i]), 6)
                for i in range(N_ACTIONS)
            },
            "state_index": state_idx,
            "state_bins": {
                "time_bin": _discretize_time(time_in_trade),
                "pnl_bin": _discretize_pnl(current_pnl),
                "regime_bin": _discretize_regime(regime),
                "momentum_bin": _discretize_momentum(momentum),
            },
            "n_updates": self._n_updates,
        }

        log.debug(
            "[RL-EXIT] State %d → %s (conf=%.2f, q=%s)",
            state_idx, ACTIONS[action_idx], confidence,
            {a: round(float(q_vals[i]), 4) for i, a in enumerate(ACTIONS)},
        )
        return result

    def record_exit(
        self,
        state_dict: Dict[str, Any],
        action: str,
        reward: float,
    ) -> None:
        """
        Record a completed exit and update the Q-table.

        Applies the reward function:
            - TAKE_PROFIT when profitable: reward = realized_pnl
            - CUT_LOSS when losing: reward = -|realized_loss| × 0.5
            - HOLD: reward = -0.01

        Args:
            state_dict: Dict with keys: time_in_trade, current_pnl, regime, momentum.
            action:     Action string ("HOLD", "TAKE_PROFIT", "CUT_LOSS").
            reward:     Observed reward (P&L or shaped reward).
        """
        if self._q_table is None:
            return

        # Encode current state
        state_idx = encode_state(
            time_in_trade=float(state_dict.get("time_in_trade", 0)),
            current_pnl=float(state_dict.get("current_pnl", 0)),
            regime=str(state_dict.get("regime", "TRENDING")),
            momentum=float(state_dict.get("momentum", 0)),
        )

        # Map action string to index
        action_upper = str(action).upper().strip()
        if action_upper in ("TAKE_PROFIT", "TP"):
            action_idx = ACTION_TAKE_PROFIT
        elif action_upper in ("CUT_LOSS", "CL", "STOP_LOSS", "SL"):
            action_idx = ACTION_CUT_LOSS
        else:
            action_idx = ACTION_HOLD

        # Apply reward shaping
        shaped_reward = self._shape_reward(action_idx, reward)

        # For terminal actions (TAKE_PROFIT, CUT_LOSS), next_state doesn't matter
        # much since the episode ends.  We use the same state as a convention.
        if action_idx in (ACTION_TAKE_PROFIT, ACTION_CUT_LOSS):
            # Terminal: no future value
            next_state_idx = state_idx
            # Override gamma effect: set next Q to 0 by using self-state
            # but the update formula handles it naturally since it's terminal
            self._q_table_terminal_update(state_idx, action_idx, shaped_reward)
        else:
            # HOLD: next state is the same trade, slightly further in time
            next_time = float(state_dict.get("time_in_trade", 0)) + 60  # assume 1 min tick
            next_state_idx = encode_state(
                time_in_trade=next_time,
                current_pnl=float(state_dict.get("current_pnl", 0)),
                regime=str(state_dict.get("regime", "TRENDING")),
                momentum=float(state_dict.get("momentum", 0)),
            )
            self.update(state_idx, action_idx, shaped_reward, next_state_idx)

        log.info(
            "[RL-EXIT] Recorded: state=%d action=%s reward=%.4f shaped=%.4f (total updates=%d)",
            state_idx, ACTIONS[action_idx], reward, shaped_reward, self._n_updates,
        )

    def _q_table_terminal_update(
        self, state: int, action: int, reward: float
    ) -> None:
        """
        Terminal-state Q-learning update (no future reward).

        Q(s, a) ← Q(s, a) + α × [r − Q(s, a)]
        """
        if self._q_table is None:
            return

        state = max(0, min(state, N_STATES - 1))
        action = max(0, min(action, N_ACTIONS - 1))

        current_q = self._q_table[state, action]
        self._q_table[state, action] = current_q + self.alpha * (reward - current_q)

        self._n_updates += 1
        self._total_reward += reward

        if self._n_updates % 10 == 0:
            self._save()

    @staticmethod
    def _shape_reward(action_idx: int, raw_reward: float) -> float:
        """
        Apply reward shaping per the design spec.

        - TAKE_PROFIT when profitable: reward = realized_pnl
        - CUT_LOSS when losing:        reward = -|realized_loss| × 0.5
        - HOLD:                         reward = -0.01
        """
        if action_idx == ACTION_TAKE_PROFIT:
            # Full reward for profitable exits
            return raw_reward

        if action_idx == ACTION_CUT_LOSS:
            # Reduced penalty for cutting losses early
            if raw_reward < 0:
                return -abs(raw_reward) * 0.5
            # If somehow cutting with a profit (unusual), give the raw reward
            return raw_reward

        # HOLD
        return HOLD_PENALTY

    def get_status(self) -> Dict[str, Any]:
        """
        Return a summary of the RL exit optimizer's current state.

        Returns:
            Dict with Q-table statistics, update count, and configuration.
        """
        status: Dict[str, Any] = {
            "numpy_available": _NUMPY_AVAILABLE,
            "q_table_initialised": self._q_table is not None,
            "n_states": N_STATES,
            "n_actions": N_ACTIONS,
            "actions": ACTIONS,
            "n_updates": self._n_updates,
            "total_reward": round(self._total_reward, 6),
            "hyperparameters": {
                "alpha": self.alpha,
                "gamma": self.gamma,
                "epsilon": self.epsilon,
            },
            "q_table_path": str(_Q_TABLE_PATH),
            "q_table_exists_on_disk": _Q_TABLE_PATH.exists(),
        }

        if self._q_table is not None:
            q = self._q_table
            status["q_table_stats"] = {
                "mean": round(float(q.mean()), 6),
                "std": round(float(q.std()), 6),
                "min": round(float(q.min()), 6),
                "max": round(float(q.max()), 6),
                "nonzero_entries": int(np.count_nonzero(q)),
                "total_entries": int(q.size),
                "coverage_pct": round(
                    int(np.count_nonzero(q)) / max(q.size, 1) * 100, 1
                ),
            }

            # Per-action stats: how often each action is the greedy choice
            greedy_actions = np.argmax(q, axis=1)
            status["greedy_action_distribution"] = {
                ACTIONS[i]: int((greedy_actions == i).sum())
                for i in range(N_ACTIONS)
            }

        return status

    def save(self) -> None:
        """Explicitly save Q-table and state to disk."""
        self._save()
        log.info("[RL-EXIT] Q-table and state saved to disk")


# ══════════════════════════════════════════════════════════════════════════════
# Module-level singleton & convenience wrappers
# ══════════════════════════════════════════════════════════════════════════════

_optimizer: Optional[RLExitOptimizer] = None


def _get_optimizer() -> Optional[RLExitOptimizer]:
    """Lazy-initialise the singleton optimizer."""
    global _optimizer
    if _optimizer is None:
        if not _NUMPY_AVAILABLE:
            return None
        _optimizer = RLExitOptimizer()
    return _optimizer


def get_exit_recommendation(
    time_in_trade: float,
    current_pnl: float,
    regime: str,
    momentum: float,
) -> Optional[Dict[str, Any]]:
    """
    Get the RL-recommended exit action for the current trade state.

    Convenience wrapper around the singleton optimizer.

    Args:
        time_in_trade: Seconds since trade entry.
        current_pnl:   Current P&L as fraction (e.g. 0.02 = +2%).
        regime:        Market regime string.
        momentum:      Momentum indicator value.

    Returns:
        Dict with {action, confidence, q_values} or None if unavailable.
    """
    opt = _get_optimizer()
    if opt is None:
        return None
    return opt.get_exit_recommendation(time_in_trade, current_pnl, regime, momentum)


def record_exit(
    state_dict: Dict[str, Any],
    action: str,
    reward: float,
) -> None:
    """
    Record a completed exit and update the Q-table.

    Convenience wrapper around the singleton optimizer.

    Args:
        state_dict: Dict with keys: time_in_trade, current_pnl, regime, momentum.
        action:     Action string ("HOLD", "TAKE_PROFIT", "CUT_LOSS").
        reward:     Observed reward (P&L value).
    """
    opt = _get_optimizer()
    if opt is None:
        log.debug("[RL-EXIT] Optimizer unavailable — exit not recorded")
        return
    opt.record_exit(state_dict, action, reward)


def get_status() -> Dict[str, Any]:
    """
    Return RL exit optimizer status.

    Convenience wrapper around the singleton optimizer.
    """
    opt = _get_optimizer()
    if opt is None:
        return {
            "numpy_available": False,
            "q_table_initialised": False,
            "error": "numpy not installed",
        }
    return opt.get_status()
