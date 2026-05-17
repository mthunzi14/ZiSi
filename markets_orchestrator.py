"""
Markets orchestrator.
Coordinates Kalshi execution alongside Polymarket for a given signal cycle.
Polymarket signals are processed by the existing _process_signal pipeline (unchanged).
This module handles the Kalshi side only.

Session 2 upgrades:
  - Cross-platform mispricing detection (Poly vs Kalshi same event)
  - Anti-correlation portfolio cap (max 2 correlated crypto-directional bets)
  - Kalshi Up/Down crypto scanner integration
  - Economic calendar event boost
  - Sports/politics blackout with news-based override
"""
import logging
from typing import List, Dict, Optional

from signal_router import routing_decision

log = logging.getLogger("zisi.orchestrator")

# ── Per-cycle caps ─────────────────────────────────────────────────────────────
MAX_KALSHI_TRADES_PER_CYCLE = 30  # was 50 — reduced for quality over quantity

# Anti-correlation cap: max 2 crypto-directional bets across BOTH platforms
MAX_CORRELATED_CRYPTO_POSITIONS = 2

# Sports/politics: only trade if matching news found within last 45 minutes
POLITICS_NEWS_WINDOW_SECS = 45 * 60

# Categories always blocked (zero edge for ZiSi)
_HARD_BLOCKED_CATEGORIES = {"SPORTS"}


def _count_open_crypto_directional(kalshi_trader=None) -> int:
    """Count open BTC/ETH directional positions across Kalshi (+ proxy Poly check)."""
    count = 0
    try:
        import json, os
        pf = os.path.join(os.path.dirname(__file__), "positions_state.json")
        if not os.path.exists(pf):
            return 0
        data = json.loads(open(pf, encoding="utf-8").read())
        for pos in data.get("active", []):
            title = str(pos.get("event_title", "")).upper()
            cat   = str(pos.get("_category", "")).upper()
            if cat == "CRYPTO" or any(t in title for t in ("BITCOIN", "ETHEREUM", "BTC", "ETH", "CRYPTO")):
                count += 1
    except Exception:
        pass
    return count


def _check_cross_platform_mispricing(signal: Dict, kalshi_events: List[Dict]) -> List[Dict]:
    """
    Detect mispricing between Polymarket and Kalshi for the same event.
    If Poly prices an event at 65% but Kalshi at 55%, trade the cheaper side.
    Returns list of enriched matches with arbitrage boost applied.
    """
    poly_conf = float(signal.get("confidence", 5) or 5)
    if poly_conf > 1:
        poly_conf_normalized = poly_conf / 10.0
    else:
        poly_conf_normalized = poly_conf

    enriched = []
    for ev in kalshi_events:
        yes_price_raw = ev.get("yes_ask") or ev.get("yes_bid") or 0
        kalshi_price  = float(yes_price_raw) / 100.0 if float(yes_price_raw) > 1 else float(yes_price_raw)
        if kalshi_price <= 0:
            continue

        # Compare Poly's implied probability vs Kalshi's price
        poly_implied = poly_conf_normalized
        gap = abs(poly_implied - kalshi_price)

        if gap >= 0.10 and kalshi_price < poly_implied:
            # Kalshi is underpricing — arbitrage opportunity
            boost = 1.0 + min(0.30, gap)   # up to 30% boost for 30-point mispricing
            enriched_ev = dict(ev)
            enriched_ev["_mispricing_boost"] = round(boost, 3)
            enriched_ev["_mispricing_gap"]   = round(gap, 3)
            log.info(
                "[ARB] Mispricing detected: poly=%.2f kalshi=%.2f gap=%.2f → boost=%.2f× | %s",
                poly_implied, kalshi_price, gap, boost, ev.get("title", "")[:50],
            )
            enriched.append(enriched_ev)
        else:
            ev_copy = dict(ev)
            ev_copy["_mispricing_boost"] = 1.0
            enriched.append(ev_copy)

    return enriched


def _is_news_backed_politics(signal: Dict, max_age_secs: int = POLITICS_NEWS_WINDOW_SECS) -> bool:
    """Return True if signal has recent news backing (for POLITICS category gate)."""
    news_ts = signal.get("_news_timestamp") or signal.get("fetched_at")
    if not news_ts:
        return False
    try:
        import time
        from datetime import datetime, timezone
        if isinstance(news_ts, str):
            news_dt = datetime.fromisoformat(news_ts.replace("Z", "+00:00"))
            age = (datetime.now(timezone.utc) - news_dt).total_seconds()
        else:
            age = time.time() - float(news_ts)
        return age <= max_age_secs
    except Exception:
        return False


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

    # Auth auto-refresh (proactive — prevents silent token expiry)
    try:
        kalshi_fetcher.auth.refresh_if_needed()
    except Exception:
        pass

    # Anti-correlation check: don't pile onto crypto-directional bets
    _crypto_positions = _count_open_crypto_directional(kalshi_trader)
    if _crypto_positions >= MAX_CORRELATED_CRYPTO_POSITIONS:
        log.info(
            "[KALSHI-CORR] %d/%d correlated crypto positions already open — skipping crypto Kalshi trades this cycle",
            _crypto_positions, MAX_CORRELATED_CRYPTO_POSITIONS,
        )

    # Fetch macro events once per cycle
    try:
        events = kalshi_fetcher.fetch_events(["politics", "economics", "financials", "crypto", "technology"])
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

    # Cross-cycle dedup: build set of tickers already held as open positions,
    # plus tickers closed within the past 2 hours (post-close cooldown).
    try:
        from kalshi.trader import _open_positions as _kalshi_positions, get_recently_closed_tickers as _get_cooldown
        _open_tickers: set = {p.get("ticker", "") for p in _kalshi_positions.values() if p.get("ticker")}
        _open_tickers |= _get_cooldown()  # adds recently-closed tickers to the block set
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
            # Cross-platform mispricing enrichment (adds _mispricing_boost to events)
            enriched_events = _check_cross_platform_mispricing(signal, events)

            matches = kalshi_matcher.match_with_category_filter(signal, enriched_events)
            summary["kalshi_matches"] += len(matches)

            for match in matches:
                if _cycle_trade_count >= MAX_KALSHI_TRADES_PER_CYCLE:
                    break

                event = match["event"]
                confidence = match["confidence"]
                event_cat  = event.get("_category", "OTHER")

                # ── Hard category blocks ───────────────────────────────────────
                if event_cat in _HARD_BLOCKED_CATEGORIES:
                    log.debug("[KALSHI-BLOCK] %s category hard-blocked: %s",
                              event_cat, event.get("title", "")[:50])
                    continue

                # ── Politics: only trade with recent news backing ─────────────
                if event_cat == "POLITICS" and not _is_news_backed_politics(signal):
                    log.debug("[KALSHI-BLOCK] POLITICS skipped — no recent news backing: %s",
                              event.get("title", "")[:50])
                    continue

                # ── Crypto anti-correlation cap ────────────────────────────────
                if event_cat == "CRYPTO" and _crypto_positions >= MAX_CORRELATED_CRYPTO_POSITIONS:
                    log.debug("[KALSHI-CORR] Skipping CRYPTO event (corr cap): %s",
                              event.get("title", "")[:50])
                    continue

                # ── Economic calendar boost ────────────────────────────────────
                _econ_boost = 1.0
                try:
                    from data_sources.economic_calendar import get_kalshi_event_boost
                    _econ_boost = get_kalshi_event_boost(event.get("title", ""))
                    if _econ_boost > 1.0:
                        confidence = min(0.99, confidence * _econ_boost)
                except Exception:
                    pass

                # ── Mispricing boost from cross-platform arbitrage ─────────────
                _mis_boost = float(event.get("_mispricing_boost", 1.0))
                if _mis_boost > 1.0:
                    confidence = min(0.99, confidence * _mis_boost)

                # ── Ticker dedup check (before any processing) ─────────────────
                ticker = (
                    event.get("ticker")
                    or event.get("id")
                    or str(hash(event.get("title", "")))
                )
                if ticker in _traded_tickers:
                    log.debug(
                        "[KALSHI-DEDUP] Ticker already traded this cycle: %s",
                        event.get("title", "")[:60],
                    )
                    continue

                # Cross-cycle check: skip if this ticker already has an open position
                if ticker in _open_tickers:
                    log.debug(
                        "[KALSHI-DEDUP] Open position already held for ticker %s — skipping re-entry",
                        ticker,
                    )
                    continue

                # ── Expiry gate: must be a same-day market ────────────────────
                hours_to_close = event.get("_hours_to_close")
                if hours_to_close is None:
                    from kalshi.fetcher import _parse_close_time
                    _, hours_to_close = _parse_close_time(event)
                if hours_to_close is None or hours_to_close > 24 or hours_to_close < 0.25:
                    log.debug(
                        "[KALSHI-EXPIRY] Skipping non-same-day market (hours=%.1f): %s",
                        hours_to_close or 999, event.get("title", "")[:50],
                    )
                    continue

                # ── Price gate: REQUIRE real price data — no 0.50 fallback ────
                yes_ask = event.get("yes_ask", 0) or 0
                yes_bid = event.get("yes_bid", 0) or 0

                if yes_ask == 0 and yes_bid == 0:
                    # No price in bulk API response — try fetching live from /markets/{ticker}
                    # This is expensive (one API call per market), so only for promising matches.
                    log.debug(
                        "[KALSHI-PRICE] No price in bulk response for %s — skipping (no fallback)",
                        event.get("title", "")[:60],
                    )
                    continue  # Skip — we do NOT use 0.50 default price
                else:
                    mid_price = (yes_ask + yes_bid) / 2 if (yes_ask and yes_bid) else (yes_ask or yes_bid)
                    normalized = mid_price / 100.0 if mid_price > 1 else mid_price
                    if normalized <= 0.10 or normalized >= 0.90:
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


def run_kalshi_updown_for_cycle(
    kalshi_auth,
    kalshi_fetcher,
    kalshi_trader,
    get_balance_fn,
) -> int:
    """
    Run the Kalshi crypto Up/Down direction scanner as part of a trading cycle.
    Integrated into the main cycle from main.py via this wrapper.
    Returns number of trades placed.
    """
    if not kalshi_auth.is_configured:
        return 0
    try:
        from kalshi.updown_scanner import run_kalshi_updown_cycle
        trades = run_kalshi_updown_cycle(
            auth=kalshi_auth,
            fetcher_instance=kalshi_fetcher,
            kalshi_trader_instance=kalshi_trader,
            get_balance_fn=get_balance_fn,
        )
        if trades:
            log.info("[KALSHI-UPDOWN] Cycle placed %d crypto direction trades", trades)
        return trades
    except Exception as exc:
        log.warning("[KALSHI-UPDOWN] Cycle error: %s", exc)
        return 0
