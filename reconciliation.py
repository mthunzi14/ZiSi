# reconciliation.py - 30s asyncio fill verification loop
import asyncio
import logging
import requests

log = logging.getLogger("zisi.reconcile")

POLY_CLOB_API = "https://clob.polymarket.com"


async def reconciliation_loop(state_mgr, telegram_fn=None) -> None:
    """
    Run forever; every 30 seconds verify open positions against CLOB.
    Fixes 'ghost fills' — positions that filled at the exchange but
    weren't recorded locally due to a timeout on the submission call.
    """
    from config import RECONCILE_INTERVAL
    while True:
        await asyncio.sleep(RECONCILE_INTERVAL)
        try:
            _run_reconcile_pass(state_mgr, telegram_fn)
        except Exception as exc:
            log.warning("[RECONCILE] Pass failed: %s", exc)


def _run_reconcile_pass(state_mgr, telegram_fn=None) -> int:
    """Check all open positions for ghost fills. Returns number corrected."""
    corrected = 0
    try:
        positions = state_mgr.get_open_positions()
    except Exception:
        return 0

    for pos in positions:
        order_id = pos.get("order_id") or pos.get("id")
        if not order_id or pos.get("confirmed"):
            continue
        try:
            r = requests.get(
                f"{POLY_CLOB_API}/order/{order_id}",
                timeout=5,
            )
            if r.status_code != 200:
                continue
            data = r.json()
            status = str(data.get("status", "")).upper()
            if status in ("FILLED", "MATCHED"):
                state_mgr.force_confirm(pos)
                corrected += 1
                log.warning("[RECONCILE] Ghost fill corrected: %s %s", pos.get("asset", "?"), order_id[:20])
                if telegram_fn:
                    telegram_fn(f"👻 Ghost fill detected + corrected: {pos.get('asset','?')} | {order_id[:20]}")
        except Exception as exc:
            log.debug("[RECONCILE] Order check failed %s: %s", order_id[:16], exc)

    if corrected:
        log.info("[RECONCILE] Pass complete — %d ghost fill(s) corrected", corrected)
    return corrected
