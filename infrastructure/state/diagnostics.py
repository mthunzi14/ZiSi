"""
diagnostics.py - ZiSi Route Diagnostics & Self-Diagnosing Execution Loop
"""
import json
import time
import logging
from pathlib import Path
import threading

log = logging.getLogger("zisi.diagnostics")

_DIAGNOSTICS_FILE = Path(__file__).parent / "diagnostics_state.json"
_lock = threading.Lock()

class RouteDiagnostics:
    """
    Tracks and persists trade execution metrics like connection latency, 
    slippage, and asymmetric fills. Automatically scales back trade sizing 
    if degrading API performance is detected.
    """
    def __init__(self):
        self.latency_history = []
        self.slippage_history = []
        self.asymmetric_fills = 0
        self.circuit_breaker_active = False
        self.load_state()
        
        # Always release circuit breaker and start with fresh logs on startup
        # to prevent permanent deadlock lockouts from historical slow runs.
        self.circuit_breaker_active = False
        self.latency_history = []
        self.slippage_history = []
        self.save_state()

    def load_state(self) -> None:
        """Load diagnostics state from file if it exists."""
        if not _DIAGNOSTICS_FILE.exists():
            return
        try:
            with _lock:
                data = json.loads(_DIAGNOSTICS_FILE.read_text(encoding="utf-8"))
            self.latency_history = data.get("latency_history", [])[-50:]
            self.slippage_history = data.get("slippage_history", [])[-50:]
            self.asymmetric_fills = int(data.get("asymmetric_fills", 0))
            self.circuit_breaker_active = bool(data.get("circuit_breaker_active", False))
        except Exception as exc:
            log.warning("[DIAGNOSTICS] Failed to load diagnostics state: %s", exc)

    def save_state(self) -> None:
        """Save diagnostics state atomically to diagnostics_state.json."""
        data = {
            "latency_history": self.latency_history,
            "slippage_history": self.slippage_history,
            "asymmetric_fills": self.asymmetric_fills,
            "circuit_breaker_active": self.circuit_breaker_active,
            "avg_latency_ms": self.get_avg_latency(),
            "avg_slippage_cents": self.get_avg_slippage(),
            "last_updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        }
        try:
            with _lock:
                tmp_path = _DIAGNOSTICS_FILE.with_suffix(".tmp")
                tmp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
                import os
                os.replace(tmp_path, _DIAGNOSTICS_FILE)
        except Exception as exc:
            log.warning("[DIAGNOSTICS] Failed to save diagnostics state: %s", exc)

    def log_execution(self, latency_ms: float, slippage_cents: float, successful_hedge: bool = True) -> None:
        """Record trade execution metrics, recalculate metrics and persist state."""
        self.latency_history.append(latency_ms)
        self.slippage_history.append(slippage_cents)

        # Cap sliding windows to the last 50 execution points
        self.latency_history = self.latency_history[-50:]
        self.slippage_history = self.slippage_history[-50:]

        if not successful_hedge:
            self.asymmetric_fills += 1
            log.critical("[DIAGNOSTICS] ASYMMETRIC FILL TRIGGERED! Total occurrences: %d", self.asymmetric_fills)

        # Check for degradation to act as circuit breaker
        avg_lat = self.get_avg_latency()
        avg_slip = self.get_avg_slippage()

        # Thresholds: Require at least 5 samples, Avg latency > 1000ms OR Avg slippage > 3c triggers gate
        if len(self.latency_history) >= 5 and (avg_lat > 1000.0 or avg_slip > 3.0):
            if not self.circuit_breaker_active:
                log.warning(
                    "[DIAGNOSTICS] Performance degraded (Avg Latency: %.1fms, Slippage: %.2fc) — "
                    "activating trade gate!", avg_lat, avg_slip
                )
                self.circuit_breaker_active = True
        else:
            if self.circuit_breaker_active:
                log.info(
                    "[DIAGNOSTICS] Performance recovered (Avg Latency: %.1fms, Slippage: %.2fc) — "
                    "releasing trade gate.", avg_lat, avg_slip
                )
                self.circuit_breaker_active = False

        self.save_state()

    def get_avg_latency(self) -> float:
        """Return the average latency of the sliding window in milliseconds."""
        if not self.latency_history:
            return 0.0
        return round(sum(self.latency_history) / len(self.latency_history), 2)

    def get_avg_slippage(self) -> float:
        """Return the average slippage of the sliding window in cents."""
        if not self.slippage_history:
            return 0.0
        return round(sum(self.slippage_history) / len(self.slippage_history), 2)

    def get_risk_multiplier(self) -> float:
        """
        Dynamically dampens trade sizes based on connection health.
        - Perfect Health: 1.0
        - Degrading Latency (300-500ms) or Slippage (1.5-3.0c): 0.5 (Half size)
        - Circuit Breaker Active: 0.0 (Trading fully halted)
        """
        if self.circuit_breaker_active:
            return 0.0

        avg_lat = self.get_avg_latency()
        avg_slip = self.get_avg_slippage()

        # If latency is pushing towards boundaries, defensively cut trade size in half
        if avg_lat > 300.0 or avg_slip > 1.5:
            return 0.5
        return 1.0

# Singleton global tracker
global_diagnostics = RouteDiagnostics()
