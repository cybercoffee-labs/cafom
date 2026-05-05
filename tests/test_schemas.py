"""Pruebas de schemas.py — CyberAssetModel y validators."""

from __future__ import annotations

import sys
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic import HttpUrl, ValidationError

import schemas  # noqa: E402


class CyberAssetModelTest(unittest.TestCase):
    def _valid_asset(self) -> dict:
        return {
            "id": "AST-001",
            "product": "CrowdStrike Falcon",
            "vendor": "CrowdStrike",
            "category": "Endpoint Detection",
            "purchase_date": date(2023, 1, 1),
            "renewal_date": date(2024, 1, 1),
            "contract_term_months": 12,
            "annual_cost_usd": Decimal("50000"),
            "capex_opex": "OPEX",
            "owner": "Security Team",
            "status": "Active",
            "health_check_url": "https://api.crowdstrike.com/status",
        }

    def test_valid_asset_parses(self) -> None:
        asset = schemas.validate_asset(self._valid_asset())
        self.assertEqual(asset.id, "AST-001")
        self.assertEqual(asset.product, "CrowdStrike Falcon")

    def test_renewal_before_purchase_rejected(self) -> None:
        bad = self._valid_asset()
        bad["renewal_date"] = date(2022, 1, 1)  # before purchase
        with self.assertRaises(ValidationError):
            schemas.validate_asset(bad)

    def test_negative_cost_rejected(self) -> None:
        bad = self._valid_asset()
        bad["annual_cost_usd"] = Decimal("-1000")
        with self.assertRaises(ValidationError):
            schemas.validate_asset(bad)

    def test_invalid_capex_opex_rejected(self) -> None:
        bad = self._valid_asset()
        bad["capex_opex"] = "INVALID"
        with self.assertRaises(ValidationError):
            schemas.validate_asset(bad)

    def test_extra_fields_allowed(self) -> None:
        asset_dict = self._valid_asset()
        asset_dict["debug_metadata"] = {"arbitrary": "value"}
        asset = schemas.validate_asset(asset_dict)
        self.assertEqual(asset.debug_metadata, {"arbitrary": "value"})

    def test_zero_cost_allowed(self) -> None:
        asset_dict = self._valid_asset()
        asset_dict["annual_cost_usd"] = Decimal("0")
        asset = schemas.validate_asset(asset_dict)
        self.assertEqual(asset.annual_cost_usd, Decimal("0"))

    def test_invalid_id_format_rejected(self) -> None:
        bad = self._valid_asset()
        bad["id"] = "INVALID-ID"
        with self.assertRaises(ValidationError):
            schemas.validate_asset(bad)


class ValidatePortfolioTest(unittest.TestCase):
    def _valid_asset_dict(self, id_num: int = 1) -> dict:
        return {
            "id": f"AST-{id_num:03d}",
            "product": f"Product {id_num}",
            "vendor": f"Vendor {id_num}",
            "category": "Test",
            "purchase_date": date(2023, 1, 1),
            "renewal_date": date(2024, 1, 1),
            "contract_term_months": 12,
            "annual_cost_usd": Decimal("10000"),
            "capex_opex": "OPEX",
            "owner": "Owner",
            "status": "Active",
            "health_check_url": "https://example.com/health",
        }

    def test_validate_portfolio_all_valid(self) -> None:
        assets = [self._valid_asset_dict(i) for i in range(1, 4)]
        result = schemas.validate_portfolio(assets)
        self.assertEqual(len(result), 3)

    def test_validate_portfolio_mixed_valid_invalid_raises(self) -> None:
        good = self._valid_asset_dict(1)
        bad = self._valid_asset_dict(2)
        bad["renewal_date"] = date(2022, 1, 1)  # before purchase
        with self.assertRaises(ValueError) as ctx:
            schemas.validate_portfolio([good, bad, self._valid_asset_dict(3)])
        self.assertIn("Row 1", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
