"""CAFOM asset lifecycle & portfolio schemas (Pydantic v2).

Adapted from ``contable_bot/core/schemas.py``. Models for:
- CyberAsset: a single asset record (product, vendor, renewal dates, cost)
- AssetPortfolioModel: collection of assets + metadata
- validation helpers

KEEP from source:
* Pydantic v2 BaseModel + ConfigDict(extra="allow")
* @field_validator decorator pattern + classmethod
* permissive (extra fields allowed for debug metadata)
* aggregated validation errors (all rows at once, not first-fail)

CHANGE:
* BankMovementModel → CyberAssetModel with contract fields
* Remove ConsolidatedOperationModel (no accounting step)
* Add validator: renewal_date > purchase_date
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Iterable, Literal

try:
    from pydantic import (
        BaseModel,
        ConfigDict,
        Field,
        HttpUrl,
        ValidationError,
        field_validator,
    )
except ImportError as exc:
    raise ImportError(
        "schemas requires pydantic>=2.0. Install with: pip install pydantic"
    ) from exc


__all__ = [
    "CyberAssetModel",
    "AssetPortfolioModel",
    "ValidationError",
    "validate_asset",
    "validate_portfolio",
]


_ALLOWED_EXTRA = ConfigDict(extra="allow", str_strip_whitespace=True)


class CyberAssetModel(BaseModel):
    """A single cybersecurity asset (product instance, contract, vendor)."""

    model_config = _ALLOWED_EXTRA

    id: str = Field(
        ..., pattern=r"^AST-\d{3,}$", description="Asset ID (AST-XXX format)"
    )
    product: str
    vendor: str
    category: str
    purchase_date: date
    renewal_date: date
    contract_term_months: int = Field(ge=1)
    annual_cost_usd: Decimal = Field(ge=0)
    capex_opex: Literal["CAPEX", "OPEX"]
    owner: str
    status: Literal["Active", "Expired", "Renewed", "Decommissioned"]
    health_check_url: HttpUrl
    last_health_check_at: Any = None  # datetime or None
    vendor_contact_email: str = ""

    @field_validator("renewal_date", mode="after")
    @classmethod
    def _renewal_after_purchase(cls, v: date, info) -> date:
        if "purchase_date" in info.data:
            purchase = info.data["purchase_date"]
            if v <= purchase:
                raise ValueError("renewal_date must be strictly > purchase_date")
        return v


class AssetPortfolioModel(BaseModel):
    """Collection of assets + metadata."""

    model_config = _ALLOWED_EXTRA

    portfolio_path: str = ""
    assets: list[CyberAssetModel] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    generated_at: str = ""


def validate_asset(obj: Any) -> CyberAssetModel:
    """Coerce an arbitrary object to CyberAssetModel. Fails if invalid."""
    return CyberAssetModel.model_validate(obj)


def validate_portfolio(
    objs: Iterable[dict[str, Any]],
) -> list[CyberAssetModel]:
    """Validate a batch of asset dicts. Aggregates all errors."""
    errors = []
    valid = []
    for i, obj in enumerate(objs):
        try:
            valid.append(CyberAssetModel.model_validate(obj))
        except ValidationError as exc:
            for err in exc.errors():
                errors.append(f"Row {i}: {err}")
    if errors:
        msg = "\n".join(errors)
        raise ValueError(msg) from None
    return valid
