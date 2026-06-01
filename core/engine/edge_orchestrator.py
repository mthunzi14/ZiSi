"""
edge_orchestrator.py - ZiSi Edge Architecture Master Integration Layer

Central orchestrator that initializes, coordinates, and queries all Edge
Architecture modules (A through M) to produce a unified trading decision
context. This is the single integration point for updown_engine.py.

Usage:
    from core.engine.edge_orchestrator import EdgeOrchestrator

    orchestrator = EdgeOrchestrator()
    ctx = await orchestrator.get_trade_context(session, asset, direction, signal, market)
    # ctx contains: regime, kelly_mult, confluence_score, heat_mult, sentiment,
    #               whale_mult, antifragile_mult, cascades, liquidity_levels
"""

import logging
import time
from typing import Dict, List, Optional

log = logging.getLogger("zisi.edge_orchestrator")


class EdgeOrchestrator:
    """
    Master integration layer for all Edge Architecture modules.

    Initializes all modules lazily on first use and provides a unified
    API for the engine to query all edge signals in a single call.
    """

    def __init__(self):
        self._regime_detector = None      # A: Regime-Shift Detector
        self._cross_asset = None          # B: Cross-Asset Signal Propagation
        self._confluence = None           # G: Multi-Timeframe Confluence Engine
        self._liquidity = None            # F: Liquidity Heatmap
        self._vol_surface = None          # E: Volatility Surface Analysis
        self._whale_tracker = None        # J: On-Chain Whale Tracking
        self._portfolio_heat = None       # L: Portfolio Heat Management
        self._antifragile = None          # M: Anti-Fragile Recovery System
        self._rl_exit = None              # I: RL Exit Optimizer
        self._initialized = False
        log.info("[EDGE] EdgeOrchestrator created — modules will initialize lazily")

    def _ensure_initialized(self) -> None:
        """Lazily initialize all edge modules on first use."""
        if self._initialized:
            return

        log.info("[EDGE] Initializing all Edge Architecture modules...")

        # A: Enhanced Regime Detector
        try:
            from core.engine.regime_detector import RegimeDetector
            self._regime_detector = RegimeDetector(timeframe="5m", atr_window=14)
            log.info("[EDGE] ✅ Module A (Regime Detector) initialized")
        except Exception as e:
            log.warning("[EDGE] ❌ Module A (Regime Detector) failed: %s", e)

        # B: Cross-Asset Signal Propagation
        try:
            from core.engine.cross_asset_propagator import CrossAssetPropagator
            self._cross_asset = CrossAssetPropagator()
            log.info("[EDGE] ✅ Module B (Cross-Asset Propagator) initialized")
        except Exception as e:
            log.warning("[EDGE] ❌ Module B (Cross-Asset Propagator) failed: %s", e)

        # G: Multi-Timeframe Confluence Engine
        try:
            from core.engine.confluence_engine import ConfluenceEngine
            self._confluence = ConfluenceEngine()
            log.info("[EDGE] ✅ Module G (Confluence Engine) initialized")
        except Exception as e:
            log.warning("[EDGE] ❌ Module G (Confluence Engine) failed: %s", e)

        # F: Liquidity Heatmap
        try:
            from core.engine.liquidity_heatmap import LiquidityHeatmap
            self._liquidity = LiquidityHeatmap()
            log.info("[EDGE] ✅ Module F (Liquidity Heatmap) initialized")
        except Exception as e:
            log.warning("[EDGE] ❌ Module F (Liquidity Heatmap) failed: %s", e)

        # E: Volatility Surface Analysis
        try:
            from core.engine.volatility_surface import VolatilitySurface
            self._vol_surface = VolatilitySurface()
            log.info("[EDGE] ✅ Module E (Volatility Surface) initialized")
        except Exception as e:
            log.warning("[EDGE] ❌ Module E (Volatility Surface) failed: %s", e)

        # J: On-Chain Whale Tracking
        try:
            from core.engine.whale_tracker import WhaleTracker
            self._whale_tracker = WhaleTracker()
            log.info("[EDGE] ✅ Module J (Whale Tracker) initialized")
        except Exception as e:
            log.warning("[EDGE] ❌ Module J (Whale Tracker) failed: %s", e)

        # L: Portfolio Heat Management
        try:
            from core.risk.portfolio_heat import PortfolioHeatManager
            self._portfolio_heat = PortfolioHeatManager()
            log.info("[EDGE] ✅ Module L (Portfolio Heat) initialized")
        except Exception as e:
            log.warning("[EDGE] ❌ Module L (Portfolio Heat) failed: %s", e)

        # M: Anti-Fragile Recovery System
        try:
            from core.risk.antifragile import AntifragileSystem
            self._antifragile = AntifragileSystem()
            log.info("[EDGE] ✅ Module M (Anti-Fragile) initialized")
        except Exception as e:
            log.warning("[EDGE] ❌ Module M (Anti-Fragile) failed: %s", e)

        # I: RL Exit Optimizer
        try:
            from core.ml.rl_exit_optimizer import RLExitOptimizer
            self._rl_exit = RLExitOptimizer()
            log.info("[EDGE] ✅ Module I (RL Exit Optimizer) initialized")
        except Exception as e:
            log.warning("[EDGE] ❌ Module I (RL Exit Optimizer) failed: %s", e)

        self._initialized = True
        log.info("[EDGE] All Edge Architecture modules initialized")

    # ── Master Context Builder ────────────────────────────────────────────────

    async def get_trade_context(
        self,
        session,  # aiohttp.ClientSession
        asset: str,
        direction: str,
        signal: Dict,
        market: Dict,
        current_price: float = 0.0,
    ) -> Dict:
        """
        Query all edge modules and return a unified trade context.

        This is the SINGLE call point for updown_engine.py. All edge
        intelligence is gathered here and returned as a flat dict.

        Returns:
            Dict with keys:
                regime_name, regime_kelly, hurdle_mult, exit_strategy,
                confluence_score, confluence_boost,
                heat_mult, heat_score,
                sentiment_score, sentiment_modifier,
                whale_mult, whale_pressure,
                antifragile_mult, aggression_state,
                cascade_signals, liquidity_levels,
                combined_confidence_boost
        """
        self._ensure_initialized()

        ctx = {
            # Defaults (safe fallbacks if any module fails)
            "regime_name": "NORMAL",
            "regime_kelly": 1.0,
            "hurdle_mult": 1.0,
            "exit_strategy": "fixed_target",
            "confluence_score": 2,
            "confluence_boost": 0.0,
            "heat_mult": 1.0,
            "heat_score": 0.0,
            "sentiment_score": 0.0,
            "sentiment_modifier": 0.0,
            "whale_mult": 1.0,
            "whale_pressure": 0.0,
            "antifragile_mult": 1.0,
            "aggression_state": "normal",
            "cascade_signals": [],
            "liquidity_levels": {},
            "combined_confidence_boost": 0.0,
        }

        # ── A: Regime Detection ──────────────────────────────────────────────
        try:
            if self._regime_detector:
                if current_price > 0:
                    self._regime_detector.update_price(current_price, symbol=asset)
                status = self._regime_detector.get_status()
                ctx["regime_name"] = status.get("regime", "NORMAL")
                ctx["regime_kelly"] = self._regime_detector.kelly_multiplier
                ctx["hurdle_mult"] = getattr(self._regime_detector, "hurdle_multiplier", 1.0)
                ctx["exit_strategy"] = getattr(self._regime_detector, "exit_strategy", "fixed_target")
        except Exception as e:
            log.debug("[EDGE] Regime query failed: %s", e)

        # ── G: Multi-Timeframe Confluence ────────────────────────────────────
        try:
            if self._confluence:
                conf_result = await self._confluence.get_confluence(session, asset, direction)
                ctx["confluence_score"] = conf_result.get("score", 2)
                ctx["confluence_boost"] = conf_result.get("win_prob_boost", 0.0)
        except Exception as e:
            log.debug("[EDGE] Confluence query failed: %s", e)

        # ── E: Volatility Surface ────────────────────────────────────────────
        try:
            if self._vol_surface:
                await self._vol_surface.update(session, asset, current_price)
                sentiment = self._vol_surface.get_sentiment(asset)
                ctx["sentiment_score"] = sentiment.get("sentiment_score", 0.0)
                ctx["sentiment_modifier"] = sentiment.get("confidence_modifier", 0.0)
        except Exception as e:
            log.debug("[EDGE] Volatility surface query failed: %s", e)

        # ── J: Whale Tracking ────────────────────────────────────────────────
        try:
            if self._whale_tracker:
                await self._whale_tracker.update(session, asset)
                whale = self._whale_tracker.get_whale_signal(asset)
                ctx["whale_mult"] = whale.get("confidence_multiplier", 1.0)
                ctx["whale_pressure"] = whale.get("whale_pressure", 0.0)
        except Exception as e:
            log.debug("[EDGE] Whale tracker query failed: %s", e)

        # ── L: Portfolio Heat ────────────────────────────────────────────────
        try:
            if self._portfolio_heat:
                if current_price > 0:
                    self._portfolio_heat.update_prices({asset: current_price})
                ctx["heat_mult"] = self._portfolio_heat.get_heat_multiplier()
                ctx["heat_score"] = self._portfolio_heat.get_heat_score()
        except Exception as e:
            log.debug("[EDGE] Portfolio heat query failed: %s", e)

        # ── M: Anti-Fragile Recovery ─────────────────────────────────────────
        try:
            if self._antifragile:
                ctx["antifragile_mult"] = self._antifragile.get_aggression_multiplier()
                af_status = self._antifragile.get_status()
                ctx["aggression_state"] = af_status.get("state", "normal")
        except Exception as e:
            log.debug("[EDGE] Anti-fragile query failed: %s", e)

        # ── B: Cross-Asset Cascade Check ─────────────────────────────────────
        try:
            if self._cross_asset and current_price > 0:
                self._cross_asset.update_price(asset, current_price, time.time())
                ctx["cascade_signals"] = self._cross_asset.check_cascade()
        except Exception as e:
            log.debug("[EDGE] Cross-asset cascade query failed: %s", e)

        # ── Combined Confidence Boost ────────────────────────────────────────
        # Aggregate all confidence modifiers into a single boost value
        ctx["combined_confidence_boost"] = round(
            ctx["confluence_boost"]
            + (ctx["sentiment_modifier"] - 1.0)
            + (0.03 if ctx["whale_mult"] > 1.0 else -0.02 if ctx["whale_mult"] < 1.0 else 0.0),
            4,
        )

        log.info(
            "[EDGE] %s %s | regime=%s(×%.2f) confluence=%d(+%.2f) "
            "heat=%.2f anti=%.2f whale=%.2f sentiment=%.2f → boost=%.3f",
            asset, direction,
            ctx["regime_name"], ctx["regime_kelly"],
            ctx["confluence_score"], ctx["confluence_boost"],
            ctx["heat_mult"], ctx["antifragile_mult"],
            ctx["whale_mult"], ctx["sentiment_modifier"],
            ctx["combined_confidence_boost"],
        )

        return ctx

    # ── Trade Outcome Feedback ────────────────────────────────────────────────

    def record_trade_outcome(self, pnl: float, portfolio_value: float) -> None:
        """Feed trade results back into learning modules (M, I)."""
        try:
            if self._antifragile:
                self._antifragile.record_trade_result(pnl, portfolio_value)
        except Exception as e:
            log.debug("[EDGE] Anti-fragile feedback failed: %s", e)

    # ── Exit Recommendation ───────────────────────────────────────────────────

    def get_exit_recommendation(
        self,
        time_in_trade: float,
        current_pnl: float,
        momentum: float = 0.0,
    ) -> Dict:
        """
        Get exit recommendation from RL optimizer + regime-aware strategy.

        Returns:
            Dict with {action: 'HOLD'|'TAKE_PROFIT'|'CUT_LOSS',
                       strategy: regime-based exit strategy,
                       confidence: 0-1}
        """
        result = {
            "action": "HOLD",
            "strategy": "fixed_target",
            "confidence": 0.5,
        }

        # Regime-based exit strategy
        try:
            if self._regime_detector:
                result["strategy"] = getattr(self._regime_detector, "exit_strategy", "fixed_target")
        except Exception:
            pass

        # RL Exit Optimizer recommendation
        try:
            if self._rl_exit:
                regime = "NORMAL"
                if self._regime_detector:
                    regime = self._regime_detector.regime
                rl_rec = self._rl_exit.get_exit_recommendation(
                    time_in_trade=time_in_trade,
                    current_pnl=current_pnl,
                    regime=regime,
                    momentum=momentum,
                )
                if rl_rec:
                    result["action"] = rl_rec.get("action", "HOLD")
                    result["confidence"] = rl_rec.get("confidence", 0.5)
        except Exception as e:
            log.debug("[EDGE] RL exit recommendation failed: %s", e)

        return result

    # ── Status ────────────────────────────────────────────────────────────────

    def get_full_status(self) -> Dict:
        """Return comprehensive status from all edge modules."""
        self._ensure_initialized()
        status = {"initialized": self._initialized, "modules": {}}

        module_map = {
            "A_regime": self._regime_detector,
            "B_cross_asset": self._cross_asset,
            "G_confluence": self._confluence,
            "F_liquidity": self._liquidity,
            "E_vol_surface": self._vol_surface,
            "J_whale": self._whale_tracker,
            "L_heat": self._portfolio_heat,
            "M_antifragile": self._antifragile,
            "I_rl_exit": self._rl_exit,
        }

        for name, module in module_map.items():
            if module and hasattr(module, "get_status"):
                try:
                    status["modules"][name] = module.get_status()
                except Exception as e:
                    status["modules"][name] = {"error": str(e)}
            else:
                status["modules"][name] = {"status": "not_loaded"}

        return status


# ── Global Singleton ─────────────────────────────────────────────────────────
# Single instance shared across the bot
edge_orchestrator = EdgeOrchestrator()
