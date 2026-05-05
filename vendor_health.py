"""CAFOM vendor health probing — TTL-cached HTTP status checks.

Adapted from ``batman_flow_engine/core/aquaman.py``. Manages vendor endpoint
availability via periodic health checks, caches results with TTL, and never
caches errors (force retry on subsequent failures).

KEEP from source:
* TTL cache pattern with threading.Lock for thread safety
* _error_result() helper to build failure response dict
* "never cache errors" rule (errors bypass cache, forced re-probe)

CHANGE:
* Replace CCXT (exchange order book) with requests HTTP probing
* check_vendor(url) → requests.get(url, timeout=5) with status categorization
* 200 ≤ status < 400 → "Healthy"; Timeout → "Degraded"; else → "Down"
* Add checked_at ISO timestamp to all responses

REMOVE:
* orderbook depth, _estimate_slippage, _depth_within_band, _infer_exchange_id
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

try:
    import requests
except ImportError as exc:
    raise ImportError(
        "vendor_health requires requests. Install with: pip install requests"
    ) from exc

logger = logging.getLogger("cafom.vendor_health")

_TTL_SECONDS = 300  # 5-minute cache TTL


@dataclass(frozen=True)
class VendorHealthResult:
    """Result of a vendor health check."""

    status: str  # "Healthy", "Degraded", "Down"
    status_code: int | None = None
    response_ms: float | None = None
    reason: str | None = None
    checked_at: str | None = None


class VendorHealthChecker:
    """TTL-cached HTTP health probes for vendor endpoints."""

    def __init__(self, ttl_seconds: float = _TTL_SECONDS) -> None:
        """Initialize checker with cache TTL (default 5 min)."""
        self._cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._ttl = ttl_seconds
        self._lock = threading.Lock()

    def check_vendor(
        self, url: str, *, timeout_sec: float = 5.0
    ) -> dict[str, Any]:
        """
        Probe a vendor endpoint and return health status.

        Returns dict with:
        - status: "Healthy" (2xx/3xx), "Degraded" (timeout), "Down" (error/4xx/5xx)
        - status_code: HTTP status (if response received)
        - response_ms: round-trip time in milliseconds (if response received)
        - reason: error reason (if error)
        - checked_at: ISO timestamp of check
        """
        with self._lock:
            now = time.time()
            if url in self._cache:
                cached_time, cached_result = self._cache[url]
                if now - cached_time < self._ttl:
                    return cached_result

        # Not cached or expired; probe live
        result = self._probe(url, timeout_sec=timeout_sec)

        # Cache only healthy results (never cache errors or degraded)
        if result.get("status") == "Healthy":
            with self._lock:
                self._cache[url] = (time.time(), result)

        return result

    def check_all(self, urls: list[str]) -> dict[str, dict[str, Any]]:
        """Check multiple vendor endpoints. Returns {url: result_dict, ...}."""
        return {url: self.check_vendor(url) for url in urls}

    def _probe(self, url: str, *, timeout_sec: float = 5.0) -> dict[str, Any]:
        """Perform the actual HTTP probe."""
        start_ms = time.time() * 1000
        try:
            resp = requests.get(url, timeout=timeout_sec)
            elapsed_ms = time.time() * 1000 - start_ms
            if 200 <= resp.status_code < 400:
                return {
                    "status": "Healthy",
                    "status_code": resp.status_code,
                    "response_ms": elapsed_ms,
                    "checked_at": datetime.now(UTC).isoformat(),
                }
            else:
                return {
                    "status": "Down",
                    "status_code": resp.status_code,
                    "response_ms": elapsed_ms,
                    "reason": f"HTTP {resp.status_code}",
                    "checked_at": datetime.now(UTC).isoformat(),
                }
        except requests.Timeout:
            return {
                "status": "Degraded",
                "response_ms": timeout_sec * 1000,
                "reason": "timeout",
                "checked_at": datetime.now(UTC).isoformat(),
            }
        except Exception as exc:
            return {
                "status": "Down",
                "reason": str(exc),
                "checked_at": datetime.now(UTC).isoformat(),
            }


def check_vendor(url: str, *, timeout_sec: float = 5.0) -> dict[str, Any]:
    """Module-level convenience: check a single vendor endpoint."""
    checker = VendorHealthChecker()
    return checker.check_vendor(url, timeout_sec=timeout_sec)


def check_all(urls: list[str]) -> dict[str, dict[str, Any]]:
    """Module-level convenience: check multiple endpoints."""
    checker = VendorHealthChecker()
    return checker.check_all(urls)
