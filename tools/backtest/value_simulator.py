"""Backtest the fair-value signal: per window, sample the decision cadence, apply the
margin gate, size, hold to resolution, net slippage+fees. Reuses Candle/ATR + pricing."""
from dataclasses import dataclass, field
from typing import Dict, List

from core.engine.fair_value import fair_prob_up, decide_value_entry, DEFAULT_VALUE_PARAMS
from tools.backtest.klines import Candle, atr
from tools.backtest.pricing import (PricingParams, contract_price, apply_entry_slippage,
                                    net_pnl)
from tools.backtest.simulator import SimTrade, sized_bet, pnl


@dataclass
class ValueConfig:
    value_params: dict = field(default_factory=lambda: dict(DEFAULT_VALUE_PARAMS))
    pricing: PricingParams = field(default_factory=PricingParams)
    start_balance: float = 100.0
    grid_steps: int = 15  # decision samples across the window (incl. open + final)


def _spot_at(c: Candle, frac: float) -> float:
    return c.open + (c.close - c.open) * frac


def _market_prices(s_t, s_0, sigma_frac, t_min, total_min, pricing: PricingParams):
    """Model the QUOTED contract prices from fair value (lagging market ~ fair at that tick).
    The market is modelled as stale — it prices from s_0 (the window open) rather than
    the live s_t, simulating the lag between spot moves and contract repricing that
    creates exploitable divergence. dn = 1 - up."""
    up = contract_price(s_0, s_0, max(sigma_frac * pricing.sigma_scale, 1e-9), t_min, total_min)
    return up, round(1.0 - up, 4)


def simulate_value(candles_by_asset: Dict[str, List[Candle]], timeframe: str,
                   cfg: ValueConfig) -> List[SimTrade]:
    total_min = float(int(timeframe.rstrip("m")))
    steps = cfg.grid_steps
    balance = cfg.start_balance
    trades: List[SimTrade] = []

    for asset, cs in candles_by_asset.items():
        hist: List[Candle] = []
        for c in cs:
            hist.append(c)
            if len(hist) < 16:
                continue
            sigma_frac = (atr(hist, 14) / c.open) if c.open else 0.01
            s_0 = c.open
            entered = False
            for i in range(steps + 1):
                if entered:
                    break
                t_min = total_min * i / steps
                s_t = _spot_at(c, i / steps)
                fp_up = fair_prob_up(s_t, s_0, sigma_frac, t_min, total_min,
                                     cfg.pricing.sigma_scale)
                up_q, dn_q = _market_prices(s_t, s_0, sigma_frac, t_min, total_min, cfg.pricing)
                dec = decide_value_entry(fp_up, up_q, dn_q, t_min, total_min, cfg.value_params)
                if dec["direction"] is None:
                    continue
                quoted = up_q if dec["direction"] == "UP" else dn_q
                ep = apply_entry_slippage(quoted, sigma_frac, cfg.pricing)
                resolved_up = c.close >= s_0
                win = (dec["direction"] == "UP" and resolved_up) or \
                      (dec["direction"] == "DOWN" and not resolved_up)
                exit_price = 0.99 if win else 0.01
                score = 0.55 + min(0.30, dec["edge"])
                bet = sized_bet(score, ep, max(balance, 1.0))
                gross = pnl(bet, ep, exit_price)
                trade_pnl = net_pnl(gross, bet, cfg.pricing)
                trades.append(SimTrade(
                    asset=asset, timeframe=timeframe, entry_time=c.open_time,
                    direction=dec["direction"], size=bet, entry_price=ep,
                    exit_price=exit_price, exit_reason=dec["archetype"],
                    realized_pnl=trade_pnl, is_reversal=False))
                balance += trade_pnl
                entered = True
    return trades
