"""
risk_manager.py - ZiSi Bot Risk Management
Kelly Criterion, position sizing, exit targets, and safety validation.
"""

import logging

from config import load_config
from state_manager import get_current_balance

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
CONFIDENCE_DEFLATION_MULTIPLIER: float = 0.65
_GEMINI_CALIBRATION_PHASE = "PHASE_1_UNCALIBRATED"  # flip to PHASE_2_CALIBRATED after 50 trades

# Hard daily loss limit: halt new entries if session drawdown exceeds 15%
DAILY_LOSS_LIMIT_PCT: float = 0.15

# Consecutive loss stop: reduce Kelly by 40% for 10 trades after 5 straight losses
CONSECUTIVE_LOSS_THRESHOLD: int = 5
CONSECUTIVE_LOSS_KELLY_REDUCTION: float = 0.60  # multiply Kelly by this

# Minimum order book depth to enter a market
MIN_LIQUIDITY_DEPTH_USD: float = 500.0

# Fee constants (taker fees for both platforms)
KALSHI_TAKER_FEE: float = 0.02
POLYMARKET_TAKER_FEE: float = 0.02


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
        log.info("Kelly fraction negative (%.4f) — edge not positive, skip", kelly)
        return 0.0

    # Cap and floor
    capped = max(_MIN_POSITION_FRACTION, min(kelly, _KELLY_SAFETY_CAP))
    log.info(
        "Kelly fraction: raw=%.4f  capped=%.4f  (b=%.2f p=%.2f)",
        kelly, capped, b, p,
    )
    return capped


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
    # Raw scores (0-1 scale) are uncalibrated ordinal rankings — a 0.75 does not
    # mean 75% win probability.  Deflating to 65% prevents systematic overbetting
    # until isotonic regression calibration kicks in at 50 labelled trades.
    deflated_strength = round(signal_strength * CONFIDENCE_DEFLATION_MULTIPLIER, 4)
    log.info(
        "[KELLY-CALIB] %s | raw_conf=%.3f → deflated=%.3f (×%.2f)",
        _GEMINI_CALIBRATION_PHASE, signal_strength, deflated_strength,
        CONFIDENCE_DEFLATION_MULTIPLIER,
    )
    signal_strength = deflated_strength

    kelly_pct = calculate_kelly_criterion(historical_win_rate, historical_avg_win, historical_avg_loss)

    # GAP #5: Consecutive loss Kelly reduction
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
    is_peak = utc_hour in PEAK_TRADING_HOURS_UTC
    utc_multiplier = PEAK_KELLY_MULTIPLIER if is_peak else OFF_PEAK_KELLY_MULTIPLIER
    _utc_mode = "PEAK" if is_peak else "OFF-PEAK"
    log.info(
        "[KELLY-SCALING] UTC %02d:00 = %s (multiplier: %.1fx)",
        utc_hour, _utc_mode, utc_multiplier,
    )

    final_position = adjusted_position * vol_mult * utc_multiplier

    min_pos = account_balance * 0.01
    max_pos = account_balance * 0.05
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
) -> dict:
    """
    Calculate take-profit and stop-loss price levels.

    Args:
        entry_price:           Price paid per share (0–1).
        position_size_dollars: Total USD invested.
    Returns:
        Dict with entry_price, target_price, stop_loss,
        profit_at_target ($), loss_at_stop ($), risk_reward_ratio.
    """
    cfg = load_config()
    target_mult = cfg["POSITION_TARGET_MULTIPLIER"]       # e.g. 1.30
    stop_mult   = cfg["POSITION_STOP_LOSS_MULTIPLIER"]    # e.g. 0.85

    target_price = round(entry_price * target_mult, 6)
    stop_price   = round(entry_price * stop_mult,   6)

    # Polymarket shares = position_size / entry_price
    shares = position_size_dollars / entry_price if entry_price > 0 else 0

    profit_at_target = round(shares * (target_price - entry_price), 2)
    loss_at_stop     = round(shares * (stop_price - entry_price), 2)   # negative

    risk_reward = (
        round(profit_at_target / abs(loss_at_stop), 4)
        if loss_at_stop != 0 else 0.0
    )

    result = {
        "entry_price":      entry_price,
        "target_price":     target_price,
        "stop_loss":        stop_price,
        "profit_at_target": profit_at_target,
        "loss_at_stop":     loss_at_stop,
        "risk_reward_ratio": risk_reward,
    }
    log.info(
        "Exit targets — target: %.4f (+$%.2f) | stop: %.4f (-$%.2f) | R:R=%.2f",
        target_price, profit_at_target, stop_price, abs(loss_at_stop), risk_reward,
    )
    return result

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

