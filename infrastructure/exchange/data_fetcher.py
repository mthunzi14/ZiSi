"""
data_fetcher.py - Unified live price and resolution fetcher for Polymarket.
Provides non-blocking endpoints to retrieve current prices and settlement resolutions.
"""
import logging
import requests
import json
from typing import Optional, Dict, Any

log = logging.getLogger("zisi.data_fetcher")

POLY_GAMMA_API = "https://gamma-api.polymarket.com"


def _fetch_market_details(market_id: str) -> Optional[dict]:
    """
    Fetch market metadata from Gamma API. Supports both:
    1. Direct Market ID (e.g. condition ID or numeric slug)
    2. Outcome Token ID (e.g. large 78-byte number)
    """
    if not market_id:
        return None

    # Determine if this is a token ID (usually a huge numeric string of >30 digits)
    is_token_id = market_id.isdigit() and len(market_id) > 20

    if is_token_id:
        url = f"{POLY_GAMMA_API}/markets"
        params = {"clob_token_ids": market_id}
    else:
        url = f"{POLY_GAMMA_API}/markets/{market_id}"
        params = {}

    try:
        resp = requests.get(url, params=params, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list):
                return data[0] if data else None
            return data
    except Exception as e:
        log.debug("[FETCHER] Error getting details for %s: %s", market_id, e)
    return None


def get_event_current_price(market_id: str) -> Dict[str, Any]:
    """
    Fetch the live price of a Polymarket market by its ID or Token ID.
    Returns a dict with 'price' or None on failure.
    """
    mkt = _fetch_market_details(market_id)
    if not mkt:
        return {"price": None}

    try:
        # Determine the price depending on the specific token ID direction
        prices = mkt.get("outcomePrices")
        if isinstance(prices, str):
            try: prices = json.loads(prices)
            except: prices = []
            
        clob_tids = mkt.get("clobTokenIds", [])
        if isinstance(clob_tids, str):
            try: clob_tids = json.loads(clob_tids)
            except: clob_tids = []

        if prices and len(prices) >= 1:
            # If we queried using a specific token ID, find which outcome index it corresponds to
            if clob_tids and market_id in clob_tids:
                idx = clob_tids.index(market_id)
                price = float(prices[idx]) if idx < len(prices) else float(prices[0])
            else:
                price = float(prices[0])
            return {"price": price}
    except Exception as e:
        log.debug("[FETCHER] Price parsing failed for %s: %s", market_id, e)

    return {"price": None}


def fetch_market_resolution(market_id: str) -> Optional[str]:
    """
    Fetch the resolution outcome of a Polymarket market by its ID or Token ID.
    Returns 'YES', 'NO', or None if unresolved.
    """
    mkt = _fetch_market_details(market_id)
    if not mkt:
        return None

    try:
        # Check if resolved
        if mkt.get("resolved") or mkt.get("closed"):
            # Check winningOutcome
            winning_outcome = mkt.get("winningOutcome")
            if winning_outcome:
                # If winning outcome matches the outcome associated with our token ID
                clob_tids = mkt.get("clobTokenIds", [])
                outcomes = mkt.get("outcomes", [])
                if isinstance(clob_tids, str):
                    try: clob_tids = json.loads(clob_tids)
                    except: clob_tids = []
                if isinstance(outcomes, str):
                    try: outcomes = json.loads(outcomes)
                    except: outcomes = []

                if clob_tids and market_id in clob_tids and outcomes:
                    idx = clob_tids.index(market_id)
                    our_outcome = outcomes[idx] if idx < len(outcomes) else ""
                    if our_outcome.upper() == winning_outcome.upper():
                        return "YES"
                    else:
                        return "NO"
                
                return winning_outcome.upper()
            
            # Fallback: check outcomePrices if winningOutcome not explicitly set
            prices = mkt.get("outcomePrices")
            if isinstance(prices, str):
                try: prices = json.loads(prices)
                except: prices = []
                
            if prices and len(prices) >= 2:
                p_yes = float(prices[0])
                p_no = float(prices[1])
                clob_tids = mkt.get("clobTokenIds", [])
                if isinstance(clob_tids, str):
                    try: clob_tids = json.loads(clob_tids)
                    except: clob_tids = []

                if p_yes >= 0.95:
                    if clob_tids and len(clob_tids) >= 1 and market_id == clob_tids[0]:
                        return "YES"
                    return "NO"
                elif p_no >= 0.95:
                    if clob_tids and len(clob_tids) >= 2 and market_id == clob_tids[1]:
                        return "YES"
                    return "NO"
    except Exception as e:
        log.debug("[FETCHER] Resolution check failed for %s: %s", market_id, e)

    return None
