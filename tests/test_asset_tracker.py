"""Pruebas de asset_tracker.py — SQLite persistence."""

from __future__ import annotations

import json
import sys
import unittest
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asset_tracker  # noqa: E402


class InitDbTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = Path("/tmp/cafom_test_db")
        self._tmp.mkdir(parents=True, exist_ok=True)
        self._db_path = self._tmp / "test.db"

    def tearDown(self) -> None:
        if self._db_path.exists():
            self._db_path.unlink()
        if self._tmp.exists():
            import shutil
            shutil.rmtree(self._tmp)

    def test_init_db_creates_assets_table(self) -> None:
        """Verify init_db() creates the assets table."""
        asset_tracker.init_db(self._db_path)
        conn = asset_tracker._get_conn()
        # Temporarily override _DB_PATH for this test
        with mock.patch.object(asset_tracker, "_DB_PATH", self._db_path):
            conn = asset_tracker._get_conn()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='assets'"
            )
            result = cursor.fetchone()
            conn.close()
        self.assertIsNotNone(result, "assets table not created")


class IngestAssetsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = Path("/tmp/cafom_test_ingest")
        self._tmp.mkdir(parents=True, exist_ok=True)
        self._db_path = self._tmp / "test.db"
        self._jsonl_path = self._tmp / "assets.jsonl"
        asset_tracker.init_db(self._db_path)

    def tearDown(self) -> None:
        if self._tmp.exists():
            import shutil
            shutil.rmtree(self._tmp)

    def _write_assets_jsonl(self, assets: list[dict]) -> None:
        """Write a list of asset dicts to JSONL."""
        with self._jsonl_path.open("w", encoding="utf-8") as f:
            for asset in assets:
                f.write(json.dumps(asset) + "\n")

    def _create_test_asset(self, asset_id: str = "AST-001") -> dict:
        """Create a valid test asset."""
        return {
            "id": asset_id,
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

    def test_ingest_writes_assets(self) -> None:
        """Verify ingest_assets() writes to SQLite."""
        assets = [self._create_test_asset("AST-001")]
        self._write_assets_jsonl(assets)

        with mock.patch.object(asset_tracker, "_DB_PATH", self._db_path):
            result = asset_tracker.ingest_assets(self._jsonl_path, self._db_path)

        self.assertEqual(result["scanned"], 1)
        self.assertEqual(result["inserted"], 1)

    def test_ingest_dedupes_by_id(self) -> None:
        """Verify ingest_assets() deduplicates by id."""
        assets = [self._create_test_asset("AST-001")]
        self._write_assets_jsonl(assets)

        with mock.patch.object(asset_tracker, "_DB_PATH", self._db_path):
            result1 = asset_tracker.ingest_assets(self._jsonl_path, self._db_path)
            result2 = asset_tracker.ingest_assets(self._jsonl_path, self._db_path)

        self.assertEqual(result1["inserted"], 1)
        self.assertEqual(result2["inserted"], 0, "Second ingest should insert 0 (already exists)")

    def test_ingest_skips_malformed_lines(self) -> None:
        """Verify ingest_assets() skips malformed JSON."""
        with self._jsonl_path.open("w", encoding="utf-8") as f:
            f.write(json.dumps(self._create_test_asset("AST-001")) + "\n")
            f.write("{ invalid json\n")
            f.write(json.dumps(self._create_test_asset("AST-002")) + "\n")

        with mock.patch.object(asset_tracker, "_DB_PATH", self._db_path):
            result = asset_tracker.ingest_assets(self._jsonl_path, self._db_path)

        # scanned counts successfully parsed assets (ignores malformed lines)
        self.assertEqual(result["scanned"], 2)
        self.assertEqual(result["inserted"], 2)


class GetAssetsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = Path("/tmp/cafom_test_get")
        self._tmp.mkdir(parents=True, exist_ok=True)
        self._db_path = self._tmp / "test.db"
        self._jsonl_path = self._tmp / "assets.jsonl"
        asset_tracker.init_db(self._db_path)

    def tearDown(self) -> None:
        if self._tmp.exists():
            import shutil
            shutil.rmtree(self._tmp)

    def _create_test_asset(self, asset_id: str, status: str = "Active") -> dict:
        return {
            "id": asset_id,
            "product": f"Product {asset_id}",
            "vendor": f"Vendor {asset_id}",
            "category": "Test",
            "purchase_date": "2023-01-01",
            "renewal_date": "2024-01-01",
            "contract_term_months": 12,
            "annual_cost_usd": 50000.0,
            "capex_opex": "OPEX",
            "owner": "Owner",
            "status": status,
            "health_check_url": "https://example.com/health",
        }

    def _setup_assets(self, assets: list[dict]) -> None:
        with self._jsonl_path.open("w", encoding="utf-8") as f:
            for asset in assets:
                f.write(json.dumps(asset) + "\n")
        with mock.patch.object(asset_tracker, "_DB_PATH", self._db_path):
            asset_tracker.ingest_assets(self._jsonl_path, self._db_path)

    def test_get_assets_returns_all(self) -> None:
        """Verify get_assets() returns all assets."""
        assets = [
            self._create_test_asset("AST-001"),
            self._create_test_asset("AST-002"),
        ]
        self._setup_assets(assets)

        with mock.patch.object(asset_tracker, "_DB_PATH", self._db_path):
            result = asset_tracker.get_assets()

        self.assertEqual(len(result), 2)

    def test_get_assets_filters_by_status(self) -> None:
        """Verify get_assets(status=...) filters correctly."""
        assets = [
            self._create_test_asset("AST-001", status="Active"),
            self._create_test_asset("AST-002", status="Expired"),
            self._create_test_asset("AST-003", status="Active"),
        ]
        self._setup_assets(assets)

        with mock.patch.object(asset_tracker, "_DB_PATH", self._db_path):
            result = asset_tracker.get_assets(status="Active")

        self.assertEqual(len(result), 2)
        for asset in result:
            self.assertEqual(asset["status"], "Active")

    def test_get_assets_returns_dict_rows(self) -> None:
        """Verify get_assets() returns dict-like rows."""
        assets = [self._create_test_asset("AST-001")]
        self._setup_assets(assets)

        with mock.patch.object(asset_tracker, "_DB_PATH", self._db_path):
            result = asset_tracker.get_assets()

        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], dict)
        self.assertEqual(result[0]["id"], "AST-001")


class GetRenewalsWithinTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = Path("/tmp/cafom_test_renewals")
        self._tmp.mkdir(parents=True, exist_ok=True)
        self._db_path = self._tmp / "test.db"
        self._jsonl_path = self._tmp / "assets.jsonl"
        asset_tracker.init_db(self._db_path)

    def tearDown(self) -> None:
        if self._tmp.exists():
            import shutil
            shutil.rmtree(self._tmp)

    def _create_test_asset(
        self, asset_id: str, renewal_date: str, cost: float = 50000.0
    ) -> dict:
        return {
            "id": asset_id,
            "product": f"Product {asset_id}",
            "vendor": f"Vendor {asset_id}",
            "category": "Test",
            "purchase_date": "2023-01-01",
            "renewal_date": renewal_date,
            "contract_term_months": 12,
            "annual_cost_usd": cost,
            "capex_opex": "OPEX",
            "owner": "Owner",
            "status": "Active",
            "health_check_url": "https://example.com/health",
        }

    def _setup_assets(self, assets: list[dict]) -> None:
        with self._jsonl_path.open("w", encoding="utf-8") as f:
            for asset in assets:
                f.write(json.dumps(asset) + "\n")
        with mock.patch.object(asset_tracker, "_DB_PATH", self._db_path):
            asset_tracker.ingest_assets(self._jsonl_path, self._db_path)

    def test_renewals_within_returns_in_window(self) -> None:
        """Verify get_renewals_within() returns assets renewing within N days."""
        today = date.today()
        tomorrow = today + timedelta(days=1)
        next_week = today + timedelta(days=7)
        next_month = today + timedelta(days=35)

        assets = [
            self._create_test_asset(
                "AST-001", tomorrow.isoformat()
            ),  # Within 30d
            self._create_test_asset(
                "AST-002", next_week.isoformat()
            ),  # Within 30d
            self._create_test_asset(
                "AST-003", next_month.isoformat()
            ),  # Beyond 30d
        ]
        self._setup_assets(assets)

        with mock.patch.object(asset_tracker, "_DB_PATH", self._db_path):
            result = asset_tracker.get_renewals_within(30)

        self.assertEqual(len(result), 2, "Should find 2 renewals within 30 days")
        ids = {asset["id"] for asset in result}
        self.assertIn("AST-001", ids)
        self.assertIn("AST-002", ids)

    def test_renewals_within_empty_when_none(self) -> None:
        """Verify get_renewals_within() returns empty when no renewals due."""
        next_year = (date.today() + timedelta(days=365)).isoformat()
        assets = [self._create_test_asset("AST-001", next_year)]
        self._setup_assets(assets)

        with mock.patch.object(asset_tracker, "_DB_PATH", self._db_path):
            result = asset_tracker.get_renewals_within(30)

        self.assertEqual(len(result), 0)

    def test_renewals_ordered_by_date(self) -> None:
        """Verify get_renewals_within() returns results ordered by renewal_date."""
        today = date.today()
        tomorrow = today + timedelta(days=1)
        two_days = today + timedelta(days=2)

        assets = [
            self._create_test_asset("AST-002", two_days.isoformat()),
            self._create_test_asset("AST-001", tomorrow.isoformat()),
        ]
        self._setup_assets(assets)

        with mock.patch.object(asset_tracker, "_DB_PATH", self._db_path):
            result = asset_tracker.get_renewals_within(30)

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["id"], "AST-001")
        self.assertEqual(result[1]["id"], "AST-002")


class DailyRenewalExposureTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = Path("/tmp/cafom_test_exposure")
        self._tmp.mkdir(parents=True, exist_ok=True)
        self._db_path = self._tmp / "test.db"
        self._jsonl_path = self._tmp / "assets.jsonl"
        asset_tracker.init_db(self._db_path)

    def tearDown(self) -> None:
        if self._tmp.exists():
            import shutil
            shutil.rmtree(self._tmp)

    def _create_test_asset(
        self, asset_id: str, renewal_date: str, cost: float
    ) -> dict:
        return {
            "id": asset_id,
            "product": f"Product {asset_id}",
            "vendor": f"Vendor {asset_id}",
            "category": "Test",
            "purchase_date": "2023-01-01",
            "renewal_date": renewal_date,
            "contract_term_months": 12,
            "annual_cost_usd": cost,
            "capex_opex": "OPEX",
            "owner": "Owner",
            "status": "Active",
            "health_check_url": "https://example.com/health",
        }

    def _setup_assets(self, assets: list[dict]) -> None:
        with self._jsonl_path.open("w", encoding="utf-8") as f:
            for asset in assets:
                f.write(json.dumps(asset) + "\n")
        with mock.patch.object(asset_tracker, "_DB_PATH", self._db_path):
            asset_tracker.ingest_assets(self._jsonl_path, self._db_path)

    def test_daily_renewal_exposure_sums_costs(self) -> None:
        """Verify daily_renewal_exposure_usd() sums costs for renewals within window."""
        today = date.today()
        tomorrow = today + timedelta(days=1)
        next_week = today + timedelta(days=7)
        next_month = today + timedelta(days=35)

        assets = [
            self._create_test_asset("AST-001", tomorrow.isoformat(), 50000.0),
            self._create_test_asset("AST-002", next_week.isoformat(), 30000.0),
            self._create_test_asset("AST-003", next_month.isoformat(), 20000.0),
        ]
        self._setup_assets(assets)

        with mock.patch.object(asset_tracker, "_DB_PATH", self._db_path):
            exposure = asset_tracker.daily_renewal_exposure_usd(30)

        self.assertAlmostEqual(float(exposure), 80000.0, places=2)

    def test_daily_renewal_exposure_defaults_30_days(self) -> None:
        """Verify daily_renewal_exposure_usd() uses 30-day default."""
        today = date.today()
        in_20_days = (today + timedelta(days=20)).isoformat()
        in_50_days = (today + timedelta(days=50)).isoformat()

        assets = [
            self._create_test_asset("AST-001", in_20_days, 10000.0),
            self._create_test_asset("AST-002", in_50_days, 10000.0),
        ]
        self._setup_assets(assets)

        with mock.patch.object(asset_tracker, "_DB_PATH", self._db_path):
            exposure = asset_tracker.daily_renewal_exposure_usd()

        self.assertAlmostEqual(float(exposure), 10000.0, places=2)

    def test_daily_renewal_exposure_zero_when_empty(self) -> None:
        """Verify daily_renewal_exposure_usd() returns 0.0 when no assets."""
        with mock.patch.object(asset_tracker, "_DB_PATH", self._db_path):
            exposure = asset_tracker.daily_renewal_exposure_usd(30)

        self.assertEqual(exposure, 0.0)


if __name__ == "__main__":
    unittest.main()
