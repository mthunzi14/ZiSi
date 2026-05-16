"""
Markets orchestrator.
Coordinates Kalshi execution alongside Polymarket for a given signal cycle.
Polymarket signals are processed by the existing _process_signal pipeline (unchanged).
This module handles the Kalshi side only.
"""
import logging
from typing import List, Dict

from signal_router import routing_decision

log = logging.getLogger("zisi.orchestrator")

# ── Per-cycle caps ─────────────────────────────────────────────────────────────
# Never trade more than this many Kalshi positions in a single 15-min cycle.
MAX_KALSHI_TRADES_PER_CYCLE = 50


def run_kalshi_for_cycle(
    signals: List[Dict],
    kalshi_fetcher,
    kalshi_matcher,
    kalshi_trader,
    kelly_fn,
    account_balance: float,
    hist_stats: Dict,
) -> Dict:
    """
    Run the full Kalshi leg of a trading cycle.

    Deduplication rules:
      1. Per-event-ticker: only ONE trade per Kalshi ticker per cycle.
         The ticker is only added to the dedup set AFTER a trade successfully
         executes — if a market is skipped by the price/volume filter, its
         ticker remains available for later signals that might match a
         different (valid) market with the same theme.
      2. Per-cycle cap: at most MAX_KALSHI_TRADES_PER_CYCLE positions per cycle.

    Returns summary dict with trade counts.
    """
    summary = {
        "kalshi_events_fetched": 0,
        "kalshi_matches": 0,
        "kalshi_trades": 0,
        "trades": [],
    }

    if not kalshi_fetcher.auth.is_configured:
        return summary

    # Fetch macro events once per cycle
    try:
        events = kalshi_fetcher.fetch_events(["politics", "economics", "sports", "financials", "crypto", "technology"])
        summary["kalshi_events_fetched"] = len(events)
        if not events:
            log.info("[KALSHI] No events returned from API")
            return summary
        log.info("[KALSHI] Fetched %d events", len(events))
    except Exception as e:
        log.warning("[KALSHI] Event fetch failed: %s", e)
        return summary

    # ── Deduplication state ────────────────────────────────────────────────────
    # IMPORTANT: only add a ticker here AFTER a trade successfully executes.
    # Do NOT pre-register tickers for markets that fail price/volume filters —
    # that would lock out valid markets from later signals in the same cycle.
    _traded_tickers: set = set()
    _cycle_trade_count = 0

    # Cross-cycle dedup: build set of tickers already held as open positions.
    # Prevents re-entering the same Kalshi market every cycle while it's still open.
    try:
        from kalshi.trader import _open_positions as _kalshi_positions
        _open_tickers: set = {p.get("ticker", "") for p in _kalshi_positions.values() if p.get("ticker")}
    except Exception:
        _open_tickers = set()

    for signal in signals:
        if _cycle_trade_count >= MAX_KALSHI_TRADES_PER_CYCLE:
            log.info(
                "[KALSHI-DEDUP] Cycle cap reached (%d trades) — skipping remaining signals",
                MAX_KALSHI_TRADES_PER_CYCLE,
            )
            break

        try:
            matches = kalshi_matcher.match_with_category_filter(signal, events)
            summary["kalshi_matches"] += len(matches)

            for match in matches:
                if _cycle_trade_count >= MAX_KALSHI_TRADES_PER_CYCLE:
                    break

                event = match["event"]
                confidence = match["confidence"]

                # ── Ticker dedup check (before any processing) ─────────────────
                ticker = (
                    event.get("ticker")
                    or event.get("id")
                    or str(hash(event.get("title", "")))
                )
                if ticker in _traded_tickers:
                    log.info(
                        "[KALSHI-DEDUP] Ticker already traded this cycle: %s",
                        event.get("title", "")[:60],
                    )
                    continue

                # Cross-cycle check: skip if this ticker already has an open position
                if ticker in _open_tickers:
                    log.info(
                        "[KALSHI-DEDUP] Open position already held for ticker %s — skipping re-entry",
                        ticker,
                    )
                    continue

                # ── Spread/volume pre-filter ───────────────────────────────────
                yes_ask = event.get("yes_ask", 0) or 0
                yes_bid = event.get("yes_bid", 0) or 0

                if yes_ask == 0 and yes_bid == 0:
                    # Kalshi bulk API doesn't return prices — use 0.50 for paper trading
                    normalized = 0.50
                    log.info(
                        "[KALSHI-PAPER] No price data — using 0.50 default for: %s",
                        event.get("title", "")[:60],
                    )
                else:
                    mid_price = (yes_ask + yes_bid) / 2 if (yes_ask and yes_bid) else (yes_ask or yes_bid)
                    normalized = mid_price / 100.0 if mid_price > 1 else mid_price
                    if normalized <= 0.05 or normalized >= 0.95:
                        log.info(
                            "[KALSHI-FILTER] Near-resolved market (mid=%.2f) skipped: %s",
                            normalized, event.get("title", "")[:60],
                        )
                        continue

                # ── GAP #1: Routing gate for Kalshi ───────────────────────────
                _kalshi_routing = routing_decision(
                    confidence=float(signal.get("confidence", 5)),
                    spread=0.03,         # Kalshi is order-book, no explicit spread param
                    has_polymarket=False,
                    has_kalshi=True,
                    kalshi_yes_price=normalized,
                )
                log.info(
                    "  [KALSHI-ROUTING] %s | conf=%.1f | %s",
                    _kalshi_routing["target"],
                    float(signal.get("confidence", 5)),
                    _kalshi_routing["reason"],
                )
                if _kalshi_routing["target"] == "SKIP":
                    log.info(
                        "  [KALSHI-ROUTING] SKIP — confidence below threshold for %s",
                        event.get("title", "")[:50],
                    )
                    continue  # ticker NOT added to dedup

                # ── Kelly position sizing ──────────────────────────────────────
                try:
                    sizing = kelly_fn(
                        account_balance=account_balance,
                        signal_strength=confidence,
                        symbol="MACRO",
                        historical_win_rate=hist_stats.get("win_rate", 0.50),
                        historical_avg_win=hist_stats.get("avg_win", 0.015),
                        historical_avg_loss=hist_stats.get("avg_loss", 0.015),
                    )
                    position_size = sizing.get("final_position", 1.0)
                except Exception as e:
                    log.warning("[KALSHI] Kelly sizing failed: %s", e)
                    position_size = account_balance * 0.01

                routing_mult = float(signal.get("kelly_multiplier", 1.0))
                if routing_mult != 1.0:
                    position_size = position_size * routing_mult
                    log.info(
                        "  [KALSHI-ROUTING] %s kelly×%.1f → $%.2f",
                        signal.get("signal_type", ""), routing_mult, position_size,
                    )

                # ── Execute trade ──────────────────────────────────────────────
                trade = kalshi_trader.execute_trade(
                    event=event,
                    signal=signal,
                    position_size=position_size,
                    confidence=confidence,
                )

                if trade:
                    # Only NOW register ticker in dedup set — trade was successful
                    _traded_tickers.add(ticker)
                    summary["kalshi_trades"] += 1
                    summary["trades"].append(trade)
                    _cycle_trade_count += 1
                    log.info(
                        "[KALSHI-EXEC] Trade %d/%d: %s | $%.2f | conf=%.2f",
                        _cycle_trade_count, MAX_KALSHI_TRADES_PER_CYCLE,
                        event.get("title", "")[:50], position_size, confidence,
                    )

        except Exception as e:
            log.warning("[KALSHI] Signal processing error: %s", e)
            continue

    log.info(
        "[KALSHI] Cycle complete — events: %d | matches: %d | trades: %d (cap: %d)",
        summary["kalshi_events_fetched"],
        summary["kalshi_matches"],
        summary["kalshi_trades"],
        MAX_KALSHI_TRADES_PER_CYCLE,
    )
    return summary
