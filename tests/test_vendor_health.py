"""Pruebas de vendor_health.py — HTTP health checks con caché TTL."""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import vendor_health  # noqa: E402


class CheckVendorHealthyTest(unittest.TestCase):
    def test_check_vendor_healthy_when_200(self) -> None:
        """Verify check_vendor() returns 'Healthy' for 2xx status."""
        mock_resp = mock.Mock()
        mock_resp.status_code = 200

        with mock.patch("vendor_health.requests.get", return_value=mock_resp):
            checker = vendor_health.VendorHealthChecker()
            result = checker.check_vendor("https://api.example.com/health")

        self.assertEqual(result["status"], "Healthy")
        self.assertEqual(result["status_code"], 200)
        self.assertIn("response_ms", result)
        self.assertIn("checked_at", result)

    def test_check_vendor_healthy_when_301(self) -> None:
        """Verify check_vendor() returns 'Healthy' for 3xx status."""
        mock_resp = mock.Mock()
        mock_resp.status_code = 301

        with mock.patch("vendor_health.requests.get", return_value=mock_resp):
            checker = vendor_health.VendorHealthChecker()
            result = checker.check_vendor("https://api.example.com")

        self.assertEqual(result["status"], "Healthy")
        self.assertEqual(result["status_code"], 301)

    def test_check_vendor_down_on_500(self) -> None:
        """Verify check_vendor() returns 'Down' for 5xx status."""
        mock_resp = mock.Mock()
        mock_resp.status_code = 500

        with mock.patch("vendor_health.requests.get", return_value=mock_resp):
            checker = vendor_health.VendorHealthChecker()
            result = checker.check_vendor("https://api.example.com")

        self.assertEqual(result["status"], "Down")
        self.assertEqual(result["status_code"], 500)
        self.assertIn("reason", result)
        self.assertIn("HTTP 500", result["reason"])

    def test_check_vendor_down_on_404(self) -> None:
        """Verify check_vendor() returns 'Down' for 4xx status."""
        mock_resp = mock.Mock()
        mock_resp.status_code = 404

        with mock.patch("vendor_health.requests.get", return_value=mock_resp):
            checker = vendor_health.VendorHealthChecker()
            result = checker.check_vendor("https://api.example.com")

        self.assertEqual(result["status"], "Down")
        self.assertEqual(result["status_code"], 404)


class CheckVendorDegradedTest(unittest.TestCase):
    def test_check_vendor_degraded_on_timeout(self) -> None:
        """Verify check_vendor() returns 'Degraded' for timeout."""
        import requests

        with mock.patch(
            "vendor_health.requests.get",
            side_effect=requests.Timeout("Connection timed out"),
        ):
            checker = vendor_health.VendorHealthChecker()
            result = checker.check_vendor(
                "https://api.example.com", timeout_sec=5.0
            )

        self.assertEqual(result["status"], "Degraded")
        self.assertEqual(result["reason"], "timeout")
        self.assertAlmostEqual(result["response_ms"], 5000.0, delta=100)

    def test_check_vendor_down_on_connection_error(self) -> None:
        """Verify check_vendor() returns 'Down' for connection error."""
        import requests

        with mock.patch(
            "vendor_health.requests.get",
            side_effect=requests.ConnectionError("Connection refused"),
        ):
            checker = vendor_health.VendorHealthChecker()
            result = checker.check_vendor("https://api.example.com")

        self.assertEqual(result["status"], "Down")
        self.assertIn("Connection refused", result["reason"])

    def test_check_vendor_down_on_generic_exception(self) -> None:
        """Verify check_vendor() returns 'Down' for any other exception."""
        with mock.patch(
            "vendor_health.requests.get", side_effect=ValueError("Bad URL")
        ):
            checker = vendor_health.VendorHealthChecker()
            result = checker.check_vendor("https://api.example.com")

        self.assertEqual(result["status"], "Down")
        self.assertIn("Bad URL", result["reason"])


class CacheTest(unittest.TestCase):
    def test_cache_hits_for_repeated_url(self) -> None:
        """Verify check_vendor() caches results (requests.get called once)."""
        mock_resp = mock.Mock()
        mock_resp.status_code = 200

        with mock.patch(
            "vendor_health.requests.get", return_value=mock_resp
        ) as mock_get:
            checker = vendor_health.VendorHealthChecker(ttl_seconds=300)
            result1 = checker.check_vendor("https://api.example.com")
            result2 = checker.check_vendor("https://api.example.com")

        # requests.get should be called only once (second call hits cache)
        self.assertEqual(mock_get.call_count, 1)
        self.assertEqual(result1["status"], "Healthy")
        self.assertEqual(result2["status"], "Healthy")

    def test_cache_expires_after_ttl(self) -> None:
        """Verify check_vendor() re-probes after TTL expires."""
        mock_resp = mock.Mock()
        mock_resp.status_code = 200

        with mock.patch(
            "vendor_health.requests.get", return_value=mock_resp
        ) as mock_get:
            checker = vendor_health.VendorHealthChecker(ttl_seconds=0.1)
            result1 = checker.check_vendor("https://api.example.com")
            time.sleep(0.2)  # Wait for TTL to expire
            result2 = checker.check_vendor("https://api.example.com")

        # requests.get should be called twice (second call after TTL)
        self.assertEqual(mock_get.call_count, 2)

    def test_errors_never_cached(self) -> None:
        """Verify check_vendor() does NOT cache error results."""
        import requests

        mock_resp_success = mock.Mock()
        mock_resp_success.status_code = 200

        with mock.patch("vendor_health.requests.get") as mock_get:
            mock_get.side_effect = [
                requests.Timeout("First timeout"),
                mock_resp_success,  # Second call succeeds
            ]
            checker = vendor_health.VendorHealthChecker(ttl_seconds=300)
            result1 = checker.check_vendor("https://api.example.com")
            result2 = checker.check_vendor("https://api.example.com")

        # First call timed out (Degraded)
        self.assertEqual(result1["status"], "Degraded")
        # Second call hit the live endpoint (not cached), got 200
        self.assertEqual(result2["status"], "Healthy")
        # requests.get called twice because error wasn't cached
        self.assertEqual(mock_get.call_count, 2)

    def test_down_status_also_not_cached(self) -> None:
        """Verify check_vendor() does NOT cache 'Down' status either."""
        mock_resp_bad = mock.Mock()
        mock_resp_bad.status_code = 500

        mock_resp_good = mock.Mock()
        mock_resp_good.status_code = 200

        with mock.patch("vendor_health.requests.get") as mock_get:
            mock_get.side_effect = [
                mock_resp_bad,  # First call returns 500
                mock_resp_good,  # Second call returns 200
            ]
            checker = vendor_health.VendorHealthChecker(ttl_seconds=300)
            result1 = checker.check_vendor("https://api.example.com")
            result2 = checker.check_vendor("https://api.example.com")

        # First call was 500 (Down), not cached
        self.assertEqual(result1["status"], "Down")
        # Second call hit live endpoint, got 200
        self.assertEqual(result2["status"], "Healthy")
        # requests.get called twice because Down wasn't cached
        self.assertEqual(mock_get.call_count, 2)


class CheckAllTest(unittest.TestCase):
    def test_check_all_multiple_urls(self) -> None:
        """Verify check_all() probes multiple endpoints."""
        mock_resp = mock.Mock()
        mock_resp.status_code = 200

        with mock.patch("vendor_health.requests.get", return_value=mock_resp):
            checker = vendor_health.VendorHealthChecker()
            results = checker.check_all(
                ["https://api1.example.com", "https://api2.example.com"]
            )

        self.assertEqual(len(results), 2)
        for url, result in results.items():
            self.assertEqual(result["status"], "Healthy")

    def test_check_all_mixed_results(self) -> None:
        """Verify check_all() handles mixed success/failure."""
        import requests

        with mock.patch("vendor_health.requests.get") as mock_get:
            mock_resp = mock.Mock()
            mock_resp.status_code = 200
            mock_get.side_effect = [mock_resp, requests.Timeout("timeout")]
            checker = vendor_health.VendorHealthChecker()
            results = checker.check_all(
                ["https://api1.example.com", "https://api2.example.com"]
            )

        self.assertEqual(results["https://api1.example.com"]["status"], "Healthy")
        self.assertEqual(results["https://api2.example.com"]["status"], "Degraded")


class ModuleLevelFunctionsTest(unittest.TestCase):
    def test_module_level_check_vendor(self) -> None:
        """Verify module-level check_vendor() convenience function."""
        mock_resp = mock.Mock()
        mock_resp.status_code = 200

        with mock.patch("vendor_health.requests.get", return_value=mock_resp):
            result = vendor_health.check_vendor("https://api.example.com")

        self.assertEqual(result["status"], "Healthy")

    def test_module_level_check_all(self) -> None:
        """Verify module-level check_all() convenience function."""
        mock_resp = mock.Mock()
        mock_resp.status_code = 200

        with mock.patch("vendor_health.requests.get", return_value=mock_resp):
            results = vendor_health.check_all(
                ["https://api1.example.com", "https://api2.example.com"]
            )

        self.assertEqual(len(results), 2)


if __name__ == "__main__":
    unittest.main()
