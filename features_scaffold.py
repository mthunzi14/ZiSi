"""
features_scaffold.py - ZiSi Feature Flags and Scaffolding.

All 10 feature classes are defined here.  Each is independently togglable
via FeatureFlags.  Set a flag to True when the feature is ready to activate.
"""
import logging
from datetime import datetime, timezone

log = logging.getLogger("zisi.features")


# ---------------------------------------------------------------------------
# Feature flags — toggle features on/off without changing other code
# ---------------------------------------------------------------------------

class FeatureFlags:
    FEATURE_1_LIQUIDITY_CHECKING          = True   # Enabled (enforced by risk_manager)
    FEATURE_2_MARKET_TYPE_CLASSIFICATION  = True   # Enabled (core of this build)
    FEATURE_3_CONFIDENCE_INTERVALS        = False
    FEATURE_4_WIN_LOSS_BREAKDOWN          = False
    FEATURE_5_MARKET_FRESHNESS_FILTER     = False
    FEATURE_6_ASYNC_MARKET_FETCH          = False
    FEATURE_7_SOURCE_CREDIBILITY          = False
    FEATURE_8_SENTIMENT_VALIDATION        = False
    FEATURE_9_CORRELATION_ANALYSIS        = False
    FEATURE_10_DRAWDOWN_PROTECTION        = False


# ---------------------------------------------------------------------------
# Feature 1 — Liquidity gate
# ---------------------------------------------------------------------------

class Feature1_LiquidityChecker:
    """Ensure a market has minimum liquidity before including it in matches."""

    def __init__(self, min_liquidity: float = 10_000):
        self.min_liquidity = min_liquidity

    def is_liquid(self, market: dict) -> bool:
        return float(market.get("liquidity", 0)) >= self.min_liquidity

    def filter(self, markets: list) -> list:
        return [m for m in markets if self.is_liquid(m)]


# ---------------------------------------------------------------------------
# Feature 2 — Market type classifier (already live via PolymarketMatcher)
# ---------------------------------------------------------------------------

class Feature2_MarketTypeClassifier:
    """Classify market type and return the appropriate Kelly multiplier."""

    MULTIPLIERS = {"UP_DOWN": 1.0, "HIT_PRICE": 0.5, "PRICE_RANGE": 0.7, "OTHER": 0.8}

    def classify(self, market: dict) -> str:
        return market.get("market_type", "OTHER")

    def kelly_scale(self, market_type: str) -> float:
        return self.MULTIPLIERS.get(market_type, 0.8)


# ---------------------------------------------------------------------------
# Feature 3 — Confidence interval widening
# ---------------------------------------------------------------------------

class Feature3_ConfidenceIntervals:
    """Widen a point confidence estimate to an (lower, upper) interval."""

    MARGIN = 0.075  # ±7.5 %

    def interval(self, confidence: float) -> tuple:
        return (
            max(0.0, confidence - self.MARGIN),
            min(1.0, confidence + self.MARGIN),
        )


# ---------------------------------------------------------------------------
# Feature 4 — Win/loss breakdown by market type and asset
# ---------------------------------------------------------------------------

class Feature4_WinLossAnalyzer:
    """Track and query win/loss outcomes by market type and asset."""

    def __init__(self):
        self._log: dict = {}

    def record(self, market_type: str, asset: str, result: str) -> None:
        key = f"{market_type}_{asset}"
        if key not in self._log:
            self._log[key] = {"wins": 0, "losses": 0}
        if result.upper() == "WIN":
            self._log[key]["wins"] += 1
        else:
            self._log[key]["losses"] += 1

    def win_rate(self, market_type: str = None, asset: str = None) -> float:
        key = f"{market_type or '*'}_{asset or '*'}"
        data = self._log.get(key, {})
        total = data.get("wins", 0) + data.get("losses", 0)
        return data.get("wins", 0) / total if total else 0.0


# ---------------------------------------------------------------------------
# Feature 5 — Market freshness filter (skip near-expiry markets)
# ---------------------------------------------------------------------------

class Feature5_MarketFreshnessFilter:
    """Skip markets that expire within min_hours_remaining hours."""

    def __init__(self, min_hours_remaining: int = 1):
        self.min_hours = min_hours_remaining

    def is_fresh(self, market: dict) -> bool:
        expires = market.get("resolutionDate") or market.get("expires_at")
        if not expires:
            return True
        try:
            expiry = datetime.fromisoformat(str(expires).replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            return (expiry - now).total_seconds() / 3600 >= self.min_hours
        except Exception:
            return True


# ---------------------------------------------------------------------------
# Feature 6 — Async market fetcher stub (reduces cycle lag)
# ---------------------------------------------------------------------------

class Feature6_AsyncMarketFetcher:
    """Parallel async fetch from multiple market sources."""

    async def fetch_all(self, sources: list) -> list:
        # Implementation: use asyncio.gather over per-source coroutines
        return []


# ---------------------------------------------------------------------------
# Feature 7 — Source credibility weighting
# ---------------------------------------------------------------------------

class Feature7_SourceCredibility:
    """Weight news signal confidence by outlet credibility score."""

    SCORES: dict = {
        "bloomberg": 0.95,
        "reuters": 0.95,
        "cnbc": 0.90,
        "coindesk": 0.85,
        "cointelegraph": 0.80,
        "twitter": 0.40,
        "unknown": 0.50,
    }

    def weight(self, source: str) -> float:
        return self.SCORES.get(source.lower(), 0.50)

    def apply(self, confidence: float, source: str) -> float:
        return round(confidence * self.weight(source), 4)


# ---------------------------------------------------------------------------
# Feature 8 — Sentiment model validator
# ---------------------------------------------------------------------------

class Feature8_SentimentValidator:
    """Tracks model predictions vs. resolved outcomes for accuracy scoring."""

    def __init__(self):
        self._predictions: list = []

    def record_prediction(self, sentiment: str, confidence: float) -> None:
        self._predictions.append({"sentiment": sentiment, "confidence": confidence})

    def accuracy(self) -> float:
        # Placeholder: requires resolution data to compute real accuracy
        return 0.0


# ---------------------------------------------------------------------------
# Feature 9 — Asset correlation detector
# ---------------------------------------------------------------------------

class Feature9_CorrelationDetector:
    """Detects correlated assets to prevent double-leverage."""

    # Simplified static correlation table (BTC/ETH highly correlated)
    _CORR: dict = {
        ("BTC", "ETH"): 0.85,
        ("ETH", "BTC"): 0.85,
        ("BTC", "SOL"): 0.70,
    }

    def coefficient(self, asset1: str, asset2: str) -> float:
        return self._CORR.get((asset1.upper(), asset2.upper()), 0.0)

    def are_correlated(self, asset1: str, asset2: str, threshold: float = 0.75) -> bool:
        return self.coefficient(asset1, asset2) >= threshold


# ---------------------------------------------------------------------------
# Feature 10 — Drawdown kill-switch
# ---------------------------------------------------------------------------

class Feature10_DrawdownProtector:
    """Halts new trades if daily loss exceeds the configured threshold."""

    def __init__(self, max_daily_loss_pct: float = 0.05):
        self.max_daily_loss_pct = max_daily_loss_pct
        self.daily_pnl: float = 0.0

    def record_trade(self, pnl: float) -> None:
        self.daily_pnl += pnl

    def should_trade(self, account_balance: float) -> bool:
        if account_balance <= 0:
            return False
        loss_pct = abs(min(self.daily_pnl, 0)) / account_balance
        if loss_pct >= self.max_daily_loss_pct:
            log.warning(
                "[DRAWDOWN-STOP] Daily loss %.1f%% >= %.1f%% threshold — halting new trades",
                loss_pct * 100, self.max_daily_loss_pct * 100,
            )
            return False
        return True
