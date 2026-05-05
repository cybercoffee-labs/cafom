"""CAFOM PDF report generator — fpdf2-based portfolio report.

Builds a multi-section PDF: cover, executive summary, asset inventory,
renewal alerts breakdown, vendor health snapshot, financial forecast,
and cost anomalies. Pure additive — no module dependencies modified.
"""

from __future__ import annotations

import io
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    from fpdf import FPDF
except ImportError as exc:
    raise ImportError(
        "report_generator requires fpdf2. Install with: pip install fpdf2"
    ) from exc

import anomaly_detector
import asset_tracker
import financial_forecast
import renewal_alerts

logger = logging.getLogger("cafom.report_generator")


# Visual constants — match dark cybersecurity theme but printable
_TITLE_COLOR = (0, 80, 30)         # Dark green
_HEADER_COLOR = (40, 40, 40)       # Dark gray
_ACCENT_COLOR = (0, 150, 70)       # Mid green
_RED_ALERT = (180, 0, 0)
_YELLOW_ALERT = (200, 130, 0)
_GREEN_OK = (0, 130, 50)


def _safe(text: Any) -> str:
    """Coerce arbitrary values to ASCII-safe strings for fpdf2 core fonts."""
    if text is None:
        return "-"
    s = str(text)
    # Replace non-Latin-1 chars (em-dash, smart quotes) with ASCII equivalents
    replacements = {
        "—": "-", "–": "-",  # em/en dash
        "‘": "'", "’": "'",
        "“": '"', "”": '"',
        "…": "...",
        " ": " ",
    }
    for k, v in replacements.items():
        s = s.replace(k, v)
    return s.encode("latin-1", "replace").decode("latin-1")


class _CafomPDF(FPDF):
    """Custom PDF with CAFOM header/footer."""

    def header(self) -> None:
        self.set_font("Helvetica", "B", 10)
        self.set_text_color(*_ACCENT_COLOR)
        self.cell(0, 8, "CAFOM | Cyber Asset Financial Operations Manager", align="L")
        self.set_text_color(120, 120, 120)
        self.set_font("Helvetica", "I", 8)
        self.cell(0, 8, datetime.now(UTC).strftime("%Y-%m-%d"), align="R")
        self.ln(12)
        self.set_text_color(0, 0, 0)

    def footer(self) -> None:
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(120, 120, 120)
        self.cell(0, 10, f"Page {self.page_no()}", align="C")


def _section_title(pdf: _CafomPDF, title: str) -> None:
    pdf.set_font("Helvetica", "B", 14)
    pdf.set_text_color(*_TITLE_COLOR)
    pdf.cell(0, 10, _safe(title), ln=True)
    pdf.set_text_color(0, 0, 0)
    pdf.ln(2)


def _kv_row(pdf: _CafomPDF, key: str, value: str) -> None:
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(60, 6, _safe(key))
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 6, _safe(value), ln=True)


def _build_cover(pdf: _CafomPDF) -> None:
    pdf.add_page()
    pdf.set_y(60)
    pdf.set_font("Helvetica", "B", 24)
    pdf.set_text_color(*_TITLE_COLOR)
    pdf.cell(0, 14, _safe("CAFOM Portfolio Report"), ln=True, align="C")
    pdf.set_font("Helvetica", "", 14)
    pdf.set_text_color(80, 80, 80)
    pdf.cell(
        0, 10,
        _safe("Cyber Asset Financial Operations Snapshot"),
        ln=True, align="C",
    )
    pdf.ln(20)
    pdf.set_font("Helvetica", "I", 11)
    pdf.set_text_color(120, 120, 120)
    pdf.cell(
        0, 8,
        _safe(f"Generated {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}"),
        ln=True, align="C",
    )
    pdf.set_text_color(0, 0, 0)


def _build_summary(pdf: _CafomPDF, assets: list[dict[str, Any]]) -> None:
    pdf.add_page()
    _section_title(pdf, "Executive Summary")

    total_assets = len(assets)
    annual_spend = sum(float(a.get("annual_cost_usd", 0) or 0) for a in assets)
    capex = sum(
        float(a.get("annual_cost_usd", 0) or 0)
        for a in assets if a.get("capex_opex") == "CAPEX"
    )
    opex = sum(
        float(a.get("annual_cost_usd", 0) or 0)
        for a in assets if a.get("capex_opex") == "OPEX"
    )
    active = sum(1 for a in assets if a.get("status") == "Active")

    _kv_row(pdf, "Total Assets:", str(total_assets))
    _kv_row(pdf, "Active Assets:", str(active))
    _kv_row(pdf, "Annual Spend (USD):", f"${annual_spend:,.2f}")
    _kv_row(pdf, "CAPEX (USD):", f"${capex:,.2f}")
    _kv_row(pdf, "OPEX (USD):", f"${opex:,.2f}")
    _kv_row(
        pdf, "Distinct Vendors:",
        str(len({a.get("vendor") for a in assets if a.get("vendor")})),
    )


def _build_inventory(pdf: _CafomPDF, assets: list[dict[str, Any]]) -> None:
    pdf.add_page()
    _section_title(pdf, "Asset Inventory")

    if not assets:
        pdf.set_font("Helvetica", "I", 10)
        pdf.cell(0, 6, _safe("No assets in portfolio."), ln=True)
        return

    pdf.set_font("Helvetica", "B", 9)
    pdf.set_fill_color(220, 230, 220)
    headers = [("ID", 22), ("Product", 50), ("Vendor", 35), ("Status", 25), ("Annual $", 30)]
    for h, w in headers:
        pdf.cell(w, 7, _safe(h), border=1, fill=True)
    pdf.ln()

    pdf.set_font("Helvetica", "", 8)
    for a in assets:
        pdf.cell(22, 6, _safe(a.get("id", "")), border=1)
        pdf.cell(50, 6, _safe(a.get("product", ""))[:30], border=1)
        pdf.cell(35, 6, _safe(a.get("vendor", ""))[:20], border=1)
        pdf.cell(25, 6, _safe(a.get("status", "")), border=1)
        cost = float(a.get("annual_cost_usd", 0) or 0)
        pdf.cell(30, 6, f"${cost:,.0f}", border=1, align="R")
        pdf.ln()


def _build_renewals(pdf: _CafomPDF, assets: list[dict[str, Any]]) -> None:
    pdf.add_page()
    _section_title(pdf, "Renewal Alerts")

    result = renewal_alerts.evaluate_portfolio(assets)
    counts = result["counts"]
    alerts = result["alerts"]

    # Counts row
    pdf.set_font("Helvetica", "B", 11)
    color_map = {
        "GREEN": _GREEN_OK,
        "YELLOW": _YELLOW_ALERT,
        "RED": _RED_ALERT,
        "CRITICAL": (130, 0, 0),
    }
    for level in ("GREEN", "YELLOW", "RED", "CRITICAL"):
        pdf.set_text_color(*color_map[level])
        pdf.cell(45, 8, _safe(f"{level}: {counts[level]}"), border=1, align="C")
    pdf.set_text_color(0, 0, 0)
    pdf.ln(12)

    # Alerts table (RED + CRITICAL)
    if alerts:
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(0, 6, _safe("Active Alerts (Red & Critical):"), ln=True)
        pdf.ln(2)

        pdf.set_font("Helvetica", "B", 9)
        pdf.set_fill_color(240, 220, 220)
        cols = [("ID", 22), ("Product", 55), ("Renewal", 28), ("Days", 18), ("Level", 22)]
        for h, w in cols:
            pdf.cell(w, 7, _safe(h), border=1, fill=True)
        pdf.ln()

        pdf.set_font("Helvetica", "", 8)
        for alert in alerts:
            pdf.cell(22, 6, _safe(alert.get("id", "")), border=1)
            pdf.cell(55, 6, _safe(alert.get("product", ""))[:35], border=1)
            pdf.cell(28, 6, _safe(alert.get("renewal_date", "")), border=1)
            pdf.cell(18, 6, _safe(alert.get("days_until", "")), border=1, align="R")
            pdf.cell(22, 6, _safe(alert.get("level", "")), border=1)
            pdf.ln()
    else:
        pdf.set_font("Helvetica", "I", 10)
        pdf.cell(0, 6, _safe("No active alerts. Portfolio healthy."), ln=True)


def _build_forecast(pdf: _CafomPDF, assets: list[dict[str, Any]]) -> None:
    pdf.add_page()
    _section_title(pdf, "12-Month Financial Forecast")

    projection = financial_forecast.project_12_months(assets)
    if not projection:
        pdf.set_font("Helvetica", "I", 10)
        pdf.cell(0, 6, _safe("No forecast data available."), ln=True)
        return

    pdf.set_font("Helvetica", "B", 9)
    pdf.set_fill_color(220, 230, 220)
    cols = [("Month", 50), ("Year", 25), ("Projected $", 50), ("Renewals", 30)]
    for h, w in cols:
        pdf.cell(w, 7, _safe(h), border=1, fill=True)
    pdf.ln()

    pdf.set_font("Helvetica", "", 9)
    for row in projection:
        pdf.cell(50, 6, _safe(row.get("month_name", "")), border=1)
        pdf.cell(25, 6, _safe(row.get("year", "")), border=1)
        cost = float(row.get("projected_cost", 0) or 0)
        pdf.cell(50, 6, f"${cost:,.2f}", border=1, align="R")
        pdf.cell(30, 6, _safe(row.get("renewals_count", 0)), border=1, align="R")
        pdf.ln()


def _build_anomalies(pdf: _CafomPDF, assets: list[dict[str, Any]]) -> None:
    pdf.add_page()
    _section_title(pdf, "Cost Anomalies (Mean + 2 sigma)")

    outliers = anomaly_detector.flag_outliers(assets)
    if not outliers:
        pdf.set_font("Helvetica", "I", 10)
        pdf.cell(
            0, 6,
            _safe("No anomalies detected — costs within category norms."),
            ln=True,
        )
        return

    pdf.set_font("Helvetica", "B", 9)
    pdf.set_fill_color(255, 230, 200)
    cols = [
        ("ID", 22), ("Product", 50), ("Cost $", 28),
        ("Cat. Mean", 30), ("Threshold", 30),
    ]
    for h, w in cols:
        pdf.cell(w, 7, _safe(h), border=1, fill=True)
    pdf.ln()

    pdf.set_font("Helvetica", "", 8)
    for o in outliers:
        pdf.cell(22, 6, _safe(o.get("id", "")), border=1)
        pdf.cell(50, 6, _safe(o.get("product", ""))[:30], border=1)
        cost = float(o.get("annual_cost_usd", 0) or 0)
        pdf.cell(28, 6, f"${cost:,.0f}", border=1, align="R")
        m = float(o.get("category_mean", 0) or 0)
        thr = float(o.get("threshold", 0) or 0)
        pdf.cell(30, 6, f"${m:,.0f}", border=1, align="R")
        pdf.cell(30, 6, f"${thr:,.0f}", border=1, align="R")
        pdf.ln()


def build_report(
    assets: list[dict[str, Any]] | None = None,
    output_path: Path | None = None,
) -> bytes:
    """
    Build a full CAFOM PDF portfolio report.

    Args:
        assets: List of asset dicts (defaults to all from asset_tracker)
        output_path: Optional path to write PDF; if None, returns bytes only

    Returns:
        PDF content as bytes
    """
    if assets is None:
        try:
            assets = asset_tracker.get_assets()
        except Exception as exc:
            logger.warning("Failed to fetch assets from tracker: %s", exc)
            assets = []

    pdf = _CafomPDF()
    pdf.set_auto_page_break(auto=True, margin=15)

    _build_cover(pdf)
    _build_summary(pdf, assets)
    _build_inventory(pdf, assets)
    _build_renewals(pdf, assets)
    _build_forecast(pdf, assets)
    _build_anomalies(pdf, assets)

    pdf_bytes = bytes(pdf.output())

    if output_path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(pdf_bytes)
        logger.info("Report written to %s", output_path)

    return pdf_bytes
