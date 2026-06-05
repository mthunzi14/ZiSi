# ZiSi Session 13 — Gate Removal & Volume Maximization

**Date**: 2026-06-03 | **Balance**: $50 clean slate | **Target**: Emulate BoneReaper + PBot WR and compounding

---

## Asset Gate Policy

| Asset | Policy |
|---|---|
| BTC, ETH, SOL, XRP | Full gate relaxation — fire on every candle |
| DOGE | Strict gates remain |

---

## 9 Fixes

### Fix 1 — MACRO-GATE: DOGE only
`updown_engine.py` ~line 696 — wrap both `_macro_up >= 6` and `_macro_dn >= 6` blocks in `if self.asset == "DOGE":`

### Fix 2 — FV-MACRO penalty: DOGE only
`updown_engine.py` ~line 558 — wrap the `_fv_m_up >= 6` / `_fv_m_dn >= 6` and `>= 5` penalty blocks in `if self.asset == "DOGE":`

### Fix 3 — TREND-CONFIRM: DOGE only
`updown_engine.py` ~line 718 — change `if entry_source == "SIG" and len(klines) >= 4:` to `if entry_source == "SIG" and self.asset == "DOGE" and len(klines) >= 4:`

### Fix 4 — CHOPPY detection: DOGE only
`updown_engine.py` ~line 663 — wrap the `if self._choppy_candles > 0: return None` block and the `_flips >= 2: self._choppy_candles = 2; return None` block in `if self.asset == "DOGE":`. Keep slope history accumulation for all assets.

### Fix 5 — LOSS-BRAKE: threshold 3 → 8
`updown_engine.py` ~line 883 — change `if _full_loss_count >= 3:` to `if _full_loss_count >= 8:`

### Fix 6 — T-5s sizing: tiered by entry price
`cycle_manager.py` ~line 303 — replace flat 0.35× with:
- entry_price < 0.10 → 1.0× (near-certainty, 10-54× ROI, max size)
- entry_price < 0.25 → 0.70×
- entry_price >= 0.25 → 0.35×

### Fix 7 — CORROBORATION: neutral default for BTC/ETH/SOL/XRP
`updown_engine.py` ~line 611 — change `_corroboration_multiplier = 1.3 if _corroborated else 0.7` to `_corroboration_multiplier = 1.3 if _corroborated else (0.7 if self.asset == "DOGE" else 1.0)`

### Fix 8 — TIMING-GATE: 4.0 → 4.5 min
`updown_engine.py` ~line 495 — change `_elapsed_min > 4.0` to `_elapsed_min > 4.5`

### Fix 9 — VPS deploy + clean slate
```bash
cd /root/ZiSi && git pull origin main
cd presentation/dashboard/frontend && npm run build
cd /root/ZiSi && pm2 restart 3
python3 miscellaneous/clean_slate.py --force --balance 50
```

---

## Expected Results (Cross-Reference)

### Volume
- < 10 trades/hr = gate still firing → check gate_log for BTC/ETH MACRO-GATE blocks
- 10-30 trades/hr = normal
- > 30 trades/hr = excellent

### Win Rate Targets
| Type | Min | Target |
|---|---|---|
| BTC/ETH 15m LAT-ARB | 68% | 80-85% |
| BTC/ETH 5m LAT-ARB | 62% | 70-75% |
| SOL/XRP LAT-ARB | 58% | 65-70% |
| T-5s near-certainty (<10¢) | 90% | 96-99% |

### PNL per Session ($50 start)
| Scenario | PNL | Balance |
|---|---|---|
| Normal (no near-certainty) | +$3 to +$7 | $53-57 |
| Good (1-2 near-certainty) | +$15 to +$35 | $65-85 |
| Exceptional (3+ near-certainty) | +$50 to +$100 | $100-150 |
| Bad session | -$5 to -$10 | $40-45 |

### First 15 Minutes
- ≥ 2 LAT-ARB entries in first candle close = working
- ZERO MACRO-GATE or TREND-CONFIRM blocks for BTC/ETH/SOL/XRP in gate_log = working
- Telegram alert for first entry = working

### Bad gate_log entries (= fix not applied)
```
{"asset": "BTC", "gate": "MACRO-GATE"}     ← BAD
{"asset": "ETH", "gate": "TREND-CONFIRM"}  ← BAD
{"asset": "SOL", "gate": "MACRO-GATE"}     ← BAD
{"asset": "DOGE", "gate": "MACRO-GATE"}    ← OK (expected)
```

---

## Session 14 Queue — Sweeper (DO NOT IMPLEMENT NOW)
T-2s scan pass: Pyth move > 0.8%, implied_prob = 0.999, enter at 95-99¢, 0.5× sizing. Replicates Punisher's sweeper bot.

---

*Session 13 spec — ZiSi Bot 2026-06-03*
