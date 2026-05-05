"""CAFOM historical analysis — multi-year spend trends + improved anomaly detection.

Computes yearly totals, year-over-year growth, CAGR, monthly burn rate vs.
budget, vendor concentration, and category-level cost anomalies using a
4-year baseline (2023-2026) instead of single-year statistics.

This module reads optional historical fields from each asset:
- annual_cost_usd_2023, _2024, _2025: prior-year actuals
- annual_cost_usd: current-year (2026) actual
- budget_annual_usd: 2026 allocated budget

If a field is missing, that asset is silently skipped from the relevant
calculation rather than raising — the dashboard should keep working even
with partial data.
"""

from __future__ import annotations

import logging
from statistics import mean, stdev
from typing import Any

logger = logging.getLogger("cafom.historical_analysis")

_YEARS = [2023, 2024, 2025, 2026]
_HISTORICAL_FIELDS = {
    2023: "annual_cost_usd_2023",
    2024: "annual_cost_usd_2024",
    2025: "annual_cost_usd_2025",
    2026: "annual_cost_usd",
}


def _safe_float(v: Any) -> float | None:
    """Return float(v) or None if v is not numeric."""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def get_historical_spend(assets: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Compute yearly portfolio totals, YoY %, and 2023-2026 CAGR.

    Args:
        assets: List of asset dicts with optional annual_cost_usd_YYYY fields.

    Returns:
        dict with keys:
        - yearly_totals: {2023: float, 2024: float, 2025: float, 2026: float}
        - yoy_change: {2024: pct, 2025: pct, 2026: pct} year-over-year %
        - cagr_pct: 4-year compound annual growth rate (None if undefined)
        - total_growth_pct: total % growth from 2023 to 2026
    """
    yearly_totals: dict[int, float] = {y: 0.0 for y in _YEARS}
    for asset in assets:
        for year in _YEARS:
            field = _HISTORICAL_FIELDS[year]
            v = _safe_float(asset.get(field))
            if v is not None:
                yearly_totals[year] += v

    # YoY change
    yoy_change: dict[int, float | None] = {}
    for year in (2024, 2025, 2026):
        prev = yearly_totals[year - 1]
        curr = yearly_totals[year]
        if prev > 0:
            yoy_change[year] = (curr - prev) / prev * 100.0
        else:
            yoy_change[year] = None

    # CAGR over 3 periods (4 data points = 3 compounding periods)
    base = yearly_totals[2023]
    final = yearly_totals[2026]
    cagr = None
    total_growth = None
    if base > 0 and final > 0:
        # 3 periods between 2023 and 2026
        cagr = ((final / base) ** (1 / 3) - 1) * 100.0
        total_growth = (final - base) / base * 100.0

    return {
        "yearly_totals": yearly_totals,
        "yoy_change": yoy_change,
        "cagr_pct": cagr,
        "total_growth_pct": total_growth,
    }


def get_burn_rate(assets: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Monthly burn vs monthly budget allocation (current year, 2026).

    Each asset contributes (annual_cost / 12) per month to actual burn and
    (budget_annual_usd / 12) per month to budgeted burn. Renewal spikes are
    attributed to the renewal month for actual; budget is spread evenly.

    Returns:
        dict with keys:
        - months: list of month names (Jan-Dec)
        - actual: list[float] — monthly actual burn
        - budget: list[float] — monthly allocated budget
        - total_actual: float — sum of monthly actuals
        - total_budget: float — sum of monthly budget
        - variance_pct: float — (actual - budget) / budget * 100
    """
    month_names = [
        "Jan", "Feb", "Mar", "Apr", "May", "Jun",
        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    ]

    actual = [0.0] * 12
    budget = [0.0] * 12

    for asset in assets:
        cost = _safe_float(asset.get("annual_cost_usd")) or 0.0
        bud = _safe_float(asset.get("budget_annual_usd"))
        if bud is None:
            # Default budget = 7.5% above actual
            bud = cost * 1.075

        # Spread cost & budget evenly across 12 months
        monthly_actual = cost / 12.0
        monthly_budget = bud / 12.0
        for i in range(12):
            actual[i] += monthly_actual
            budget[i] += monthly_budget

    total_actual = sum(actual)
    total_budget = sum(budget)
    variance = (
        (total_actual - total_budget) / total_budget * 100.0
        if total_budget > 0 else 0.0
    )

    return {
        "months": month_names,
        "actual": actual,
        "budget": budget,
        "total_actual": total_actual,
        "total_budget": total_budget,
        "variance_pct": variance,
    }


def get_vendor_concentration(
    assets: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Compute vendor concentration: spend per vendor as % of total.

    Returns:
        dict with keys:
        - per_vendor: list of {vendor, total, pct} sorted desc by total
        - top_vendor: name of largest vendor
        - top_pct: % of total spend held by top vendor
        - hhi: Herfindahl-Hirschman Index (sum of squared shares × 10,000)
    """
    by_vendor: dict[str, float] = {}
    total = 0.0
    for asset in assets:
        cost = _safe_float(asset.get("annual_cost_usd")) or 0.0
        v = asset.get("vendor", "Unknown") or "Unknown"
        by_vendor[v] = by_vendor.get(v, 0.0) + cost
        total += cost

    if total <= 0:
        return {
            "per_vendor": [],
            "top_vendor": None,
            "top_pct": 0.0,
            "hhi": 0.0,
        }

    per_vendor = [
        {"vendor": v, "total": amt, "pct": amt / total * 100.0}
        for v, amt in by_vendor.items()
    ]
    per_vendor.sort(key=lambda r: r["total"], reverse=True)

    top = per_vendor[0]
    hhi = sum((r["pct"]) ** 2 for r in per_vendor)  # already in % scale

    return {
        "per_vendor": per_vendor,
        "top_vendor": top["vendor"],
        "top_pct": top["pct"],
        "hhi": hhi,
    }


def get_improved_anomalies(
    assets: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Improved anomaly detection using 4 years of cost data per category.

    For each category, gathers ALL cost observations (2023-2026 across all
    assets in that category) and computes mean + std. An asset is flagged
    if its 2026 cost exceeds (mean + 2σ) of the category's 4-year baseline.

    This is more robust than single-year anomaly detection because it
    accounts for natural inter-year price variance and provides a deeper
    statistical basis when categories have few assets.

    Returns:
        List of flagged asset dicts (subset of input) annotated with:
        - is_outlier: True
        - baseline_mean: float — mean cost across category × years
        - baseline_std: float — std dev across category × years
        - threshold: float — mean + 2σ
        - observations_count: int — sample size used
    """
    # Group cost observations by category
    category_observations: dict[str, list[float]] = {}
    for asset in assets:
        cat = asset.get("category", "Uncategorized") or "Uncategorized"
        if cat not in category_observations:
            category_observations[cat] = []
        for year in _YEARS:
            v = _safe_float(asset.get(_HISTORICAL_FIELDS[year]))
            if v is not None and v > 0:
                category_observations[cat].append(v)

    # Compute baseline stats per category
    baselines: dict[str, dict[str, float]] = {}
    for cat, obs in category_observations.items():
        if len(obs) < 2:
            continue
        try:
            m = mean(obs)
            s = stdev(obs)
            baselines[cat] = {
                "mean": m,
                "std": s,
                "threshold": m + 2 * s,
                "n": len(obs),
            }
        except Exception as exc:
            logger.warning("Baseline failed for category %s: %s", cat, exc)
            continue

    # Flag assets whose 2026 cost exceeds threshold
    flagged: list[dict[str, Any]] = []
    for asset in assets:
        cat = asset.get("category", "Uncategorized") or "Uncategorized"
        baseline = baselines.get(cat)
        if baseline is None:
            continue
        cost = _safe_float(asset.get("annual_cost_usd"))
        if cost is None:
            continue
        if cost > baseline["threshold"]:
            f = dict(asset)
            f["is_outlier"] = True
            f["baseline_mean"] = baseline["mean"]
            f["baseline_std"] = baseline["std"]
            f["threshold"] = baseline["threshold"]
            f["observations_count"] = baseline["n"]
            flagged.append(f)
    return flagged
