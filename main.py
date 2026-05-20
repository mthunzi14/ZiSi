"""
main.py - ZiSi Bot — Polymarket Up/Down asyncio engine
6 independent asyncio tasks: BTC-5m, BTC-15m, ETH-5m, SOL-5m, XRP-5m, reconciliation.
"""
import asyncio
import logging
import sys
from datetime import datetime, timezone

from config import load_config, log_config_startup, ASSETS, TIMEFRAMES
try:
    from logger import setup_file_logging
except ImportError:
    def setup_file_logging(level="INFO"): pass
from state_manager import (
    initialize_state, initialize_runtime_tracking, update_runtime_tracking,
    update_heartbeat, get_current_balance,
)
from updown_engine import UpDownEngine
from risk_manager import entry_price_gate, check_exposure_caps, check_daily_loss_halt
from reconciliation import reconciliation_loop
from regime_filter import time_gate_open
import state_manager

log = logging.getLogger("zisi.main")

# ── Global state ──────────────────────────────────────────────────────────────
_engines: dict[str, UpDownEngine] = {}
_starting_balance: float = 0.0


def _try_telegram(msg: str) -> None:
    try:
        from telegram_bot import send_alert
        send_alert(msg)
    except Exception:
        pass


# ── Candle boundary alignment ─────────────────────────────────────────────────

async def _sleep_to_next_candle(interval_minutes: int) -> None:
    """Sleep until the next candle boundary."""
    interval_secs = interval_minutes * 60
    now = datetime.now(timezone.utc).timestamp()
    next_boundary = (int(now) // interval_secs + 1) * interval_secs
    sleep_secs = next_boundary - now + 1.5
    if sleep_secs > 0:
        await asyncio.sleep(sleep_secs)


# ── Per-asset loop ────────────────────────────────────────────────────────────

async def asset_loop(asset: str, timeframe: str, offset_seconds: int = 0) -> None:
    """Independent asyncio task for one asset/timeframe pair."""
    global _starting_balance

    if offset_seconds > 0:
        await asyncio.sleep(offset_seconds)

    interval_minutes = int(timeframe.rstrip("m"))
    engine = _engines[f"{asset}/{timeframe}"]

    log.info("[MAIN] %s/%s task started — aligning to next candle boundary", asset, timeframe)
    await _sleep_to_next_candle(interval_minutes)

    while True:
        try:
            if not time_gate_open():
                await _sleep_to_next_candle(interval_minutes)
                continue

            current_balance = get_current_balance()
            if check_daily_loss_halt(_starting_balance, current_balance):
                log.warning("[MAIN] Daily loss halt active — all trading paused")
                _try_telegram("HALT ZiSi: daily loss halt triggered — trading paused for today")
                await asyncio.sleep(3600)
                continue

            open_positions = state_manager.get_open_positions()
            if not check_exposure_caps(asset, open_positions):
                await _sleep_to_next_candle(interval_minutes)
                continue

            signal = engine.generate_signal()
            if signal is None:
                await _sleep_to_next_candle(interval_minutes)
                continue

            direction   = signal["direction"]
            score       = signal["score"]
            market      = signal["market"]
            entry_price = market["up_price"] if direction == "UP" else market["dn_price"]

            if not entry_price_gate(entry_price, score):
                log.info("[MAIN] %s/%s: price gate blocked %.2f @ score %.2f", asset, timeframe, entry_price, score)
                await _sleep_to_next_candle(interval_minutes)
                continue

            bet_usd  = engine.compute_size(score, entry_price, current_balance)
            up_price = market["up_price"]
            dn_price = market["dn_price"]
            is_dual  = UpDownEngine.should_dual_enter(up_price, dn_price)

            if is_dual:
                main_usd, hedge_usd = engine.compute_dual_sizes(
                    score, entry_price,
                    dn_price if direction == "UP" else up_price,
                    current_balance,
                )
                _place_trade(asset, timeframe, direction, market, main_usd, entry_price, score, "DUAL_MAIN")
                hedge_dir   = "DOWN" if direction == "UP" else "UP"
                hedge_price = dn_price if direction == "UP" else up_price
                _place_trade(asset, timeframe, hedge_dir, market, hedge_usd, hedge_price, score, "DUAL_HEDGE")
            else:
                _place_trade(asset, timeframe, direction, market, bet_usd, entry_price, score, "SINGLE")

            update_runtime_tracking()

        except Exception as exc:
            log.error("[MAIN] %s/%s loop error: %s", asset, timeframe, exc, exc_info=True)

        await _sleep_to_next_candle(interval_minutes)


def _place_trade(asset, timeframe, direction, market, usd_amount, entry_price, score, trade_type="SINGLE"):
    """Record a paper trade in positions_state.json."""
    try:
        from trader import place_paper_trade
        shares      = round(usd_amount / entry_price)
        actual_cost = shares * entry_price
        market_id   = (market["up_market"] if direction == "UP" else market["dn_market"]).get("id", "")
        place_paper_trade(
            event_id    = market["event_id"],
            market_id   = market_id,
            amount_dollars = actual_cost,
            direction   = "YES" if direction == "UP" else "NO",
            entry_price = entry_price,
            event_title = f"[UPDOWN][{asset}][{timeframe}][{trade_type}] {market['event_title']}",
            expiry_ts   = market["expiry_ts"],
        )
        log.info(
            "[MAIN] TRADE: %s/%s %s | $%.2f @ %.0fc | score=%.2f | %s",
            asset, timeframe, direction, actual_cost, entry_price * 100, score, trade_type,
        )
        _try_telegram(
            f"TRADE {asset}/{timeframe} {direction} | ${actual_cost:.2f} @ {entry_price*100:.0f}c | score={score:.2f} | {trade_type}"
        )
    except Exception as exc:
        log.error("[MAIN] Trade placement failed: %s", exc)


# ── Entry point ───────────────────────────────────────────────────────────────

async def main() -> None:
    global _starting_balance

    cfg = load_config()
    setup_file_logging(cfg.get("LOG_LEVEL", "INFO"))
    log_config_startup(cfg)

    _starting_balance = get_current_balance()
    initialize_runtime_tracking()

    for asset in ASSETS:
        for tf in TIMEFRAMES.get(asset, ["5m"]):
            key = f"{asset}/{tf}"
            _engines[key] = UpDownEngine(asset, tf, state_manager, _try_telegram)
            log.info("[MAIN] Engine registered: %s", key)

    log.info("[MAIN] Launching 6 asyncio tasks (5 asset loops + reconciliation)")

    await asyncio.gather(
        asset_loop("BTC", "5m",  offset_seconds=0),
        asset_loop("BTC", "15m", offset_seconds=0),
        asset_loop("ETH", "5m",  offset_seconds=90),
        asset_loop("SOL", "5m",  offset_seconds=180),
        asset_loop("XRP", "5m",  offset_seconds=270),
        reconciliation_loop(state_manager, _try_telegram),
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("[MAIN] Shutdown requested")
        sys.exit(0)
