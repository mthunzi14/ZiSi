"""Candle-by-candle replay of the ZiSi strategy with concurrency caps."""
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from core.engine.signal_core import decide_signal, DEFAULT_SIGNAL_PARAMS
from core.engine.updown_engine import _compute_rsi, _compute_momentum
from tools.backtest.klines import Candle, ofi_proxy, atr
from tools.backtest.pricing import PricingParams, entry_price, price_path_exit


def pnl(size: float, entry: float, exit: float) -> float:
    """Realized P&L exactly as execute_exit computes it: (size/entry)*(exit-entry)."""
    if entry <= 0:
        return 0.0
    shares = size / entry
    return round(shares * (exit - entry), 4)


class ConcurrencyGate:
    """Mirrors MAX_OPEN_PER_ASSET / MAX_TOTAL_OPEN from config."""
    def __init__(self, max_per_asset: int = 2, max_total: int = 6):
        self.max_per_asset = max_per_asset
        self.max_total = max_total
        self._open: Dict[str, int] = {}

    @property
    def total(self) -> int:
        return sum(self._open.values())

    def try_open(self, asset: str) -> bool:
        if self.total >= self.max_total:
            return False
        if self._open.get(asset, 0) >= self.max_per_asset:
            return False
        self._open[asset] = self._open.get(asset, 0) + 1
        return True

    def close(self, asset: str) -> None:
        if self._open.get(asset, 0) > 0:
            self._open[asset] -= 1


@dataclass
class SimTrade:
    asset: str
    timeframe: str
    entry_time: int
    direction: str
    size: float
    entry_price: float
    exit_price: float
    exit_reason: str
    realized_pnl: float
    is_reversal: bool


@dataclass
class SimConfig:
    signal_params: dict = field(default_factory=lambda: dict(DEFAULT_SIGNAL_PARAMS))
    pricing: PricingParams = field(default_factory=PricingParams)
    max_per_asset: int = 2
    max_total: int = 6
    bet_usd: float = 5.0  # flat sizing for v1 replay; sweep can vary later


def _intra_candle_spot(c: Candle, steps: int = 10) -> List[float]:
    """Approximate the within-candle spot path by linear interpolation open->close."""
    return [c.open + (c.close - c.open) * (i / steps) for i in range(steps + 1)]


def simulate(candles_by_asset: Dict[str, List[Candle]], timeframe: str,
             cfg: SimConfig) -> List[SimTrade]:
    """Replay one timeframe across assets. Trades open/close within the same candle
    (short-TF markets resolve each candle), so the concurrency gate is opened and
    released per candle in chronological order across assets."""
    total_min = float(int(timeframe.rstrip("m")))
    grid_steps = 10
    minutes = [total_min * i / grid_steps for i in range(grid_steps + 1)]

    # Build a chronological event list across assets keyed by candle open_time.
    times = sorted({c.open_time for cs in candles_by_asset.values() for c in cs})
    by_time: Dict[int, List[tuple]] = {}
    for asset, cs in candles_by_asset.items():
        hist: List[Candle] = []
        for c in cs:
            hist.append(c)
            if len(hist) >= 16:
                by_time.setdefault(c.open_time, []).append((asset, list(hist)))

    gate = ConcurrencyGate(cfg.max_per_asset, cfg.max_total)
    trades: List[SimTrade] = []
    for t in times:
        for asset, hist in by_time.get(t, []):
            closes = [c.close for c in hist]
            rsi = _compute_rsi(closes)
            mom = _compute_momentum(closes)
            cur = hist[-1]
            ofi = ofi_proxy(cur)
            dec = decide_signal(rsi, mom, ofi, timeframe, cfg.signal_params)
            if dec["blocked"] or dec["direction"] is None:
                continue
            if not gate.try_open(asset):
                continue
            sigma_frac = (atr(hist, 14) / cur.open) if cur.open else 0.01
            ep = entry_price(dec["direction"], dec["is_reversal"], rsi, sigma_frac,
                             cfg.pricing, regime_atr_frac=sigma_frac)
            spot_path = _intra_candle_spot(cur, grid_steps)
            xp, reason = price_path_exit(dec["direction"], cur.open, ep, spot_path,
                                         minutes, sigma_frac, total_min, cfg.pricing)
            trades.append(SimTrade(
                asset=asset, timeframe=timeframe, entry_time=cur.open_time,
                direction=dec["direction"], size=cfg.bet_usd, entry_price=ep,
                exit_price=xp, exit_reason=reason, realized_pnl=pnl(cfg.bet_usd, ep, xp),
                is_reversal=dec["is_reversal"]))
            gate.close(asset)  # short-TF trade resolves within its candle
    return trades
