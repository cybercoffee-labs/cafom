"""CAFOM API — FastAPI service for asset tracking and monitoring.

Provides HTTP endpoints for asset inventory, vendor health checks, renewal alerts,
and portfolio overview. Adapts from contable_bot/api.py with CAFOM-specific domain.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel

import asset_tracker
import renewal_alerts
import report_generator
import schemas
import vendor_health
from dual_writer import log_asset

logger = logging.getLogger("cafom.api")


class HealthResponse(BaseModel):
    """Response for healthz endpoint."""

    status: str
    service: str


class AssetCreateRequest(BaseModel):
    """Request body for POST /assets."""

    id: str
    product: str
    vendor: str
    category: str
    purchase_date: str
    renewal_date: str
    contract_term_months: int
    annual_cost_usd: float
    capex_opex: str
    owner: str
    status: str
    health_check_url: str


class AssetCreateResponse(BaseModel):
    """Response for POST /assets."""

    id: str
    status: str


class VendorHealthSummary(BaseModel):
    """Summary of vendor health status."""

    healthy: int
    degraded: int
    down: int
    vendors: dict[str, dict[str, Any]]


class RenewalAlertResponse(BaseModel):
    """Response for GET /renewals/alerts."""

    counts: dict[str, int]
    alerts: list[dict[str, Any]]


def create_app(db_path: Path | None = None) -> FastAPI:
    """
    Create and configure the CAFOM FastAPI application.

    Args:
        db_path: Optional path to SQLite database (defaults to data/cafom.db)

    Returns:
        Configured FastAPI instance
    """
    if db_path:
        asset_tracker.init_db(db_path)

    app = FastAPI(
        title="CAFOM",
        description="Cyber Asset Financial Operations Manager",
        version="0.1.0",
    )

    @app.get("/healthz", response_model=HealthResponse)
    def healthz() -> HealthResponse:
        """Health check endpoint."""
        return HealthResponse(status="ok", service="cafom")

    @app.get("/assets", response_model=list[dict[str, Any]])
    def get_assets_endpoint(status: str | None = Query(None)) -> list[dict[str, Any]]:
        """
        Fetch all assets or filter by status.

        Query parameters:
            status: Optional asset status to filter by (Active, Expired, etc.)

        Returns:
            List of asset dictionaries
        """
        try:
            if db_path:
                # Temporarily set the DB path for this call
                original_path = asset_tracker._DB_PATH
                asset_tracker._DB_PATH = db_path
                try:
                    result = asset_tracker.get_assets(status=status)
                finally:
                    asset_tracker._DB_PATH = original_path
                return result
            return asset_tracker.get_assets(status=status)
        except Exception as exc:
            logger.error("Error fetching assets: %s", exc)
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/assets", response_model=AssetCreateResponse)
    def create_asset(req: AssetCreateRequest) -> AssetCreateResponse:
        """
        Create a new asset.

        Request body: CyberAsset data
        Returns: Confirmation with asset ID
        """
        try:
            asset_dict = req.model_dump()
            # Validate via schemas
            validated = schemas.validate_asset(asset_dict)

            # Log to JSONL
            log_success = log_asset(validated.model_dump())
            if not log_success:
                raise ValueError("Failed to log asset to JSONL")

            # Ingest into SQLite
            if db_path:
                import tempfile

                with tempfile.NamedTemporaryFile(
                    mode="w", suffix=".jsonl", delete=False
                ) as f:
                    import json

                    f.write(json.dumps(asset_dict) + "\n")
                    jsonl_path = Path(f.name)
                try:
                    asset_tracker.ingest_assets(jsonl_path, db_path)
                finally:
                    jsonl_path.unlink()

            return AssetCreateResponse(id=validated.id, status="created")
        except schemas.ValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            logger.error("Error creating asset: %s", exc)
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.get("/vendors/health", response_model=VendorHealthSummary)
    def get_vendors_health() -> VendorHealthSummary:
        """
        Check health of all vendor endpoints.

        Queries the asset database for distinct vendor URLs and probes each one.
        Returns summary and detailed results per vendor.
        """
        try:
            # Get all assets to extract vendor URLs
            if db_path:
                original_path = asset_tracker._DB_PATH
                asset_tracker._DB_PATH = db_path
                try:
                    assets = asset_tracker.get_assets()
                finally:
                    asset_tracker._DB_PATH = original_path
            else:
                assets = asset_tracker.get_assets()

            urls = {asset.get("health_check_url") for asset in assets if asset.get("health_check_url")}
            urls = [u for u in urls if u]  # Remove None

            if not urls:
                return VendorHealthSummary(
                    healthy=0, degraded=0, down=0, vendors={}
                )

            # Check all vendors
            checker = vendor_health.VendorHealthChecker()
            results = checker.check_all(list(urls))

            # Summarize
            healthy = sum(
                1 for r in results.values() if r.get("status") == "Healthy"
            )
            degraded = sum(
                1 for r in results.values() if r.get("status") == "Degraded"
            )
            down = sum(1 for r in results.values() if r.get("status") == "Down")

            return VendorHealthSummary(
                healthy=healthy,
                degraded=degraded,
                down=down,
                vendors=results,
            )
        except Exception as exc:
            logger.error("Error checking vendor health: %s", exc)
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.get("/renewals/alerts", response_model=RenewalAlertResponse)
    def get_renewal_alerts() -> RenewalAlertResponse:
        """
        Get renewal alert summary for the portfolio.

        Evaluates all assets and returns counts per alert level plus
        detailed alerts for RED and CRITICAL renewals.
        """
        try:
            if db_path:
                original_path = asset_tracker._DB_PATH
                asset_tracker._DB_PATH = db_path
                try:
                    assets = asset_tracker.get_assets()
                finally:
                    asset_tracker._DB_PATH = original_path
            else:
                assets = asset_tracker.get_assets()

            result = renewal_alerts.evaluate_portfolio(assets)
            return RenewalAlertResponse(
                counts=result["counts"],
                alerts=result["alerts"],
            )
        except Exception as exc:
            logger.error("Error evaluating renewal alerts: %s", exc)
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.get("/report")
    def get_report(format: str = Query("pdf", pattern="^(pdf)$")) -> Response:
        """
        Generate and stream a portfolio PDF report.

        Query parameters:
            format: Output format (currently only 'pdf' supported)

        Returns:
            PDF binary content with appropriate Content-Type and download headers.
        """
        try:
            if db_path:
                original_path = asset_tracker._DB_PATH
                asset_tracker._DB_PATH = db_path
                try:
                    assets = asset_tracker.get_assets()
                finally:
                    asset_tracker._DB_PATH = original_path
            else:
                assets = asset_tracker.get_assets()

            pdf_bytes = report_generator.build_report(assets=assets)
            filename = "cafom_portfolio_report.pdf"
            return Response(
                content=pdf_bytes,
                media_type="application/pdf",
                headers={
                    "Content-Disposition": f'attachment; filename="{filename}"',
                },
            )
        except Exception as exc:
            logger.error("Error generating report: %s", exc)
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    return app


if __name__ == "__main__":
    import uvicorn

    app = create_app()
    uvicorn.run(app, host="0.0.0.0", port=8000)
