# ZiSi v2 — Wire Fair-Value Signal + VPS Demo (Plan 2 of 2) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement the CODE tasks (1–4) task-by-task. Task 5 is a VPS ops runbook executed over SSH, not local TDD.

**Goal:** Wire the validated fair-value signal into the live engine as the primary entry (reversal-snipe + existing archetypes preserved), instrument it for honest measurement, then deploy ZiSi to the Hetzner Ireland VPS in DEMO to measure our real edge + lead against live Polymarket quotes.

**Architecture:** Additive injection in `generate_signal` after the real L2 market fetch: a fair-value/margin entry that fills at the REAL live quote (never at fair value). A JSONL logger records every fair-value decision + resolution for true WR. A lead probe logs Binance-move→Polymarket-reprice timing. Deployment uses the existing `docs/VPS_MIGRATION.md` (PM2 24/7, dashboard bound to localhost, SSH-tunnel access, no domain).

**Tech Stack:** Python 3.13 (`unittest` — NOT pytest), Node (dashboard), PM2/nginx on Ubuntu 22.04. Repo root `C:\Users\mthun\Downloads\ZiSi_Bot`.

**Spec:** `docs/superpowers/specs/2026-05-31-zisi-v2-type1-pivot-design.md`
**Prereq:** Plan 1 (`fair_value.py`, validated) on branch `feat/zisi-v2-fairvalue` merged to main.

**⚠️ Honesty invariant (applies to all tasks):** demo entries are recorded at the **real live L2 quote + slippage**, NEVER at the fair-value price. Violating this recreates the fake-80%-WR illusion.

---

## Task 1: Fair-value live entry helper

**Files:**
- Modify: `core/engine/updown_engine.py` (add method `_fair_value_entry`)
- Test: `tests/test_fair_value_entry.py`

Pure-ish helper: given the current klines + live quotes, compute fair_prob and the margin decision. Returns the same dict shape as `decide_value_entry` plus the spot inputs (for logging).

- [ ] **Step 1: Write the failing test**

`tests/test_fair_value_entry.py`:
```python
import unittest
from core.engine.updown_engine import UpDownEngine


class _FakeState:
    def get_open_positions(self): return []


class TestFairValueEntry(unittest.TestCase):
    def _engine(self):
        return UpDownEngine("BTC", "15m", _FakeState(), lambda *a, **k: None)

    def _klines(self, last_open, last_close):
        # 20 flat warmup candles + current candle [open_time,o,h,l,c,vol,...]
        ks = [[i * 900000, 100.0, 100.1, 99.9, 100.0, 50.0] for i in range(20)]
        ks.append([20 * 900000, last_open, max(last_open, last_close) + 0.1,
                   min(last_open, last_close) - 0.1, last_close, 50.0])
        return ks

    def test_no_edge_returns_none(self):
        eng = self._engine()
        # spot == open -> fair ~0.5; quote 0.50 -> no edge
        r = eng._fair_value_entry(self._klines(100.0, 100.0), spot=100.0,
                                  up_price=0.50, dn_price=0.50, elapsed_min=1.0)
        self.assertIsNone(r["direction"])

    def test_underpriced_up_fires(self):
        eng = self._engine()
        # spot well above open -> fair high; UP quote cheap 0.50 -> UP edge
        r = eng._fair_value_entry(self._klines(100.0, 100.0), spot=100.6,
                                  up_price=0.50, dn_price=0.50, elapsed_min=7.5)
        self.assertEqual(r["direction"], "UP")
        self.assertGreater(r["edge"], 0.0)
        self.assertIn(r["archetype"], ("moderate", "near_certainty"))
```

- [ ] **Step 2: Run it, verify FAIL** — `python -m unittest tests.test_fair_value_entry -v` → AttributeError: no `_fair_value_entry`.

- [ ] **Step 3: Add the method to `UpDownEngine` in `core/engine/updown_engine.py`** (place it just above `generate_signal`):
```python
    def _fair_value_entry(self, klines, spot, up_price, dn_price, elapsed_min):
        """Fair-value (spot-distance) margin decision at the REAL live quotes.
        Returns decide_value_entry's dict plus fp_up/sigma for logging."""
        from core.engine.fair_value import fair_prob_up, decide_value_entry
        try:
            s_0 = float(klines[-1][1])          # current window open = strike
        except (IndexError, ValueError, TypeError):
            return {"direction": None, "edge": 0.0, "archetype": None, "fp_up": 0.5, "sigma_frac": 0.0}
        total_min = float(int(self.timeframe.rstrip("m")))
        # sigma_frac = ATR(14) / price from kline high/low/close (indices 2,3,4)
        trs = []
        for i in range(max(1, len(klines) - 14), len(klines)):
            h, l, pc = float(klines[i][2]), float(klines[i][3]), float(klines[i - 1][4])
            trs.append(max(h - l, abs(h - pc), abs(l - pc)))
        atr = (sum(trs) / len(trs)) if trs else 0.0
        sigma_frac = (atr / s_0) if s_0 else 0.01
        fp_up = fair_prob_up(spot, s_0, sigma_frac, elapsed_min, total_min)
        dec = decide_value_entry(fp_up, up_price, dn_price, elapsed_min, total_min)
        dec["fp_up"] = round(fp_up, 4)
        dec["sigma_frac"] = round(sigma_frac, 6)
        return dec
```

- [ ] **Step 4: Run it, verify PASS** — `python -m unittest tests.test_fair_value_entry -v` (2 pass).

- [ ] **Step 5: Commit**
```bash
git add core/engine/updown_engine.py tests/test_fair_value_entry.py
git commit -m "feat(engine): fair-value entry helper (margin decision at real L2 quote)"
```

---

## Task 2: Inject fair-value as the primary entry (additive)

**Files:**
- Modify: `core/engine/updown_engine.py` (`generate_signal`, just after the market fetch / `up_price`,`dn_price` lines)
- Modify: `config.py` (add `FAIR_VALUE_MODE` flag)

- [ ] **Step 1: Add the flag to `config.py`** (module-level, near the other ZiSi params):
```python
# Fair-value (Type-1) primary entry. When True, a spot-distance mispricing that
# clears EDGE_MARGIN fires an entry at the real L2 quote BEFORE the momentum cascade.
FAIR_VALUE_MODE: bool = True
```

- [ ] **Step 2: Inject in `generate_signal`** — immediately AFTER the lines that set `up_price = market["up_price"]` / `dn_price = market["dn_price"]` / `is_dual_eligible = ...` and BEFORE the `if raw_dir is None:` dual block, insert:
```python
        # ── Fair-value primary entry (additive). Reversal-snipe keeps priority;
        #    fair-value fills at the REAL L2 quote (never at fair value). ──
        try:
            from config import FAIR_VALUE_MODE
        except Exception:
            FAIR_VALUE_MODE = False
        if FAIR_VALUE_MODE and not _dec["is_reversal"]:
            now_ts = datetime.now(timezone.utc).timestamp()
            candle_open_ts = float(klines[-1][0]) / 1000.0
            elapsed_min = max(0.0, (now_ts - candle_open_ts) / 60.0)
            fv = self._fair_value_entry(klines, closes[-1], up_price, dn_price, elapsed_min)
            if fv["direction"] is not None:
                raw_dir = fv["direction"]
                # score from edge: 0.55 baseline + edge (cap influence), near-certainty bonus
                score_base = min(0.90, 0.55 + min(0.30, fv["edge"]) +
                                 (0.05 if fv["archetype"] == "near_certainty" else 0.0))
                self._last_fair_value = fv  # stash for logging (Task 3)
                log.info("[FAIR-VALUE] %s/%s %s | fp=%.3f quote=%.3f edge=%.3f (%s)",
                         self.asset, self.timeframe, raw_dir, fv["fp_up"],
                         up_price if raw_dir == "UP" else dn_price, fv["edge"], fv["archetype"])
```
This sets `raw_dir`/`score_base` so the existing cascade (regime, OBI, AI veto, return) runs unchanged and the entry fills at `up_price`/`dn_price` (real quote). When fair-value does not fire, behavior is exactly as before.

- [ ] **Step 3: Verify the engine still imports + existing tests pass**
Run: `python -m py_compile core/engine/updown_engine.py config.py`
Run: `python -m unittest tests.test_updown_engine tests.test_fair_value_entry tests.test_signal_core -v`
Expected: all PASS (injection is additive; existing tests unaffected since they don't drive the live market path).

- [ ] **Step 4: Commit**
```bash
git add core/engine/updown_engine.py config.py
git commit -m "feat(engine): fair-value primary entry injection (flagged, additive, real-quote fills)"
```

---

## Task 3: Honest fair-value trade logger

**Files:**
- Create: `infrastructure/state/fair_value_log.py`
- Test: `tests/test_fair_value_log.py`
- Modify: `core/engine/updown_engine.py` (call the logger when a fair-value entry is taken)

- [ ] **Step 1: Write the failing test**

`tests/test_fair_value_log.py`:
```python
import json
import os
import tempfile
import unittest
from infrastructure.state.fair_value_log import log_fair_value_entry


class TestFairValueLog(unittest.TestCase):
    def test_appends_jsonl_row(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "fv.jsonl")
            log_fair_value_entry(
                {"asset": "BTC", "timeframe": "15m", "direction": "UP",
                 "fp_up": 0.62, "quote": 0.50, "edge": 0.12, "archetype": "moderate",
                 "entry_ts": 1780000000.0}, path=path)
            with open(path, encoding="utf-8") as fh:
                rows = [json.loads(l) for l in fh if l.strip()]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["direction"], "UP")
            self.assertAlmostEqual(rows[0]["quote"], 0.50)

    def test_two_entries_append(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "fv.jsonl")
            for _ in range(2):
                log_fair_value_entry({"asset": "ETH", "quote": 0.4}, path=path)
            with open(path, encoding="utf-8") as fh:
                self.assertEqual(sum(1 for l in fh if l.strip()), 2)
```

- [ ] **Step 2: Run it, verify FAIL** (ModuleNotFoundError).

- [ ] **Step 3: Create `infrastructure/state/fair_value_log.py`**:
```python
"""Append-only JSONL log of fair-value entries for HONEST win-rate measurement.
Records the real quote we filled at + the fair-value edge we believed we had, so
realized WR can be compared against the backtest's lag-conditional expectation."""
import json
import os
from typing import Optional

_DEFAULT = os.path.join(os.path.dirname(__file__), "fair_value_trades.jsonl")


def log_fair_value_entry(row: dict, path: Optional[str] = None) -> None:
    """Append one fair-value entry record as a JSON line. Never raises into the engine."""
    target = path or _DEFAULT
    try:
        with open(target, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    except Exception:
        pass
```

- [ ] **Step 4: Run it, verify PASS** (2 tests).

- [ ] **Step 5: Wire the logger into the fair-value entry** — in `generate_signal`, inside the `if fv["direction"] is not None:` block from Task 2 (after the `log.info`), add:
```python
                try:
                    from infrastructure.state.fair_value_log import log_fair_value_entry
                    log_fair_value_entry({
                        "asset": self.asset, "timeframe": self.timeframe, "direction": raw_dir,
                        "fp_up": fv["fp_up"], "quote": (up_price if raw_dir == "UP" else dn_price),
                        "edge": fv["edge"], "archetype": fv["archetype"],
                        "elapsed_min": round(elapsed_min, 2), "entry_ts": now_ts,
                    })
                except Exception:
                    pass
```

- [ ] **Step 6: Verify + commit**
Run: `python -m py_compile core/engine/updown_engine.py && python -m unittest tests.test_fair_value_log -v`
```bash
git add infrastructure/state/fair_value_log.py tests/test_fair_value_log.py core/engine/updown_engine.py
git commit -m "feat(obs): honest fair-value entry logger (real quote + believed edge)"
```

---

## Task 4: Lead probe (Binance-move → Polymarket-reprice timing)

**Files:**
- Create: `infrastructure/observability/lead_probe.py`
- Test: `tests/test_lead_probe.py`

Pure timing math now (the live tap is wired in Plan 2.1 / deployment); this task delivers the tested measurement primitive.

- [ ] **Step 1: Write the failing test**

`tests/test_lead_probe.py`:
```python
import unittest
from infrastructure.observability.lead_probe import reprice_lag_seconds


class TestLeadProbe(unittest.TestCase):
    def test_lag_is_reprice_minus_move(self):
        # Binance moved at t=10.0; Polymarket repriced past threshold at t=12.5 -> 2.5s lag
        self.assertAlmostEqual(reprice_lag_seconds(binance_move_ts=10.0,
                                                   poly_reprice_ts=12.5), 2.5, places=4)

    def test_negative_means_we_are_behind(self):
        # Polymarket repriced BEFORE we saw the move -> negative lag (we have no lead)
        self.assertLess(reprice_lag_seconds(binance_move_ts=10.0, poly_reprice_ts=9.7), 0)

    def test_none_when_missing(self):
        self.assertIsNone(reprice_lag_seconds(binance_move_ts=None, poly_reprice_ts=12.5))
        self.assertIsNone(reprice_lag_seconds(binance_move_ts=10.0, poly_reprice_ts=None))
```

- [ ] **Step 2: Run it, verify FAIL** (ModuleNotFoundError).

- [ ] **Step 3: Create `infrastructure/observability/lead_probe.py`**:
```python
"""Measure ZiSi's potential LEAD: how long after a Binance spot move does the
Polymarket book reprice? Positive lag = the market is slow = we have a window to
act. Negative = the book moved first = we have no lead (Type-2 territory)."""
from typing import Optional


def reprice_lag_seconds(binance_move_ts: Optional[float],
                        poly_reprice_ts: Optional[float]) -> Optional[float]:
    """Seconds between a Binance spot move and the Polymarket book repricing past it.
    Positive = our potential lead; negative = we are behind; None if either ts missing."""
    if binance_move_ts is None or poly_reprice_ts is None:
        return None
    return poly_reprice_ts - binance_move_ts
```

- [ ] **Step 4: Run it, verify PASS** (3 tests).

- [ ] **Step 5: Commit**
```bash
git add infrastructure/observability/lead_probe.py tests/test_lead_probe.py
git commit -m "feat(obs): lead-probe reprice-lag primitive"
```

---

## Task 5: VPS deployment runbook (executed on the Hetzner Ireland box over SSH)

> Not local TDD — these are ordered ops steps run ON the VPS. The detailed reference is `docs/VPS_MIGRATION.md`. Dashboard bound to localhost; access via SSH tunnel; no domain.

- [ ] **Step 1: Merge Plan 1 + Plan 2 code to `main` and push** (from the dev machine)
```bash
git checkout main && git merge feat/zisi-v2-fairvalue && python -m unittest discover -s tests -p "test_*.py"
git push origin main
```
Expected: full suite OK, push succeeds.

- [ ] **Step 2: Provision + harden the VPS** — follow `docs/VPS_MIGRATION.md` §1–2 (Hetzner CPX11 Ubuntu 22.04, non-root sudo user, SSH-key auth, `ufw` allowing only OpenSSH; **do NOT open port 5000**).

- [ ] **Step 3: Install runtime + clone** — `docs/VPS_MIGRATION.md` §3: Node 18+, Python 3.10+ venv, `git clone`, `pip install -r requirements.txt && pip install vaderSentiment`, `npm --prefix presentation/dashboard/frontend install`, `npm --prefix presentation/dashboard/backend install`, build the frontend.
Verify the LSTM + deps load: `python -c "from core.ml.ai_injector import injector; print('ml ok')"`.

- [ ] **Step 4: Secrets** — create `.env` from `.env.example` on the VPS, `chmod 600 .env`. Demo needs no trading keys (paper). Confirm Polymarket/Binance/Pyth public endpoints reachable: `curl -s https://api.binance.com/api/v3/ping` returns `{}`.

- [ ] **Step 5: Bind the dashboard to localhost** — in `presentation/dashboard/backend/server.js`, ensure `app.listen(PORT, '127.0.0.1', ...)` (loopback only). If it currently binds `0.0.0.0`, change the host arg to `'127.0.0.1'`. Commit this change.

- [ ] **Step 6: Add `update.sh` at repo root** (one-command redeploy):
```bash
#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
git pull --ff-only
npm --prefix presentation/dashboard/frontend run build
pm2 restart zisi
echo "ZiSi updated + restarted."
```
`chmod +x update.sh`, commit.

- [ ] **Step 7: PM2 ecosystem** — create `ecosystem.config.js` (manages the Node supervisor, which itself spawns the Python bot — do NOT double-manage the bot) per `docs/VPS_MIGRATION.md` §5; `pm2 start ecosystem.config.js`, `pm2 install pm2-logrotate`, `pm2 startup` + `pm2 save`.

- [ ] **Step 8: Clean-slate the demo to $100** — `python miscellaneous/clean_slate.py` on the VPS → $100, 0 positions, fresh history.

- [ ] **Step 9: Decommission the laptop daemon** — disable the Windows scheduled task `Zisi-Bot Auto Start` on the laptop so only the VPS runs (single source of truth). Confirm the laptop bot is stopped.

- [ ] **Step 10: Verify live** — `pm2 logs zisi` shows the engine booting, WS connecting, and `[FAIR-VALUE]` entries appearing. Open the dashboard via SSH tunnel from your machine: `ssh -L 5000:localhost:5000 <user>@<vps-ip>` then browse `http://localhost:5000`.

- [ ] **Step 11: Let it run + read the honest result** — after a session, analyze `infrastructure/state/fair_value_trades.jsonl`: realized WR vs the believed edge, and the lead-probe lags. Compare WR to the backtest's ≥1-min-lag ~62.5%. **GO** (WR clears entry-breakeven by a healthy margin) → scale toward live. **NO-GO** (WR ≈ entry price ≈ coin-flip) → we lack the lead → pivot to Type-2 execution (resting orders / colocation).

---

## Self-Review (completed)

**Spec coverage:** §4.1/4.2 fair-value primary + margin → Tasks 1–2; §4.6 reversal-snipe preserved → Task 2 (`not _dec["is_reversal"]` keeps reversal priority); honesty invariant (real-quote fills) → Tasks 2–3; §5 demo→VPS path → Task 5; lead measurement (the decisive unknown) → Task 4 + Step 11; localhost+SSH tunnel, PM2, no domain, decommission laptop, update.sh → Task 5. **Deferred (correctly):** caps removal + capital-risk-model and hold-to-resolution exit changes (those are live-behavior changes to make AFTER the demo proves the edge — kept out so the demo measures the signal cleanly first); Type-2 execution layer (only if NO-GO).

**Placeholder scan:** code steps contain complete code; tests have real assertions; the only non-code task (5) references concrete commands + the existing handbook. No TBD/TODO.

**Type consistency:** `_fair_value_entry(...)` returns `{direction, edge, archetype, fp_up, sigma_frac}` consistently across Tasks 1–3; `log_fair_value_entry(row, path=None)` and `reprice_lag_seconds(binance_move_ts, poly_reprice_ts)` signatures stable; `FAIR_VALUE_MODE` flag name consistent.

## Notes
- Exits remain the current engine's (TARGET_HIT/MARKET_EXPIRED) for this demo — we are measuring ENTRY edge first; the hold-to-resolution exit change is a follow-up once the entry edge is confirmed, to avoid changing two variables at once.
- The lead probe's live tap (subscribing the timing taps to the two gateways) is a small follow-up wiring once deployed; Task 4 ships the tested primitive so the math is correct.
