# ZiSi Complete Rebuild Plan — "Back to Prime, Then Past It"
**Date:** 2026-06-09  ·  **Author:** Claude (Opus 4.8) for Mthunzi  ·  **Scope:** Option 4 — full rebuild incl. FV directional-edge model
**Status:** PLAN — awaiting go/no-go before code edits

---

## 0. Objective & Success Criteria

**Goal:** FV becomes the primary profit engine again (June-5 form), every trade type fires with volume, wrong-side losses are eliminated, and the blended result *exceeds* the June-5 peak ($700 from $100) with far less give-back.

**Success criteria (measurable):**
- FV fires on **every candle it has a real edge** across BTC/ETH/SOL/XRP/DOGE × {5m, 15m} — hundreds of FV trades/day.
- All 7 types active: FV, NCS, SWEEP, LAT-ARB, REV-SNIPE, REV-STREAK, SIG.
- **Blended win rate 75–82%**, strongly positive expectancy (per-type targets in §2).
- No repeat of the "always wrong-side SIG" pattern (regime-aware direction).
- Sizing scales with **directional confidence**, mentor-style.

**Honest WR framing:** NCS/SWEEP/LAT-ARB are genuinely 90%+ (near-certainty, carry the trade *count*). FV/SIG/REV are 55–70% directional but **carry the profit** via payoff (45–55¢ → 99¢ = +80–120%/win). Literal 90%-on-every-type is not real on 5m crypto; the conviction-weighted blend is how the mentors actually win.

---

## 1. The Mentor Blueprint (what we are emulating)

Three archetypes from `TRADES.txt.docx`, covering the whole price curve:

| Mentor | Archetype | Signature | ZiSi type it maps to |
|---|---|---|---|
| **PBot-6 (Peabod)** | ATM directional edge | lives at 46–54¢, sizes BIG when read is strong | **FV** (primary) |
| **RITB123 (Rith)** | confidence barbell | huge size on cheap (32–46¢) *and* locks (87–98¢) | FV + REV-SNIPE + NCS |
| **Bonereaper** | momentum→resolution + cross-asset | rides 55–99¢ into close; fires same direction across BTC/ETH/SOL/XRP/DOGE on macro moves | LAT-ARB + cross-asset propagation + NCS |

**Three laws extracted from their tickets:**
1. **Size by confidence, not by price.** A high-confidence 47¢ bet earns the same big stake as a 90¢ lock. (PBot-6 $194@54¢, Rith $2,295@46.4¢.)
2. **Propagate macro moves across correlated assets.** One confirmed direction → trade all 5 coins that way. (Bonereaper all-Down across 5 assets @ 5:30–5:45.)
3. **Hold to resolution; the hold is the edge, not the fill.** (Both Bonereaper & Punisher.) — ZiSi already does this for short-TF. Keep it.

---

## 2. Target role / WR / volume per trade type (post-rebuild)

| Type | Role | Band | WR target | Volume | Sizing driver |
|---|---|---|---|---|---|
| **FV** ⭐ | PRIMARY directional engine | 35–82¢ w/ edge | 62–70% | **High** | FV edge-score (confidence) |
| **NCS** | near-cert base | 90–99¢, T-45s | 93–97% | High | $0.50-target, capped |
| **SWEEP** | locked base | 75–99¢, T-2s | 95–98% | Mod–High | small fixed % |
| **LAT-ARB** | momentum→resolution | 55–85¢ late | 70–80% | Mod | discount × confidence |
| **REV-SNIPE** | cheap-value barbell | <35¢ | 38–46% (pays 150–450%) | Low–Mod | small, edge-scaled |
| **REV-STREAK** | 1h trend fade | counter-trend after 4 | 60–70% | Low | quarter-Kelly |
| **SIG** | momentum in confirmed TREND only | 35–65¢ | 56–63% | Mod | edge-score, halved in chop |

---

## 3. Root-cause recap (from the code-verified diagnosis)

| # | Problem | Root cause (file:line) |
|---|---|---|
| R1 | **FV silent** | 5 stacked FV gates wall 44–65¢: `main.py:413` (UPPER-DEAD), `:423` (ATM-CORE), `:432` (COIN-FLIP), `:261` (LOSS-COOLDOWN); `updown_engine.py:646` (ARCH-GATE). `d2b06aa` killed the last path. |
| R2 | **FV weak at ATM even when allowed** | `fair_value.py:28-38` CDF is **driftless** → ~coin-flip at 50¢, no directional conviction. |
| R3 | **SIG always wrong-side** | trailing-momentum + trend-gate forces agreement with finished move (`signal_core.py:182`, `updown_engine.py:861`); fires at candle boundary on stale data; `apply_regime()` fade is a **no-op** (`regime_filter.py:48`). |
| R4 | **Sizing inverted** | deep-contrarian <40¢ gets **30%** bankroll, near-cert gets ×0.25 (`updown_engine.py:1498`). Backwards vs mentors. |
| R5 | **Alts vetoed instead of propagated** | `LEADER-GUARD` blocks alts when leaders move (`main.py:319`). Mentor does the opposite. |
| R6 | **June-9 "restore" backwards** | `a6a018f` un-gated LOSING SIG, left WINNING FV gated. |
| R7 | **Volume choke** | 15s global ENTRY-COOLDOWN drops candle-boundary bursts (`main.py:582`); 15M-CORR-CAP & dedups block concurrent TFs. |

Resolution is **real Binance candle close** (`trader.py:864-923`) — not a broken RNG. Confirmed.

---

## 4. Phased Implementation

> Order = safest/highest-leverage first. Phases 1, 3, 4, 5, 6 are low-risk and can go live quickly. **Phase 2 (FV model) is gated behind a backtest** before VPS.

### Phase 1 — Un-gate FV (restore the engine) — *low risk*
Remove/relax the FV-blocking gates so FV can trade its 35–82¢ sweet spot:
- `app/main.py:413` **FV-UPPER-DEAD (56–65¢)** → remove.
- `app/main.py:423` **FV-ATM-CORE (44–56¢)** → remove.
- `app/main.py:432` **FV-COIN-FLIP** → replace with a single **edge-score guard**: allow ATM only if FV edge-score ≥ threshold (see Phase 2); until Phase 2 lands, allow with current score gate relaxed.
- `app/main.py:261-289` **FV-LOSS-COOLDOWN** → reduce 10 min → 0 (delete) or 2 min max; one loss must not silence an asset.
- `core/engine/updown_engine.py:646-658` **FV-ARCH-GATE** → remove regime restriction (allow all regimes), OR invert so it only *boosts* required-edge in chop rather than blocking.
- `app/main.py:398` **SAME-DIR-GATE** → keep but raise trigger to ≥4 same-dir & score<0.75 (less aggressive).
- Revert FV `_min_edge` toward June-5 values (`updown_engine.py:673-699`): 0.05 in 25–50¢ zone, 0.12 mid-50s, 0.10 high. Remove macro penalty stacking up to 0.25 → cap at +0.08.

### Phase 2 — FV directional edge (the model rewrite) — *gated by backtest*
Make the fair-value probability **directional**, not driftless. In `core/engine/fair_value.py`:
- Current: `fp_up = N( (spot-strike)/strike / (σ·√(remaining)) )` with drift = 0.
- New: add a **drift/confidence term** `μ̂` blended from signals ZiSi already computes:
  - Pyth/Binance intra-candle `pct_move` (cycle_manager.py:221)
  - CVD + order-book imbalance OBI (cycle_manager.py:316-356)
  - OFI / RSI-momentum (signal_core.py)
  - `fp_up = N( ((spot-strike)/strike + μ̂·(remaining)) / (σ·√(remaining)) )`
- Emit an **edge-score / confidence** (0–1) = function of |edge| and signal agreement. This drives sizing (Phase 4) and the ATM guard (Phase 1).
- **Backtest gate:** run the new model against stored history (`miscellaneous/backtester.py`, `archive/local_vps_trades_archive.json`) and the mentor windows. Must show ≥60% directional WR at 44–56¢ before it ships to VPS.

### Phase 3 — Confidence-tiered sizing (fix the inverted ladder) — *low risk*
In `core/engine/updown_engine.py:1477-1559` + `core/risk/position_sizer.py`:
- Replace price-based ladder with **confidence-based** bankroll fraction keyed off FV edge-score:
  - edge-score 0.10–0.15 → 2–4% bankroll
  - 0.15–0.25 → 5–10%
  - >0.25 or prob>0.85 → 15–25%
  - near-cert prob>0.95 → NCS reverse-engineered sizing (unchanged)
- **Remove the 30% deep-contrarian default** (`:1498`); cheap (<35¢) entries sized small (1–3%) unless edge-score is high.
- Keep half-Kelly as the ceiling; cap any single bet at 25% bankroll.

### Phase 4 — SIG fix (regime-aware direction) — *low risk*
- `core/engine/regime_filter.py:48-53`: make `apply_regime()` **real**:
  - TREND regime → SIG follows momentum (current behavior).
  - MEAN_REVERTING / COMPRESSION → SIG **fades** the finished move (flip direction) OR abstains.
  - VOLATILE_CHAOS → abstain.
- `updown_engine.py:861-875` trend-gate: only enforce agreement in confirmed TREND.
- Re-add a light SIG mid-range guard so cheap contrarian SIG can't fire on weak score (replaces the deleted dead zone): block SIG 35–57¢ if score<0.70.

### Phase 5 — Cross-asset propagation + universe — *medium risk*
- `app/main.py:319-356` **LEADER-GUARD**: invert intent — when BOTH BTC & ETH confirm a direction with strong edge, **propagate** that direction as a signal boost to SOL/XRP/DOGE (don't veto same-direction alt entries). Keep a veto only for *contradiction* with very high leader confidence.
- Use existing `core/engine/cross_asset_propagator.py`.
- **Add DOGE** to the active asset loops in `app/main.py` (it's already in the leader-guard set).
- Relax `15M-CORR-CAP` (`main.py:449`) 2→3 and same-asset dedup to allow concurrent 5m+15m on one asset (mentor behavior).

### Phase 6 — Volume & BTC weighting — *low risk*
- `app/main.py:582` ENTRY-COOLDOWN 15s → 3–5s, and **queue** candle-boundary bursts instead of dropping them (don't lose the candle).
- FV-RATE (`main.py:252`) 3/60s → 6/60s.
- **BTC > ETH:** give BTC a +1 priority tier / slightly lower edge threshold in `signal_router.py` / sizing weight; ETH secondary; SOL/XRP/DOGE tertiary scalp size.

### Phase 7 — Guardrails that don't strangle
- Replace blanket dead-zones with **per-edge** guards (only block when edge-score is genuinely weak).
- Keep: real-Binance resolution, hold-to-resolution for short-TF, NCS momentum guard, atomic state writes.
- Keep balance reset to **$50, manual only** (no auto session-reset/archiving — user-controlled).

---

## 5. Validation & Rollout

1. **Unit tests** updated for every changed gate/sizing path (`tests/`).
2. **Backtest** Phase 2 model + Phase 3 sizing against `archive/local_vps_trades_archive.json` and the 3 mentor windows; confirm WR/EV targets in §2.
3. **Stage to VPS** in order: Phase 1 → 3 → 4 → 6 (low-risk, immediate), then Phase 5, then Phase 2 (after backtest passes).
4. **Clean slate $50** before live observation:
   `cd /root/ZiSi && git pull && npm run build --prefix presentation/dashboard/frontend && python3 miscellaneous/clean_slate.py --force --balance 50 && pkill -f main.py && pm2 restart zisi-dashboard`
5. Observe 24–48h: confirm trades on every tab, FV dominance, blended WR.

---

## 6. Expected Outcome

- **Trades on every tab** — yes: each type un-gated and routed to its mentor-mapped band.
- **FV is the engine again** — un-gated + real directional edge + confidence sizing = PBot-6's edge, mechanized.
- **Volume:** 5 assets × {5m,15m} ≈ 60+ candle-opportunities/hr; FV on the fraction with edge + NCS/SWEEP/LAT-ARB near every close → **hundreds of trades/day**.
- **Beats June 5:** same upside engine, plus a high-WR near-certainty base, plus genuine ATM edge, minus the wrong-side SIG bleed → higher peak, far less give-back.

---

## 7. Open decisions locked (veto anytime)
1. FV gets a real directional-edge model (Phase 2). ✅
2. SIG stays alive, fires only in confirmed TREND, fades/abstains in chop (Phase 4). ✅
3. Sizing driven by confidence/edge-score, not price (Phase 3). ✅
4. WR = conviction-weighted blend (75–82%), not literal 90%-per-type. ✅
