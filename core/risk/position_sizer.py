"""
position_sizer.py - ZiSi Adaptive Kelly Position Sizing (Edge Architecture v2)

Implements Advancement D: Mathematically optimal bet sizing using half-Kelly
criterion with dynamic inputs from all Edge Architecture modules:
  - Regime detector (A) → regime-adjusted win probability
  - Confluence engine (G) → multi-timeframe confidence boost
  - Anti-fragile system (M) → performance-based aggression multiplier
  - Portfolio heat (L) → correlated exposure dampening
  - Volatility surface (E) → sentiment-based confidence modifier
  - Whale tracker (J) → whale activity confidence multiplier

Half-Kelly = f*/2 reduces variance while maintaining most of the edge.
Floor: $0.50 | Ceiling: $5.00 | Never risk >5% of bankroll per trade.
"""
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Dict, Optional
from core.engine.state_manager import GLOBAL_POSITIONS_LOCK

log = logging.getLogger("zisi.position_sizer")

# ── Kelly Formula Constants ─────────────────────────────────────────────────
# Base win rates by signal type (empirically calibrated)
_BASE_WIN_RATES: Dict[str, float] = {
    "TYPE_A_HIGH": 0.72,   # Strong RSI + OFI confluence
    "TYPE_A_LOW":  0.65,   # RSI-only directional
    "TYPE_B_HIGH": 0.58,   # Moderate signal
    "TYPE_B_LOW":  0.52,   # Weak/marginal signal
}

# Average payout ratio on Polymarket UP/DOWN markets
# Typical entry ~40-55c → payout $1.00 → avg ratio ~1.5-2.0
_DEFAULT_PAYOUT_RATIO: float = 1.50

# Position size guards
_MIN_POSITION_USD: float = 0.50
_MAX_POSITION_USD: float = 5.00
_MAX_BANKROLL_FRACTION: float = 0.05  # Never risk >5% per trade

# Kelly multipliers by signal type (backward compat)
_SIGNAL_TYPE_MULT: Dict[str, float] = {
    "TYPE_A_HIGH": 1.5,
    "TYPE_A_LOW":  1.0,
    "TYPE_B_HIGH": 0.8,
    "TYPE_B_LOW":  0.4,
}

# Kelly multipliers by market type
_MARKET_TYPE_MULT: Dict[str, float] = {
    "UP_DOWN":     1.0,
    "PRICE_RANGE": 0.8,
    "HIT_PRICE":   0.5,
    "OTHER":       0.6,
}


class PositionSizer:
    """
    Adaptive Kelly Position Sizer — the central sizing hub.

    Integrates signals from regime detector, confluence engine, anti-fragile
    system, portfolio heat manager, volatility surface, and whale tracker
    to compute mathematically optimal position sizes.

    Usage:
        sizer = PositionSizer(account_balance=100.0)
        sizer.reset_cycle()
        size = sizer.calculate_adaptive(
            signal=signal_dict,
            market=market_dict,
            regime_kelly=1.2,
            confluence_boost=0.10,
            antifragile_mult=1.0,
            heat_mult=1.0,
            sentiment_modifier=0.05,
            whale_mult=1.0,
        )
    """

    def __init__(
        self,
        account_balance: float = 100.0,
        max_cycle_capital: float = 100.0,
        max_trades_per_cycle: int = 40,
    ):
        self.account_balance = account_balance
        self.max_cycle_capital = max_cycle_capital
        self.max_trades_per_cycle = max_trades_per_cycle
        self._capital_used: float = 0.0
        self._trades: int = 0

    def reset_cycle(self) -> None:
        """Reset counters at the start of each cycle."""
        self._capital_used = 0.0
        self._trades = 0

    # ── Primary Adaptive Kelly Calculator ──────────────────────────────────────

    def calculate_adaptive(
        self,
        signal: Dict,
        market: Dict,
        regime_kelly: float = 1.0,
        confluence_boost: float = 0.0,
        antifragile_mult: float = 1.0,
        heat_mult: float = 1.0,
        sentiment_modifier: float = 0.0,
        whale_mult: float = 1.0,
        category_weight: float = 1.0,
        min_position_usd: Optional[float] = None,
        max_position_usd: Optional[float] = None,
        max_bankroll_fraction: Optional[float] = None,
    ) -> float:
        """
        Compute position size using half-Kelly with all edge module inputs.

        Args:
            signal: Signal dict with signal_type, score, affected_cryptos
            market: Market dict with market_type, resolutionDate
            regime_kelly: Kelly multiplier from regime detector (A)
            confluence_boost: Win probability boost from confluence engine (G)
            antifragile_mult: Aggression multiplier from anti-fragile system (M)
            heat_mult: Dampening multiplier from portfolio heat manager (L)
            sentiment_modifier: Confidence modifier from volatility surface (E)
            whale_mult: Confidence multiplier from whale tracker (J)
            category_weight: Category weight (existing)

        Returns:
            Dollar amount to bet, or 0.0 if limits reached.
        """
        if self._trades >= self.max_trades_per_cycle:
            log.info("[KELLY] Cycle trade limit reached (%d/%d)", self._trades, self.max_trades_per_cycle)
            return 0.0
        if self._capital_used >= self.max_cycle_capital:
            log.info("[KELLY] Cycle capital limit reached ($%.2f/$%.2f)", self._capital_used, self.max_cycle_capital)
            return 0.0

        trigger = signal.get("trigger", "") or signal.get("entry_source", "") or ""
        trigger_upper = str(trigger).upper()
        entry_price = signal.get("entry_price", 0.45)
        _assets = signal.get("affected_cryptos", [])
        _asset = _assets[0].upper() if _assets else ""

        # Map to Strategy Pillar
        if trigger_upper in ["REV_SNIPE", "REV_STREAK", "SWEEP", "NCS"]:
            pillar = "CORE_SNIPER"
        elif trigger_upper in ["SIG", "MEAN_REV", "ASIAN_SESSION"]:
            pillar = "ASYMMETRIC_BARBELL"
        elif trigger_upper in ["LAT_ARB", "LAT_RAW"]:
            pillar = "LATENCY_ARBITRAGE"
        else:
            pillar = "CORE_SNIPER"

        if pillar == "ASYMMETRIC_BARBELL" and entry_price <= 0.20:
            raw_usd = self.account_balance * 0.01
            log.info(
                "[KELLY-PILLAR-2] Flat 1.0%% allocation for underdog (price=%.2fc): $%.2f",
                entry_price * 100, raw_usd
            )
        else:
            # Step 1: Determine base win probability from signal type
            signal_type = signal.get("signal_type", "TYPE_B_LOW")
            base_win_rate = _BASE_WIN_RATES.get(signal_type, 0.52)

            # Step 2: Apply confluence boost (from Multi-TF analysis)
            adjusted_wr = min(0.90, base_win_rate + confluence_boost)

            # Step 3: Apply sentiment modifier (from Volatility Surface)
            adjusted_wr = min(0.90, adjusted_wr + sentiment_modifier)

            # Step 4: Apply rolling win-rate adjustment
            wr_mult = get_rolling_wr_multiplier(_asset) if _asset else 1.0
            adjusted_wr = min(0.90, adjusted_wr * wr_mult)

            # Step 5: Compute payout ratio from entry price
            if entry_price > 0 and entry_price < 1:
                payout_ratio = (1.0 - entry_price) / entry_price
            else:
                payout_ratio = _DEFAULT_PAYOUT_RATIO

            p = adjusted_wr
            q = 1.0 - p
            b = payout_ratio

            if b <= 0:
                full_kelly = 0.0
            else:
                full_kelly = (b * p - q) / b

            # Apply Kelly fraction by Strategy Pillar
            if pillar == "CORE_SNIPER":
                kelly_fraction = full_kelly * 0.25
                log.info("[KELLY-PILLAR-1] Quarter-Kelly (full=%.4f, fraction=%.4f)", full_kelly, kelly_fraction)
            elif pillar == "ASYMMETRIC_BARBELL":
                # Entry price > 0.20 -> Half-Kelly
                kelly_fraction = full_kelly * 0.50
                log.info("[KELLY-PILLAR-2] Half-Kelly (full=%.4f, fraction=%.4f)", full_kelly, kelly_fraction)
            elif pillar == "LATENCY_ARBITRAGE":
                # Full Kelly scaled by target asset beta scaling factors
                beta_factor = {"BTC": 1.0, "ETH": 1.0, "SOL": 0.75, "XRP": 0.50, "DOGE": 0.50}.get(_asset, 1.0)
                kelly_fraction = full_kelly * beta_factor
                log.info("[KELLY-PILLAR-3] Full Kelly scaled by beta %.2fx (full=%.4f, fraction=%.4f)", beta_factor, full_kelly, kelly_fraction)
            else:
                kelly_fraction = full_kelly * 0.25

            if kelly_fraction <= 0:
                log.info("[KELLY] Negative or zero Kelly fraction (%.4f) - Edge insufficient. Terminating size computation.", kelly_fraction)
                return 0.0

            # Step 7: Apply all multipliers
            combined_mult = regime_kelly * antifragile_mult * heat_mult * whale_mult

            # Step 8: Calculate raw USD size
            raw_usd = kelly_fraction * self.account_balance * combined_mult

            # Step 9: Apply expiry multiplier (existing)
            exp_mult = _expiry_multiplier(market)
            raw_usd *= exp_mult

            # Step 10: Apply category weight (existing)
            raw_usd *= category_weight

            # Step 10.5: Streak Dampener
            consec_wins = get_consecutive_wins()
            if consec_wins >= 5:
                damp_factor = (0.90) ** max(0, consec_wins - 5)
                log.info("[KELLY-STREAK] Active streak of %d wins (>=5) -> scaling size down by %.4f (Original raw_usd: $%.2f)", consec_wins, damp_factor, raw_usd)
                raw_usd *= damp_factor

        # Step 11: Enforce guards
        # Bounds may be overridden by the caller so a single canonical sizer
        # (UpDownEngine.compute_size) can apply ONE consistent floor/ceiling
        # across its adaptive and fallback paths. Defaults preserve the original
        # behaviour for any other caller (e.g. cycle_manager and unit tests).
        _floor = min_position_usd if min_position_usd is not None else _MIN_POSITION_USD
        _ceiling = max_position_usd if max_position_usd is not None else _MAX_POSITION_USD
        _frac = max_bankroll_fraction if max_bankroll_fraction is not None else _MAX_BANKROLL_FRACTION

        # Never exceed max bankroll fraction
        max_from_bankroll = self.account_balance * _frac
        raw_usd = min(raw_usd, max_from_bankroll)

        # Enforce floor and ceiling
        size = max(_floor, min(raw_usd, _ceiling))

        # Respect remaining cycle capital
        remaining = self.max_cycle_capital - self._capital_used
        size = min(size, remaining)
        if size < _MIN_POSITION_USD:
            return 0.0

        self._capital_used += size
        self._trades += 1

        log.info(
            "[KELLY] Trigger=%s | WR=%.1f%% payout=%.2f kelly_fraction=%.4f | "
            "regime*%.2f anti*%.2f heat*%.2f whale*%.2f | "
            "-> $%.2f | cycle=$%.2f/%d trades",
            trigger if trigger else "Kelly", 
            adjusted_wr * 100 if 'adjusted_wr' in locals() else 0.0,
            payout_ratio if 'payout_ratio' in locals() else 0.0,
            kelly_fraction if 'kelly_fraction' in locals() else 0.0,
            regime_kelly, antifragile_mult, heat_mult, whale_mult,
            size, self._capital_used, self._trades,
        )
        return round(size, 2)

    # ── Legacy Calculator (backward compatibility) ─────────────────────────────

    def calculate(
        self,
        signal: Dict,
        market: Dict,
        category_weight: float = 1.0,
    ) -> float:
        """
        Legacy position sizing (backward compatible).
        Delegates to calculate_adaptive with default multipliers.
        """
        return self.calculate_adaptive(
            signal=signal,
            market=market,
            category_weight=category_weight,
        )

    # ── Properties ─────────────────────────────────────────────────────────────

    @property
    def capital_used(self) -> float:
        return self._capital_used

    @property
    def trades_this_cycle(self) -> int:
        return self._trades

    def remaining_capital(self) -> float:
        return max(0.0, self.max_cycle_capital - self._capital_used)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_consecutive_wins() -> int:
    """
    Count the number of consecutive wins in recent trade history.
    Reads closed trades from positions_state.json and checks realized_pnl.
    """
    try:
        pf = os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "positions_state.json")
        pf = os.path.normpath(pf)
        if not os.path.exists(pf):
            return 0
        with GLOBAL_POSITIONS_LOCK:
            with open(pf, "r", encoding="utf-8") as f:
                data = json.loads(f.read())
        closed = data.get("closed", [])
        consec = 0
        for t in reversed(closed):
            if float(t.get("realized_pnl", 0)) > 0:
                consec += 1
            else:
                break
        return consec
    except Exception as e:
        log.warning("[STREAK] Could not compute consecutive wins: %s", e)
        return 0


# Rolling win-rate cache: {asset_key: (timestamp, multiplier)}
_wr_cache: Dict = {}
_WR_CACHE_TTL = 300  # 5 minutes


def get_rolling_wr_multiplier(asset: str) -> float:
    """
    Compute a Kelly multiplier based on the last 10 trades for this asset.
    Reads closed trades from positions_state.json.
    Returns 1.2x if WR > 70%, 0.5x if WR < 30%, 1.0x if <10 samples.
    """
    key = asset.upper()
    now = time.time()
    cached = _wr_cache.get(key)
    if cached and now - cached[0] < _WR_CACHE_TTL:
        return cached[1]
    try:
        pf = os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "positions_state.json")
        pf = os.path.normpath(pf)
        with GLOBAL_POSITIONS_LOCK:
            with open(pf, "r", encoding="utf-8") as f:
                data = json.loads(f.read())
        closed = data.get("closed", [])
        asset_trades = [
            t for t in closed
            if key in str(t.get("affected_cryptos", "")).upper()
            or key in str(t.get("market_title", "")).upper()
        ]
        recent = asset_trades[-10:]
        if len(recent) < 5:
            _wr_cache[key] = (now, 1.0)
            return 1.0
        wins = sum(1 for t in recent if float(t.get("realized_pnl", 0)) > 0)
        wr = wins / len(recent)
        if wr > 0.70:
            mult = 1.20
        elif wr < 0.30:
            mult = 0.50
        elif wr < 0.40:
            mult = 0.75
        else:
            mult = 1.0
        log.debug("[ROLLING-WR] %s last%d WR=%.0f%% → Kelly×%.2f", key, len(recent), wr * 100, mult)
        _wr_cache[key] = (now, mult)
        return mult
    except Exception:
        return 1.0


def _expiry_multiplier(market: Dict) -> float:
    """Scale down positions on markets that expire soon."""
    expires = market.get("resolutionDate") or market.get("expires_at")
    if not expires:
        return 1.0
    try:
        expiry = datetime.fromisoformat(str(expires).replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        hours = (expiry - now).total_seconds() / 3600
        if hours < 1:
            return 0.3
        if hours < 6:
            return 0.7
        if hours < 24:
            return 0.95
        return 1.0
    except Exception:
        return 1.0


def calculate_exit_targets(
    entry_price: float,
    position_size_dollars: float,
    direction: str = "UP",
) -> dict:
    """
    Calculate take-profit and stop-loss price levels dynamically based on entry cost.
    For BOTH 'UP' and 'DN' (DOWN/NO) contract tokens, profit targets must appreciate toward 99¢
    and stop losses must drop toward 1¢.
    """
    from config import load_config
    cfg = load_config()
    target_mult = cfg["POSITION_TARGET_MULTIPLIER"]       # e.g. 1.30
    
    # Price-Dependent Dynamic Stop Loss: tighter stops on expensive contracts
    if entry_price > 0.65:
        stop_mult = 0.90  # 10% stop loss
    else:
        stop_mult = 0.85  # standard 15% stop loss (x0.85)

    profit_margin_delta = entry_price * (target_mult - 1.0)
    risk_loss_delta = entry_price * (1.0 - stop_mult)
    
    target_price = entry_price + profit_margin_delta
    stop_price = entry_price - risk_loss_delta

    target_price = round(target_price, 6)
    stop_price = round(stop_price, 6)

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
    return result

