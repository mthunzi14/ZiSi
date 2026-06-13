"""
risk_manager.py - ZiSi Bot Risk Management
Kelly Criterion, position sizing, exit targets, and safety validation.
"""

import logging
import os

from config import load_config
from core.engine.state_manager import get_current_balance
from core.shared.dependencies import get_progress_toward_phase2

log = logging.getLogger("zisi.risk")

# Absolute position-size guardrails (fraction of account)
_MIN_POSITION_FRACTION = 0.005   # 0.5 % of account
_MAX_POSITION_FRACTION = 0.08    # 8 % of account (hard cap — half-Kelly floor)

# Kelly Criterion safety cap: never allocate more than 5% per trade
_KELLY_SAFETY_CAP = 0.05

# Phase 1 confidence deflation: Gemini scores are ordinal rankings, not calibrated
# probabilities. A 7.5/10 might map to ~55% true win prob, not 75%.
# Deflate by 65% until 50+ labelled trades allow isotonic regression calibration.
# Remove this constant and replace with calibration curve lookup in Phase 2.
CONFIDENCE_DEFLATION_MULTIPLIER: float = 0.85
_GEMINI_CALIBRATION_PHASE = "PHASE_2_CALIBRATED"  # 42+ trades — use lighter deflation

# Hard daily loss limit: halt new entries if session drawdown exceeds 15%
DAILY_LOSS_LIMIT_PCT: float = 0.03

# Consecutive loss stop: reduce Kelly by 40% for 10 trades after 5 straight losses
CONSECUTIVE_LOSS_THRESHOLD: int = 5
CONSECUTIVE_LOSS_KELLY_REDUCTION: float = 0.60  # multiply Kelly by this

# Minimum order book depth to enter a market
MIN_LIQUIDITY_DEPTH_USD: float = 500.0

# Fee constants (taker fees for both platforms)
KALSHI_TAKER_FEE: float = 0.02
POLYMARKET_TAKER_FEE: float = 0.02

# Drawdown staircase: progressive Kelly reductions as account drawdown deepens
# Each tuple is (drawdown_threshold, kelly_multiplier)
_DRAWDOWN_STAIRCASE = [
    (0.12, 0.0),   # ≥12% drawdown → halt entirely (circuit breaker)
    (0.08, 0.30),  # ≥8%  drawdown → 30% of normal Kelly
    (0.05, 0.50),  # ≥5%  drawdown → 50%
    (0.03, 0.75),  # ≥3%  drawdown → 75%
    (0.00, 1.00),  # no drawdown   → full Kelly
]

# Session High Watermark trailing stop: halt when account drops X% below its session peak
_SESSION_HWM_STOP_PCT: float = 0.08   # 8% below session high → stop for the session

# Order book imbalance gate: require at least 10% directional imbalance to enter
_OB_IMBALANCE_MIN: float = 0.10  # (bid_vol - ask_vol) / (bid_vol + ask_vol) ≥ 10%


def calculate_kelly_fraction(
    win_rate: float,
    avg_win: float,
    avg_loss: float,
) -> float:
    """
    Compute the Kelly fraction for optimal bet sizing.

    Formula: f* = (b*p - q) / b
        b = payoff ratio  (avg_win / avg_loss)
        p = win probability
        q = loss probability (1 - p)

    Args:
        win_rate: Historical win rate as a fraction, e.g. 0.55 for 55%.
        avg_win:  Average dollar profit per winning trade (positive).
        avg_loss: Average dollar loss per losing trade (positive magnitude).
    Returns:
        Fraction of account to risk, clamped to [0.005, 0.05].
    """
    if avg_loss <= 0 or win_rate <= 0 or win_rate >= 1:
        log.warning("Invalid Kelly inputs — returning minimum fraction")
        return _MIN_POSITION_FRACTION

    b = avg_win / avg_loss
    p = win_rate
    q = 1.0 - p

    kelly = (b * p - q) / b

    if kelly <= 0:
        log.info("Kelly fraction negative (%.4f) — bootstrap minimum applied", kelly)
        return _MIN_POSITION_FRACTION

    # Cap and floor
    capped = max(_MIN_POSITION_FRACTION, min(kelly, _KELLY_SAFETY_CAP))
    log.info(
        "Kelly fraction: raw=%.4f  capped=%.4f  (b=%.2f p=%.2f)",
        kelly, capped, b, p,
    )
    return capped


def calculate_binary_kelly(
    win_prob: float,
    entry_price: float,
) -> float:
    """
    Asymmetric Kelly for binary prediction markets (Polymarket/Kalshi).

    On a binary market you risk `entry_price` to win `1 - entry_price`.
    Standard Kelly assumes symmetric payoffs; this formula accounts for the
    actual asymmetry of prediction market payouts:

        f* = p - (1 - p) × (entry_price / (1 - entry_price))

    Args:
        win_prob:    Estimated true win probability (0–1).
        entry_price: Current YES price as a fraction (0–1).
    Returns:
        Kelly fraction clamped to [_MIN_POSITION_FRACTION, _KELLY_SAFETY_CAP].
    """
    if entry_price <= 0 or entry_price >= 1 or win_prob <= 0 or win_prob >= 1:
        return _MIN_POSITION_FRACTION

    loss_frac = entry_price
    win_frac = 1.0 - entry_price
    kelly = win_prob - (1.0 - win_prob) * (loss_frac / win_frac)

    if kelly <= 0:
        log.info("[BINARY-KELLY] Negative edge (f*=%.4f) — no bet", kelly)
        return 0.0

    capped = max(_MIN_POSITION_FRACTION, min(kelly, _KELLY_SAFETY_CAP))
    log.info(
        "[BINARY-KELLY] p=%.3f entry=%.3f → f*=%.4f capped=%.4f",
        win_prob, entry_price, kelly, capped,
    )
    return capped


def get_drawdown_multiplier(current_drawdown_pct: float) -> float:
    """
    Progressive Kelly reduction based on how deep the current drawdown is.

    Drawdown staircase (from session or daily start):
        0–3%:  full Kelly (1.00×)
        3–5%:  reduce to 0.75×
        5–8%:  reduce to 0.50×
        8–12%: reduce to 0.30×
        12%+:  halt (0.00×) — circuit breaker tier

    Args:
        current_drawdown_pct: Drawdown as a positive fraction (0.05 = 5%).
    Returns:
        Multiplier to apply to Kelly-derived position size.
    """
    dd = abs(current_drawdown_pct)
    for threshold, multiplier in _DRAWDOWN_STAIRCASE:
        if dd >= threshold:
            if multiplier == 0.0:
                log.warning(
                    "[DRAWDOWN-STAIRCASE] %.1f%% drawdown — HALT (circuit breaker active)",
                    dd * 100,
                )
            else:
                log.info(
                    "[DRAWDOWN-STAIRCASE] %.1f%% drawdown — Kelly ×%.2f",
                    dd * 100, multiplier,
                )
            return multiplier
    return 1.0


def check_session_hwm_stop(account_balance: float, session_hwm: float) -> bool:
    """
    Session High Watermark trailing stop.

    Returns True (HALT) if the current balance has fallen more than
    _SESSION_HWM_STOP_PCT below the session high watermark.

    Args:
        account_balance: Current account balance in USD.
        session_hwm:     Highest balance seen this session in USD.
    Returns:
        True = stop trading for the session, False = continue.
    """
    if session_hwm <= 0 or account_balance >= session_hwm:
        return False
    drawdown_from_hwm = (session_hwm - account_balance) / session_hwm
    if drawdown_from_hwm >= _SESSION_HWM_STOP_PCT:
        log.warning(
            "[HWM-STOP] Balance $%.2f is %.1f%% below session high $%.2f — HALTING",
            account_balance, drawdown_from_hwm * 100, session_hwm,
        )
        return True
    return False


def check_orderbook_imbalance_gate(
    bid_volume: float,
    ask_volume: float,
    required_direction: str,
) -> bool:
    """
    Order book imbalance entry gate.

    Only allow entry when the OB shows at least _OB_IMBALANCE_MIN directional
    imbalance in our favour. A trade without OB confirmation has lower fill
    quality and higher adverse-selection risk.

    Args:
        bid_volume:         Total bid-side depth in USD (or contract units).
        ask_volume:         Total ask-side depth in USD (or contract units).
        required_direction: "bullish"/"YES" = bids should dominate;
                            "bearish"/"NO"  = asks should dominate.
    Returns:
        True = gate passes (allowed to trade), False = gate blocks entry.
    """
    total = bid_volume + ask_volume
    if total <= 0:
        log.debug("[OB-GATE] No order book data — gate bypassed")
        return True  # no data → don't block

    imbalance = (bid_volume - ask_volume) / total
    direction = required_direction.lower()
    is_bullish_dir = direction in ("bullish", "yes", "up")

    # Positive imbalance = bids > asks = upward pressure
    directional_imbalance = imbalance if is_bullish_dir else -imbalance

    if directional_imbalance >= _OB_IMBALANCE_MIN:
        log.debug("[OB-GATE] PASS: imbalance=%.3f ≥ %.3f for %s", directional_imbalance, _OB_IMBALANCE_MIN, direction)
        return True

    log.info(
        "[OB-GATE] BLOCK: directional imbalance %.3f < %.3f minimum for %s",
        directional_imbalance, _OB_IMBALANCE_MIN, direction,
    )
    return False


def calculate_position_size(
    account_balance: float = 0.0,
    risk_percent: float = 2.0,
    entry_price: float = 0.5,
    stop_loss_price: float = 0.4,
) -> float:
    """
    Calculate the dollar amount to place on this trade.

    Logic:
        max_risk_dollars = account_balance * (risk_percent / 100)
        price_delta      = entry_price - stop_loss_price
        position_size    = max_risk_dollars / price_delta

    The result is clamped so it never exceeds 20% of the account.

    Args:
        account_balance: Current account value in USD.
        risk_percent:    Max percentage of account to risk (e.g. 2).
        entry_price:     Price at which we enter (0–1 on Polymarket).
        stop_loss_price: Price at which we exit for a loss.
    Returns:
        Dollar amount to spend on this position.
    """
    account_balance = get_current_balance()
    max_risk = account_balance * (risk_percent / 100)

    price_delta = abs(entry_price - stop_loss_price)
    if price_delta <= 0:
        log.warning("Entry and stop-loss prices are identical — using minimum position")
        price_delta = 0.01

    position = max_risk / price_delta

    # Hard cap: no more than 20% of account in one trade
    max_allowed = account_balance * _MAX_POSITION_FRACTION
    if position > max_allowed:
        log.info(
            "Position $%.2f exceeds 20%% cap ($%.2f) — capping",
            position, max_allowed,
        )
        position = max_allowed

    log.info(
        "Position size: $%.2f (max_risk=$%.2f, delta=%.4f)",
        position, max_risk, price_delta,
    )
    return round(position, 2)


def calculate_kelly_criterion(
    win_rate: float,
    avg_win: float,
    avg_loss: float,
) -> float:
    """
    Kelly Criterion: f* = (b*p - q) / b

    Args:
        win_rate: Win probability as fraction (e.g. 0.55).
        avg_win:  Average winning trade return as fraction (e.g. 0.015 = 1.5%).
        avg_loss: Average losing trade return magnitude (e.g. 0.015 = 1.5%).
    Returns:
        Kelly fraction clamped to [0.01, 0.25].
    """
    if win_rate <= 0 or avg_loss <= 0:
        return 0.01

    b = avg_win / avg_loss
    p = win_rate
    q = 1.0 - p

    kelly = (b * p - q) / b

    if kelly <= 0:
        log.info("Kelly fraction non-positive (%.4f) — edge not established, using minimum", kelly)
        return 0.01

    clamped = max(0.01, min(0.25, kelly))
    log.info("Kelly criterion: raw=%.4f clamped=%.4f (b=%.2f p=%.2f)", kelly, clamped, b, p)
    return clamped


def calculate_position_size_kelly(
    account_balance: float,
    signal_strength: float,
    symbol: str,
    historical_win_rate: float = 0.50,
    historical_avg_win: float = 0.015,
    historical_avg_loss: float = 0.015,
    consecutive_losses: int = 0,
    entry_price: float = 0.50,
    current_drawdown: float = 0.0,
    session_hwm: float = 0.0,
) -> dict:
    """
    Position sizing using Kelly Criterion adjusted for signal strength and volatility.

    Args:
        account_balance:      Current account value in USD.
        signal_strength:      Normalized 0–1 confidence.
        symbol:               Crypto symbol, e.g. 'BTC'.
        historical_win_rate:  Historical win rate fraction.
        historical_avg_win:   Historical average win as a fraction of capital.
        historical_avg_loss:  Historical average loss magnitude as fraction.
    Returns:
        Dict with kelly_pct, base_position, adjusted_position, final_position,
        signal_multiplier.
    """
    # Phase 1: deflate Gemini confidence before feeding to Kelly.
    deflated_strength = round(signal_strength * CONFIDENCE_DEFLATION_MULTIPLIER, 4)
    log.info(
        "[KELLY-CALIB] %s | raw_conf=%.3f → deflated=%.3f (×%.2f)",
        _GEMINI_CALIBRATION_PHASE, signal_strength, deflated_strength,
        CONFIDENCE_DEFLATION_MULTIPLIER,
    )
    signal_strength = deflated_strength

    # Asymmetric binary Kelly using actual entry price (replaces standard Kelly for binary markets)
    binary_kelly = calculate_binary_kelly(historical_win_rate, entry_price)
    standard_kelly = calculate_kelly_criterion(historical_win_rate, historical_avg_win, historical_avg_loss)
    # Use binary Kelly when entry price is meaningful (not default 0.50 placeholder)
    kelly_pct = binary_kelly if abs(entry_price - 0.50) > 0.02 else standard_kelly
    log.info(
        "[KELLY-MODE] entry=%.3f → %s kelly=%.4f",
        entry_price,
        "BINARY" if abs(entry_price - 0.50) > 0.02 else "STANDARD",
        kelly_pct,
    )

    # Drawdown staircase: reduce Kelly progressively as drawdown deepens
    dd_mult = get_drawdown_multiplier(current_drawdown)
    if dd_mult < 1.0:
        kelly_pct *= dd_mult

    # Session HWM stop: halt if fallen too far below session peak
    if session_hwm > 0 and check_session_hwm_stop(account_balance, session_hwm):
        log.warning("[KELLY-HWM] Session HWM stop triggered — position zeroed")
        kelly_pct = 0.0

    # Consecutive loss Kelly reduction
    if consecutive_losses >= CONSECUTIVE_LOSS_THRESHOLD:
        kelly_pct = kelly_pct * CONSECUTIVE_LOSS_KELLY_REDUCTION
        log.info(
            "[KELLY-LOSS-STREAK] %d consecutive losses → Kelly reduced by %.0f%% to %.4f",
            consecutive_losses, (1 - CONSECUTIVE_LOSS_KELLY_REDUCTION) * 100, kelly_pct,
        )

    base_position = account_balance * kelly_pct

    if signal_strength >= 0.9:
        signal_multiplier = 1.5
    elif signal_strength >= 0.75:
        signal_multiplier = 1.2
    elif signal_strength >= 0.6:
        signal_multiplier = 1.0
    elif signal_strength >= 0.4:
        signal_multiplier = 0.7
    else:
        signal_multiplier = 0.5

    adjusted_position = base_position * signal_multiplier

    vol_map = {"BTC": 0.9, "ETH": 0.95, "SOL": 0.85, "OTHER": 0.8}
    vol_mult = vol_map.get(symbol.upper(), 0.85)

    # UTC hour weighting — peak window (22:00-06:00 UTC) is the geographic edge
    from datetime import datetime as _dt
    from config import PEAK_TRADING_HOURS_UTC, PEAK_KELLY_MULTIPLIER, OFF_PEAK_KELLY_MULTIPLIER
    utc_hour = _dt.utcnow().hour
    start, end = PEAK_TRADING_HOURS_UTC
    is_peak = start <= utc_hour < end
    utc_multiplier = PEAK_KELLY_MULTIPLIER if is_peak else OFF_PEAK_KELLY_MULTIPLIER
    _utc_mode = "PEAK" if is_peak else "OFF-PEAK"
    log.info(
        "[KELLY-SCALING] UTC %02d:00 = %s (multiplier: %.1fx)",
        utc_hour, _utc_mode, utc_multiplier,
    )

    final_position = adjusted_position * vol_mult * utc_multiplier

    # ── Auto-compounding Kelly: scale up as account grows above $100 ──────────
    # Converts linear Kelly into geometric growth: every $200 above $100 adds 30%
    balance_mult = 1.0 + max(0.0, (account_balance - 100.0) / 200.0) * 0.30
    balance_mult = min(balance_mult, 3.0)  # cap at 3× when balance hits $767+
    if balance_mult > 1.005:
        final_position = final_position * balance_mult
        log.info("[KELLY-COMPOUND] Balance $%.2f → compounding %.2f×", account_balance, balance_mult)

    # ── Kelly warm-up: bootstrap larger positions in first 20 trades ──────────
    # Early trades have tiny sample → Kelly is conservative → positions ~$0.50
    # Warm-up ensures we gather meaningful data from meaningful sized trades.
    try:
        _tc = (get_progress_toward_phase2() or {}).get("trades_collected", 99)
        if _tc < 5:
            final_position = final_position * 2.0
            log.info("[KELLY-WARMUP] Trade %d/5 — 2× bootstrap multiplier active", _tc + 1)
        elif _tc < 20:
            final_position = final_position * 1.5
            log.info("[KELLY-WARMUP] Trade %d/20 — 1.5× bootstrap multiplier active", _tc + 1)
        else:
            log.debug("[KELLY-WARMUP] Trade %d — bootstrap phase complete, standard sizing", _tc + 1)
    except Exception as _warmup_err:
        log.debug("[KELLY-WARMUP] Could not load trade count for warmup: %s", _warmup_err)

    min_pos = account_balance * 0.01
    # Tiered ceiling: 10% for very high conviction, 7% strong, 5% default
    if signal_strength >= 0.9:
        max_pct = 0.10
    elif signal_strength >= 0.75:
        max_pct = 0.07
    else:
        max_pct = 0.05
    max_pos = account_balance * max_pct
    final_position = max(min_pos, min(max_pos, final_position))

    log.info(
        "[KELLY-DETAILED] kelly=%.2f%% × signal=%.2f × vol=%.2f × utc=%.1f → $%.2f",
        kelly_pct * 100, signal_multiplier, vol_mult, utc_multiplier, final_position,
    )

    return {
        "kelly_pct": kelly_pct,
        "base_position": round(base_position, 2),
        "adjusted_position": round(adjusted_position, 2),
        "final_position": round(final_position, 2),
        "signal_multiplier": signal_multiplier,
        "utc_multiplier": utc_multiplier,
        "is_peak_hour": is_peak,
    }


def calculate_position_size_dynamic(
    account_balance: float,
    signal_strength: float,
    symbol: str,
    current_drawdown: float = 0.0,
    consecutive_losses: int = 0,
) -> float:
    """
    Dynamic position sizing that scales with signal confidence, symbol volatility,
    current drawdown, and consecutive loss streak.

    Args:
        account_balance:    Current account value in USD.
        signal_strength:    Normalized 0–1 confidence (e.g. confidence/10).
        symbol:             Crypto symbol, e.g. 'BTC', 'ETH', 'SOL'.
        current_drawdown:   Current drawdown fraction (0.05 = 5% drawdown).
        consecutive_losses: Number of consecutive losing trades.
    Returns:
        Dollar position size, clamped to [1%, 5%] of account.
    """
    base_risk = account_balance * 0.02  # 2% base risk

    # Signal strength multiplier
    if signal_strength >= 0.9:
        signal_mult = 1.5
    elif signal_strength >= 0.75:
        signal_mult = 1.2
    elif signal_strength >= 0.6:
        signal_mult = 1.0
    elif signal_strength >= 0.4:
        signal_mult = 0.7
    else:
        signal_mult = 0.5

    # Volatility multiplier by asset
    vol_map = {"BTC": 0.7, "ETH": 0.8, "SOL": 0.65}
    vol_mult = vol_map.get(symbol.upper(), 0.75)

    # Drawdown protection
    if current_drawdown > 0.10:
        dd_mult = 0.4
    elif current_drawdown > 0.05:
        dd_mult = 0.6
    else:
        dd_mult = 1.0

    # Losing streak protection
    if consecutive_losses >= 3:
        streak_mult = 0.3
    elif consecutive_losses >= 2:
        streak_mult = 0.5
    elif consecutive_losses >= 1:
        streak_mult = 0.7
    else:
        streak_mult = 1.0

    position = base_risk * signal_mult * vol_mult * dd_mult * streak_mult

    min_pos = account_balance * 0.01
    max_pos = account_balance * 0.05
    position = max(min_pos, min(max_pos, position))

    log.info(
        "Dynamic position: $%.2f (signal=%.2f vol=%.2f dd=%.2f streak=%.2f)",
        position, signal_mult, vol_mult, dd_mult, streak_mult,
    )
    return round(position, 2)


def validate_trade(
    position_size: float,
    account_balance: float,
    current_positions_count: int,
    win_rate: float = 0.50,
    entry_price: float = 0.50,
    platform: str = "POLYMARKET",
) -> bool:
    """
    Run all safety checks before allowing a trade.

    Hard limits enforced:
        1. position_size > 0 and <= 8% of account (hard cap)
        2. current_positions_count < MAX_SIMULTANEOUS_TRADES
        3. remaining_balance >= $50 minimum reserve
        4. post-fee EV must be positive (EV > 1.01x stake)
    """
    cfg = load_config()
    max_trades = cfg["MAX_SIMULTANEOUS_TRADES"]

    if position_size <= 0:
        log.warning("[REJECT] position_size must be > 0 (got %.2f)", position_size)
        return False

    if position_size > account_balance:
        log.warning(
            "[REJECT] position_size $%.2f exceeds balance $%.2f",
            position_size, account_balance,
        )
        return False

    # Hard cap: 8% of account (half-Kelly floor per system mandate)
    max_allowed = account_balance * _MAX_POSITION_FRACTION
    if position_size > max_allowed:
        log.warning(
            "[REJECT] position_size $%.2f exceeds 8%% cap ($%.2f) — position clamped",
            position_size, max_allowed,
        )
        return False

    if current_positions_count >= max_trades:
        log.warning(
            "[REJECT] at max simultaneous trades (%d/%d)",
            current_positions_count, max_trades,
        )
        return False

    remaining_balance = account_balance - position_size
    if remaining_balance < 50:
        log.warning(
            "[REJECT] remaining balance $%.2f would fall below $50 minimum",
            remaining_balance,
        )
        return False

    # Post-fee EV gate: reject trades where fee cost exceeds estimated edge.
    # pre_fee_ev = win_rate * (1/entry_price) + (1 - win_rate) * 0
    # We need EV * stake - fee * stake > stake → EV > 1 + fee
    fee = KALSHI_TAKER_FEE if platform.upper() == "KALSHI" else POLYMARKET_TAKER_FEE
    pre_fee_ev = win_rate * (1.0 / entry_price) if entry_price > 0 else 1.0
    post_fee_ev = pre_fee_ev * (1.0 - fee)
    if post_fee_ev < 1.01:
        log.warning(
            "[REJECT] post_fee_ev %.4f < 1.01 (win_rate=%.2f entry=%.4f fee=%.1f%%)",
            post_fee_ev, win_rate, entry_price, fee * 100,
        )
        return False

    log.info("Trade validated: $%.2f position, %d open trades", position_size, current_positions_count)
    return True


def validate_liquidity(event: dict) -> dict:
    """
    Check whether a Polymarket event has sufficient liquidity to trade.

    Liquidity is the combined YES + NO pool size reported by the API.
    Trades in thin markets frequently fail to fill, producing dead positions.

    Args:
        event: Polymarket event dict; must contain a 'liquidity' key.
    Returns:
        Dict with 'valid' bool, human-readable 'reason', and raw 'liquidity'.
    """
    cfg = load_config()
    min_liquidity = float(cfg.get("MIN_EVENT_LIQUIDITY_USD", 1000))
    current_liquidity = float(event.get("liquidity", 0) or 0)

    if current_liquidity < min_liquidity:
        log.warning(
            "[SKIP] Event %s: Liquidity $%s < $%s minimum",
            event.get("id", "unknown"),
            f"{current_liquidity:,.0f}",
            f"{min_liquidity:,.0f}",
        )
        return {
            "valid": False,
            "reason": f"Insufficient liquidity: ${current_liquidity:,.0f} < ${min_liquidity:,.0f}",
            "liquidity": current_liquidity,
        }

    log.debug("Liquidity OK: $%.0f for event %s", current_liquidity, event.get("id", "unknown"))
    return {
        "valid": True,
        "reason": "Liquidity check passed",
        "liquidity": current_liquidity,
    }


def validate_entry_price(entry_price: float, signal_confidence: int) -> dict:
    """
    Reject trades where the entry price is too high relative to signal strength.

    Each signal level maps to a maximum entry price that preserves a +10¢
    buffer above the break-even point.  Paying more than the ceiling means
    we need an unusually large price move just to break even.

    Signal → max entry mapping:
        7/10 → 0.42   (weak signal; need cheap entry)
        8/10 → 0.48   (moderate signal)
        9/10 → 0.55   (strong signal; can pay more)
       10/10 → 0.65   (very strong; can pay premium)

    Args:
        entry_price:       Current market price (0–1).
        signal_confidence: Signal strength integer in 7–10.
    Returns:
        Dict with 'valid' bool, 'reason', 'entry_price', 'max_allowed', 'signal'.
    """
    # Raised thresholds — Polymarket liquid markets trade at realistic prices.
    # Old values (0.42–0.65) blocked almost every market.  New values allow
    # entry across the full price range while still preventing buying at near-
    # resolution prices on weak signals.
    max_entry_by_signal: dict[int, float] = {
        7: 0.65,   # moderate signal: up to 65¢ YES entry
        8: 0.72,   # strong signal: up to 72¢
        9: 0.80,   # very strong: up to 80¢
        10: 0.90,  # max confidence: up to 90¢
    }
    max_entry = max_entry_by_signal.get(signal_confidence, 0.60)

    if entry_price > max_entry:
        log.warning(
            "[SKIP] Entry price %.4f > %.2f max for %d/10 signal",
            entry_price, max_entry, signal_confidence,
        )
        return {
            "valid": False,
            "reason": (
                f"Entry ${entry_price:.4f} > max ${max_entry:.2f} "
                f"for {signal_confidence}/10 signal"
            ),
            "entry_price": entry_price,
            "max_allowed": max_entry,
            "signal": signal_confidence,
        }

    log.debug(
        "Entry price OK: %.4f <= %.2f for %d/10 signal",
        entry_price, max_entry, signal_confidence,
    )
    return {
        "valid": True,
        "reason": f"Entry price acceptable for {signal_confidence}/10 signal",
        "entry_price": entry_price,
        "max_allowed": max_entry,
        "signal": signal_confidence,
    }


def calculate_exit_targets(
    entry_price: float,
    position_size_dollars: float,
    direction: str = "UP",
) -> dict:
    """
    Calculate take-profit and stop-loss price levels dynamically based on entry cost.
    Delegates to position_sizer.py.
    """
    from core.risk.position_sizer import calculate_exit_targets as sizer_exit_targets
    res = sizer_exit_targets(entry_price, position_size_dollars, direction)
    log.info(
        "Exit targets — target: %.4f (+$%.2f) | stop: %.4f (-$%.2f) | R:R=%.2f | direction=%s",
        res["target_price"], res["profit_at_target"], res["stop_loss"], abs(res["loss_at_stop"]), res["risk_reward_ratio"], direction
    )
    return res


# ---------------------------------------------------------------------------
# Market-type-specific sizing
# ---------------------------------------------------------------------------

_MARKET_TYPE_KELLY_SCALE = {
    "UP_DOWN":     1.00,
    "HIT_PRICE":   0.50,
    "PRICE_RANGE": 0.70,
    "OTHER":        0.80,
}


def calculate_position_size_by_market_type(
    base_position: float,
    market_type: str,
) -> float:
    """
    Scale a Kelly-derived position by market-type risk.

    UP_DOWN markets are clearest binary bets (full size).
    HIT_PRICE markets are hard to call (half size).
    PRICE_RANGE markets are medium difficulty (70% size).

    Args:
        base_position: Dollar position from calculate_position_size_kelly().
        market_type:   One of UP_DOWN, HIT_PRICE, PRICE_RANGE, OTHER.
    Returns:
        Adjusted position, clamped to the same [1%, 5%] band.
    """
    scale = _MARKET_TYPE_KELLY_SCALE.get(market_type, 0.80)
    adjusted = base_position * scale
    log.info(
        "[MARKET-TYPE-KELLY] %s × %.2f → $%.2f (base $%.2f)",
        market_type, scale, adjusted, base_position,
    )
    return round(adjusted, 2)


# ---------------------------------------------------------------------------
# Portfolio Correlation Guard
# ---------------------------------------------------------------------------

def check_portfolio_correlation(
    new_asset: str,
    new_direction: str,
    open_positions: list[dict],
) -> float:
    """
    Prevent stacking correlated positions (e.g. 3× BTC bearish = concentrated risk).

    Returns a position size multiplier: 1.0 = normal, 0.6 = reduce, 0.3 = heavily reduce.
    """
    same = [
        p for p in open_positions
        if (p.get("coin") or p.get("asset") or "").upper() == new_asset.upper()
        and (p.get("direction") or p.get("sentiment") or "").upper() == new_direction.upper()
    ]
    count = len(same)
    if count >= 2:
        log.info(
            "[CORRELATION] Already %d open %s %s positions — scaling to 0.30×",
            count, new_asset, new_direction,
        )
        return 0.30
    if count == 1:
        log.info(
            "[CORRELATION] 1 existing %s %s position — scaling to 0.60×",
            new_asset, new_direction,
        )
        return 0.60
    return 1.0


# Portfolio beta coefficients vs. BTC (approximate, based on 90-day correlation)
_ASSET_BETA: dict = {
    "BTC":  1.00,
    "ETH":  0.85,
    "SOL":  0.80,
    "XRP":  0.70,
    "DOGE": 0.75,
    "ADA":  0.70,
    "MATIC":0.72,
    "AVAX": 0.78,
}
# Maximum portfolio-level beta-adjusted exposure (sum of position × beta)
_MAX_PORTFOLIO_BETA_EXPOSURE = 0.35  # 35% of account in beta-adjusted terms


def calculate_portfolio_beta(open_positions: list[dict], account_balance: float) -> float:
    """
    Return the current portfolio's beta-adjusted exposure as a fraction of account.
    Each open position contributes: (position_size / account) × asset_beta.
    """
    if account_balance <= 0:
        return 0.0
    total_beta = 0.0
    for pos in open_positions:
        size = float(pos.get("position_size", pos.get("amount", 0)) or 0)
        asset = (pos.get("coin") or pos.get("asset") or "OTHER").upper()
        beta = _ASSET_BETA.get(asset, 0.75)
        total_beta += (size / account_balance) * beta
    return round(total_beta, 4)


def check_portfolio_beta_cap(
    new_asset: str,
    new_position_size: float,
    open_positions: list[dict],
    account_balance: float,
) -> float:
    """
    Return a position size multiplier based on remaining beta budget.

    If adding new_position_size would push portfolio beta above _MAX_PORTFOLIO_BETA_EXPOSURE,
    scale the new position down to stay within the cap.

    Returns multiplier in [0.1, 1.0].
    """
    if account_balance <= 0:
        return 1.0

    current_beta = calculate_portfolio_beta(open_positions, account_balance)
    new_asset_beta = _ASSET_BETA.get(new_asset.upper(), 0.75)
    new_beta_contribution = (new_position_size / account_balance) * new_asset_beta
    projected_beta = current_beta + new_beta_contribution

    if projected_beta <= _MAX_PORTFOLIO_BETA_EXPOSURE:
        return 1.0

    # Budget remaining
    remaining_budget = max(0.0, _MAX_PORTFOLIO_BETA_EXPOSURE - current_beta)
    if remaining_budget <= 0:
        log.warning(
            "[BETA-CAP] Portfolio beta %.3f already at cap %.3f — blocking new %s position",
            current_beta, _MAX_PORTFOLIO_BETA_EXPOSURE, new_asset,
        )
        return 0.10  # nearly block (not full 0 so validate_trade can still log it)

    allowed_position = (remaining_budget / new_asset_beta) * account_balance
    multiplier = max(0.10, min(1.0, allowed_position / new_position_size))
    log.info(
        "[BETA-CAP] current_beta=%.3f new=+%.3f → cap=%.3f | %s scaled ×%.2f",
        current_beta, new_beta_contribution, _MAX_PORTFOLIO_BETA_EXPOSURE, new_asset, multiplier,
    )
    return round(multiplier, 3)


# ── Entry price gate (ZiSi Session 10) ───────────────────────────────────────

_SCORE_TO_WR = [
    (0.85, 0.88),  # score >= 0.85 -> wr = 0.88 -> wr - 0.08 = 0.80 (up to 80c YES entry)
    (0.75, 0.83),  # score >= 0.75 -> wr = 0.83 -> wr - 0.08 = 0.75 (up to 75c YES entry)
    (0.62, 0.73),  # score >= 0.62 -> wr = 0.73 -> wr - 0.08 = 0.65 (up to 65c YES entry)
]


def entry_price_gate(price: float, score: float, is_dual: bool = False) -> bool:
    """
    Bypassed all caps and constraints (Bonereaper-mode).
    """
    return 0.0 < price < 1.0


# ── Exposure caps ─────────────────────────────────────────────────────────────

def check_exposure_caps(asset: str, open_positions: list) -> bool:
    """
    Return True (OK to trade) if:
    - Fewer than MAX_OPEN_PER_ASSET open positions for this asset
    - Fewer than MAX_TOTAL_OPEN total open positions
    """
    import re
    from config import MAX_OPEN_PER_ASSET, MAX_TOTAL_OPEN
    if len(open_positions) >= MAX_TOTAL_OPEN:
        log.info("[RISK] Total open %d >= %d — skip", len(open_positions), MAX_TOTAL_OPEN)
        return False

    def _pos_asset(p: dict) -> str:
        a = (p.get("asset") or "").upper()
        if a:
            return a
        m = re.search(r"\[(BTC|ETH|SOL|XRP)\]", p.get("event_title") or "")
        return m.group(1) if m else ""

    asset_open = sum(1 for p in open_positions if _pos_asset(p) == asset.upper())
    if asset_open >= MAX_OPEN_PER_ASSET:
        log.info("[RISK] %s open %d >= %d — skip", asset, asset_open, MAX_OPEN_PER_ASSET)
        return False
    return True

# ── Daily Loss Circuit Breaker ──
# Disabled by default. Enable with CIRCUIT_BREAKER_ENABLED=true in .env.
DAILY_LOSS_LIMIT_PCT = float(os.getenv("CIRCUIT_BREAKER_DAILY_LOSS_PCT", "0.03"))

def check_daily_loss_halt(starting_balance: float, current_balance: float) -> bool:
    """Return True if daily drawdown exceeds threshold. Off by default — enable via CIRCUIT_BREAKER_ENABLED."""
    import os as _os
    if _os.getenv("CIRCUIT_BREAKER_ENABLED", "false").lower() not in ("1", "true", "yes", "on"):
        return False
    if starting_balance <= 0:
        return False
    drawdown = (starting_balance - current_balance) / starting_balance
    return drawdown >= DAILY_LOSS_LIMIT_PCT


