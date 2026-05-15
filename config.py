"""
config.py - ZiSi Bot Configuration Loader
Loads, validates, and provides access to all .env configuration.
"""

import os
import re
import logging
from dotenv import load_dotenv
from state_manager import initialize_state, get_current_balance

# ── UTC Hour Weighting ────────────────────────────────────────────────────────
# 22:00-06:00 UTC = geographic advantage window (100% Kelly)
# 06:00-22:00 UTC = heavy bot competition (50% Kelly)
PEAK_TRADING_HOURS_UTC: tuple = (22, 23, 0, 1, 2, 3, 4, 5)
PEAK_KELLY_MULTIPLIER: float = 1.0
OFF_PEAK_KELLY_MULTIPLIER: float = 0.5

# ── Market Filtering — 70/30 Strategy ────────────────────────────────────────
# PRIMARY: BTC + ETH (70% of trades — most liquid, clearest signals)
# SECONDARY: POLITICS (30% — emotional traders, sentiment edge stronger)
PRIMARY_MARKETS: frozenset = frozenset({"BTC", "ETH"})
SECONDARY_MARKETS: frozenset = frozenset({"POLITICS"})
ALLOWED_MARKETS: frozenset = PRIMARY_MARKETS | SECONDARY_MARKETS
MARKET_WEIGHTS: dict = {"BTC": 0.35, "ETH": 0.35, "POLITICS": 0.30}

# Load .env from the bot's directory
_ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
load_dotenv(_ENV_PATH)

# Initialize persistent account state once at import time
_initial_balance = initialize_state()

# Module-level logger (plain print until logging is configured)
_log = logging.getLogger("zisi.config")

# All keys that MUST be present and non-empty
_REQUIRED_KEYS = [
    "POLYMARKET_GAMMA_API_URL",
    "POLYMARKET_DATA_API_URL",
    "POLYMARKET_CLOB_API_URL",
    "NEWSAPI_KEY",
    "GOOGLE_DRIVE_FOLDER_ID",
    "GMAIL_SENDER_EMAIL",
]

# Keys that are secret and must never be printed
_SECRET_KEYS = {
    "NEWSAPI_KEY",
    "KALSHI_API_KEY",
    "GMAIL_APP_PASSWORD",
}


def load_config() -> dict:
    """
    Load all .env variables into a structured Python dict.

    Returns:
        dict: All configuration grouped by domain.
    Raises:
        ValueError: If any required key is missing.
    """
    raw = {
        # Polymarket endpoints
        "POLYMARKET_GAMMA_API_URL": os.getenv("POLYMARKET_GAMMA_API_URL", "https://gamma-api.polymarket.com"),
        "POLYMARKET_DATA_API_URL": os.getenv("POLYMARKET_DATA_API_URL", "https://data-api.polymarket.com"),
        "POLYMARKET_CLOB_API_URL": os.getenv("POLYMARKET_CLOB_API_URL", "https://clob.polymarket.com"),

        # API keys
        "NEWSAPI_KEY": os.getenv("NEWSAPI_KEY", ""),
        "KALSHI_API_KEY": os.getenv("KALSHI_API_KEY", ""),

        # Anthropic / Claude
        "ANTHROPIC_API_KEY": os.getenv("ANTHROPIC_API_KEY", ""),

        # Free AI sentiment fallbacks (auto-detected, no code changes needed)
        "GEMINI_API_KEY": os.getenv("GEMINI_API_KEY", ""),   # Priority 2: Gemini Flash
        "GROQ_API_KEY":   os.getenv("GROQ_API_KEY",   ""),   # Priority 3: Groq Llama 3.3 70B

        # Google integration
        "GOOGLE_DRIVE_FOLDER_ID": os.getenv("GOOGLE_DRIVE_FOLDER_ID", ""),
        "GOOGLE_CREDENTIALS_FILE": os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json"),
        "GMAIL_SENDER_EMAIL": os.getenv("GMAIL_SENDER_EMAIL", ""),
        "GMAIL_APP_PASSWORD": os.getenv("GMAIL_APP_PASSWORD", ""),
        "GMAIL_ENABLED": os.getenv("GMAIL_ENABLED", "true").lower() == "true",

        # Bot meta
        "BOT_NAME": os.getenv("BOT_NAME", "ZiSi"),
        "BOT_VERSION": os.getenv("BOT_VERSION", "1.0"),
        "BOT_MODE": os.getenv("BOT_MODE", "paper_trading"),
        "CHECK_INTERVAL_MINUTES": int(os.getenv("CHECK_INTERVAL_MINUTES", "15")),
        "MAX_CHECK_INTERVAL_MINUTES": int(os.getenv("MAX_CHECK_INTERVAL_MINUTES", "20")),

        # Risk management — balance loaded from account_state.json, not .env
        "ACCOUNT_BALANCE": get_current_balance(),
        "RISK_PER_TRADE_PERCENT": float(os.getenv("RISK_PER_TRADE_PERCENT", "2")),
        "SIGNAL_THRESHOLD": int(os.getenv("SIGNAL_THRESHOLD", "7")),
        "MAX_SIMULTANEOUS_TRADES": int(os.getenv("MAX_SIMULTANEOUS_TRADES", "5")),
        "MIN_EVENT_LIQUIDITY_USD": float(os.getenv("MIN_EVENT_LIQUIDITY_USD", "1000")),

        # Position management
        "POSITION_TARGET_MULTIPLIER": float(os.getenv("POSITION_TARGET_MULTIPLIER", "1.5")),
        "POSITION_STOP_LOSS_MULTIPLIER": float(os.getenv("POSITION_STOP_LOSS_MULTIPLIER", "0.50")),
        "POSITION_HOLD_TIME_HOURS": int(os.getenv("POSITION_HOLD_TIME_HOURS", "24")),

        # Logging
        "LOG_TO_DRIVE": os.getenv("LOG_TO_DRIVE", "true").lower() == "true",
        "LOG_TO_CONSOLE": os.getenv("LOG_TO_CONSOLE", "true").lower() == "true",
        "DAILY_REPORT_TIME": os.getenv("DAILY_REPORT_TIME", "09:00"),
        "DAILY_REPORT_EMAIL": os.getenv("DAILY_REPORT_EMAIL", "true").lower() == "true",

        # API behaviour
        "API_TIMEOUT_SECONDS": int(os.getenv("API_TIMEOUT_SECONDS", "10")),
        "API_RETRY_COUNT": int(os.getenv("API_RETRY_COUNT", "3")),
        "API_RETRY_BACKOFF_SECONDS": int(os.getenv("API_RETRY_BACKOFF_SECONDS", "5")),

        # Logging level
        "LOG_LEVEL": os.getenv("LOG_LEVEL", "INFO"),
    }

    # Check required keys
    missing = [k for k in _REQUIRED_KEYS if not raw.get(k)]
    if missing:
        raise ValueError(f"Missing required config keys: {', '.join(missing)}")

    validate_config(raw)
    return raw


def validate_config(config: dict) -> bool:
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
    for url_key in ("POLYMARKET_GAMMA_API_URL", "POLYMARKET_DATA_API_URL", "POLYMARKET_CLOB_API_URL"):
        if not url_pattern.match(config.get(url_key, "")):
            errors.append(f"{url_key} must be a valid URL starting with http(s)://")

    # Numeric range checks
    balance = config.get("ACCOUNT_BALANCE", 0)
    if not isinstance(balance, (int, float)) or balance <= 0:
        errors.append("ACCOUNT_BALANCE must be > 0")

    risk = config.get("RISK_PER_TRADE_PERCENT", 0)
    if not (1 <= risk <= 5):
        errors.append("RISK_PER_TRADE_PERCENT must be between 1 and 5")

    threshold = config.get("SIGNAL_THRESHOLD", 0)
    if not (5 <= threshold <= 10):
        errors.append("SIGNAL_THRESHOLD must be between 5 and 10")

    max_trades = config.get("MAX_SIMULTANEOUS_TRADES", 0)
    if not (1 <= max_trades <= 10):
        errors.append("MAX_SIMULTANEOUS_TRADES must be between 1 and 10")

    if config.get("BOT_MODE") not in ("paper_trading", "live_trading"):
        errors.append("BOT_MODE must be 'paper_trading' or 'live_trading'")

    if errors:
        raise ValueError("Config validation failed:\n  " + "\n  ".join(errors))

    return True


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
        f"Signal threshold: {cfg['SIGNAL_THRESHOLD']}/10 | "
        f"Max trades: {cfg['MAX_SIMULTANEOUS_TRADES']}"
    )
