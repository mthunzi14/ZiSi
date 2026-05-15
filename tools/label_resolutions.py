"""
tools/label_resolutions.py — Resolution Labeler for ML Training

Reads signal_evaluations.jsonl, fetches the final outcome of each
matched Polymarket market from the CLOB API, and writes ground-truth
WIN/LOSS/PUSH labels to signal_evaluations_labeled.jsonl.

This is the Phase 2 ML unlock: once you have real outcome labels you
can retrain the ML models on actual win/loss data instead of proxies.

Run:
    python tools/label_resolutions.py

Output:
    ZiSi_Bot/signal_evaluations_labeled.jsonl  — original entries + outcome label

Label logic:
    - Signal was BULLISH → YES bet placed
        WIN  if market resolved YES
        LOSS if market resolved NO
    - Signal was BEARISH → NO bet placed
        WIN  if market resolved NO
        LOSS if market resolved YES
    - Market unresolved / price still between 0.05–0.95 → OPEN (skip)
    - Market cancelled / no trade → PUSH
"""

import json
import os
import sys
import time
from pathlib import Path
from datetime import datetime, timezone

import requests

# ── Paths ─────────────────────────────────────────────────────────────────────
_ROOT = Path(__file__).parent.parent
_INPUT  = _ROOT / "signal_evaluations.jsonl"
_OUTPUT = _ROOT / "signal_evaluations_labeled.jsonl"
_CLOB   = "https://clob.polymarket.com"

# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe_get(url: str, timeout: int = 10) -> dict:
    try:
        r = requests.get(url, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"  [WARN] GET {url} failed: {e}")
        return {}


def _fetch_market_outcome(market_id: str) -> str:
    """
    Query the CLOB API for a market's current state.

    Returns:
        "YES"      — market resolved YES
        "NO"       — market resolved NO
        "OPEN"     — market not yet resolved
        "UNKNOWN"  — API error or market not found
    """
    if not market_id:
        return "UNKNOWN"

    data = _safe_get(f"{_CLOB}/markets/{market_id}")
    if not data:
        return "UNKNOWN"

    # Check resolution via tokens
    tokens = data.get("tokens", [])
    for token in tokens:
        outcome = (token.get("outcome") or "").upper()
        winner  = token.get("winner", False)
        if winner is True:
            return outcome  # "YES" or "NO"

    # Check via market status fields
    resolved = data.get("resolved", False)
    if not resolved:
        # Check if price is essentially 0 or 1 (de facto resolved)
        price = float(data.get("lastTradePrice", data.get("price", 0.5)) or 0.5)
        if price >= 0.97:
            return "YES"
        if price <= 0.03:
            return "NO"
        return "OPEN"

    # market.resolved = True but no winner token — cancelled
    return "UNKNOWN"


def _derive_label(sentiment: str, market_outcome: str) -> str:
    """
    Given the signal direction and market outcome, return WIN/LOSS/PUSH/OPEN.
    """
    sentiment = (sentiment or "bullish").lower()
    outcome   = market_outcome.upper()

    if outcome == "OPEN":
        return "OPEN"
    if outcome == "UNKNOWN":
        return "PUSH"

    # Bullish → bet YES
    if sentiment in ("bullish", "positive"):
        return "WIN" if outcome == "YES" else "LOSS"

    # Bearish → bet NO
    if sentiment in ("bearish", "negative"):
        return "WIN" if outcome == "NO" else "LOSS"

    # Neutral — we wouldn't have placed a trade, but label anyway
    return "PUSH"


# ── Main ──────────────────────────────────────────────────────────────────────

def run_labeler(dry_run: bool = False) -> None:
    if not _INPUT.exists():
        print(f"[ERROR] signal_evaluations.jsonl not found at {_INPUT}")
        sys.exit(1)

    entries = []
    with _INPUT.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    print(f"[LABELER] Loaded {len(entries)} entries from signal_evaluations.jsonl")

    # Only label entries that were matched to a real market
    labeled_count  = 0
    skipped_open   = 0
    skipped_nomatch = 0
    already_labeled = 0

    output_lines = []

    for i, entry in enumerate(entries):
        # Skip Kalshi trades (different resolution mechanism)
        if entry.get("type") == "KALSHI_TRADE":
            output_lines.append(entry)
            continue

        # Already labeled
        if entry.get("outcome_label"):
            already_labeled += 1
            output_lines.append(entry)
            continue

        matched_event = entry.get("matched_event")
        market_id     = entry.get("market_id") or entry.get("condition_id") or ""

        if not matched_event or not market_id:
            skipped_nomatch += 1
            output_lines.append(entry)
            continue

        # Rate limit: 2 requests/second to avoid Polymarket throttle
        time.sleep(0.5)

        print(
            f"  [{i+1}/{len(entries)}] Fetching outcome for: "
            f"{str(matched_event)[:50]} ({market_id[:12]}...)"
        )

        market_outcome = _fetch_market_outcome(market_id)
        label = _derive_label(entry.get("sentiment", "bullish"), market_outcome)

        if label == "OPEN":
            skipped_open += 1
            output_lines.append(entry)
            continue

        # Attach label
        labeled_entry = dict(entry)
        labeled_entry["outcome_label"]     = label       # WIN / LOSS / PUSH
        labeled_entry["market_outcome"]    = market_outcome
        labeled_entry["labeled_at"]        = datetime.now(timezone.utc).isoformat()
        labeled_count += 1

        color = "✅" if label == "WIN" else "❌" if label == "LOSS" else "➖"
        print(f"    {color} {label} (market resolved {market_outcome})")

        output_lines.append(labeled_entry)

    # Write output
    if not dry_run:
        with _OUTPUT.open("w", encoding="utf-8") as fh:
            for entry in output_lines:
                fh.write(json.dumps(entry) + "\n")
        print(f"\n[LABELER] Written to {_OUTPUT}")
    else:
        print("\n[LABELER] DRY RUN — no file written")

    # Summary
    win_labels  = [e for e in output_lines if e.get("outcome_label") == "WIN"]
    loss_labels = [e for e in output_lines if e.get("outcome_label") == "LOSS"]
    win_rate = len(win_labels) / (len(win_labels) + len(loss_labels)) if (win_labels or loss_labels) else 0

    print(f"""
[LABELER] Summary
─────────────────────────────────
  Total entries:      {len(entries)}
  Already labeled:    {already_labeled}
  Newly labeled:      {labeled_count}
    → WIN:            {len(win_labels)}
    → LOSS:           {len(loss_labels)}
    Actual Win Rate:  {win_rate:.1%}
  Open (unresolved):  {skipped_open}
  No market match:    {skipped_nomatch}
─────────────────────────────────
Once you have ≥20 WIN/LOSS labels, run:
  python ml_pipeline.py --retrain
""")


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    if dry:
        print("[LABELER] Dry run mode — no output file will be written\n")
    run_labeler(dry_run=dry)
