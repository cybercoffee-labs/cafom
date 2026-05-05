"""Pruebas de api.py — FastAPI endpoints."""

from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import api  # noqa: E402


class HealthzTest(unittest.TestCase):
    def test_healthz_ok(self) -> None:
        """Verify GET /healthz returns ok status."""
        app = api.create_app()
        client = TestClient(app)
        response = client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["service"], "cafom")


class GetAssetsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = Path("/tmp/cafom_test_api")
        self._tmp.mkdir(parents=True, exist_ok=True)
        self._db_path = self._tmp / "test.db"

    def tearDown(self) -> None:
        if self._tmp.exists():
            import shutil
            shutil.rmtree(self._tmp)

    def test_get_assets_empty(self) -> None:
        """Verify GET /assets returns empty list on fresh DB."""
        import asset_tracker
        asset_tracker.init_db(self._db_path)

        app = api.create_app(self._db_path)
        client = TestClient(app)
        response = client.get("/assets")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data, [])

    def test_get_assets_with_status_filter(self) -> None:
        """Verify GET /assets?status=... filters correctly."""
        import json
        import asset_tracker

        # Create DB and add test assets
        asset_tracker.init_db(self._db_path)

        jsonl_path = self._tmp / "assets.jsonl"
        with jsonl_path.open("w", encoding="utf-8") as f:
            f.write(json.dumps({
                "id": "AST-001",
                "product": "Product 1",
                "vendor": "Vendor 1",
                "category": "Test",
                "purchase_date": "2023-01-01",
                "renewal_date": "2024-01-01",
                "contract_term_months": 12,
                "annual_cost_usd": 10000.0,
                "capex_opex": "OPEX",
                "owner": "Owner",
                "status": "Active",
                "health_check_url": "https://example.com/health",
            }) + "\n")
            f.write(json.dumps({
                "id": "AST-002",
                "product": "Product 2",
                "vendor": "Vendor 2",
                "category": "Test",
                "purchase_date": "2023-01-01",
                "renewal_date": "2024-01-01",
                "contract_term_months": 12,
                "annual_cost_usd": 20000.0,
                "capex_opex": "CAPEX",
                "owner": "Owner",
                "status": "Expired",
                "health_check_url": "https://example2.com/health",
            }) + "\n")

        asset_tracker.ingest_assets(jsonl_path, self._db_path)

        app = api.create_app(self._db_path)
        client = TestClient(app)

        # Test filter by Active
        response = client.get("/assets?status=Active")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["status"], "Active")


class PostAssetTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = Path("/tmp/cafom_test_api_post")
        self._tmp.mkdir(parents=True, exist_ok=True)
        self._db_path = self._tmp / "test.db"

    def tearDown(self) -> None:
        if self._tmp.exists():
            import shutil
            shutil.rmtree(self._tmp)

    def _valid_asset(self) -> dict:
        return {
            "id": "AST-001",
            "product": "CrowdStrike Falcon",
            "vendor": "CrowdStrike",
            "category": "Endpoint Detection",
            "purchase_date": "2023-01-01",
            "renewal_date": "2024-01-01",
            "contract_term_months": 12,
            "annual_cost_usd": 50000.0,
            "capex_opex": "OPEX",
            "owner": "Security Team",
            "status": "Active",
            "health_check_url": "https://api.crowdstrike.com/status",
        }

    def test_post_asset_creates(self) -> None:
        """Verify POST /assets creates an asset."""
        import asset_tracker
        asset_tracker.init_db(self._db_path)

        app = api.create_app(self._db_path)
        client = TestClient(app)
        response = client.post("/assets", json=self._valid_asset())

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["id"], "AST-001")
        self.assertEqual(data["status"], "created")

    def test_post_invalid_asset_returns_422(self) -> None:
        """Verify POST /assets with invalid data returns 422."""
        import asset_tracker
        asset_tracker.init_db(self._db_path)

        app = api.create_app(self._db_path)
        client = TestClient(app)

        # Missing required fields
        response = client.post("/assets", json={"id": "BAD-001"})
        self.assertEqual(response.status_code, 422)

    def test_post_asset_renewal_before_purchase_rejected(self) -> None:
        """Verify POST /assets rejects if renewal_date < purchase_date."""
        import asset_tracker
        asset_tracker.init_db(self._db_path)

        app = api.create_app(self._db_path)
        client = TestClient(app)

        bad_asset = self._valid_asset()
        bad_asset["renewal_date"] = "2022-01-01"  # Before purchase_date

        response = client.post("/assets", json=bad_asset)
        self.assertEqual(response.status_code, 422)


class RenewalAlertsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = Path("/tmp/cafom_test_api_alerts")
        self._tmp.mkdir(parents=True, exist_ok=True)
        self._db_path = self._tmp / "test.db"

    def tearDown(self) -> None:
        if self._tmp.exists():
            import shutil
            shutil.rmtree(self._tmp)

    def test_renewals_alerts_returns_structured_response(self) -> None:
        """Verify GET /renewals/alerts returns structured response."""
        import json
        import asset_tracker

        asset_tracker.init_db(self._db_path)

        # Create test assets with various renewal dates
        from datetime import date, timedelta

        today = date.today()
        assets = [
            {
                "id": "AST-001",
                "product": "Product 1",
                "vendor": "Vendor 1",
                "category": "Test",
                "purchase_date": "2023-01-01",
                "renewal_date": (today + timedelta(days=100)).isoformat(),
                "contract_term_months": 12,
                "annual_cost_usd": 10000.0,
                "capex_opex": "OPEX",
                "owner": "Owner",
                "status": "Active",
                "health_check_url": "https://example.com/health",
            },
            {
                "id": "AST-002",
                "product": "Product 2",
                "vendor": "Vendor 2",
                "category": "Test",
                "purchase_date": "2023-01-01",
                "renewal_date": (today + timedelta(days=-5)).isoformat(),
                "contract_term_months": 12,
                "annual_cost_usd": 20000.0,
                "capex_opex": "CAPEX",
                "owner": "Owner",
                "status": "Active",
                "health_check_url": "https://example2.com/health",
            },
        ]

        jsonl_path = self._tmp / "assets.jsonl"
        with jsonl_path.open("w", encoding="utf-8") as f:
            for asset in assets:
                f.write(json.dumps(asset) + "\n")

        asset_tracker.ingest_assets(jsonl_path, self._db_path)

        app = api.create_app(self._db_path)
        client = TestClient(app)

        response = client.get("/renewals/alerts")
        self.assertEqual(response.status_code, 200)
        data = response.json()

        # Verify structure
        self.assertIn("counts", data)
        self.assertIn("alerts", data)
        self.assertIn("GREEN", data["counts"])
        self.assertIn("RED", data["counts"])


class VendorHealthTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = Path("/tmp/cafom_test_api_vendors")
        self._tmp.mkdir(parents=True, exist_ok=True)
        self._db_path = self._tmp / "test.db"

    def tearDown(self) -> None:
        if self._tmp.exists():
            import shutil
            shutil.rmtree(self._tmp)

    def test_vendors_health_returns_summary(self) -> None:
        """Verify GET /vendors/health returns summary structure."""
        import json
        import asset_tracker

        asset_tracker.init_db(self._db_path)

        # Create test asset
        asset = {
            "id": "AST-001",
            "product": "Product 1",
            "vendor": "Vendor 1",
            "category": "Test",
            "purchase_date": "2023-01-01",
            "renewal_date": "2024-01-01",
            "contract_term_months": 12,
            "annual_cost_usd": 10000.0,
            "capex_opex": "OPEX",
            "owner": "Owner",
            "status": "Active",
            "health_check_url": "https://api.example.com/health",
        }

        jsonl_path = self._tmp / "assets.jsonl"
        with jsonl_path.open("w", encoding="utf-8") as f:
            f.write(json.dumps(asset) + "\n")

        asset_tracker.ingest_assets(jsonl_path, self._db_path)

        # Mock vendor_health checker
        mock_result = {
            "https://api.example.com/health": {
                "status": "Healthy",
                "status_code": 200,
                "response_ms": 45.5,
                "checked_at": "2026-05-04T12:00:00Z",
            }
        }

        with mock.patch(
            "api.vendor_health.VendorHealthChecker.check_all",
            return_value=mock_result,
        ):
            app = api.create_app(self._db_path)
            client = TestClient(app)

            response = client.get("/vendors/health")
            self.assertEqual(response.status_code, 200)
            data = response.json()

            # Verify structure
            self.assertIn("healthy", data)
            self.assertIn("degraded", data)
            self.assertIn("down", data)
            self.assertIn("vendors", data)

    def test_vendors_health_empty_when_no_urls(self) -> None:
        """Verify GET /vendors/health returns zero counts when no vendors."""
        import asset_tracker

        asset_tracker.init_db(self._db_path)

        app = api.create_app(self._db_path)
        client = TestClient(app)

        response = client.get("/vendors/health")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["healthy"], 0)
        self.assertEqual(data["degraded"], 0)
        self.assertEqual(data["down"], 0)


if __name__ == "__main__":
    unittest.main()
