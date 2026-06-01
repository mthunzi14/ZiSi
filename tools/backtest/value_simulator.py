"""Backtest the fair-value signal with NO lookahead.

Replays REAL 1-minute closes inside each aligned window (instead of interpolating
open->close, which leaks the outcome). The market QUOTE is modeled as lagging the
true spot by `lag_min` minutes — so the captured edge exists ONLY to the extent the
market is slow. Sweep `lag_min` to get a sensitivity curve; the VPS demo (real quotes,
real latency) is the true go/no-go. Sizing/costs reuse the existing backtester pieces.
"""
from dataclasses import dataclass, field
from typing import Dict, List

from core.engine.fair_value import fair_prob_up, decide_value_entry, DEFAULT_VALUE_PARAMS
from tools.backtest.klines import Candle, atr
from tools.backtest.pricing import (PricingParams, contract_price, apply_entry_slippage,
                                    net_pnl)
from tools.backtest.simulator import SimTrade, sized_bet, pnl

_ATR_HISTORY = 30  # 1m candles before a window used to estimate vol (strictly prior -> no lookahead)


@dataclass
class ValueConfig:
    value_params: dict = field(default_factory=lambda: dict(DEFAULT_VALUE_PARAMS))
    pricing: PricingParams = field(default_factory=PricingParams)
    start_balance: float = 100.0
    lag_min: int = 1       # market repricing lag in minutes (the edge source); swept by the runner
    window_min: int = 15   # window length in minutes (15m-first)


def _group_windows(candles: List[Candle], window_min: int):
    """Yield (start_index, [candles]) for each COMPLETE aligned window of window_min 1m candles."""
    win_ms = window_min * 60_000
    buckets: Dict[int, List[int]] = {}
    for idx, c in enumerate(candles):
        buckets.setdefault((c.open_time // win_ms) * win_ms, []).append(idx)
    for key in sorted(buckets):
        idxs = buckets[key]
        if len(idxs) == window_min:
            yield idxs[0], [candles[i] for i in idxs]


def simulate_value(candles_1m_by_asset: Dict[str, List[Candle]],
                   cfg: ValueConfig) -> List[SimTrade]:
    """candles_1m_by_asset: {asset: [1-minute Candle, ...]}. Returns closed SimTrades."""
    total_min = float(cfg.window_min)
    balance = cfg.start_balance
    trades: List[SimTrade] = []

    for asset, raw in candles_1m_by_asset.items():
        candles = sorted(raw, key=lambda c: c.open_time)
        for start_idx, window in _group_windows(candles, cfg.window_min):
            if start_idx < _ATR_HISTORY:
                continue  # need strictly-prior history for vol (no lookahead)
            s_0 = window[0].open
            if s_0 <= 0:
                continue
            prior = candles[start_idx - _ATR_HISTORY:start_idx]
            # 1m ATR scaled to the window horizon (sqrt-time); approximate vol, refined by calibration
            sigma_frac = (atr(prior, 14) / s_0) * (cfg.window_min ** 0.5)
            resolved_up = window[-1].close >= s_0
            for j, candle in enumerate(window):
                t_min = float(j + 1)            # minutes elapsed at this 1m close
                s_t = candle.close              # REAL current spot (no lookahead)
                lag_idx = j - cfg.lag_min
                s_lag = window[lag_idx].close if lag_idx >= 0 else s_0  # market is lag_min behind
                sigma = max(sigma_frac * cfg.pricing.sigma_scale, 1e-9)
                fp_up = fair_prob_up(s_t, s_0, sigma_frac, t_min, total_min, cfg.pricing.sigma_scale)
                up_q = contract_price(s_lag, s_0, sigma, t_min, total_min)
                dn_q = round(1.0 - up_q, 4)
                dec = decide_value_entry(fp_up, up_q, dn_q, t_min, total_min, cfg.value_params)
                if dec["direction"] is None:
                    continue
                quoted = up_q if dec["direction"] == "UP" else dn_q
                ep = apply_entry_slippage(quoted, sigma_frac, cfg.pricing)
                win = (dec["direction"] == "UP" and resolved_up) or \
                      (dec["direction"] == "DOWN" and not resolved_up)
                exit_price = 0.99 if win else 0.01
                score = 0.55 + min(0.30, dec["edge"])
                bet = sized_bet(score, ep, max(balance, 1.0))
                trade_pnl = net_pnl(pnl(bet, ep, exit_price), bet, cfg.pricing)
                trades.append(SimTrade(
                    asset=asset, timeframe=f"{cfg.window_min}m", entry_time=window[0].open_time,
                    direction=dec["direction"], size=bet, entry_price=ep, exit_price=exit_price,
                    exit_reason=dec["archetype"], realized_pnl=trade_pnl, is_reversal=False))
                balance += trade_pnl
                break  # one entry per window, then hold to resolution
    return trades
