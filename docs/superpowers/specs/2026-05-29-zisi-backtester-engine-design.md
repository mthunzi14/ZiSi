# ZiSi Backtester Engine (WP2-v1) — Design Specification

> **Date:** 2026-05-29
> **Scope:** `tools/historical_backtest.py` engine + calibration + CLI/JSON report
> **Out of scope (separate v2 spec):** `/api/backtest/heatmap` route + dashboard heatmap component
> **Status:** Approved design → spec for implementation plan

---

## 1. Purpose & non-goals

**Purpose.** Build a high-fidelity historical backtester that replays ZiSi's **exact signal-decision logic** over Binance spot klines, prices the synthetic Polymarket contract with a **calibrated** model, and produces an **advisory** parameter-sensitivity report — validated against ZiSi's real closed trades before any recommendation is trusted.

**Explicit non-goals (documented limitations, not built in v1):**
- Bit-for-bit replay of the live strategy is **impossible** — live `generate_signal()` consumes real-time-only inputs (OFI WebSocket, Polymarket L2 book, Pyth oracle tick, ML/LSTM edge context). The backtester is a faithful replay of the **RSI + momentum + OFI-proxy cascade** with a **calibrated synthetic contract price**, not a perfect mirror.
- No tick-data ingest, no ML/LSTM replay, no Pyth path. These are listed as known approximations.
- The dashboard heatmap + API route are **v2** (separate spec).

## 2. Mandate & safety (non-negotiable)

ZiSi runs under a hard mandate: **no change may lower trade volume, win-rate, or PnL.** The backtester touches this in two ways, both guarded:

1. **Advisory-only.** The sweep **never writes `config.py`.** It prints "to apply, change X→Y" and the user decides. Any recommended parameter cell that *lowers* simulated trade count vs the current config is **flagged**, not hidden.
2. **Hard calibration gate.** The tool **refuses to print sweep recommendations** unless the price model first reproduces the real closed trades within tolerance (see §9). A miscalibrated model can never silently drive a config change.

The only change to **live** code is a behavior-preserving extraction (§5), covered by a golden test → live volume/WR/PnL unchanged by construction.

## 3. Ground-truth calibration data (verified live, 2026-05-29T11:41Z)

16 closed trades, 14W/2L (87.5% WR), +$107.82 realized, balance $207.82, Wilson 95% CI ≈ 64.0–96.5%. Source of truth: `infrastructure/exchange/positions_state.json` (read live at calibration time — never hardcoded).

Empirical findings that shape the model:
- **Entries span 0.06–0.60.** Most are 0.42–0.55 (≈ATM momentum). One deep reversal snipe: XRP/15m NO @ **0.06**.
- **`TARGET_HIT` exits scatter 0.88–0.99** (0.88/0.89/0.90/0.93/0.99). Cause: short-TF `target_price=0.88` + 30s reconcile loop catching the price somewhere past 0.88.
- **`MARKET_EXPIRED` exits ≈ 0.50** (losers 0.55→0.50, 0.60→0.50; even the XRP winner: 0.06→0.50). The paper engine resolves expiries at the live/fallback mid (~0.50), **not** binary 0/1 settlement.
- **No stops on short TF** (`stop_loss=-1.0`).
- **P&L formula (verified):** `profit = (size / entry_price) × (exit_price − entry_price)`.

**Mandatory test vectors:**
- **XRP idx-9** (NO @ 0.06 → 0.50, +733%, $3 size, +$22): the reversal branch MUST reproduce a deep-discount entry on this candle or calibration fails.
- **A representative ATM TARGET_HIT** (e.g. BTC/5m NO @ 0.50 → 0.89, +78%).
- **A loser** (e.g. BTC/5m @ 0.60 → 0.50, −17%) to confirm MARKET_EXPIRED ≈ 0.50 modeling.

## 4. Architecture — small, single-purpose units

```
tools/historical_backtest.py     # CLI orchestrator: ingest → calibrate → (gated) sweep → report
tools/backtest/__init__.py
tools/backtest/klines.py         # Binance /api/v3/klines ingest + local cache; OFI proxy (taker-buy ratio)
tools/backtest/pricing.py        # synthetic contract price: BSM N(d2) path + reversal branch + ATR slippage; exit model
tools/backtest/simulator.py      # candle-by-candle replay; concurrency queue; exit logic; trade ledger
tools/backtest/calibration.py    # fit vs real trades; error report; PASS/FAIL gate
tools/backtest/sweep.py          # advisory param grid + objective metrics + ranked output
tools/backtest/results/<ts>.json # output artifact
core/engine/signal_core.py       # NEW — pure decide_signal() shared by live engine + simulator
```
Position sizing inside the simulator reuses the real `core/risk` / `compute_size` path so simulated sizes match live.

## 5. Shared signal core (the one live-code change)

Extract the pure cascade currently inline in `updown_engine.generate_signal()` (≈ lines 348–396) into:

```python
# core/engine/signal_core.py
DEFAULT_SIGNAL_PARAMS = {           # == today's hardcoded constants (zero behavior change)
    "rsi_up": 60, "rsi_dn": 40,
    "mom_up": 0.02, "mom_dn": -0.02,
    "ofi_confirm": 0.45,
    "rsi_up_soft": 54, "rsi_dn_soft": 46, "mom_soft": 0.01,
    "reversal_lo": 20, "reversal_hi": 80,
    # OFI divergence block thresholds (5m/15m/neutral) as today
}

def decide_signal(rsi, mom, ofi, timeframe, params=DEFAULT_SIGNAL_PARAMS) -> dict:
    """Pure function. Returns {"direction": "UP"|"DOWN"|None, "score": float, "is_reversal": bool}."""
```

- **Live** `generate_signal()` calls `decide_signal(...)` with defaults → identical behavior.
- **Backtester** `simulator.py` calls it with swept `params`.
- **Golden test:** pin `decide_signal` output across a fixture grid of (rsi, mom, ofi, timeframe) to the pre-refactor results. Plus existing `tests/test_updown_engine.py` must stay green.

## 6. Data layer — `klines.py`

- Fetch 5m & 15m klines for `[BTC, ETH, SOL, XRP]` from `https://api.binance.com/api/v3/klines` (symbols `BTCUSDT` etc.), paginated to the requested window; cache to `tools/backtest/cache/<sym>_<tf>.json` (or parquet) keyed by window to avoid refetch.
- **OFI proxy:** Binance klines field 9 = *taker buy base asset volume*, field 5 = *total base volume*. Define `ofi_proxy = clamp(2·(taker_buy/total) − 1, −1, +1)` ∈ [−1,+1], matching the sign convention of the live OFI (positive = buy pressure). Validate bounds in tests.
- ATR(14) computed per timeframe from kline high/low/close for the pricing σ.

## 7. Pricing model — `pricing.py`

**Entry price:**
- **Momentum entries** (non-reversal): model entry at candle open as `P_entry = clamp(N(d2(t_entry)) + slippage, 0.01, 0.99)`, where the chosen-outcome price starts ≈ 0.50 ATM.
- **Reversal-snipe entries** (`is_reversal` from `decide_signal`, RSI<`reversal_lo`/>`reversal_hi`): model the deep-discount fill (the 0.06-type entry). Entry discount is a calibrated function of how extreme RSI is. **Must reproduce the XRP idx-9 entry.**

**Intra-candle path:** `d2(t) = ((S_t − S_0)/S_0) / (σ · √((T − t)/T))`, `P_t = clamp(N(d2(t)), 0.01, 0.99)`. `S_0` = candle open (strike), `S_t` = spot at minute `t`, `T` = candle length (5 or 15), σ = ATR(14) fraction × calibrated scale. Converges to 0/1 at expiry.

**ATR-relative slippage (refinement):** entry slippage = `f(ATR_pct, regime)` — wider in `VOLATILE_CHAOS` (high ATR), tighter in `COMPRESSION` (low ATR), instead of a flat constant. Calibrated against real entry-price error.

**Exit model — replicate the paper engine, NOT theoretical settlement:**
- Walk the candle on a **30s reconcile grid** (matching `RECONCILE_INTERVAL`).
- If `P_t ≥ 0.88` at a reconcile tick → `TARGET_HIT`, exit at `P_t` (naturally lands in 0.88–0.99, reproducing the observed overshoot distribution).
- Else at expiry → `MARKET_EXPIRED`, exit ≈ **0.50** (calibrated mid, not 0/1).
- No short-TF stop.

**P&L:** `profit = (size / entry) × (exit − entry)`.

## 8. Concurrency simulation — `simulator.py`

Enforce, via a simulated priority queue mirroring `_validate_trade_slot`:
- `MAX_OPEN_PER_ASSET = 2`, `MAX_TOTAL_OPEN = 6`.
- Candle-boundary alignment; late-entry gate (15s) analog.
- Circuit-breaker skip windows (consecutive-loss) as in live.
- Sizing via the real `compute_size` path (unified $1 floor / $5–$20 score ceiling / 15% bankroll).

Emit a trade ledger identical in shape to `positions_state.json` closed entries (entry/exit/size/pnl/reason) for direct comparison.

## 9. Calibration & validation gate — `calibration.py`

Runs **before** any sweep. Steps:
1. Read the real 16 closed trades live from `positions_state.json`.
2. Replay the historical window covering them with `DEFAULT_SIGNAL_PARAMS`.
3. Match simulated trades to real trades (by asset/timeframe/entry-time bucket).
4. Compute error metrics:
   - **Mean entry-price error** (target **< 7¢**).
   - **Win/loss agreement rate** (target **≥ 80%**).
   - **Simulated WR vs real WR** delta (report).
   - **XRP idx-9 reproduced?** (boolean, **required true**).
5. Print a calibration report. **If mean entry error ≥ 7¢ OR XRP vector fails OR W/L agreement < 80% → print a loud WARNING and BLOCK the sweep** (exit before recommendations).

## 10. Advisory sweep — `sweep.py`

- Grid over: RSI bounds (`rsi_up`/`rsi_dn`), OFI confirm/divergence gates, TARGET_HIT threshold / exit behavior.
- Per cell, over the full historical window, compute: **trade count, win-rate, expectancy, Sharpe, total PnL, max drawdown.**
- Rank by a configurable objective (default: expectancy, tie-break PnL), print a ranked console table + JSON.
- **Mandate guard:** flag any cell whose trade count < current-config trade count.
- **Never writes config.** Prints explicit "to apply: set `rsi_up` 60→58" diffs for the user to action manually.

## 11. Output

- `tools/backtest/results/<ISO8601>.json`: `{config, calibration_report, sweep_results[], current_config_baseline}`.
- Console: calibration report → (if passed) ranked sweep table with the mandate flags.

## 12. Testing

- `pricing`: N(d2) monotonicity in (S_t−S_0); clamps [0.01,0.99]; `t→T` convergence to 0/1; reversal-branch reproduces a 0.06-class entry.
- `klines`: OFI proxy bounds [−1,1] + sign; cache hit/miss.
- `calibration`: error metrics on the real 16 trades; XRP test vector; gate blocks on injected bad model.
- `signal_core`: golden equivalence vs pre-refactor cascade; existing `test_updown_engine` green.
- `simulator`: concurrency caps respected; P&L formula matches `execute_exit` on fixtures.

## 13. File manifest (created/changed)

| File | Change |
|---|---|
| `core/engine/signal_core.py` | **new** — pure `decide_signal` + `DEFAULT_SIGNAL_PARAMS` |
| `core/engine/updown_engine.py` | refactor `generate_signal` to call `decide_signal` (behavior-preserving) |
| `tools/historical_backtest.py` | **new** — CLI orchestrator |
| `tools/backtest/{klines,pricing,simulator,calibration,sweep}.py` | **new** |
| `tests/test_signal_core.py`, `tests/test_backtest_pricing.py`, `tests/test_backtest_calibration.py` | **new** |

## 14. Open risks / known approximations

- **OFI proxy ≠ live WS OFI** — taker-buy ratio is a kline-bar aggregate, not tick-level order flow. ~90% directional fidelity expected; quantified by the calibration gate.
- **MARKET_EXPIRED ≈ 0.50** is an empirical paper-engine convention that may evolve if the live exit logic changes — calibration re-validates each run.
- **n=16** real trades is a thin calibration set; the gate tolerances (7¢, 80%) are deliberately loose and should tighten as the live sample grows.
- **ML/Pyth omitted** — backtest models the pre-ML signal; live may diverge when the edge context is active.
