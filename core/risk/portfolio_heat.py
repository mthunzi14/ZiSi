"""
portfolio_heat.py - Portfolio-wide correlation risk & exposure tracker.

Monitors total portfolio exposure across all active positions and prevents
correlated risk from accumulating unchecked.

Core mechanics:
  * **Correlation matrix**: rolling 1-hour Pearson correlation between
    BTC, ETH, SOL, XRP price changes.  Updated on each ``update_prices()``
    call.
  * **Heat score** (0–1): sum of each open position's
    ``size × avg_correlation_to_other_positions``, normalised to [0, 1].
  * **Dampening multiplier**: scales new position sizes down when heat
    rises above 0.7 (thresholds: 0.7 → 60%, 0.85 → 40%, 0.95 → 20%).

Persistence:
  Correlation data persisted to ``portfolio_heat_state.json`` in project root.
  Open positions read from ``infrastructure/exchange/positions_state.json``
  under the global positions lock.

Public API:
  update_prices(prices: dict)
  get_heat_score() -> float
  get_heat_multiplier() -> float
  get_correlation_matrix() -> dict
  get_status() -> dict
"""

import json
import logging
import math
import os
import time
from collections import deque
from pathlib import Path
from typing import Any

log = logging.getLogger("zisi.portfolio_heat")

# ── Paths ────────────────────────────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).parent.parent.parent
_POSITIONS_FILE = _PROJECT_ROOT / "data" / "positions_state.json"
_HEAT_STATE_FILE = _PROJECT_ROOT / "portfolio_heat_state.json"

# ── Constants ────────────────────────────────────────────────────────────────
_TRACKED_ASSETS: list[str] = ["BTC", "ETH", "SOL", "XRP"]
_ROLLING_WINDOW: int = 60  # 1 hour of minute-level samples (≈ 60 ticks)
_DAMPENING_THRESHOLDS: list[tuple[float, float]] = [
    # (heat_threshold, multiplier)   — checked high-to-low
    (0.95, 0.20),
    (0.85, 0.40),
    (0.70, 0.60),
]
_DEFAULT_MULTIPLIER: float = 1.0


# ── Helpers ──────────────────────────────────────────────────────────────────

def _pearson(xs: list[float], ys: list[float]) -> float:
    """
    Compute Pearson correlation coefficient between *xs* and *ys*.

    Returns 0.0 if there are fewer than 3 data points or zero variance.
    """
    n = min(len(xs), len(ys))
    if n < 3:
        return 0.0

    xs = xs[:n]
    ys = ys[:n]

    mean_x = sum(xs) / n
    mean_y = sum(ys) / n

    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)

    denom = math.sqrt(var_x * var_y)
    if denom < 1e-12:
        return 0.0

    return cov / denom


def _pct_changes(prices: list[float]) -> list[float]:
    """Return percentage changes between consecutive prices."""
    if len(prices) < 2:
        return []
    return [
        (prices[i] - prices[i - 1]) / prices[i - 1]
        for i in range(1, len(prices))
        if prices[i - 1] != 0
    ]


# ── Main class ───────────────────────────────────────────────────────────────

class PortfolioHeat:
    """
    Portfolio heat tracker.

    Usage::

        heat = PortfolioHeat()
        heat.update_prices({"BTC": 109000, "ETH": 2500, "SOL": 180, "XRP": 2.5})
        mult = heat.get_heat_multiplier()   # e.g. 0.60 when heat > 0.7
    """

    def __init__(self) -> None:
        # Rolling price history: asset -> deque of (timestamp, price)
        self._price_history: dict[str, deque[tuple[float, float]]] = {
            asset: deque(maxlen=_ROLLING_WINDOW + 1)
            for asset in _TRACKED_ASSETS
        }
        # Cached correlation matrix: (asset_a, asset_b) -> pearson_r
        self._corr_matrix: dict[tuple[str, str], float] = {}
        self._last_heat_score: float = 0.0
        self._update_count: int = 0
        self._last_positions: list[dict[str, Any]] = []

        # Restore persisted state
        self._load_state()
        log.info(
            "[PortfolioHeat] initialised — tracking %s, window=%d ticks",
            ", ".join(_TRACKED_ASSETS), _ROLLING_WINDOW,
        )

    # ── Public API ───────────────────────────────────────────────────────

    def update_prices(self, prices: dict[str, float]) -> None:
        """
        Feed latest spot prices for tracked assets.

        Args:
            prices: mapping of asset symbol → USD price,
                    e.g. ``{"BTC": 109000, "ETH": 2500}``.
        """
        now = time.time()
        updated = False

        for asset in _TRACKED_ASSETS:
            price = prices.get(asset) or prices.get(asset.lower())
            if price is not None and float(price) > 0:
                self._price_history[asset].append((now, float(price)))
                updated = True

        if updated:
            self._update_count += 1
            self._recompute_correlations()
            self._recompute_heat()
            self._persist_state()

    def get_heat_score(self) -> float:
        """Return the current portfolio heat score (0.0–1.0)."""
        return round(self._last_heat_score, 4)

    def get_heat_multiplier(self) -> float:
        """
        Return the position-size dampening multiplier.

        Based on the current heat score:
          heat > 0.95 → 0.20 (20% sizing)
          heat > 0.85 → 0.40 (40% sizing)
          heat > 0.70 → 0.60 (60% sizing)
          heat ≤ 0.70 → 1.00 (full sizing)
        """
        score = self._last_heat_score
        for threshold, multiplier in _DAMPENING_THRESHOLDS:
            if score > threshold:
                return multiplier
        return _DEFAULT_MULTIPLIER

    def get_correlation_matrix(self) -> dict[str, dict[str, float]]:
        """
        Return the current rolling correlation matrix as a nested dict.

        Example return::

            {
                "BTC": {"BTC": 1.0, "ETH": 0.87, "SOL": 0.72, "XRP": 0.55},
                "ETH": {"BTC": 0.87, "ETH": 1.0, ...},
                ...
            }
        """
        matrix: dict[str, dict[str, float]] = {}
        for a in _TRACKED_ASSETS:
            matrix[a] = {}
            for b in _TRACKED_ASSETS:
                if a == b:
                    matrix[a][b] = 1.0
                else:
                    key = tuple(sorted([a, b]))
                    matrix[a][b] = round(
                        self._corr_matrix.get((key[0], key[1]), 0.0), 4  # type: ignore[arg-type]
                    )
        return matrix

    def get_status(self) -> dict[str, Any]:
        """Return an operational status snapshot."""
        return {
            "heat_score": self.get_heat_score(),
            "heat_multiplier": self.get_heat_multiplier(),
            "tracked_assets": _TRACKED_ASSETS,
            "rolling_window": _ROLLING_WINDOW,
            "samples": {
                asset: len(self._price_history[asset])
                for asset in _TRACKED_ASSETS
            },
            "open_positions": len(self._last_positions),
            "total_updates": self._update_count,
            "dampening_thresholds": [
                {"above": t, "multiplier": m} for t, m in _DAMPENING_THRESHOLDS
            ],
        }

    # ── Internal computation ─────────────────────────────────────────────

    def _recompute_correlations(self) -> None:
        """Recompute Pearson correlations between every pair of tracked assets."""
        changes: dict[str, list[float]] = {}
        for asset in _TRACKED_ASSETS:
            prices = [p for _, p in self._price_history[asset]]
            changes[asset] = _pct_changes(prices)

        for i, a in enumerate(_TRACKED_ASSETS):
            for b in _TRACKED_ASSETS[i + 1:]:
                key = (a, b)  # always alphabetically sorted since list is sorted
                r = _pearson(changes[a], changes[b])
                self._corr_matrix[key] = round(r, 4)

    def _recompute_heat(self) -> None:
        """
        Compute the portfolio heat score from open positions and the
        correlation matrix.

        heat_contribution_i = position_size_i × avg_correlation(i, others)
        total_heat = Σ heat_contribution_i
        Normalised to [0, 1].
        """
        positions = self._read_positions()
        self._last_positions = positions

        if not positions:
            self._last_heat_score = 0.0
            return

        # Extract asset + size from each position
        pos_data: list[tuple[str, float]] = []
        for pos in positions:
            asset = self._extract_asset(pos)
            size = float(pos.get("position_size", 0) or pos.get("size", 0) or 0)
            if asset and size > 0:
                pos_data.append((asset.upper(), size))

        if not pos_data:
            self._last_heat_score = 0.0
            return

        # Compute heat contribution per position
        total_heat = 0.0
        total_size = sum(s for _, s in pos_data)

        for i, (asset_i, size_i) in enumerate(pos_data):
            if len(pos_data) == 1:
                # Single position — no cross-correlation to measure
                avg_corr = 0.0
            else:
                corr_sum = 0.0
                corr_count = 0
                for j, (asset_j, _) in enumerate(pos_data):
                    if i == j:
                        continue
                    if asset_i == asset_j:
                        corr_sum += 1.0  # same asset = perfect correlation
                    else:
                        key = tuple(sorted([asset_i, asset_j]))
                        corr_sum += abs(self._corr_matrix.get((key[0], key[1]), 0.0))  # type: ignore[arg-type]
                    corr_count += 1
                avg_corr = corr_sum / corr_count if corr_count > 0 else 0.0

            total_heat += size_i * avg_corr

        # Normalise: divide by total position size to get 0–1 range
        if total_size > 0:
            self._last_heat_score = min(1.0, max(0.0, total_heat / total_size))
        else:
            self._last_heat_score = 0.0

        log.debug(
            "[PortfolioHeat] heat=%.4f mult=%.2f positions=%d",
            self._last_heat_score, self.get_heat_multiplier(), len(pos_data),
        )

    def _read_positions(self) -> list[dict[str, Any]]:
        """
        Read open positions from positions_state.json under the global lock.

        Returns empty list on any error — never crashes.
        """
        if not _POSITIONS_FILE.exists():
            return []
        try:
            from core.engine.state_manager import GLOBAL_POSITIONS_LOCK

            with GLOBAL_POSITIONS_LOCK:
                data = json.loads(_POSITIONS_FILE.read_text(encoding="utf-8"))
            return data.get("active", [])
        except Exception as exc:
            log.debug("[PortfolioHeat] Failed to read positions: %s", exc)
            return []

    @staticmethod
    def _extract_asset(position: dict[str, Any]) -> str | None:
        """
        Extract the primary crypto asset from a position dict.

        Looks for ``affected_cryptos`` list, then falls back to parsing
        ``market_title`` or ``event_title``.
        """
        # Direct list
        cryptos = position.get("affected_cryptos")
        if cryptos and isinstance(cryptos, list) and cryptos:
            return str(cryptos[0]).upper()

        # Parse from title
        title = str(
            position.get("market_title", "")
            or position.get("event_title", "")
        ).upper()
        for asset in _TRACKED_ASSETS:
            if asset in title:
                return asset

        # Check for full names
        _name_map = {
            "BITCOIN": "BTC", "ETHEREUM": "ETH",
            "SOLANA": "SOL", "RIPPLE": "XRP",
        }
        for name, sym in _name_map.items():
            if name in title:
                return sym

        return None

    # ── Persistence ──────────────────────────────────────────────────────

    def _persist_state(self) -> None:
        """Write correlation matrix and heat score to disk."""
        try:
            # Serialise correlation matrix with string keys
            corr_serialised: dict[str, float] = {
                f"{a}:{b}": v for (a, b), v in self._corr_matrix.items()
            }
            # Serialise price history (only last N for space efficiency)
            history_serialised: dict[str, list[list[float]]] = {}
            for asset in _TRACKED_ASSETS:
                history_serialised[asset] = [
                    [ts, price] for ts, price in self._price_history[asset]
                ]

            payload = {
                "heat_score": self._last_heat_score,
                "heat_multiplier": self.get_heat_multiplier(),
                "correlation_matrix": corr_serialised,
                "price_history": history_serialised,
                "update_count": self._update_count,
                "last_updated": time.time(),
            }

            tmp_path = _HEAT_STATE_FILE.with_suffix(".tmp")
            tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            os.replace(tmp_path, _HEAT_STATE_FILE)
        except OSError as exc:
            log.debug("[PortfolioHeat] persist failed: %s", exc)

    def _load_state(self) -> None:
        """Restore correlation data and price history from disk."""
        if not _HEAT_STATE_FILE.exists():
            return
        try:
            data = json.loads(_HEAT_STATE_FILE.read_text(encoding="utf-8"))

            # Restore correlation matrix
            for key_str, value in data.get("correlation_matrix", {}).items():
                parts = key_str.split(":")
                if len(parts) == 2:
                    self._corr_matrix[(parts[0], parts[1])] = float(value)

            # Restore price history
            for asset in _TRACKED_ASSETS:
                history = data.get("price_history", {}).get(asset, [])
                for entry in history:
                    if isinstance(entry, list) and len(entry) == 2:
                        self._price_history[asset].append(
                            (float(entry[0]), float(entry[1]))
                        )

            self._update_count = int(data.get("update_count", 0))
            self._last_heat_score = float(data.get("heat_score", 0.0))

            log.info(
                "[PortfolioHeat] restored state — heat=%.4f, %d prior updates",
                self._last_heat_score, self._update_count,
            )
        except Exception as exc:
            log.warning("[PortfolioHeat] failed to load state: %s", exc)
