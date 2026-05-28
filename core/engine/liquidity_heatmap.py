"""
liquidity_heatmap.py - Order book depth analysis & stop-hunt detection.

Analyses L2 order-book data (bids/asks with sizes) to identify price levels
where volume clusters 3x+ above average — "liquidity pools" — that act as
magnets or stop-hunt targets for binary-option price action.

Key concepts:
  * **Liquidity pool**: a price level whose aggregate volume ≥ 3× the
    book-wide average.  Bid-side pools → support magnets; ask-side pools →
    resistance magnets.
  * **Stop-hunt pattern**: thin order depth on the approach side of a
    cluster with a thick cluster behind it — a classic setup for price to
    sweep through stops before reversing.
  * **Smart levels**: safe stops placed *behind* the nearest liquidity wall;
    magnet targets placed at the nearest cluster in trade direction.

Public API:
  update_book(bids, asks, token_id)
  get_clusters(token_id) -> dict
  get_stop_hunt_risk(direction, token_id) -> float
  get_smart_levels(direction, current_price, token_id) -> dict
  get_status() -> dict
"""

import logging
import time
from collections import defaultdict
from typing import Any

log = logging.getLogger("zisi.liquidity_heatmap")

# ── Configuration ────────────────────────────────────────────────────────────
_VOLUME_MULT_THRESHOLD: float = 3.0   # how many × avg volume = "liquidity pool"
_CLUSTER_WINDOW: float = 0.01         # group orders within 1 cent
_BOOK_TTL_SECONDS: float = 10.0       # cache per-token TTL
_THIN_BOOK_THRESHOLD: float = 0.5     # approach side volume < 50% of avg → "thin"


# ── Data structures ──────────────────────────────────────────────────────────

class _BookSnapshot:
    """Immutable snapshot of a processed order book."""

    __slots__ = (
        "token_id", "timestamp",
        "bid_clusters", "ask_clusters",
        "avg_bid_volume", "avg_ask_volume",
        "raw_bids", "raw_asks",
    )

    def __init__(
        self,
        token_id: str,
        raw_bids: list[dict[str, float]],
        raw_asks: list[dict[str, float]],
    ) -> None:
        self.token_id = token_id
        self.timestamp = time.time()
        self.raw_bids = raw_bids
        self.raw_asks = raw_asks

        # Cluster + averages
        self.bid_clusters: list[dict[str, Any]] = []
        self.ask_clusters: list[dict[str, Any]] = []
        self.avg_bid_volume: float = 0.0
        self.avg_ask_volume: float = 0.0

        self._process()

    # ── Internal processing ──────────────────────────────────────────────

    def _process(self) -> None:
        """Cluster raw orders, compute averages, flag liquidity pools."""
        self.bid_clusters = self._cluster_orders(self.raw_bids, side="bid")
        self.ask_clusters = self._cluster_orders(self.raw_asks, side="ask")

        bid_vols = [c["volume"] for c in self.bid_clusters] if self.bid_clusters else [0.0]
        ask_vols = [c["volume"] for c in self.ask_clusters] if self.ask_clusters else [0.0]

        self.avg_bid_volume = sum(bid_vols) / len(bid_vols) if bid_vols else 0.0
        self.avg_ask_volume = sum(ask_vols) / len(ask_vols) if ask_vols else 0.0

        # Tag pools that exceed threshold
        for c in self.bid_clusters:
            c["is_pool"] = (
                self.avg_bid_volume > 0
                and c["volume"] >= self.avg_bid_volume * _VOLUME_MULT_THRESHOLD
            )
            c["label"] = "support_magnet" if c["is_pool"] else "bid_cluster"

        for c in self.ask_clusters:
            c["is_pool"] = (
                self.avg_ask_volume > 0
                and c["volume"] >= self.avg_ask_volume * _VOLUME_MULT_THRESHOLD
            )
            c["label"] = "resistance_magnet" if c["is_pool"] else "ask_cluster"

    @staticmethod
    def _cluster_orders(orders: list[dict[str, float]], side: str) -> list[dict[str, Any]]:
        """Group orders within ``_CLUSTER_WINDOW`` cents of each other."""
        if not orders:
            return []

        # Normalise input: accept both str and float price/size
        parsed: list[tuple[float, float]] = []
        for o in orders:
            try:
                price = float(o.get("price", 0))  # type: ignore[arg-type]
                size = float(o.get("size", 0))    # type: ignore[arg-type]
                if price > 0 and size > 0:
                    parsed.append((price, size))
            except (TypeError, ValueError):
                continue

        if not parsed:
            return []

        # Sort by price (ascending)
        parsed.sort(key=lambda x: x[0])

        clusters: list[dict[str, Any]] = []
        cluster_start = parsed[0][0]
        cluster_prices: list[float] = []
        cluster_volume: float = 0.0
        cluster_order_count: int = 0

        for price, size in parsed:
            if price - cluster_start <= _CLUSTER_WINDOW:
                cluster_prices.append(price)
                cluster_volume += size
                cluster_order_count += 1
            else:
                # Flush previous cluster
                clusters.append({
                    "price_low": cluster_prices[0],
                    "price_high": cluster_prices[-1],
                    "price_mid": sum(cluster_prices) / len(cluster_prices),
                    "volume": round(cluster_volume, 4),
                    "order_count": cluster_order_count,
                    "side": side,
                })
                cluster_start = price
                cluster_prices = [price]
                cluster_volume = size
                cluster_order_count = 1

        # Flush last cluster
        if cluster_prices:
            clusters.append({
                "price_low": cluster_prices[0],
                "price_high": cluster_prices[-1],
                "price_mid": sum(cluster_prices) / len(cluster_prices),
                "volume": round(cluster_volume, 4),
                "order_count": cluster_order_count,
                "side": side,
            })

        return clusters


# ── Main class ───────────────────────────────────────────────────────────────

class LiquidityHeatmap:
    """
    Order book depth analyser and stop-hunt detector.

    Usage::

        heatmap = LiquidityHeatmap()
        heatmap.update_book(bids, asks, token_id="0xabc...")
        clusters = heatmap.get_clusters(token_id="0xabc...")
        risk = heatmap.get_stop_hunt_risk("UP", token_id="0xabc...")
        levels = heatmap.get_smart_levels("UP", current_price=0.55, token_id="0xabc...")
    """

    def __init__(self) -> None:
        self._books: dict[str, _BookSnapshot] = {}
        self._update_count: int = 0
        log.info("[LiquidityHeatmap] initialised")

    # ── Public API ───────────────────────────────────────────────────────

    def update_book(
        self,
        bids: list[dict[str, float]],
        asks: list[dict[str, float]],
        token_id: str,
    ) -> None:
        """
        Ingest a new L2 order-book snapshot for *token_id*.

        Args:
            bids: list of ``{"price": float, "size": float}`` dicts (buy side).
            asks: list of ``{"price": float, "size": float}`` dicts (sell side).
            token_id: Polymarket condition-token identifier.
        """
        try:
            snapshot = _BookSnapshot(token_id, bids, asks)
            self._books[token_id] = snapshot
            self._update_count += 1

            n_bid_pools = sum(1 for c in snapshot.bid_clusters if c.get("is_pool"))
            n_ask_pools = sum(1 for c in snapshot.ask_clusters if c.get("is_pool"))
            log.debug(
                "[LiquidityHeatmap] %s updated — %d bid clusters (%d pools), "
                "%d ask clusters (%d pools)",
                token_id[:12], len(snapshot.bid_clusters), n_bid_pools,
                len(snapshot.ask_clusters), n_ask_pools,
            )
        except Exception as exc:
            log.error("[LiquidityHeatmap] update_book failed for %s: %s", token_id[:12], exc)

    def get_clusters(self, token_id: str) -> dict[str, Any]:
        """
        Return support/resistance clusters for *token_id*.

        Returns:
            dict with keys ``support_magnets``, ``resistance_magnets``,
            ``all_bid_clusters``, ``all_ask_clusters``, ``stale`` flag.
        """
        snap = self._get_fresh(token_id)
        if snap is None:
            return self._empty_clusters()

        return {
            "support_magnets": [c for c in snap.bid_clusters if c.get("is_pool")],
            "resistance_magnets": [c for c in snap.ask_clusters if c.get("is_pool")],
            "all_bid_clusters": snap.bid_clusters,
            "all_ask_clusters": snap.ask_clusters,
            "avg_bid_volume": snap.avg_bid_volume,
            "avg_ask_volume": snap.avg_ask_volume,
            "stale": False,
        }

    def get_stop_hunt_risk(self, direction: str, token_id: str) -> float:
        """
        Compute stop-hunt risk score (0.0–1.0) for *direction* on *token_id*.

        A high score means:
        - Price is approaching a thick liquidity cluster
        - The approach side has thin depth — classic stop-hunt setup

        Args:
            direction: ``"UP"`` or ``"DOWN"``  (the predicted binary outcome).
            token_id: Polymarket condition-token identifier.

        Returns:
            0.0 (no risk) – 1.0 (very high risk).
        """
        snap = self._get_fresh(token_id)
        if snap is None:
            return 0.0

        direction_up = direction.upper() in ("UP", "BULLISH", "YES")

        try:
            return self._calc_stop_hunt_risk(snap, direction_up)
        except Exception as exc:
            log.error("[LiquidityHeatmap] stop_hunt_risk error: %s", exc)
            return 0.0

    def get_smart_levels(
        self,
        direction: str,
        current_price: float,
        token_id: str,
    ) -> dict[str, float | None]:
        """
        Compute smart stop and target levels using liquidity analysis.

        Args:
            direction: ``"UP"`` or ``"DOWN"``.
            current_price: current market mid-price (0–1 for binary).
            token_id: Polymarket condition-token identifier.

        Returns:
            dict with ``safe_stop`` and ``magnet_target`` (or None).
        """
        snap = self._get_fresh(token_id)
        if snap is None:
            return {"safe_stop": None, "magnet_target": None}

        direction_up = direction.upper() in ("UP", "BULLISH", "YES")

        try:
            safe_stop = self._get_safe_stop(snap, direction_up, current_price)
            magnet_target = self._get_magnet_target(snap, direction_up, current_price)
            return {
                "safe_stop": round(safe_stop, 4) if safe_stop is not None else None,
                "magnet_target": round(magnet_target, 4) if magnet_target is not None else None,
            }
        except Exception as exc:
            log.error("[LiquidityHeatmap] get_smart_levels error: %s", exc)
            return {"safe_stop": None, "magnet_target": None}

    def get_status(self) -> dict[str, Any]:
        """Return operational status summary."""
        now = time.time()
        active_tokens = [
            tid for tid, snap in self._books.items()
            if now - snap.timestamp < _BOOK_TTL_SECONDS
        ]
        return {
            "tracked_tokens": len(self._books),
            "active_tokens": len(active_tokens),
            "total_updates": self._update_count,
            "ttl_seconds": _BOOK_TTL_SECONDS,
            "volume_threshold_mult": _VOLUME_MULT_THRESHOLD,
            "cluster_window_cents": _CLUSTER_WINDOW,
        }

    # ── Private helpers ──────────────────────────────────────────────────

    def _get_fresh(self, token_id: str) -> _BookSnapshot | None:
        """Return the cached snapshot if within TTL, else None."""
        snap = self._books.get(token_id)
        if snap is None:
            log.debug("[LiquidityHeatmap] No book data for %s", token_id[:12])
            return None
        age = time.time() - snap.timestamp
        if age > _BOOK_TTL_SECONDS:
            log.debug(
                "[LiquidityHeatmap] Book for %s is stale (%.1fs old)",
                token_id[:12], age,
            )
            return None
        return snap

    def _calc_stop_hunt_risk(self, snap: _BookSnapshot, direction_up: bool) -> float:
        """
        Score stop-hunt risk.

        Logic:
        - For UP direction: price approaches ask-side clusters from below.
          Thin bids near price + thick ask cluster ahead = stop-hunt risk.
        - For DOWN direction: mirror — thin asks near price + thick bid cluster.

        Components (each 0–1, weighted):
          0.50  cluster_thickness — how much the target cluster exceeds avg
          0.30  approach_thinness — how thin the approach side is
          0.20  cluster_proximity — closer clusters = higher risk
        """
        if direction_up:
            target_clusters = [c for c in snap.ask_clusters if c.get("is_pool")]
            approach_clusters = snap.bid_clusters
            avg_approach = snap.avg_bid_volume
        else:
            target_clusters = [c for c in snap.bid_clusters if c.get("is_pool")]
            approach_clusters = snap.ask_clusters
            avg_approach = snap.avg_ask_volume

        if not target_clusters:
            return 0.0

        # 1. Cluster thickness — max pool volume relative to avg (capped at 1.0)
        if direction_up:
            avg_target = snap.avg_ask_volume
        else:
            avg_target = snap.avg_bid_volume

        max_pool_vol = max(c["volume"] for c in target_clusters)
        thickness = min(1.0, (max_pool_vol / avg_target - 1.0) / 9.0) if avg_target > 0 else 0.0

        # 2. Approach-side thinness
        if approach_clusters and avg_approach > 0:
            min_approach_vol = min(c["volume"] for c in approach_clusters)
            thinness = max(0.0, 1.0 - (min_approach_vol / (avg_approach * _THIN_BOOK_THRESHOLD)))
        else:
            thinness = 1.0  # empty approach = maximally thin

        # 3. Cluster proximity (use total number of clusters between price
        #    and the pool — fewer intervening clusters = higher proximity)
        total_levels = len(snap.bid_clusters) + len(snap.ask_clusters)
        if total_levels > 1:
            pool_index = next(
                (i for i, c in enumerate(target_clusters)),
                0,
            )
            proximity = max(0.0, 1.0 - pool_index / max(1, total_levels))
        else:
            proximity = 0.5

        risk = 0.50 * thickness + 0.30 * thinness + 0.20 * proximity
        return round(min(1.0, max(0.0, risk)), 4)

    @staticmethod
    def _get_safe_stop(
        snap: _BookSnapshot,
        direction_up: bool,
        current_price: float,
    ) -> float | None:
        """
        Place stop *behind* the nearest liquidity wall relative to direction.

        - UP trade → stop below the nearest bid-side pool (support wall).
        - DOWN trade → stop above the nearest ask-side pool (resistance wall).

        Returns price level or None if no pool found.
        """
        if direction_up:
            # Find bid pools below current price
            pools = [
                c for c in snap.bid_clusters
                if c.get("is_pool") and c["price_mid"] < current_price
            ]
            if not pools:
                return None
            # Nearest pool below
            nearest = max(pools, key=lambda c: c["price_mid"])
            # Place stop just below the low of that cluster
            return max(0.0, nearest["price_low"] - _CLUSTER_WINDOW)
        else:
            # Find ask pools above current price
            pools = [
                c for c in snap.ask_clusters
                if c.get("is_pool") and c["price_mid"] > current_price
            ]
            if not pools:
                return None
            nearest = min(pools, key=lambda c: c["price_mid"])
            return min(1.0, nearest["price_high"] + _CLUSTER_WINDOW)

    @staticmethod
    def _get_magnet_target(
        snap: _BookSnapshot,
        direction_up: bool,
        current_price: float,
    ) -> float | None:
        """
        Find the nearest liquidity pool in the trade direction as a take-profit
        magnet — price tends to be drawn toward large resting volume.

        - UP trade → nearest ask-side pool (resistance) above current price.
        - DOWN trade → nearest bid-side pool (support) below current price.
        """
        if direction_up:
            pools = [
                c for c in snap.ask_clusters
                if c.get("is_pool") and c["price_mid"] > current_price
            ]
            if not pools:
                return None
            return min(pools, key=lambda c: c["price_mid"])["price_mid"]
        else:
            pools = [
                c for c in snap.bid_clusters
                if c.get("is_pool") and c["price_mid"] < current_price
            ]
            if not pools:
                return None
            return max(pools, key=lambda c: c["price_mid"])["price_mid"]

    @staticmethod
    def _empty_clusters() -> dict[str, Any]:
        return {
            "support_magnets": [],
            "resistance_magnets": [],
            "all_bid_clusters": [],
            "all_ask_clusters": [],
            "avg_bid_volume": 0.0,
            "avg_ask_volume": 0.0,
            "stale": True,
        }
