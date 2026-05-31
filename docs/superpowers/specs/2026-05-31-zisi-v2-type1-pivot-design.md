# ZiSi v2 — Type-1 "PBot-Style" Strategy Pivot — Design Specification

> **Date:** 2026-05-31
> **Status:** Approved design → spec for implementation plan
> **Scope:** Replace ZiSi's primary entry signal with a simple, backtest-validated fair-value (spot-distance-from-strike) signal under strict entry-price discipline; change exits to hold-to-resolution; prioritise 15m BTC and raise volume. Validate in the backtester BEFORE the live paper bot.
> **Out of scope (future phases):** Type-2 market-making / GTC laddering, the execution-latency layer, Ireland/colo VPS migration, self-recorded tick data.

---

## 1. Thesis (one line)
Replace the fragile 18-layer signal cascade with **one simple, validated signal** — spot-distance-from-strike fair value vs the live contract price — enforce entry-price/win-rate discipline so we naturally enter low-and-probable, **hold to resolution**, trade **15m-BTC-first at high volume**, and **prove it in the backtester before it touches the live paper bot.**

## 2. Why (the edge, from PBot-6's builder @0x_Punisher's public playbook)
- **The signal must be simple.** *"Your strategy is too complex and too fragile. RSI/MACD/divergence layers… The bots that survive use CLOB momentum, spot distance from strike, time-weighted signals, and day-of-week edges. That is it."* ZiSi's 18-layer cascade is the disease.
- **Entry-price ↔ win-rate is the law.** *"Enter at 57¢ and you need >57% WR to break even… +5¢ to break even, +10¢ to profit. You cannot simply filter for lower prices — your base strategy must naturally enter lower with higher probability."* Real directional WR lands at **52–56%**, not the 70%+ paper shows.
- **15-minute BTC first.** *"5-minute is brutal. Bots that bleed on 5m print immediately on 15m."*
- **Shares-first sizing, chunked, capital preservation; hold-to-resolution; reconciliation loop.** (ZiSi already has shares-first ✓ and the silent-fill reconciliation loop ✓.)

## 3. Diagnosis: why ZiSi bleeds now (the "L2-fixed → we bleed" mystery)
When the L2 book was broken, ZiSi fell back to synthetic/cheap fallback prices and *recorded* entries at artificially low prices → tiny breakeven bar → inflated ~80% paper WR on fills that "did not exist." Now the L2 book is real, ZiSi enters at true ~50–60¢ momentum prices → breakeven bar jumps to ~55% → its ~50% directional edge no longer clears it → it bleeds. Compounded by the **momentum-exhaustion trap** (shorting assets already oversold at RSI ~27). The pivot fixes the root cause: **only fire when the edge clears the real breakeven bar.**

## 4. Design

### 4.1 New primary signal — fair-value spot-distance (the simple winner)
Add a value-divergence signal (reusing the backtester's pricing math, which already passes calibration at 4.1¢ error):

```
fair_prob_up = clamp(N(d2), 0.01, 0.99)
d2 = ((S_t - S_0) / S_0) / (sigma * sqrt((T - t)/T))
  S_0 = strike (candle open),  S_t = live spot (Pyth/Binance),
  T   = window length (15 or 5 min),  t = minutes elapsed,
  sigma = ATR(14)/S_0 * sigma_scale  (sigma_scale from backtest calibration)
```
At decision time(s) in the window, compute `fair_prob_up`, read live contract prices `up_price`, `dn_price` from the L2 book, then:
```
edge_up = fair_prob_up        - up_price
edge_dn = (1 - fair_prob_up)  - dn_price
```

### 4.2 Entry-price discipline (THE core gate — fixes the bleed)
- Enter **UP** iff `edge_up >= EDGE_MARGIN`; enter **DOWN** iff `edge_dn >= EDGE_MARGIN`. If both qualify, take the larger edge.
- `EDGE_MARGIN` default **0.05** (breakeven buffer) with a **preferred target of 0.10** (the "+5¢/+10¢" rule). Tunable per coin/timeframe via backtest.
- **No hard price ceiling** (the 0.80 cap is removed) — near-certainty 90¢ entries are allowed *if* they still clear the margin. The margin gate — not a price filter — is what makes us "enter lower with higher probability."
- Decision timing: evaluate at **window open and ~60s in** (per the playbook), configurable.

### 4.3 Exit policy — hold to resolution
- **Remove** the 0.88 `TARGET_HIT` cap and the **salvage-sell-at-~8¢** logic (it locks coin-flip losses). Winners ride to ~100¢, losers to ~0¢; the edge plays out over volume.
- Optional **aggressive stop on clear losers** is configurable and **OFF by default** (short windows often do better with no stop) — to be A/B-tested in the backtester, never assumed.

### 4.4 15m-BTC-first + volume
- Lead with **15m BTC + ETH**; de-emphasise 5m. Keep multi-asset but 15m-weighted.
- **Volume comes from removing the redundant blocking gates** (volume-climax gate, OFI-divergence block, score<0.50 reject, multi-gate cascade), NOT from loosening quality — the single `EDGE_MARGIN` gate becomes the quality control. Trade *every* window that clears the margin.

### 4.5 Sizing
- Keep **shares-first** (already correct ✓). Small, consistent, **chunked** bets scaled by `edge_margin × balance`; optional DCA-into-winners; **capital preservation over perfect entries**; never all-in. Respect existing bankroll caps.

### 4.6 Keep / do not touch
Reversal-snipe archetype ✓ (proven cheap-entry edge), silent-fill reconciliation loop ✓, shares-first sizing ✓, warmup/quality gate ✓, pure `signal_core` structure (so a future Type-2 execution layer slots in).

## 5. Validate-FIRST protocol (the safety gate)
1. Implement the fair-value signal + `EDGE_MARGIN` gate as a **mode in `signal_core`** (alongside, not destroying, the current logic).
2. Run it through the **existing backtester** over historical 15m BTC/ETH (it already calibrates within 4.1¢): measure WR, expectancy (net of modeled fee/slippage), trade count, max drawdown, and WR-minus-breakeven margin.
3. **Promotion gate:** only wire it as the live paper primary if backtest shows **positive expectancy**, **WR exceeds entry-implied breakeven by ≥ ~3–5%**, and **trade count ≫ current**. If not, iterate the signal — do not deploy hope.
4. Future phases (separate specs): **shadow mode** (zero-balance real orders) → **live 10% size** → execution-latency layer + **Ireland VPS**.

## 6. Success criteria (the owner's objectives)
- **Trade volume:** materially higher than today (every margin-clearing window, not a few/day).
- **Win rate:** clears entry-implied breakeven + margin (target real ≥ ~55% at the entry prices taken).
- **Compounding P&L:** steady, low-variance up-line driven by many small edges (no single outlier dominating) — the PBot/Bone Reaper signature.

## 7. Mandate alignment
The Triple Mandate (no change may lower volume / win-rate / PnL) is *served*, not threatened: the current bot is bleeding, and the pivot's explicit purpose is to raise all three. The validate-first gate guarantees we never deploy a signal that doesn't clear breakeven in backtest.

## 8. Current-code reconciliation note
The repo has evolved since the backtester build (`signal_core.py` now has `TradingSessionManager` session-scaling; three "sprint" merges; uncommitted changes across ~10 core files from other tooling). The implementation plan MUST read the **actual current** `signal_core.py`, `updown_engine.py`, `trader.py`, and the backtester before editing, build the new signal **additively** (a new mode, behind a flag), and must not clobber uncommitted work without confirmation.

## 9. Open risks / honest caveats
- **Paper validates the brain, not the speed.** The dominant live edge (FIFO-queue latency) is not capturable in paper; live profitability also needs the execution layer + colo VPS (future phase). Paper + backtest prove the signal+discipline have edge; they do not guarantee live PnL.
- **Adverse selection:** in live, resting below ask disproportionately fills the losing side. Modeled only crudely in v1; flagged for the shadow-mode phase.
- **Fair-value model is an approximation** (driftless N(d₂), kline-proxy vol); the calibration gate bounds—but does not eliminate—model error. Re-validate as the live sample grows.
- **n is still small.** Backtest tolerances are loose at current sample size; tighten as data accumulates.
