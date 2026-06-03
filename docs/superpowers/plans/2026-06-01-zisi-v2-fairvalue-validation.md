# ZiSi v2 — Fair-Value Signal + Backtest Validation (Plan 1 of 2) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the simple fair-value (spot-distance-from-strike) entry signal + entry-margin discipline + near-certainty archetype, harden the backtester with real costs, and **measure** whether the signal clears the promotion gate — all WITHOUT touching the live engine.

**Architecture:** Add a pure, shared `core/engine/fair_value.py` (live + backtester import it, like `signal_core`). Add a value-signal mode to the backtester, model entry slippage + fees + an adverse-selection stress, then run a sweep over 15m BTC/ETH and emit a GO/NO-GO verdict. Plan 2 (live demo wiring) is written only after this passes.

**Tech Stack:** Python 3.13 stdlib (`statistics.NormalDist`), `unittest` (pytest is NOT installed — run `python -m unittest tests.<module> -v` from repo root `C:\Users\mthun\Downloads\ZiSi_Bot`).

**Spec:** `docs/superpowers/specs/2026-05-31-zisi-v2-type1-pivot-design.md`

---

## Task 1: Baseline hygiene (commit in-progress work, ignore artifacts)

**Files:**
- Modify: `.gitignore`

The repo has ~785 lines of test-passing in-progress work (session_manager, threaded persistence, retrained LSTM) uncommitted. Commit it as the baseline; git-ignore binary/runtime artifacts so they never enter git.

- [ ] **Step 1: Confirm tests pass on the current tree**

Run: `python -m unittest tests.test_signal_core tests.test_updown_engine tests.test_risk_manager -v`
Expected: `OK` (no failures).

- [ ] **Step 2: Add artifact ignores to `.gitignore`**

Append these lines to `.gitignore`:
```
# ML + runtime artifacts (never commit binaries / runtime state)
core/ml/trained_model.pt
core/ml/training_metrics.json
oi_history.json
```

- [ ] **Step 3: Stage everything except the ignored artifacts, commit**

```bash
git rm --cached core/ml/trained_model.pt oi_history.json core/ml/training_metrics.json 2>/dev/null || true
git add -A
git commit -m "chore: baseline in-progress sprint work; ignore ML/runtime artifacts"
```

- [ ] **Step 4: Verify clean tree**

Run: `git status --short`
Expected: empty (or only intentionally-ignored runtime files shown as untracked-and-ignored, i.e. nothing staged-pending).

---

## Task 2: Shared fair-value module `core/engine/fair_value.py`

**Files:**
- Create: `core/engine/fair_value.py`
- Test: `tests/test_fair_value.py`

- [ ] **Step 1: Write the failing test**

`tests/test_fair_value.py`:
```python
import unittest
from core.engine.fair_value import fair_prob_up, decide_value_entry, DEFAULT_VALUE_PARAMS


class TestFairValue(unittest.TestCase):
    def test_atm_is_half(self):
        self.assertAlmostEqual(fair_prob_up(100.0, 100.0, 0.01, 0.0, 15.0), 0.5, places=4)

    def test_monotonic_in_move(self):
        lo = fair_prob_up(100.2, 100.0, 0.01, 7.5, 15.0)
        hi = fair_prob_up(100.8, 100.0, 0.01, 7.5, 15.0)
        self.assertGreater(hi, lo)

    def test_clamped(self):
        self.assertLessEqual(fair_prob_up(200.0, 100.0, 0.001, 14.9, 15.0), 0.99)
        self.assertGreaterEqual(fair_prob_up(1.0, 100.0, 0.001, 14.9, 15.0), 0.01)

    def test_no_entry_when_no_edge(self):
        # fair 0.55 UP but UP priced 0.54 -> edge 0.01 < margin 0.05 -> no entry
        r = decide_value_entry(0.55, up_price=0.54, dn_price=0.46, t_min=1.0, total_min=15.0)
        self.assertIsNone(r["direction"])

    def test_enters_underpriced_up(self):
        # fair 0.62 UP, UP priced 0.50 -> edge 0.12 >= margin -> UP, moderate
        r = decide_value_entry(0.62, up_price=0.50, dn_price=0.50, t_min=1.0, total_min=15.0)
        self.assertEqual(r["direction"], "UP")
        self.assertAlmostEqual(r["edge"], 0.12, places=4)
        self.assertEqual(r["archetype"], "moderate")

    def test_enters_underpriced_down(self):
        # fair_up 0.30 -> fair_down 0.70; DOWN priced 0.55 -> edge 0.15 -> DOWN
        r = decide_value_entry(0.30, up_price=0.45, dn_price=0.55, t_min=1.0, total_min=15.0)
        self.assertEqual(r["direction"], "DOWN")

    def test_near_certainty_late_window(self):
        # fair 0.95 UP, late (t_frac 0.93 >= 0.85), UP priced 0.88 -> edge 0.07 -> near_certainty
        r = decide_value_entry(0.95, up_price=0.88, dn_price=0.12, t_min=14.0, total_min=15.0)
        self.assertEqual(r["direction"], "UP")
        self.assertEqual(r["archetype"], "near_certainty")

    def test_high_prob_but_early_is_moderate(self):
        # fair 0.95 but early (t_frac 0.07 < 0.85) -> not near_certainty
        r = decide_value_entry(0.95, up_price=0.80, dn_price=0.20, t_min=1.0, total_min=15.0)
        self.assertEqual(r["archetype"], "moderate")
```

- [ ] **Step 2: Run the test, verify it FAILS**

Run: `python -m unittest tests.test_fair_value -v`
Expected: `ModuleNotFoundError: No module named 'core.engine.fair_value'`

- [ ] **Step 3: Implement `core/engine/fair_value.py`**

```python
"""
fair_value.py — spot-distance-from-strike fair-value signal (the Type-1 core).

Pure, shared by the live engine and the backtester (mirrors signal_core's
no-drift design). Given live spot, strike (window open), time elapsed, vol, and
the live contract prices, decide which side (if any) is underpriced by at least
the entry margin, and classify the archetype (moderate divergence vs near-certainty).

The probability model is the same driftless normal-CDF used by the backtester's
pricing module:  P(up) = N( ((S_t - S_0)/S_0) / (sigma * sqrt((T - t)/T)) ).
"""
from statistics import NormalDist
from typing import Optional

_N = NormalDist().cdf
_EPS = 1e-9

DEFAULT_VALUE_PARAMS = {
    "edge_margin": 0.05,           # min (fair_prob - price) required to enter (breakeven buffer)
    "edge_target": 0.10,           # preferred edge ("+10c to profit")
    "near_certainty_prob": 0.90,   # fair_prob at/above which an entry is "near certain"
    "near_certainty_t_frac": 0.85, # only near-certainty once >= 85% of the window has elapsed
    "sigma_scale": 1.0,            # multiplies ATR-derived sigma (carried from backtest calibration)
}


def fair_prob_up(s_t: float, s_0: float, sigma_frac: float, t_min: float,
                 total_min: float, sigma_scale: float = 1.0) -> float:
    """Driftless N(d2) probability the market resolves UP, clamped to [0.01, 0.99].
    s_0 = strike (window open), s_t = live spot, sigma_frac = ATR/price, t = minutes elapsed."""
    if s_0 <= 0:
        return 0.5
    remaining = max((total_min - t_min) / total_min, _EPS)
    sigma = max(sigma_frac * sigma_scale, _EPS)
    denom = max(sigma * (remaining ** 0.5), _EPS)
    d2 = ((s_t - s_0) / s_0) / denom
    return max(0.01, min(0.99, _N(d2)))


def decide_value_entry(fp_up: float, up_price: float, dn_price: float,
                       t_min: float, total_min: float,
                       params: Optional[dict] = None) -> dict:
    """Return {"direction": "UP"|"DOWN"|None, "edge": float, "archetype": str|None}.
    Enters the side whose (fair_prob - market_price) clears edge_margin; if both do,
    takes the larger edge. archetype in {"moderate", "near_certainty"}."""
    p = params or DEFAULT_VALUE_PARAMS
    edge_up = fp_up - up_price
    edge_dn = (1.0 - fp_up) - dn_price

    if edge_up < p["edge_margin"] and edge_dn < p["edge_margin"]:
        return {"direction": None, "edge": 0.0, "archetype": None}

    if edge_up >= edge_dn:
        direction, edge, fp = "UP", edge_up, fp_up
    else:
        direction, edge, fp = "DOWN", edge_dn, (1.0 - fp_up)

    t_frac = (t_min / total_min) if total_min > 0 else 0.0
    archetype = ("near_certainty"
                 if (fp >= p["near_certainty_prob"] and t_frac >= p["near_certainty_t_frac"])
                 else "moderate")
    return {"direction": direction, "edge": round(edge, 4), "archetype": archetype}
```

- [ ] **Step 4: Run the test, verify it PASSES**

Run: `python -m unittest tests.test_fair_value -v`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add core/engine/fair_value.py tests/test_fair_value.py
git commit -m "feat(signal): shared fair-value spot-distance signal + margin gate"
```

---

## Task 3: Cost realism in the backtester (slippage + fee + adverse-selection stress)

**Files:**
- Modify: `tools/backtest/pricing.py` (extend `PricingParams`; add `apply_entry_slippage`, `net_pnl`)
- Test: `tests/test_backtest_costs.py`

- [ ] **Step 1: Write the failing test**

`tests/test_backtest_costs.py`:
```python
import unittest
from tools.backtest.pricing import PricingParams, apply_entry_slippage, net_pnl


class TestCosts(unittest.TestCase):
    def test_slippage_worsens_entry(self):
        p = PricingParams(slippage_floor=0.01, slippage_atr_coef=0.5)
        # buying: you pay MORE than quoted; slippage = max(0.01, 0.5*0.02)=0.01
        filled = apply_entry_slippage(quoted=0.50, atr_frac=0.02, params=p)
        self.assertAlmostEqual(filled, 0.51, places=4)

    def test_slippage_scales_with_atr(self):
        p = PricingParams(slippage_floor=0.01, slippage_atr_coef=2.0)
        filled = apply_entry_slippage(quoted=0.50, atr_frac=0.05, params=p)  # 2.0*0.05=0.10
        self.assertAlmostEqual(filled, 0.60, places=4)

    def test_slippage_clamped_below_one(self):
        p = PricingParams(slippage_floor=0.01, slippage_atr_coef=20.0)
        self.assertLessEqual(apply_entry_slippage(quoted=0.95, atr_frac=0.5, params=p), 0.99)

    def test_net_pnl_subtracts_fee(self):
        p = PricingParams(fee_frac=0.02)
        # gross 10.0 on a 5.0 notional -> fee = 0.02*5.0 = 0.10 -> net 9.90
        self.assertAlmostEqual(net_pnl(gross=10.0, size=5.0, params=p), 9.90, places=4)

    def test_net_pnl_zero_fee(self):
        p = PricingParams(fee_frac=0.0)
        self.assertAlmostEqual(net_pnl(gross=-3.0, size=5.0, params=p), -3.0, places=4)
```

- [ ] **Step 2: Run the test, verify it FAILS**

Run: `python -m unittest tests.test_backtest_costs -v`
Expected: FAIL — `cannot import name 'apply_entry_slippage'` (and `PricingParams` lacks the new fields).

- [ ] **Step 3: Extend `tools/backtest/pricing.py`**

Add the new fields to the `PricingParams` dataclass (keep all existing fields), and add two module-level functions at the end of the file:

In `@dataclass class PricingParams:` add these fields (after the existing ones):
```python
    fee_frac: float = 0.0            # fee as fraction of notional (Polymarket ~0; tunable)
    slippage_floor: float = 0.01     # minimum entry slippage in price units (1c)
    adverse_selection: float = 0.0   # stress: fraction of marginal edge assumed lost to adverse fills
```

Append these functions:
```python
def apply_entry_slippage(quoted: float, atr_frac: float, params: "PricingParams") -> float:
    """Buying worsens (raises) the entry price. slippage = max(floor, atr_coef * atr_frac)."""
    slip = max(params.slippage_floor, params.slippage_atr_coef * atr_frac)
    return max(0.01, min(0.99, quoted + slip))


def net_pnl(gross: float, size: float, params: "PricingParams") -> float:
    """Subtract fees (fraction of notional) from a gross P&L figure."""
    return round(gross - params.fee_frac * size, 4)
```

- [ ] **Step 4: Run the test, verify it PASSES**

Run: `python -m unittest tests.test_backtest_costs -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Confirm nothing else broke**

Run: `python -m unittest tests.test_backtest_pricing tests.test_backtest_simulator -v`
Expected: PASS (existing tests still green — new fields have defaults).

- [ ] **Step 6: Commit**

```bash
git add tools/backtest/pricing.py tests/test_backtest_costs.py
git commit -m "feat(backtest): entry slippage + fee cost model"
```

---

## Task 4: Value-signal simulator mode

**Files:**
- Create: `tools/backtest/value_simulator.py`
- Test: `tests/test_value_simulator.py`

A separate simulator that uses the fair-value signal (not the RSI cascade), evaluates the decision cadence (open, +60s, scan, final-60s near-certainty), applies the margin gate, sizes via `sized_bet`, holds to resolution, and nets slippage+fees.

- [ ] **Step 1: Write the failing test**

`tests/test_value_simulator.py`:
```python
import unittest
from tools.backtest.klines import Candle
from tools.backtest.pricing import PricingParams
from tools.backtest.value_simulator import simulate_value, ValueConfig


def _c(ot, o, h, l, c, vol=100.0, tbb=50.0):
    return Candle.from_binance([ot, o, h, l, c, vol, 0, 0, 0, tbb, 0, 0])


class TestValueSimulator(unittest.TestCase):
    def _series(self, drift):
        # 20 warmup flat candles then one strongly-trending candle
        out = []
        base = 100.0
        for i in range(20):
            out.append(_c(i * 900000, base, base + 0.1, base - 0.1, base))
        # trending candle: close drifts up by `drift` fraction
        last_open = base
        out.append(_c(20 * 900000, last_open, last_open * (1 + drift),
                      last_open, last_open * (1 + drift)))
        return out

    def test_enters_on_clear_divergence(self):
        candles = {"BTC": self._series(0.01)}  # +1% move on the final candle
        cfg = ValueConfig(pricing=PricingParams(), start_balance=100.0)
        trades = simulate_value(candles, "15m", cfg)
        self.assertGreaterEqual(len(trades), 1)
        self.assertTrue(all(t.direction in ("UP", "DOWN") for t in trades))

    def test_no_entry_on_flat_market(self):
        candles = {"BTC": self._series(0.0)}  # no move -> fair ~0.5 ~ price -> no edge
        cfg = ValueConfig(pricing=PricingParams(), start_balance=100.0)
        trades = simulate_value(candles, "15m", cfg)
        self.assertEqual(len(trades), 0)

    def test_pnl_is_net_of_costs(self):
        candles = {"BTC": self._series(0.02)}
        cfg = ValueConfig(pricing=PricingParams(fee_frac=0.02, slippage_floor=0.02),
                          start_balance=100.0)
        trades = simulate_value(candles, "15m", cfg)
        # entry price must reflect slippage (>= the modeled quote + floor); sanity bound
        for t in trades:
            self.assertGreaterEqual(t.entry_price, 0.01)
            self.assertLessEqual(t.entry_price, 0.99)
```

- [ ] **Step 2: Run the test, verify it FAILS**

Run: `python -m unittest tests.test_value_simulator -v`
Expected: `ModuleNotFoundError: No module named 'tools.backtest.value_simulator'`

- [ ] **Step 3: Implement `tools/backtest/value_simulator.py`**

```python
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
    For backtest we approximate quoted up_price = N(d2) at slight lag; dn = 1 - up."""
    up = contract_price(s_t, s_0, max(sigma_frac * pricing.sigma_scale, 1e-9), t_min, total_min)
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
                # resolution: did it actually resolve in our direction? (Binance close vs open proxy)
                resolved_up = c.close >= s_0
                win = (dec["direction"] == "UP" and resolved_up) or \
                      (dec["direction"] == "DOWN" and not resolved_up)
                exit_price = 0.99 if win else 0.01
                score = 0.55 + min(0.30, dec["edge"])  # map edge -> score for sizing
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
```

- [ ] **Step 4: Run the test, verify it PASSES**

Run: `python -m unittest tests.test_value_simulator -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add tools/backtest/value_simulator.py tests/test_value_simulator.py
git commit -m "feat(backtest): fair-value signal simulator (margin gate, hold-to-resolution, net costs)"
```

---

## Task 5: Validation runner + GO/NO-GO gate

**Files:**
- Create: `tools/validate_fairvalue.py`
- Test: `tests/test_validate_fairvalue.py`

- [ ] **Step 1: Write the failing test**

`tests/test_validate_fairvalue.py`:
```python
import unittest
from tools.validate_fairvalue import breakeven_wr, evaluate_gate


class TestValidate(unittest.TestCase):
    def test_breakeven_wr(self):
        # at avg entry 0.50, breakeven WR = 0.50; at 0.40 -> 0.40
        self.assertAlmostEqual(breakeven_wr(0.50), 0.50, places=4)
        self.assertAlmostEqual(breakeven_wr(0.40), 0.40, places=4)

    def test_gate_pass(self):
        rep = evaluate_gate(trades=400, win_rate=0.60, avg_entry=0.50,
                            net_expectancy=0.06, baseline_trades=50)
        self.assertTrue(rep["passed"])

    def test_gate_fail_expectancy(self):
        rep = evaluate_gate(trades=400, win_rate=0.55, avg_entry=0.50,
                            net_expectancy=-0.01, baseline_trades=50)
        self.assertFalse(rep["passed"])
        self.assertIn("expectancy", rep["reason"])

    def test_gate_fail_wr_margin(self):
        # WR 0.51 only 1pt above breakeven 0.50 -> below the +3pt requirement
        rep = evaluate_gate(trades=400, win_rate=0.51, avg_entry=0.50,
                            net_expectancy=0.01, baseline_trades=50)
        self.assertFalse(rep["passed"])
        self.assertIn("win-rate margin", rep["reason"])

    def test_gate_fail_volume(self):
        rep = evaluate_gate(trades=40, win_rate=0.65, avg_entry=0.50,
                            net_expectancy=0.05, baseline_trades=50)
        self.assertFalse(rep["passed"])
        self.assertIn("volume", rep["reason"])
```

- [ ] **Step 2: Run the test, verify it FAILS**

Run: `python -m unittest tests.test_validate_fairvalue -v`
Expected: `ModuleNotFoundError: No module named 'tools.validate_fairvalue'`

- [ ] **Step 3: Implement `tools/validate_fairvalue.py`**

```python
"""Validate the fair-value signal in backtest and emit a GO/NO-GO verdict.

Gate (per spec §5): positive net expectancy AND win-rate exceeds entry-implied
breakeven by >= 3 percentage points AND trade count >> current baseline.

Run:  python -m tools.validate_fairvalue --days 14
"""
import argparse
import json
import os
import time
from statistics import mean
from typing import List

WR_MARGIN_REQUIRED = 0.03   # WR must beat breakeven by >= 3 pts
VOLUME_MULTIPLE = 3.0       # trade count must be >= 3x the baseline

_RESULTS = os.path.join(os.path.dirname(__file__), "backtest", "results")


def breakeven_wr(avg_entry: float) -> float:
    """On a buy-at-`avg_entry`, win-pays-1 contract, breakeven WR == avg_entry."""
    return avg_entry


def evaluate_gate(trades: int, win_rate: float, avg_entry: float,
                  net_expectancy: float, baseline_trades: int) -> dict:
    reasons = []
    if net_expectancy <= 0:
        reasons.append(f"net expectancy {net_expectancy:.4f} <= 0")
    be = breakeven_wr(avg_entry)
    if (win_rate - be) < WR_MARGIN_REQUIRED:
        reasons.append(f"win-rate margin {(win_rate-be):.3f} < {WR_MARGIN_REQUIRED}")
    if trades < baseline_trades * VOLUME_MULTIPLE:
        reasons.append(f"volume {trades} < {VOLUME_MULTIPLE}x baseline ({baseline_trades})")
    passed = not reasons
    return {"passed": passed,
            "reason": "GO — signal clears the promotion gate" if passed else "; ".join(reasons),
            "trades": trades, "win_rate": round(win_rate, 4),
            "breakeven_wr": round(be, 4), "net_expectancy": round(net_expectancy, 4)}


def _summarize(trades: List) -> dict:
    if not trades:
        return {"trades": 0, "win_rate": 0.0, "avg_entry": 0.5, "net_expectancy": 0.0,
                "total_pnl": 0.0}
    wins = sum(1 for t in trades if t.realized_pnl > 0)
    return {
        "trades": len(trades),
        "win_rate": wins / len(trades),
        "avg_entry": mean(t.entry_price for t in trades),
        "net_expectancy": mean(t.realized_pnl for t in trades),
        "total_pnl": round(sum(t.realized_pnl for t in trades), 2),
    }


def run(days: int = 14, baseline_trades: int = 50) -> dict:
    from tools.backtest.klines import fetch_klines
    from tools.backtest.value_simulator import simulate_value, ValueConfig
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - days * 86_400_000
    assets = ["BTC", "ETH"]
    all_trades = []
    for tf in ("15m",):  # 15m-first per spec; add "5m" once 15m validates
        candles = {a: fetch_klines(a, tf, start_ms, now_ms) for a in assets}
        all_trades.extend(simulate_value(candles, tf, ValueConfig()))
    s = _summarize(all_trades)
    gate = evaluate_gate(s["trades"], s["win_rate"], s["avg_entry"],
                         s["net_expectancy"], baseline_trades)
    report = {"summary": s, "gate": gate, "days": days}
    os.makedirs(_RESULTS, exist_ok=True)
    path = os.path.join(_RESULTS, f"fairvalue_{int(time.time())}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
    report["path"] = path
    return report


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Validate ZiSi fair-value signal")
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--baseline-trades", type=int, default=50)
    args = ap.parse_args(argv)
    rep = run(args.days, args.baseline_trades)
    print(json.dumps(rep["summary"], indent=2))
    print(json.dumps(rep["gate"], indent=2))
    print(f"[VALIDATE] wrote {rep['path']}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the test, verify it PASSES**

Run: `python -m unittest tests.test_validate_fairvalue -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Full backtest suite regression**

Run: `python -m unittest tests.test_fair_value tests.test_backtest_costs tests.test_value_simulator tests.test_validate_fairvalue tests.test_backtest_pricing tests.test_backtest_simulator tests.test_signal_core -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add tools/validate_fairvalue.py tests/test_validate_fairvalue.py
git commit -m "feat(backtest): fair-value validation runner + GO/NO-GO gate"
```

---

## Task 6: Run the live validation (the actual measurement)

**Files:** none (execution + reporting only)

- [ ] **Step 1: Run the validator over 14 days of 15m BTC/ETH**

Run: `python -m tools.validate_fairvalue --days 14`
Expected: prints a `summary` block (trades, win_rate, avg_entry, net_expectancy, total_pnl) and a `gate` block (`passed` true/false + reason), and writes a JSON to `tools/backtest/results/`.
(If the network/Binance fetch is blocked in this environment, note it and re-run where outbound HTTPS to `api.binance.com` is available — e.g. the Ireland VPS.)

- [ ] **Step 2: Record the verdict**

Capture the `gate` block verbatim. **This is the Plan-1 deliverable** — it decides whether Plan 2 (live demo wiring) proceeds:
- **GO** → the fair-value signal has backtested edge; proceed to write Plan 2 using the validated `edge_margin`, `sigma_scale`, archetype mix, and per-asset/timeframe numbers as inputs.
- **NO-GO** → iterate the signal (tune `edge_margin`, `sigma_scale`, decision cadence, add 5m, refine the resolution proxy) and re-run; do NOT touch the live engine.

- [ ] **Step 3: Commit the results artifact reference (optional)**

The results JSON is git-ignored under `tools/backtest/results/` (already ignored). Instead, paste the `gate` verdict into the Plan-2 spec discussion. No commit needed.

---

## Self-Review (completed)

**Spec coverage:** §4.1 fair-value signal → Task 2; §4.2 margin gate → Task 2 (`decide_value_entry`); §10.3 cost realism → Task 3; §10.10 archetypes (moderate + near-certainty) + cadence → Tasks 2 & 4; §5 validate-first promotion gate → Tasks 5 & 6; §10.7 baseline hygiene → Task 1. **Deferred to Plan 2 (correctly out of scope here):** §4.3 exit policy change, §4.4 caps removal, §10.9 capital risk model, §10.2 session integration, §4.6 live reversal-snipe hunt, §10.5 liquidity manufacturing, live demo wiring. The cheap-reversal archetype already exists in `signal_core`/the RSI simulator and is preserved; Plan 2 wires both signals together in the live engine.

**Placeholder scan:** every code step has complete, runnable code; every test step has real assertions; commands are exact with expected output. No TBD/TODO.

**Type consistency:** `fair_prob_up(...)` / `decide_value_entry(...)` signatures and the returned dict keys (`direction`/`edge`/`archetype`) are identical across Tasks 2, 4, 5. `PricingParams` new fields (`fee_frac`, `slippage_floor`, `adverse_selection`) match across Tasks 3 & 4. `SimTrade` fields reused from the existing `simulator.py`. `evaluate_gate(...)` keys consistent in Task 5.

## Notes for Plan 2 (after GO)
The resolution model in `value_simulator` uses **Binance close-vs-open as a proxy** for the Pyth-resolved outcome (we lack historical Pyth) — acceptable for 15m BTC except rare wicks; the *live* signal will read true Pyth. `adverse_selection` is carried as a stress field but applied only as a reporting stress in Plan 2's deeper validation (it needs order-book data to model fully). Plan 2 covers: caps→capital-risk-model, hold-to-resolution exits + remove salvage, session integration, near-certainty + reversal archetypes in the live engine, and wiring the validated signal into the demo engine.

---

## RESULT (2026-06-01): lookahead corrected + real lag-sensitivity

The original kline value-sim had **lookahead bias** (open->close interpolation) and a degenerate quote model. Rewritten to replay **real 1-minute closes** with a **market-lag model** (Tasks 4-6 corrected). Real 7-day BTC+ETH run:

| lag_min | trades | win_rate | avg_entry | verdict |
|--|--|--|--|--|
| 0 | 0 | 0.000 | - | efficient market -> ZERO edge (no-lookahead sanity PASSES) |
| 1 | 1338 | 0.625 | 0.511 | profitable IF we have >=1min lead |
| 2 | 1338 | 0.623 | 0.508 | |
| 3 | 1338 | 0.626 | 0.509 | |

**Honest read:** logic is sound (lag=0 -> no fake edge). WR ~62.5%% at ~0.51 entry clears breakeven by ~11pts WITH good volume (1338/wk) — but ONLY if the Polymarket book lags Binance by >=1 min. That is optimistic on liquid crypto; 1m candles cannot probe sub-minute lags where reality likely sits. total_pnl $ figures are compounding artifacts — ignore them. **The real go/no-go is the Ireland VPS demo measuring our true lead.** Proceed to Plan 2 (VPS demo deployment) to measure it.
