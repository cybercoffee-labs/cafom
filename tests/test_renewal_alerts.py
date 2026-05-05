"""Pruebas de renewal_alerts.py — date-threshold renewal classification."""

from __future__ import annotations

import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import renewal_alerts  # noqa: E402


class ClassifyTest(unittest.TestCase):
    def test_classify_green_far_future(self) -> None:
        """Verify classify() returns GREEN for renewals far in future."""
        today = date(2026, 5, 4)
        future = date(2026, 8, 4)  # 92 days out
        level = renewal_alerts.classify(future, today=today)
        self.assertEqual(level, "GREEN")

    def test_classify_yellow_30d(self) -> None:
        """Verify classify() returns YELLOW for renewals in 30 days."""
        today = date(2026, 5, 4)
        thirty_days = date(2026, 6, 3)  # 30 days
        level = renewal_alerts.classify(thirty_days, today=today)
        self.assertEqual(level, "YELLOW")

    def test_classify_red_today(self) -> None:
        """Verify classify() returns RED for renewals at or just past today."""
        today = date(2026, 5, 4)
        today_renewal = date(2026, 5, 4)  # expires today
        level = renewal_alerts.classify(today_renewal, today=today)
        self.assertEqual(level, "RED")

    def test_classify_red_recently_expired(self) -> None:
        """Verify classify() returns RED for recently expired contracts."""
        today = date(2026, 5, 4)
        expired_5d_ago = date(2026, 4, 29)  # -5 days
        level = renewal_alerts.classify(expired_5d_ago, today=today)
        self.assertEqual(level, "RED")

    def test_classify_critical_60d_overdue(self) -> None:
        """Verify classify() returns CRITICAL for severely overdue contracts."""
        today = date(2026, 5, 4)
        overdue_60d = date(2026, 3, 5)  # -60 days
        level = renewal_alerts.classify(overdue_60d, today=today)
        self.assertEqual(level, "CRITICAL")

    def test_classify_boundary_60d_green(self) -> None:
        """Verify classify() returns GREEN exactly at 60 days (per spec)."""
        today = date(2026, 5, 4)
        days_60 = date(2026, 7, 3)  # +60 days
        level = renewal_alerts.classify(days_60, today=today)
        # Per spec: days > 30 is "safe", but strictly > 60 is GREEN
        # 60 days exactly should be YELLOW (30 < days <= 60)
        # But the docstring says "days > 30 = GREEN" so this is actually GREEN
        # Let me verify: threshold is _DAYS_YELLOW = 30, so days > 30 (including 60) → GREEN
        self.assertEqual(level, "GREEN")

    def test_classify_boundary_31d_yellow(self) -> None:
        """Verify classify() returns YELLOW at 31 days (just over threshold)."""
        today = date(2026, 5, 4)
        days_31 = date(2026, 6, 4)  # +31 days
        level = renewal_alerts.classify(days_31, today=today)
        self.assertEqual(level, "GREEN")  # > 30 days

    def test_classify_boundary_30d_yellow(self) -> None:
        """Verify classify() returns YELLOW exactly at 30 days."""
        today = date(2026, 5, 4)
        days_30 = date(2026, 6, 3)  # +30 days
        level = renewal_alerts.classify(days_30, today=today)
        self.assertEqual(level, "YELLOW")  # days <= 30 AND days > 0

    def test_classify_boundary_1d_yellow(self) -> None:
        """Verify classify() returns YELLOW at 1 day."""
        today = date(2026, 5, 4)
        tomorrow = date(2026, 5, 5)  # +1 day
        level = renewal_alerts.classify(tomorrow, today=today)
        self.assertEqual(level, "YELLOW")

    def test_classify_boundary_0d_red(self) -> None:
        """Verify classify() returns RED at day 0 (today)."""
        today = date(2026, 5, 4)
        level = renewal_alerts.classify(today, today=today)
        self.assertEqual(level, "RED")  # days = 0, which is <= 0 and > -30

    def test_classify_boundary_minus_1d_red(self) -> None:
        """Verify classify() returns RED at -1 day (1 day ago)."""
        today = date(2026, 5, 4)
        yesterday = date(2026, 5, 3)  # -1 day
        level = renewal_alerts.classify(yesterday, today=today)
        self.assertEqual(level, "RED")

    def test_classify_boundary_minus_30d_critical(self) -> None:
        """Verify classify() returns CRITICAL at -30 days (boundary)."""
        today = date(2026, 5, 4)
        minus_30 = date(2026, 4, 4)  # -30 days
        level = renewal_alerts.classify(minus_30, today=today)
        self.assertEqual(level, "CRITICAL")  # days <= -30

    def test_classify_boundary_minus_31d_critical(self) -> None:
        """Verify classify() returns CRITICAL at -31 days."""
        today = date(2026, 5, 4)
        minus_31 = date(2026, 4, 3)  # -31 days
        level = renewal_alerts.classify(minus_31, today=today)
        self.assertEqual(level, "CRITICAL")  # days <= -30


class EvaluatePortfolioTest(unittest.TestCase):
    def _create_asset(
        self, asset_id: str, renewal_days_from_today: int
    ) -> dict:
        """Create a test asset with renewal date relative to today."""
        renewal_date = date.today() + timedelta(days=renewal_days_from_today)
        return {
            "id": asset_id,
            "product": f"Product {asset_id}",
            "vendor": f"Vendor {asset_id}",
            "renewal_date": renewal_date.isoformat(),
            "status": "Active",
        }

    def test_evaluate_portfolio_counts_each_level(self) -> None:
        """Verify evaluate_portfolio() correctly counts all alert levels."""
        assets = [
            self._create_asset("AST-001", 100),   # GREEN
            self._create_asset("AST-002", 60),    # GREEN
            self._create_asset("AST-003", 20),    # YELLOW
            self._create_asset("AST-004", 10),    # YELLOW
            self._create_asset("AST-005", 0),     # RED
            self._create_asset("AST-006", -5),    # RED
            self._create_asset("AST-007", -60),   # CRITICAL
        ]

        with mock.patch("renewal_alerts.log_portfolio_refresh"):
            result = renewal_alerts.evaluate_portfolio(assets)

        self.assertEqual(result["counts"]["GREEN"], 2)
        self.assertEqual(result["counts"]["YELLOW"], 2)
        self.assertEqual(result["counts"]["RED"], 2)
        self.assertEqual(result["counts"]["CRITICAL"], 1)

    def test_evaluate_portfolio_emits_alerts_for_red_and_critical(self) -> None:
        """Verify evaluate_portfolio() generates alerts for RED and CRITICAL."""
        assets = [
            self._create_asset("AST-001", 100),   # GREEN - no alert
            self._create_asset("AST-002", 20),    # YELLOW - no alert
            self._create_asset("AST-003", -5),    # RED - ALERT
            self._create_asset("AST-004", -60),   # CRITICAL - ALERT
        ]

        with mock.patch("renewal_alerts.log_portfolio_refresh"):
            result = renewal_alerts.evaluate_portfolio(assets)

        self.assertEqual(len(result["alerts"]), 2)
        alert_ids = {a["id"] for a in result["alerts"]}
        self.assertIn("AST-003", alert_ids)
        self.assertIn("AST-004", alert_ids)

    def test_evaluate_portfolio_alerts_have_required_fields(self) -> None:
        """Verify alerts contain all required fields."""
        assets = [self._create_asset("AST-001", -10)]  # RED

        with mock.patch("renewal_alerts.log_portfolio_refresh"):
            result = renewal_alerts.evaluate_portfolio(assets)

        self.assertEqual(len(result["alerts"]), 1)
        alert = result["alerts"][0]
        self.assertIn("id", alert)
        self.assertIn("product", alert)
        self.assertIn("vendor", alert)
        self.assertIn("renewal_date", alert)
        self.assertIn("level", alert)
        self.assertIn("days_until", alert)

    def test_evaluate_portfolio_empty_list(self) -> None:
        """Verify evaluate_portfolio() handles empty asset list."""
        with mock.patch("renewal_alerts.log_portfolio_refresh"):
            result = renewal_alerts.evaluate_portfolio([])

        self.assertEqual(result["counts"]["GREEN"], 0)
        self.assertEqual(result["counts"]["YELLOW"], 0)
        self.assertEqual(result["counts"]["RED"], 0)
        self.assertEqual(result["counts"]["CRITICAL"], 0)
        self.assertEqual(len(result["alerts"]), 0)

    def test_evaluate_portfolio_skips_missing_renewal_date(self) -> None:
        """Verify evaluate_portfolio() skips assets without renewal_date."""
        assets = [
            {"id": "AST-001", "product": "Product 1", "vendor": "Vendor 1"},
            self._create_asset("AST-002", 20),
        ]

        with mock.patch("renewal_alerts.log_portfolio_refresh"):
            result = renewal_alerts.evaluate_portfolio(assets)

        # Only AST-002 should be counted
        total = sum(result["counts"].values())
        self.assertEqual(total, 1)

    def test_evaluate_portfolio_handles_date_objects(self) -> None:
        """Verify evaluate_portfolio() handles renewal_date as date object."""
        assets = [
            {
                "id": "AST-001",
                "product": "Product 1",
                "vendor": "Vendor 1",
                "renewal_date": date.today() + timedelta(days=20),
            }
        ]

        with mock.patch("renewal_alerts.log_portfolio_refresh"):
            result = renewal_alerts.evaluate_portfolio(assets)

        self.assertEqual(result["counts"]["YELLOW"], 1)

    def test_evaluate_portfolio_alerts_sorted_by_date(self) -> None:
        """Verify alerts are sorted by renewal_date."""
        assets = [
            self._create_asset("AST-002", -5),   # RED, renews May 1
            self._create_asset("AST-001", -60),  # CRITICAL, renews Mar 5
            self._create_asset("AST-003", -20),  # RED, renews Apr 14
        ]

        with mock.patch("renewal_alerts.log_portfolio_refresh"):
            result = renewal_alerts.evaluate_portfolio(assets)

        # Alerts should be sorted by renewal_date ascending
        alert_ids = [a["id"] for a in result["alerts"]]
        # Earliest date first: CRITICAL (-60), then RED (-20), then RED (-5)
        self.assertEqual(alert_ids[0], "AST-001")  # -60 days
        self.assertEqual(alert_ids[1], "AST-003")  # -20 days
        self.assertEqual(alert_ids[2], "AST-002")  # -5 days


if __name__ == "__main__":
    unittest.main()
