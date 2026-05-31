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

### 4.4 15m-BTC-first + maximum volume
- Lead with **15m BTC + ETH**; keep multi-asset but **15m-weighted** (still want strong volume on 5m and all assets — just bias quality/size toward 15m).
- **Volume comes from removing redundant blocking gates** (volume-climax gate, OFI-divergence block, score<0.50 reject, the multi-gate cascade), NOT from loosening quality — the single `EDGE_MARGIN` gate becomes the quality control. Trade *every* window that clears the margin.
- **Remove the concurrency caps entirely.** `MAX_TOTAL_OPEN=6` / `MAX_OPEN_PER_ASSET=2` structurally forbid PBot/Bone-Reaper-level volume (they hold dozens of simultaneous positions). Remove (or set extremely high) both caps; control risk via **small per-trade size**, not a position-count ceiling. Capital preservation is enforced by sizing, not by refusing trades.
- **Expand the market universe** to every liquid Up/Down window the margin gate qualifies — more assets and (optionally) more window lengths — to maximise the number of margin-clearing opportunities.

### 4.5 Sizing
- Keep **shares-first** (already correct ✓). Small, consistent, **chunked** bets scaled by `edge_margin × balance`; optional DCA-into-winners; **capital preservation over perfect entries**; never all-in. Respect existing bankroll caps.

### 4.6 Keep / do not touch
Reversal-snipe archetype ✓ (proven cheap-entry edge), silent-fill reconciliation loop ✓, shares-first sizing ✓, warmup/quality gate ✓, pure `signal_core` structure (so a future Type-2 execution layer slots in).

## 5. Validate-FIRST protocol (the safety gate)
1. Implement the fair-value signal + `EDGE_MARGIN` gate as a **mode in `signal_core`** (alongside, not destroying, the current logic).
2. Run it through the **existing backtester** over historical 15m BTC/ETH (it already calibrates within 4.1¢): measure WR, expectancy (net of modeled fee/slippage), trade count, max drawdown, and WR-minus-breakeven margin.
3. **Promotion gate:** only wire it as the live paper primary if backtest shows **positive expectancy**, **WR exceeds entry-implied breakeven by ≥ ~3–5%**, and **trade count ≫ current**. If not, iterate the signal — do not deploy hope.
4. Promotion path in plain **demo → live** terms (no jargon):
   - **Demo (PC)** — what we run now: simulated fills on live market data. Proves the *logic*.
   - **Demo (Ireland VPS)** — identical demo logic, but running on the Hetzner Ireland box with *real* FIFO-queue latency and real execution timing, still zero capital. Proves the edge survives real speed. (Owner already has this VPS — available whenever.)
   - **Live** — real capital, small size first (~10%), then scale. This is also where the Type-2 execution layer + liquidity manufacturing (§10.5) come online.

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

---

## 10. Folded-in decisions (post-review) — comprehensive

### 10.1 Resolution source = Pyth (signal/oracle alignment)
PBot's public guidance states these Up/Down markets resolve on **Pyth** — which is exactly why ZiSi already streams Pyth. **The fair-value signal (§4.1) must read spot from the SAME Pyth feed/asset/timestamp that resolves the market**, so `S_0` (strike = window open) and `S_t` (live) are measured on the resolving oracle, not a mismatched exchange. This eliminates the "1¢ magic flip" basis risk. *(Owner to double-check the exact resolution text on a live market; if a market ever resolves on Binance/Coinbase instead, the signal source for that market must switch to match.)* Cross-check Pyth vs Binance/Coinbase on suspicious resolutions and pause that timeframe if they diverge.

### 10.2 Session-adaptive, profitable in EVERY session
Per PBot, behaviour differs sharply by **weekday vs weekend** and **Asian vs EU vs US** hours. The existing `core/shared/session_manager.py` (`TradingSessionManager`) is the foundation — **integrate it into the margin gate**, do not bypass it. Concretely:
- Each session carries its own tuned `EDGE_MARGIN`, sizing, and (optionally) which assets/windows are active.
- Goal: the bot **prints in every session** by demanding a *larger* edge in thin/low-edge sessions and trading more freely in high-edge sessions — never going dark.
- Day-of-week edges (a named PBot edge) are first-class inputs, sourced from the accumulating trade history + backtest.

### 10.3 Backtest realism = fees + slippage + adverse selection
The validate-first promotion gate (§5) is only trustworthy if the backtester models real costs, or it reproduces the "70% backtest → 52% live" trap. Harden the backtester to model: Polymarket spread/fees, slippage from book depth (not midpoint), fill probability, and a crude **adverse-selection** penalty (resting below ask disproportionately fills the losing side). Calibrate toward "backtest within ~3% of live."

### 10.4 Inventory-before-cutting + hunt & amplify the reversal snipe
- **Pre-implementation inventory (mandatory first plan task):** map every gate/indicator in the *current, post-sprints* signal path (`signal_core.py`, `updown_engine.py`, `cycle_manager.py`, `session_manager.py`, regime/edge layers) and tag each **KEEP / REDUNDANT / FIX**. Remove ONLY confirmed redundancy. Honor "don't remove what works."
- **Reversal-snipe hunt:** locate *every* reversal-snipe code path (RSI<20→UP / >80→DOWN cheap-discount entries), preserve them, and **give them more opportunities** (it's a proven high-edge archetype). Ensure the pivot does not starve them.

### 10.5 Liquidity manufacturing (Type-2 seed, live phase)
For live entries when the book is thin: GTC limit at mid+1 tick, or naked-sell the opposite side, or pre-split shares and rest GTC on the other side as a synthetic position. Build behind the same `signal_core`/execution boundary so it slots in for Type-2 without touching the signal. Not built in demo v1; designed-for now.

### 10.6 Build for Type-2 from day one
Everything additive and behind clean interfaces so the Type-2 market-making/laddering execution layer + the latency hot-path can slot in on the Ireland VPS later. The owner's explicit end-goal is Bone-Reaper-tier (Type-2) → live capital.

### 10.7 Baseline hygiene (before implementation)
Commit the other tooling's in-progress work (785 lines, tests green: `session_manager`, threaded persistence, dormancy gates, retrained LSTM) as the clean baseline; **git-ignore binary/runtime artifacts** (`core/ml/trained_model.pt`, `oi_history.json`, training metrics). Build the pivot additively on this baseline — clobber nothing.

### 10.8 Targets & expected results (honest, bounded)
- **Win rate:** directional core ~**55%**; **blended book ~58–62%** (lifted by reversal snipes + near-certainty ≥90¢ entries + session edges). 60%+ is a *blend* outcome, not a directional guarantee.
- **Volume:** from a few/day → **dozens/day** (every margin-clearing window across assets, caps removed).
- **Equity:** bleeding stops; **gentle, low-variance up-drift** once the backtest validates. The smooth diagonal is a *multi-week / hundreds-of-trades* result, not a single session.
- **Hard caveat:** demo proves the *brain*, not the *speed*; live PnL also needs the VPS execution stage (the FIFO-latency edge isn't capturable in PC demo). We deploy live only after demo-on-VPS confirms the edge survives real latency.

### 10.9 Risk model AFTER removing position caps (mandatory replacement control)
Removing `MAX_TOTAL_OPEN` / `MAX_OPEN_PER_ASSET` (§4.4) without a replacement would let the bot deploy the whole bankroll at once. Replace the *count* cap with **capital** controls:
- **Portfolio deployment cap:** total USD across all open positions ≤ ~**60–70% of bankroll** (tunable). Unlimited number of positions, bounded total capital.
- **Small per-trade size:** ~**1–3% of bankroll** each (chunked), scaled by edge margin. Many small bets = the low-variance grind.
- **Per-asset $ exposure soft-cap:** avoid ending up 100% one-directional on a single asset (concentration risk) — generous, not a volume choke.
- Net effect: **unlimited trade count, bounded capital** → maximum volume without all-in risk. This is PBot's "capital preservation beats perfect entries / bet in chunks."

### 10.10 Entry archetypes & decision cadence (the three shots)
The fair-value + margin engine naturally yields three entry archetypes — all gated by `EDGE_MARGIN`, all held to resolution:
1. **Moderate-divergence (early window):** spot has moved, contract lags → enter ~40–60¢ when `fair_prob − price ≥ margin`.
2. **Cheap reversal-snipe:** existing proven archetype (RSI<20→UP / >80→DOWN) at deep discount — preserved and given more opportunities (§10.4).
3. **Near-certainty (final ~60s):** when time-left is small and |S_t − S_0| is large, `fair_prob → ~0.95`; if the contract still trades <~0.90, buy it (pay ~90¢ to win ~10¢ at ~95% WR). High-frequency, ultra-low-variance — a primary contributor to the smooth equity line. Removes the old 80¢ ceiling for this case (gated by margin, not price).

**Decision cadence per window:** evaluate at **open**, at **+60s**, then **scan continuously**, with a dedicated **final-60s pass** for the near-certainty archetype. One window can fire more than one archetype over its life.
