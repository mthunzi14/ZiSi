"""
Kalshi API Authentication — RSA-PSS SHA-256 signature signing.

Kalshi's API v2 (api.elections.kalshi.com) uses asymmetric RSA-PSS signing.
Auth headers per request:
  KALSHI-ACCESS-KEY       = key ID from .env
  KALSHI-ACCESS-TIMESTAMP = current time in milliseconds (integer)
  KALSHI-ACCESS-SIGNATURE = base64(RSA-PSS-SHA256(timestamp+METHOD+full_path))

where full_path = /trade-api/v2 + endpoint path (e.g. /trade-api/v2/markets?limit=5)
"""
import base64
import logging
import os
import time
from typing import Dict, Tuple

import requests

log = logging.getLogger("zisi.kalshi.auth")

_API_PREFIX = "/trade-api/v2"


class KalshiAuth:
    def __init__(self):
        self.key_id = os.getenv("KALSHI_KEY_ID", "").strip()
        raw_key = os.getenv("KALSHI_PRIVATE_KEY", "").strip().replace("\\n", "\n")

        self.base_url = "https://api.elections.kalshi.com/trade-api/v2"
        self._private_key = None
        self.is_configured = False

        if not self.key_id:
            log.warning("[KALSHI] KALSHI_KEY_ID not set — Kalshi trading disabled")
            return
        if not raw_key:
            log.warning("[KALSHI] KALSHI_PRIVATE_KEY not set — Kalshi trading disabled")
            return

        try:
            from cryptography.hazmat.primitives import serialization
            self._private_key = serialization.load_pem_private_key(
                raw_key.encode("utf-8"),
                password=None,
            )
            self.is_configured = True
            log.info(
                "[KALSHI] RSA-PSS auth configured | key_id=%s... | key_bits=%s",
                self.key_id[:12],
                getattr(self._private_key, "key_size", "?"),
            )
        except Exception as exc:
            log.error("[KALSHI] Failed to load RSA private key: %s", exc)

    # ── Header generation ──────────────────────────────────────────────────────

    def get_headers(self, method: str, path: str) -> Dict[str, str]:
        """
        Build RSA-PSS authenticated headers for a Kalshi API request.

        Args:
            method: HTTP method in uppercase ("GET", "POST", …)
            path:   Endpoint path relative to base_url, e.g. "/markets?limit=50"
                    The /trade-api/v2 prefix is added automatically for signing.
        """
        if not self.is_configured or self._private_key is None:
            return {"Content-Type": "application/json"}

        timestamp_ms = int(time.time() * 1000)
        # Signature covers the full path from domain root (not from base_url)
        full_path = f"{_API_PREFIX}{path}"
        signature = self._sign(timestamp_ms, method.upper(), full_path)

        return {
            "KALSHI-ACCESS-KEY":       self.key_id,
            "KALSHI-ACCESS-TIMESTAMP": str(timestamp_ms),
            "KALSHI-ACCESS-SIGNATURE": signature,
            "Content-Type":            "application/json",
        }

    def _sign(self, timestamp_ms: int, method: str, full_path: str) -> str:
        """RSA-PSS SHA-256 sign the canonical message; return base64 string."""
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding

        message = f"{timestamp_ms}{method}{full_path}".encode("utf-8")
        sig_bytes = self._private_key.sign(
            message,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH,
            ),
            hashes.SHA256(),
        )
        return base64.b64encode(sig_bytes).decode("utf-8")

    # ── Connection test ────────────────────────────────────────────────────────

    def validate_connection(self) -> Tuple[bool, str]:
        """Return (ok, message). Hits /portfolio/balance as a live auth check."""
        if not self.is_configured:
            return False, "RSA key not loaded"
        try:
            path = "/portfolio/balance"
            resp = requests.get(
                f"{self.base_url}{path}",
                headers=self.get_headers("GET", path),
                timeout=8,
            )
            if resp.status_code == 200:
                data = resp.json()
                return True, f"Connected | balance={data.get('balance', 0)}"
            return False, f"HTTP {resp.status_code}: {resp.text[:120]}"
        except Exception as exc:
            return False, f"Connection error: {exc}"
