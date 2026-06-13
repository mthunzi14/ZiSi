"""
trade_calibration_logger.py — Obsidian-style trade logging for Platt scaling & weekly analysis.

Writes every closed trade to a daily JSONL file with the full context needed to:
  1. Calibrate FV probabilities (Platt scaling: raw fv_confidence → actual WR)
  2. Identify regime-dependent performance patterns
  3. Run weekly P&L attribution by strategy / timeframe / price_band / regime

Fields logged per trade:
  ts, asset, timeframe, strategy, direction, entry_price, fv_confidence,
  regime, whale_pressure, ofi, pct_move_at_entry, time_remaining_sec,
  exit_price, pnl, result (WIN/LOSS/EVEN), exit_reason, hold_sec
"""
import json
import logging
import os
import re
import time
from datetime import datetime, timezone

log = logging.getLogger("zisi.calibration_logger")

_CAL_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "calibration_trades",
)
os.makedirs(_CAL_DIR, exist_ok=True)

# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_meta_from_title(title: str) -> dict:
    asset_m = re.search(r'\[(BTC|ETH|SOL|XRP|DOGE|HYPE|BNB)\]', title or "")
    tf_m = re.search(r'\[(5m|15m|1h)\]', title or "")
    return {
        "asset": asset_m.group(1) if asset_m else "UNKNOWN",
        "timeframe": tf_m.group(1) if tf_m else "UNKNOWN",
    }


def _read_regime_status() -> str:
    try:
        base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        path = os.path.join(base, "regime_status.json")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f).get("regime", "UNKNOWN")
    except Exception:
        pass
    return "UNKNOWN"


# ── Public API ────────────────────────────────────────────────────────────────

def log_trade_closed(pos: dict, profit: float, exit_price: float, exit_reason: str) -> None:
    try:
        title = pos.get("event_title", "")
        meta = _extract_meta_from_title(title)

        open_time = pos.get("open_time")
        if isinstance(open_time, datetime):
            entry_ts = open_time.timestamp()
        elif isinstance(open_time, str):
            try:
                entry_ts = datetime.fromisoformat(open_time).timestamp()
            except Exception:
                entry_ts = time.time()
        else:
            entry_ts = time.time()
        hold_sec = round(time.time() - entry_ts, 1)

        entry_strategy = _infer_strategy(title)
        entry_price = float(pos.get("entry_price", 0.0))
        direction = pos.get("direction", "UNKNOWN")
        result = "WIN" if profit > 0 else ("LOSS" if profit < 0 else "EVEN")

        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "asset": meta["asset"],
            "timeframe": meta["timeframe"],
            "strategy": entry_strategy,
            "direction": direction,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "pnl": round(profit, 4),
            "result": result,
            "exit_reason": exit_reason,
            "hold_sec": hold_sec,
            "regime": _read_regime_status(),
            # Extended fields populated when available
            "fv_confidence": pos.get("fv_confidence", None),
            "whale_pressure": pos.get("whale_pressure", None),
            "ofi": pos.get("ofi", None),
            "pct_move_at_entry": pos.get("pct_move", None),
            "time_remaining_sec": pos.get("time_remaining_sec", None),
        }

        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        path = os.path.join(_CAL_DIR, f"trades_{date_str}.jsonl")
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

    except Exception as e:
        log.debug("[CAL-LOG] Failed to log trade: %s", e)


def log_fv_rejection(asset: str, timeframe: str, reason: str, entry_price: float,
                     fv_confidence: float, regime: str) -> None:
    """Log FV gate rejection for calibration analysis (why-rejected instrumentation)."""
    try:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        path = os.path.join(_CAL_DIR, f"fv_rejections_{date_str}.jsonl")
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": datetime.now(timezone.utc).isoformat(),
                "asset": asset,
                "timeframe": timeframe,
                "reason": reason,
                "entry_price": round(entry_price, 4),
                "fv_confidence": round(fv_confidence, 4),
                "regime": regime,
            }) + "\n")
    except Exception:
        pass


# ── Internal ──────────────────────────────────────────────────────────────────

def _infer_strategy(title: str) -> str:
    t = title.upper()
    if "FAIR_VAL" in t or "FAIR-VAL" in t:
        return "FAIR_VAL"
    if "LAT" in t and "ARB" in t:
        return "LAT-ARB"
    if "CLOSE-SNIPE-EARLY" in t or "SNIPE_EARLY" in t:
        return "CLOSE-SNIPE-EARLY"
    if "CLOSE-SNIPE" in t or "CLOSE_SNIPE" in t:
        return "CLOSE-SNIPE"
    if "REVERSAL_SNIPE" in t or "REVERSAL-SNIPE" in t:
        return "REVERSAL-SNIPE"
    if "REVERSAL_STREAK" in t or "REVERSAL-STREAK" in t:
        return "REVERSAL-STREAK"
    if "RESOLUTION_SWEEP" in t or "SWEEP" in t:
        return "SWEEP"
    return "SIG"
