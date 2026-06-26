"""
config.py - ZiSi Bot Configuration Loader
Loads, validates, and provides access to all .env configuration.
"""

import os
import re
import logging
from dotenv import load_dotenv

# Core active trading assets that have live markets on Polymarket
ASSETS: list = ["BTC", "ETH", "SOL", "XRP", "DOGE", "Alternative", "Sports", "Politics", "Gaming"]

# Inactive/Future altcoins supported by indicators but dormant to prevent rate-limit congestion
FUTURE_ASSETS: list = []

TIMEFRAMES: dict = {
    "BTC": ["5m", "15m"],
    "ETH": ["5m", "15m"],
    "SOL": ["5m", "15m"],
    "XRP": ["5m", "15m"],
    "DOGE": ["5m", "15m"],
    "Alternative": ["5m", "15m"],
    "Sports": ["5m", "15m"],
    "Politics": ["5m", "15m"],
    "Gaming": ["5m", "15m"],
}

ALLOWED_TIMEFRAMES: list = ["5m", "15m"]

# Active trading window (UTC hours, inclusive start/exclusive end) - set to 24/7
TIME_GATE_UTC: tuple = (0, 24)

# Inversion detection
INVERSION_WINDOW: int = 40
INVERSION_TRIGGER_WR: float = 0.20
INVERSION_RECOVERY_WR: float = 0.52

# Dual-entry cap
DUAL_ENTRY_MAX_COMBINED: float = 0.92

# Circuit breaker
CIRCUIT_BREAKER_LOSSES: int = 2
CIRCUIT_BREAKER_SKIP: int = 2

# Daily loss limit (fraction of account)
MAX_DAILY_LOSS_PCT: float = 0.03

# Warmup guard
WARMUP_SECONDS: int = 15
WARMUP_MIN_TICKS: int = 3
WARMUP_MAX_JUMP: float = 0.05

# Reconciliation interval (seconds) — 15s catches stop-loss before price
# overshoots threshold (avg stop was executing at 3c instead of ~9c at 30s)
RECONCILE_INTERVAL: int = 15

# Position limits
MAX_OPEN_PER_ASSET: int = 2
MAX_TOTAL_OPEN: int = 8

# Fair-value (Type-1) primary entry. When True, a spot-distance mispricing that
# clears EDGE_MARGIN fires an entry at the real L2 quote BEFORE the momentum cascade.
FAIR_VALUE_MODE: bool = True

# ── Strategy-Specific Overlays (Sprint 12 / Mentor Emulation) ──────────────────
OVERLAY_C_ENABLED: bool = True
OVERLAY_C_SPEC_BUDGET_PCT: float = 0.01          # 1.0% of total balance
OVERLAY_C_MAX_UNDERDOG_PRICE: float = 0.20        # contracts priced <= 20c

OVERLAY_B_ENABLED: bool = True
OVERLAY_B_FREEZE_MIDPOINTS: bool = False          # DO NOT freeze 40c-60c contracts during breakout (allows PBOT midpoint execution)
OVERLAY_B_TREND_ALIGNMENT_THRESHOLD: int = 3      # requires 3/4 alignment across timeframes
OVERLAY_B_ADX_THRESHOLD: float = 25.0             # ADX threshold for breakout strength

# Dynamic Pricing and Liquidation Rules (Mentor Emulation)
MIN_ENTRY_PRICE: float = 0.01                    # Allow options <= 20c (ritb123)
MAX_ENTRY_PRICE: float = 0.99                    # Allow certainty snipes >= 90c (bonereaper)
SWEEPER_MODE_ENABLED: bool = True                # Mentor emulation: sweep high-certainty contracts
CERTAINTY_PRICE_THRESHOLD: float = 0.90          # Target the 99.6¢ premium sweeps (bonereaper)
MAX_HOLD_TIME_SECONDS: int = 300                 # Strict 5-min absolute liquidation cap
SHORT_TF_SALVAGE_ENABLED: bool = True            # Allow early exit on 5m/15m contracts
SALVAGE_FLOOR_PRICE: float = 0.30               # Certova’s dynamic 30¢ early mitigation barrier
DYNAMIC_DECAY_THRESHOLD: float = 0.70           # Dynamic decay ratio threshold
SHORT_TF_SALVAGE_FLOOR: float = 0.30            # Legacy compat salvage floor
SHORT_TF_STOP_LOSS_ENABLED: bool = True          # Activate stop-loss floor on short-tf
SHORT_TF_STOP_LOSS_PCT: float = 0.20            # Cut at 20% of entry price

# ── Tiered Capital Isolation Layer Configuration (Sprint 13 / Seven-Fleet) ──
PROGRESSIVE_STAKING_SIZES = [10.00, 15.00, 25.00, 40.00, 60.00]
PROGRESSIVE_STAKING_TYPES = ["REV_SNIPE", "SIG"]
TIER2_SANDBOX_TYPES = ["LAT_ARB", "SWEEP", "NCS", "REV_STREAK", "FV"]
TIER2_SANDBOX_EXPOSURE_CAP = 0.30
TIER2_SWEEPER_SIZE = 10.00
TIER2_LATENCY_ARB_MAX_DELTA_MS = 2500.0
TIER2_NCS_SLIPPAGE_CAP = 0.98
TIER2_FV_Z_THRESHOLD = 1.8
TIER2_FV_TARGET_Z = 0.5

# ── Backward-compat aliases (old modules still import these) ─────────────────
PEAK_TRADING_HOURS_UTC    = TIME_GATE_UTC  # replaced by TIME_GATE_UTC in new code
PEAK_KELLY_MULTIPLIER     = 1.0
OFF_PEAK_KELLY_MULTIPLIER = 0.5

# ── Load .env ─────────────────────────────────────────────────────────────────
# __file__ resolves to the project root when config.py lives there, so
# this will correctly find /root/ZiSi/.env on the VPS.
_ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
load_dotenv(_ENV_PATH)

# Module-level logger (plain print until logging is configured)
_log = logging.getLogger("zisi.config")

# Keys that MUST be present and non-empty
_REQUIRED_KEYS = [
    "POLYMARKET_GAMMA_API_URL",
    "POLYMARKET_DATA_API_URL",
    "POLYMARKET_CLOB_API_URL",
]

# Keys that are secret and must never be printed
_SECRET_KEYS = {
    "GMAIL_APP_PASSWORD",
}


_cached_balance: float = 50.0
_last_balance_check: float = 0.0
_BALANCE_CACHE_TTL_SEC: float = 5.0

def _get_cached_account_balance() -> float:
    global _cached_balance, _last_balance_check
    import time
    now = time.time()
    if now - _last_balance_check > _BALANCE_CACHE_TTL_SEC:
        try:
            # PATCHED: import from new canonical location (core.engine) —
            # the old infrastructure.state namespace has been removed.
            from core.engine.state_manager import get_current_balance
            _cached_balance = get_current_balance()
            _last_balance_check = now
        except Exception:
            pass
    return _cached_balance


def load_config() -> dict:
    """
    Load all .env variables into a structured Python dict.

    Returns:
        dict: All configuration grouped by domain.
    Raises:
        ValueError: If any required key is missing or validation fails.
    """
    raw = {
        # Polymarket endpoints
        "POLYMARKET_GAMMA_API_URL": os.getenv(
            "POLYMARKET_GAMMA_API_URL", "https://gamma-api.polymarket.com"
        ),
        "POLYMARKET_DATA_API_URL": os.getenv(
            "POLYMARKET_DATA_API_URL", "https://data-api.polymarket.com"
        ),
        "POLYMARKET_CLOB_API_URL": os.getenv(
            "POLYMARKET_CLOB_API_URL", "https://clob.polymarket.com"
        ),

        # Google integration
        "GOOGLE_DRIVE_FOLDER_ID": os.getenv("GOOGLE_DRIVE_FOLDER_ID", ""),
        "GOOGLE_CREDENTIALS_FILE": os.getenv(
            "GOOGLE_CREDENTIALS_FILE", "credentials.json"
        ),
        "GMAIL_SENDER_EMAIL": os.getenv("GMAIL_SENDER_EMAIL", ""),
        "GMAIL_APP_PASSWORD": os.getenv("GMAIL_APP_PASSWORD", ""),
        "GMAIL_ENABLED": os.getenv("GMAIL_ENABLED", "true").lower() == "true",

        # Bot meta
        "BOT_NAME": os.getenv("BOT_NAME", "ZiSi"),
        "BOT_VERSION": os.getenv("BOT_VERSION", "2.0"),
        "KALSHI_EMAIL": os.getenv("KALSHI_EMAIL", ""),
        "KALSHI_PASSWORD": os.getenv("KALSHI_PASSWORD", ""),
        "KALSHI_API_KEY": os.getenv("KALSHI_API_KEY", ""),
        "KALSHI_PRIVATE_KEY": os.getenv("KALSHI_PRIVATE_KEY", ""),
        "BOT_MODE": os.getenv("BOT_MODE", "paper_trading"),

        # Risk management — balance loaded from account_state.json, not .env
        "ACCOUNT_BALANCE": _get_cached_account_balance(),
        "RISK_PER_TRADE_PERCENT": float(os.getenv("RISK_PER_TRADE_PERCENT", "2")),
        "MAX_SIMULTANEOUS_TRADES": int(os.getenv("MAX_SIMULTANEOUS_TRADES", "6")),
        "MIN_EVENT_LIQUIDITY_USD": float(os.getenv("MIN_EVENT_LIQUIDITY_USD", "500")),

        # Logging
        "LOG_TO_DRIVE": os.getenv("LOG_TO_DRIVE", "true").lower() == "true",
        "LOG_TO_CONSOLE": os.getenv("LOG_TO_CONSOLE", "true").lower() == "true",
        "ZERO_DISK_LOGGING": os.getenv("ZERO_DISK_LOGGING", "false").lower() == "true",
        "DAILY_REPORT_TIME": os.getenv("DAILY_REPORT_TIME", "09:00"),
        "DAILY_REPORT_EMAIL": os.getenv("DAILY_REPORT_EMAIL", "true").lower() == "true",

        # API behaviour
        "API_TIMEOUT_SECONDS": int(os.getenv("API_TIMEOUT_SECONDS", "10")),
        "API_RETRY_COUNT": int(os.getenv("API_RETRY_COUNT", "3")),
        "API_RETRY_BACKOFF_SECONDS": int(os.getenv("API_RETRY_BACKOFF_SECONDS", "5")),

        # Position management (backward-compat keys)
        "POSITION_TARGET_MULTIPLIER":    float(os.getenv("POSITION_TARGET_MULTIPLIER",   "1.5")),
        "POSITION_STOP_LOSS_MULTIPLIER": float(os.getenv("POSITION_STOP_LOSS_MULTIPLIER", "0.50")),
        "POSITION_HOLD_TIME_HOURS":      int(os.getenv("POSITION_HOLD_TIME_HOURS",        "24")),

        # Logging level
        "LOG_LEVEL": os.getenv("LOG_LEVEL", "INFO"),

        # ZiSi intelligence params (mirrored from module-level constants)
        "ASSETS": ASSETS,
        "TIMEFRAMES": TIMEFRAMES,
        "TIME_GATE_UTC": TIME_GATE_UTC,
        "INVERSION_WINDOW": INVERSION_WINDOW,
        "INVERSION_TRIGGER_WR": INVERSION_TRIGGER_WR,
        "INVERSION_RECOVERY_WR": INVERSION_RECOVERY_WR,
        "DUAL_ENTRY_MAX_COMBINED": DUAL_ENTRY_MAX_COMBINED,
        "CIRCUIT_BREAKER_LOSSES": CIRCUIT_BREAKER_LOSSES,
        "CIRCUIT_BREAKER_SKIP": CIRCUIT_BREAKER_SKIP,
        "MAX_DAILY_LOSS_PCT": MAX_DAILY_LOSS_PCT,
        "WARMUP_SECONDS": WARMUP_SECONDS,
        "WARMUP_MIN_TICKS": WARMUP_MIN_TICKS,
        "WARMUP_MAX_JUMP": WARMUP_MAX_JUMP,
        "RECONCILE_INTERVAL": RECONCILE_INTERVAL,
        "MAX_OPEN_PER_ASSET": MAX_OPEN_PER_ASSET,
        "MAX_TOTAL_OPEN": MAX_TOTAL_OPEN,
        "FV_NIGHT_SESSION_START_UTC": int(os.getenv("FV_NIGHT_SESSION_START_UTC", "2")),
        "FV_NIGHT_SESSION_END_UTC": int(os.getenv("FV_NIGHT_SESSION_END_UTC", "9")),

        # Overlays
        "OVERLAY_C_ENABLED": os.getenv("OVERLAY_C_ENABLED", "true").lower() == "true",
        "OVERLAY_C_SPEC_BUDGET_PCT": float(os.getenv("OVERLAY_C_SPEC_BUDGET_PCT", "0.01")),
        "OVERLAY_C_MAX_UNDERDOG_PRICE": float(os.getenv("OVERLAY_C_MAX_UNDERDOG_PRICE", "0.20")),
        "OVERLAY_B_ENABLED": os.getenv("OVERLAY_B_ENABLED", "true").lower() == "true",
        "OVERLAY_B_FREEZE_MIDPOINTS": os.getenv("OVERLAY_B_FREEZE_MIDPOINTS", "false").lower() == "true", # default false
        "OVERLAY_B_TREND_ALIGNMENT_THRESHOLD": int(os.getenv("OVERLAY_B_TREND_ALIGNMENT_THRESHOLD", "3")),
        "OVERLAY_B_ADX_THRESHOLD": float(os.getenv("OVERLAY_B_ADX_THRESHOLD", "25.0")),

        # Mentor parameters
        "MIN_ENTRY_PRICE": float(os.getenv("MIN_ENTRY_PRICE", str(MIN_ENTRY_PRICE))),
        "MAX_ENTRY_PRICE": float(os.getenv("MAX_ENTRY_PRICE", str(MAX_ENTRY_PRICE))),
        "SWEEPER_MODE_ENABLED": os.getenv("SWEEPER_MODE_ENABLED", str(SWEEPER_MODE_ENABLED)).lower() == "true",
        "CERTAINTY_PRICE_THRESHOLD": float(os.getenv("CERTAINTY_PRICE_THRESHOLD", str(CERTAINTY_PRICE_THRESHOLD))),
        "MAX_HOLD_TIME_SECONDS": int(os.getenv("MAX_HOLD_TIME_SECONDS", str(MAX_HOLD_TIME_SECONDS))),
        "SALVAGE_FLOOR_PRICE": float(os.getenv("SALVAGE_FLOOR_PRICE", str(SALVAGE_FLOOR_PRICE))),
        "DYNAMIC_DECAY_THRESHOLD": float(os.getenv("DYNAMIC_DECAY_THRESHOLD", str(DYNAMIC_DECAY_THRESHOLD))),
        "SHORT_TF_SALVAGE_ENABLED": os.getenv("SHORT_TF_SALVAGE_ENABLED", str(SHORT_TF_SALVAGE_ENABLED)).lower() == "true",
        "SHORT_TF_SALVAGE_FLOOR": float(os.getenv("SHORT_TF_SALVAGE_FLOOR", str(SHORT_TF_SALVAGE_FLOOR))),
        "SHORT_TF_STOP_LOSS_ENABLED": os.getenv("SHORT_TF_STOP_LOSS_ENABLED", str(SHORT_TF_STOP_LOSS_ENABLED)).lower() == "true",
        "SHORT_TF_STOP_LOSS_PCT": float(os.getenv("SHORT_TF_STOP_LOSS_PCT", str(SHORT_TF_STOP_LOSS_PCT))),
        "ALLOWED_TIMEFRAMES": ALLOWED_TIMEFRAMES,
        "PROGRESSIVE_STAKING_SIZES": PROGRESSIVE_STAKING_SIZES,
        "PROGRESSIVE_STAKING_TYPES": PROGRESSIVE_STAKING_TYPES,
        "TIER2_SANDBOX_TYPES": TIER2_SANDBOX_TYPES,
        "TIER2_SANDBOX_EXPOSURE_CAP": TIER2_SANDBOX_EXPOSURE_CAP,
        "TIER2_SWEEPER_SIZE": TIER2_SWEEPER_SIZE,
        "TIER2_LATENCY_ARB_MAX_DELTA_MS": TIER2_LATENCY_ARB_MAX_DELTA_MS,
        "TIER2_NCS_SLIPPAGE_CAP": TIER2_NCS_SLIPPAGE_CAP,
        "TIER2_FV_Z_THRESHOLD": TIER2_FV_Z_THRESHOLD,
        "TIER2_FV_TARGET_Z": TIER2_FV_TARGET_Z,
        "ENABLE_NCS": os.getenv("ENABLE_NCS", "true").lower() == "true",
        "ENABLE_SWEEPER": os.getenv("ENABLE_SWEEPER", "true").lower() == "true",
    }

    # Check required keys
    missing = [k for k in _REQUIRED_KEYS if not raw.get(k)]
    if missing:
        raise ValueError(f"Missing required config keys: {', '.join(missing)}")

    _validate(raw)
    return raw


def _validate(config: dict) -> bool:
    """
    Verify all values are the correct type and within acceptable ranges.

    Raises:
        ValueError: On any invalid value.
    Returns:
        True if all checks pass.
    """
    errors = []

    # URL format check
    url_pattern = re.compile(r"^https?://")
    for url_key in (
        "POLYMARKET_GAMMA_API_URL",
        "POLYMARKET_DATA_API_URL",
        "POLYMARKET_CLOB_API_URL",
    ):
        if not url_pattern.match(config.get(url_key, "")):
            errors.append(f"{url_key} must be a valid URL starting with http(s)://")

    # Balance check
    balance = config.get("ACCOUNT_BALANCE", 0)
    if not isinstance(balance, (int, float)):
        errors.append("ACCOUNT_BALANCE must be a number")

    # Mode check
    if config.get("BOT_MODE") not in ("paper_trading", "live_trading"):
        errors.append("BOT_MODE must be 'paper_trading' or 'live_trading'")

    if errors:
        raise ValueError("Config validation failed:\n  " + "\n  ".join(errors))

    return True


# Keep old name as alias for any callers that used validate_config directly
validate_config = _validate


def get_config(key: str, default=None):
    """
    Retrieve a single config value with an optional fallback.

    Args:
        key: The config key (e.g. 'RISK_PER_TRADE_PERCENT').
        default: Value returned if key is absent.
    Returns:
        The config value or default.
    """
    try:
        cfg = load_config()
        return cfg.get(key, default)
    except Exception:
        return default


def log_config_startup(config: dict | None = None) -> None:
    """
    Print a safe startup summary (no secret values) to the console.
    """
    cfg = config or load_config()
    mode_tag = "PAPER" if cfg["BOT_MODE"] == "paper_trading" else "LIVE"
    print(
        f"BOT STARTING: {cfg['BOT_NAME']} v{cfg['BOT_VERSION']} | "
        f"Account: ${cfg['ACCOUNT_BALANCE']:.0f} | "
        f"Risk: {cfg['RISK_PER_TRADE_PERCENT']:.0f}% | "
        f"Mode: {mode_tag} | "
        f"Max simultaneous positions: {cfg['MAX_SIMULTANEOUS_TRADES']} | "
        f"Assets: {cfg['ASSETS']}"
    )
