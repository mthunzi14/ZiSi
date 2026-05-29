# ZiSi Extraterrestrial Research: Proposed Edges for Next-Level Performance

**Status: Proposals only — nothing in this document has been built or activated.**
All ideas below are future research directions. No code has been changed. Every proposal is grounded in ZiSi's real architecture (as of 2026-05-29: 16 closed trades, 14W/2L, +$107.82 on $100 start, 87.5% WR).

---

## Priority Summary Table

| # | Idea | Priority | Est. Complexity | Est. WR Impact | Est. Volume Impact |
|---|------|----------|-----------------|----------------|--------------------|
| 1 | Polymarket CLOB OBI as a secondary confirmation gate | **HIGH** | Low — gateway already exists | +2–5% WR | Neutral to slight drop |
| 2 | Cross-asset lead-lag refinement (calibrated ratios) | **HIGH** | Medium — CrossAssetPropagator exists | +3–6% WR on alts | +15–25% volume on SOL/XRP |
| 3 | Regime-adaptive RSI bands | **HIGH** | Low — signal_core.py params already parameterised | +2–4% WR | +10–20% volume in COMPRESSION |
| 4 | Funding-rate / perp-basis directional bias | **MED** | Medium — new data source required | +2–4% WR | Neutral |
| 5 | Pyth-vs-Polymarket latency edge | **HIGH** | Low — Pyth SSE already running | +3–6% WR on near-close entries | +5–10% volume |
| 6 | Intra-candle Pyth velocity signal (bonus idea 1) | **MED** | Low | +2–3% WR | +5–10% volume |
| 7 | Polymarket trade-flow sentiment (bonus idea 2) | **MED** | Medium | +2–4% WR | Neutral to slight increase |
| 8 | Adaptive candle-boundary pre-entry timing (bonus idea 3) | **HIGH** | Low–Medium | +3–5% WR | +10–15% volume |
| 9 | OFI depth profile beyond best-bid/ask (bonus idea 4) | **MED** | Medium | +2–3% WR | Neutral |

---

## Introduction

ZiSi is already performing at an elite level for a paper-trading bot operating in a niche prediction-market microstructure. The edges that remain are subtle, structural, and require thinking about the system holistically — not just adding more indicators. This document proposes nine directions for research and eventual implementation. Each is grounded in the actual data feeds, modules, and constraints of the live bot. None require third-party paid data unless explicitly noted.

The mandate is non-negotiable: every proposed change must maintain or increase trade volume, win-rate, and PnL. Where a proposal involves a new filter that might reduce raw trade count, it is paired with a compensating volume-recovery mechanism.

---

## 1. Polymarket CLOB Order Book Imbalance (Contract-Level OBI)

### Hypothesis / Edge

ZiSi currently uses Binance spot `bookTicker` OFI (taker buy/sell flow on the underlying crypto market) as its order-flow confirmation layer. This is an upstream signal — it reflects sentiment about BTC/ETH/SOL/XRP as assets. However, there is a second, distinct order-flow signal that ZiSi is not yet reading: the Polymarket CLOB itself.

The YES and NO tokens on a Polymarket Up/Down contract have their own live order books. When sophisticated market participants (market-makers, informed traders) believe a candle will close UP, they push bid pressure on the YES token specifically. This signal is orthogonal to spot OFI — it is the revealed-preference of traders who are already committed to the same bet ZiSi wants to make. A strong bid imbalance on YES while spot OFI is also positive produces a doubly-confirmed entry; a conflict (spot OFI positive, YES OBI negative) should at minimum reduce score or block the trade.

### Why It Fits ZiSi's Microstructure

The `ExtraterrestrialWSGateway` in `infrastructure/websocket/extraterrestrial_ws_gateway.py` already maintains a live L2 cache of `{"bid": float, "ask": float, "ts": float}` per token. The `_parse_clob_book` function in `updown_engine.py` already extracts best-bid and best-ask. What does not yet exist is a depth-weighted OBI calculation across the top N levels of the Polymarket order book.

The Polymarket CLOB WebSocket (`wss://ws-subscriptions-clob.polymarket.com/ws/market`) delivers full order-book snapshots and incremental updates. The gateway's `_process_message` method already parses `bids` and `asks` arrays — it currently only stores `bids[0]` and `asks[0]`. Extending this to accumulate top-3 or top-5 levels is minimal code change.

### Concrete Integration

- Extend `_process_message` in `ExtraterrestrialWSGateway` to compute a depth-weighted OBI across the top 5 bid/ask levels: `OBI = (sum_bid_qty - sum_ask_qty) / (sum_bid_qty + sum_ask_qty)`.
- Store this value in `l2_cache[token_id]["obi"]` alongside bid/ask.
- Expose a `get_obi(token_id)` method on the gateway.
- In `updown_engine.py`, after `_resolve_l2_prices` succeeds, read `polymarket_l2_gateway.get_obi(up_tk)`. For a UP trade, require this to be positive (or at least not strongly negative). For a DOWN trade, require `get_obi(dn_tk)` to be positive (since the NO/DOWN token OBI is the relevant signal).
- Wire this as a supplemental score boost in `generate_signal`: if contract-OBI confirms direction, add +0.04 to score; if it conflicts, subtract 0.03 or block below a score threshold.

### Data / Effort Required

- No new data source. The WebSocket is already connected and subscribed to token IDs.
- Code change: ~30 lines in `extraterrestrial_ws_gateway.py` (depth accumulation), ~15 lines in `updown_engine.py` (reading and applying it).
- Backtesting: Cannot backfill Polymarket OBI history easily, so this needs forward paper-testing over 50+ trades.

### Risk and Mandate Respect

The main risk is that the Polymarket order book on short-duration Up/Down contracts is thin and can be easily spoofed or mean-reverting (market-makers balance books). This means OBI should be a soft modifier (+/- score), not a hard block, except in extreme cases (OBI < -0.60 while going UP). At that threshold, a block is justified because it signals active selling against the position. Volume impact is roughly neutral — blocks will occur on genuinely ambiguous setups, not on high-conviction ones.

**Priority: HIGH**

---

## 2. Cross-Asset Lead-Lag Refinement with Calibrated Ratios

### Hypothesis / Edge

The existing `CrossAssetPropagator` (Module B) detects BTC velocity moves of ≥0.15% in ≤30s and flags cascade signals for ETH, SOL, XRP. This is structurally correct but uses a fixed 0.15% threshold and a conservative default lag of 5.0s when no empirical data is available. The opportunity is to make this smarter:

1. **Differentiated thresholds by magnitude**: A 0.15% BTC move is marginal; a 0.40%+ move in a single candle is a regime-level event. Above 0.40%, the cascade into SOL/XRP is near-certain and fast (~3–8s). Below 0.25%, the cascade is probabilistic and slower.
2. **Calibrated lead-lag ratios by asset pair**: XRP historically lags BTC by longer than SOL in microstructure studies. The empirical lag history already tracked in `_lag_history` can feed a tiered entry delay that pre-positions before the alt completes its move.
3. **Magnitude scaling**: If BTC moves 0.60% UP, the expected SOL move is not also 0.60% — it is typically 0.70–0.90% (beta > 1.0 in short windows). Knowing the expected alt magnitude helps predict whether the alt Up/Down contract will close in-the-money.

### Why It Fits ZiSi's Microstructure

ZiSi trades 5m and 15m Up/Down binary contracts. A BTC move at T=+5s into a 5m candle creates a compounding signal: BTC itself is likely generating a ZiSi UP signal on BTC/5m, AND the lagging alts are about to follow. The optimal play is to stack trades on BTC/5m (primary), then ETH/5m and SOL/5m (secondary, entered within the same candle). The existing module already generates cascade signals via `check_cascade()` but the `combined_confidence_boost` from the edge orchestrator does not yet directly trigger a secondary trade entry on a lagging asset — it only boosts the score of the asset currently being evaluated.

A refined approach: when a cascade signal exists for asset X, and ZiSi is evaluating asset X for the current candle, the cascade signal's `confidence` (currently computed as `correlation × velocity_factor`) should directly elevate the score into the HIGH tier if confidence > 0.80, bypassing the soft RSI/momentum gate and only requiring OFI non-contradiction.

### Concrete Integration

- In `decide_signal` (signal_core.py) or in `generate_signal` (updown_engine.py), after receiving cascade signals from `edge_orchestrator.get_trade_context()`, check if `cascade_signals` contains an entry for `self.asset`.
- If a high-confidence cascade exists (confidence > 0.78, btc_pct_move > 0.35%), override `score_base` to at minimum 0.75 and set direction to match the cascade direction.
- Add tiered BTC thresholds to `_DEFAULT_THRESHOLDS` in `cross_asset_propagator.py`:
  - BTC move 0.15–0.30%: min_corr = 0.75, window = 30s (current behavior)
  - BTC move 0.30–0.50%: min_corr = 0.65, window = 20s (faster, lower bar)
  - BTC move > 0.50%: min_corr = 0.55, window = 12s (near-certain cascade, very fast)
- Persist and publish the rolling beta (alt_pct_move / btc_pct_move) for each pair to help downstream sizing — a high-beta cascade deserves a larger position.

### Data / Effort Required

- No new data source needed.
- Code changes: ~50 lines in `cross_asset_propagator.py` (tiered thresholds + beta tracking), ~20 lines in `updown_engine.py` (cascade override path in `generate_signal`).
- The empirical lag history (already being tracked) self-calibrates over time — after 100 cascade observations per pair, the default 5.0s lag becomes well-informed.

### Risk and Mandate Respect

Risk: if the cascade threshold fires on a BTC move that quickly reverses (a wick-spike), the lagging alt trades will all be wrong simultaneously. The minimum `btc_pct_move > 0.35%` filter and minimum cascade `confidence > 0.78` should reduce this. Additionally, the existing Polymarket CLOB spread gate and price_gate_passes check will block entries if the market has already repriced the alt contract to reflect the cascade (i.e., the edge has been consumed). Volume impact: +15–25% on SOL/XRP because calibrated cascades will generate more high-confidence signals on alts during BTC trend candles.

**Priority: HIGH**

---

## 3. Regime-Adaptive RSI Bands

### Hypothesis / Edge

The `signal_core.py` module uses fixed RSI thresholds: up trigger at RSI > 60 (hard) or > 54 (soft), down at < 40 (hard) or < 46 (soft), reversal snipe at < 20 / > 80. These are good universal defaults. But the RSI's behavioral meaning changes completely with market regime:

- In **TRENDING** regime (strong directional momentum): RSI tends to stay elevated (60–80 range) for extended periods. The 60 threshold will generate valid UP signals, but the 80 reversal-snipe will fire prematurely — price may run to 85+ before reversing. Raising the reversal threshold to 85 in TRENDING preserves the reversal snipe edge without catching falling knives mid-trend.
- In **VOLATILE_CHAOS** regime: RSI oscillates wildly. A 60 reading in chaos is less informative than in trending markets. Tightening the hard trigger to RSI > 65 reduces noise-signal entries that look valid but are in choppy tape.
- In **COMPRESSION** regime (low ATR, narrow Bollinger bands): The market is coiling. RSI oscillates in a tighter band (40–60 typically). The soft-path OFI-confirmed entry (RSI > 54 + OFI > 0.45) is actually the primary signal here — tightening the soft threshold to RSI > 52 with OFI > 0.35 generates more entries in compressions that are about to break out.
- In **MEAN_REVERTING**: The standard 60/40 bands are appropriate. No change needed.

The key insight is that ZiSi's regime detector already classifies into four states and writes to `regime_status.json` every cycle. The `signal_core.py` function already accepts a `params` dict for threshold overrides. The backtester in `tools/backtest/simulator.py` already sweeps these parameters. Regime-adaptive bands are a natural extension of existing infrastructure.

### Concrete Integration

- Define a `REGIME_RSI_PARAMS` dict in `signal_core.py` (or in `config.py`):
  ```python
  REGIME_RSI_PARAMS = {
      "TRENDING":       {"rsi_up": 60.0, "reversal_hi": 85.0, "reversal_lo": 15.0, ...},
      "MEAN_REVERTING": DEFAULT_SIGNAL_PARAMS,  # unchanged
      "VOLATILE_CHAOS": {"rsi_up": 65.0, "rsi_dn": 35.0, "reversal_hi": 82.0, ...},
      "COMPRESSION":    {"rsi_up_soft": 52.0, "ofi_confirm_up": 0.35, ...},
  }
  ```
- In `updown_engine.generate_signal`, before calling `decide_signal`, read the current regime from `get_regime_mode()` and look up the corresponding params dict. Pass it as `params=regime_params` to `decide_signal`.
- The backtester can validate each regime-specific parameter set against historical candle data split by regime classification.

### Data / Effort Required

- No new data source.
- Code change: ~40 lines (define dicts, update the `generate_signal` call site).
- Backtesting: Run the backtester separately on candles where each regime was active. Requires labelling the historical candle set with regime at time of trade. This is the main effort — approximately 2–4 hours of backtesting work.

### Risk and Mandate Respect

Risk: over-fitting regime params to historical data. Mitigation: use conservative adjustments (±5 RSI units at most) and require at least 20 historical trades per regime bucket before trusting the calibrated numbers. Volume impact: COMPRESSION-regime trades are currently underrepresented because the soft-path OFI threshold is strict. Lowering it to 0.35 during compression should unlock 10–20% more entries from compressions that break out cleanly. This respects the mandate directly.

**Priority: HIGH**

---

## 4. Funding-Rate / Perpetual Basis as a Directional Bias

### Hypothesis / Edge

Binance perpetual futures funding rates are the market's consensus on short-term directional bias. When funding is strongly positive (longs paying shorts), the crowd is heavily long and a downward mean-reversion is statistically more likely over the next 1–4 hours. When funding is strongly negative (shorts paying longs), the crowd is heavily short and upward mean-reversion is likely.

For a 5m Up/Down binary, this is a slow signal (funding is reset every 8 hours on Binance), but it serves as a regime-level prior that should bias the asymmetry of signal acceptance. Specifically:

- If funding rate > +0.08%: DOWN signals should get a small score boost (+0.03); UP signals need a higher RSI threshold (tighten to 63 instead of 60) to filter out crowded-long entries.
- If funding rate < -0.06%: UP signals get a boost; DOWN signals need tighter thresholds.
- If funding is near zero (−0.02 to +0.02): no adjustment (the default).

Additionally, the perp-spot basis (perpetual price vs spot price) provides a real-time premium/discount signal that updates tick-by-tick. A positive basis means perp is expensive vs spot (longs are paying up), which is a bearish micro-signal. This updates at Binance WebSocket speed, making it a fast confirmation input for ZiSi's 5m entries.

### Concrete Integration

- Add a Binance perp funding rate REST poll to `infrastructure/exchange/data_fetcher.py` (or a new lightweight module). The Binance API endpoint is `GET /fapi/v1/premiumIndex` — one call per 8 hours per symbol is sufficient for the funding rate, with a separate `bookTicker` stream for `BTCUSDT` perp vs spot basis.
- Extend `BinanceWebSocketIngest` or create a sibling `PerpWebSocketIngest` that subscribes to `btcusdt@bookTicker` on `stream.binancefuture.com` and maintains a `_perp_premium` dict (perp_price - spot_price) per symbol.
- In `generate_signal`, after computing `score`, read the funding bias and apply the small directional adjustment.
- Add a `funding_bias` key to the signal dict for Telegram notification context.

### Data / Effort Required

- New data source: Binance Futures WebSocket (`wss://fstream.binance.com`). Free, no API key required for public streams.
- Effort: ~80 lines for a `PerpWebSocketIngest` class (mirrors existing `BinanceWebSocketIngest` structure), ~15 lines in `generate_signal`.
- Key risk: funding rates are slow-moving and can remain extreme for days without triggering the expected mean-reversion within a single 5m candle. This signal should be a mild bias, not a block. The perp basis is faster but also noisier.

### Risk and Mandate Respect

Risk: over-weighting a slow macro signal in a fast-moving short-duration binary contract context. The mitigant is small adjustment magnitudes (±0.03 score, ±3 RSI points). Volume impact is roughly neutral — positive funding makes UP trades harder to enter but DOWN trades easier. The net change in entry volume should be near zero.

**Priority: MED**

---

## 5. Pyth-vs-Polymarket Latency Edge (Near-Close Exploitation)

### Hypothesis / Edge

This is arguably the sharpest structural edge available to ZiSi right now, given existing infrastructure.

The Pyth Hermes SSE stream provides sub-100ms price updates. The Polymarket CLOB reprices more slowly — market-makers update their quotes every 1–3 seconds, sometimes slower near candle boundaries when liquidity thins. There is a predictable latency gap:

1. With ~10–20 seconds remaining in a 5m candle, the Pyth oracle knows the current BTC/ETH/SOL/XRP price with 99%+ confidence.
2. The Polymarket YES/NO contract prices may still reflect the price state from 2–5 seconds ago, especially if the asset moved sharply in the last few seconds of the candle.
3. If Pyth shows BTC at $68,250 and the opening price of the candle was $67,900, BTC is currently UP ~0.51%. The YES token on the BTC/5m UP contract should be pricing near 90c+ — but if market-makers are slow, it might still show 80c.
4. This creates an arbitrage-like entry: buy YES at 80c when it "should" be at 90c, collecting a theoretical 10c edge before expiry in 15 seconds.

ZiSi's `updown_engine.py` already overwrites `closes[-1]` with the Pyth price in `generate_signal`. But the engine currently runs at candle-open (candle-boundary entry). A second, separate near-close scan — triggered 15–20 seconds before candle expiry — could exploit this latency gap.

### Concrete Integration

- This is an entirely new signal path, not a modification of the existing one.
- In `core/engine/cycle_manager.py` (which controls the candle-boundary timing), add a secondary trigger at T-15s before candle close for active markets (where ZiSi has not yet entered and the candle is near resolution).
- At T-15s, compute the implied Pyth-based outcome: if `(pyth_price - candle_open_price) / candle_open_price > 0.002` (i.e., +0.2% move UP since candle open), the UP contract is very likely to resolve YES.
- Fetch the live Polymarket price for UP token. If it is < (implied_outcome_probability - 0.06), enter — this is a >6c mispricing with <15s to resolution.
- Entry size should be smaller (half normal Kelly) due to the binary, time-compressed nature of near-close entries.
- A hard cutoff: do not enter if less than 8 seconds remain (too close to resolution, slippage risk).

### Data / Effort Required

- No new data source: Pyth SSE is running, Polymarket CLOB WebSocket is running.
- Effort: ~60 lines in `cycle_manager.py` or a new `near_close_scanner.py`, plus modifications to the `prefetch_upcoming_market` logic to track candle open prices.
- The candle open price is already available from Binance klines (first element of the previous candle's close = current candle's open, or `klines[-1][1]` for the open price of the current candle).

### Risk and Mandate Respect

Risk: near-close entries are inherently riskier — there is no time for a favorable move to develop. The market must already be committed at entry. The mitigant is requiring a 6c+ implied discount (strict value threshold), and capping size at 50% of normal Kelly. These entries cannot be stops managed — they resolve within seconds. Volume impact: +5–10% additional trades from near-close setups, with a projected WR of 70%+ because only deeply discounted contracts with near-certain Pyth-implied outcomes qualify. This directly respects the mandate.

**Priority: HIGH**

---

## 6. Intra-Candle Pyth Velocity Signal (Bonus Idea 1)

### Hypothesis / Edge

The Pyth oracle delivers price updates 10–20 times per second. Currently ZiSi reads only the current Pyth price at signal generation time (candle boundary entry). The velocity — how fast price moved in the first 30 seconds of the new candle — is a powerful leading indicator for whether the binary contract will resolve in-the-money.

If BTC gains 0.15% in the first 30 seconds of a 5m candle, the candle has structural momentum. If it gains 0.30%+ in the first 30 seconds, the candle is almost certainly going to close UP (unless a sharp reversal occurs, which is statistically rare in a trending regime). This intra-candle velocity is orthogonal to the RSI (which looks back 14 periods) and to the OFI (which measures order-book flow, not realized price movement).

### Concrete Integration

- In `pyth_oracle_service.py`, augment `GLOBAL_ORACLE_CACHE` with a rolling 60-second price history per symbol: `{"price": float, "timestamp": float, "history_30s": deque(maxlen=30)}`.
- Compute `velocity_30s = (latest_price - price_30s_ago) / price_30s_ago * 100` on each update.
- In `generate_signal`, read this velocity and apply: if `velocity_30s > 0.12%` and direction == UP, add +0.05 to score; if `velocity_30s > 0.25%`, add +0.10 to score (strong momentum confirmation).
- If velocity contradicts direction (e.g., velocity is -0.15% but signal says UP), treat as a soft block or score penalty of -0.08.

### Data / Effort Required

- No new data source.
- Code change: ~25 lines in `pyth_oracle_service.py` + ~15 lines in `generate_signal`.
- This is probably the highest effort-to-reward ratio idea on this list.

**Priority: MED**

---

## 7. Polymarket Trade-Flow Sentiment Analysis (Bonus Idea 2)

### Hypothesis / Edge

The Polymarket CLOB WebSocket emits real-time trade executions in addition to order book updates. A stream of recent trades on the YES/NO tokens reveals not just pricing but transaction flow — how many contracts are being bought vs sold, at what sizes. Large buy orders on YES at below-market prices suggest informed conviction. A series of small sells against a static bid suggest market-maker rebalancing (noise).

Trade-flow sentiment is the prediction-market equivalent of Binance taker buy/sell volume (which ZiSi already uses via OFI). The key difference: Polymarket trade flow reflects the views of participants who are specifically betting on the same 5m binary contract ZiSi is evaluating. This is higher-quality directional information than the underlying spot OFI.

### Concrete Integration

- The Polymarket CLOB WebSocket (`wss://ws-subscriptions-clob.polymarket.com/ws/market`) also provides a `trades` channel. The `ExtraterrestrialWSGateway` subscription message already sends `custom_feature_enabled: True` — it may already receive trade events, but `_process_message` currently only parses order book updates.
- Extend `_process_message` to handle trade events: when `event_type == "trade"` or the message contains a `side` and `size` field, accumulate a rolling taker-buy/taker-sell volume counter per token.
- Compute a Polymarket-OFI: `pm_ofi = (taker_buy_size - taker_sell_size) / (taker_buy_size + taker_sell_size)` over the last 60 seconds.
- In `generate_signal`, read `polymarket_l2_gateway.get_trade_ofi(up_tk)`. Use as a mild score modifier.

### Data / Effort Required

- No new data source — trades may already be delivered by the existing WebSocket subscription.
- Effort: ~40 lines to parse and accumulate trade events, ~10 lines in `generate_signal`.
- Forward paper-testing required for at least 30 trades to validate the signal quality, since historical Polymarket trade data is not easily accessible.

**Priority: MED**

---

## 8. Adaptive Candle-Boundary Pre-Entry Timing (Bonus Idea 3)

### Hypothesis / Edge

ZiSi enters trades in the first ~15 seconds of a new candle. This timing is correct for most setups, but there are two distinct entry quality scenarios that current architecture does not distinguish:

**Scenario A — Strong pre-candle momentum**: In the final 10 seconds before the new candle opens, Pyth is already showing a strong directional move. The first millisecond of the new candle should be entered as fast as possible — any delay costs fair-value.

**Scenario B — Ambiguous pre-candle state**: The asset has been oscillating. Waiting 15–25 seconds into the new candle for OFI to settle and Polymarket pricing to stabilize produces a better entry price and higher-quality signal.

Currently ZiSi uses a fixed 4-attempt loop with 1.0s then 1.5s sleeps to resolve L2 prices (`_resolve_l2_prices`). This is a one-size-fits-all approach. An adaptive timing model would:

1. For high-confidence pre-signals (Pyth velocity > 0.10% in the last 15s before candle close), reduce the sleep between L2 attempts to 0.5s — aggressive entry.
2. For neutral pre-signals, extend the sleep to 2.0s — wait for market stabilization.
3. For contradictory pre-signals, wait 30s into the candle before even attempting an entry — give the market time to commit.

### Concrete Integration

- In `updown_engine.py`, before calling `_resolve_l2_prices`, compute a `pre_signal_quality` metric from the last 15s of Pyth velocity.
- Pass `urgency="high"/"normal"/"low"` into `_resolve_l2_prices` and adjust the sleep durations accordingly.
- The `prefetch_upcoming_market` method (already runs 20s before candle open) can cache the pre-signal quality, making it available at candle open with zero latency.

### Data / Effort Required

- No new data source.
- Code change: ~30 lines across `updown_engine.py` and `pyth_oracle_service.py`.
- Low risk — the change is to timing behavior, not signal logic.

**Priority: HIGH**

---

## 9. OFI Depth Profile Beyond Best-Bid/Ask (Bonus Idea 4)

### Hypothesis / Edge

The current Binance spot OFI implementation in `spot_websocket_ingest.py` uses the `bookTicker` stream, which provides only the single best bid and ask (top-1 level). This is fast but shallow. A more informative signal is the depth-weighted OFI across the top 5–10 levels of the Binance order book.

Best-bid/ask OFI can be spoofed — a large order placed at best-bid to signal buying interest can be cancelled in milliseconds. Top-10 depth OFI is harder to spoof because it requires large capital across multiple price levels. When the top-10 Binance bid-side has 3x the quantity of the ask-side, that is a structural imbalance that predicts short-term upward price pressure with higher reliability than the top-1 snapshot.

### Concrete Integration

- Subscribe to `btcusdt@depth5` (or `@depth10`) on the Binance WebSocket in addition to `@bookTicker`. This provides the top 5 bid/ask levels with quantities in real-time.
- In `BinanceWebSocketIngest._process_tick`, handle depth messages: compute `depth_ofi = (sum_top5_bid_qty - sum_top5_ask_qty) / (sum_top5_bid_qty + sum_top5_ask_qty)`.
- Store separately as `depth_ofi_value` in `_market_books`.
- Expose via `get_current_depth_ofi(symbol)`.
- In `generate_signal`, blend the existing EMA-smoothed OFI with depth OFI: `combined_ofi = 0.6 * ema_ofi + 0.4 * depth_ofi`. Use this as the OFI value passed to `decide_signal`.

### Data / Effort Required

- No new data source — Binance depth stream is the same WebSocket, just a different stream name.
- Note: `@depth5` sends updates at 1000ms intervals (not tick-level), so it is inherently slightly stale vs `@bookTicker`. This is acceptable for 5m candle-boundary entries.
- Code change: ~50 lines in `spot_websocket_ingest.py`.
- Potential concern: more data to parse means slightly higher CPU load on the ingest daemon. At the trade frequency ZiSi operates (8 assets × 2 timeframes = 16 signals per candle boundary), this is negligible.

**Priority: MED**

---

## Cross-Cutting Architecture Notes

### On Not Breaking What Is Working

ZiSi's 87.5% win rate is exceptional. The two losses were likely unavoidable — structural market noise, not signal failures. Any new idea proposed above should be introduced incrementally:

1. First, add the new signal as a **logging-only observer** for 20+ trades. Log what it would have done (boosted/blocked/unchanged) without actually modifying behavior.
2. Validate: does the new signal correlate with outcomes? If it would have boosted winning trades more than losing ones, the edge is real.
3. Then activate with a small multiplier. Never make it a hard block until 50+ trade validation.

This is especially important for Proposals 1 (contract OBI) and 7 (trade-flow sentiment) because Polymarket's thin book means these signals may have high noise on low-liquidity contracts.

### On the Reversal Snipe (XRP/15m 6¢ trade)

The +733% return from the reversal snipe (RSI < 20, entry at 6¢) is the highest-alpha trade in ZiSi's history. Proposal 3 (regime-adaptive RSI bands) should explicitly protect this path:

- In TRENDING regime, the reversal snipe threshold should be tightened (RSI < 15 to avoid catching a mid-trend dip), not loosened.
- In VOLATILE_CHAOS regime, the reversal snipe deserves the most protection — deep discounts in chaotic markets are where the highest-EV entries live, because market-makers over-price uncertainty.

### On the Score Architecture

ZiSi's score system runs from ~0.50 (minimum viable) to ~0.95 (maximum confidence). The proposals above collectively introduce several +0.03 to +0.10 score modifiers. It is important that these do not compound into artificial 1.0 scores that bypass the price gate. The existing `min(1.0, score + boost)` ceiling is already in place, but the intent matters: a 1.0 score should mean "near-certain outcome given all available signals," not "five small boosts accumulated."

Recommendation: introduce a **signal count gate** — do not allow more than three independent boosts to contribute to a single trade's score. If four or more would apply, take the three highest-confidence ones. This prevents spurious over-confidence from signal accumulation.

---

## Summary of Top 3 Proposed Edges

1. **Pyth-vs-Polymarket Latency Edge (Proposal 5)** is the most structurally compelling because it exploits a real, measurable asymmetry between two data sources that ZiSi already has running. Sub-second Pyth data confirms outcome with near-certainty 10–20 seconds before expiry; slow Polymarket repricing creates a 5–10c discount window. This is as close to free money as prediction-market trading gets. The risk (no time to manage the trade) is mitigated by the strict value-discount entry threshold.

2. **Regime-Adaptive RSI Bands (Proposal 3)** is the highest leverage-per-line-of-code change because `signal_core.py` is already parameterised and the backtester already sweeps parameters. Tightening bands in VOLATILE_CHAOS and opening the soft path in COMPRESSION directly translates to fewer noise trades and more clean breakout captures, respectively. This is the least risky proposal on the list.

3. **Polymarket CLOB OBI Confirmation (Proposal 1)** completes the signal stack by introducing a feedback loop from the prediction market itself. Spot OFI (Binance) + Contract OBI (Polymarket YES token) + Pyth velocity constitutes a three-source signal confirmation that is structurally superior to any single-source approach. The infrastructure investment is minimal (~45 lines of code), and the risk of false blocks is low if applied as a soft score modifier rather than a hard gate.
