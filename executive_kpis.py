"""CAFOM executive KPIs — board-ready operational and financial metrics.

Surfaces three high-leverage views for security leadership:

* Critical Gap Recovery — every overdue contract scored by financial exposure
  with a recommended remediation action.
* Days of Coverage Lost — aggregate "unprotected days" across the portfolio
  and the implied financial risk per category.
* Monthly Burn Rate Trend — actual vs budget per month plus the cumulative
  trajectory so leadership can see whether the team is converging toward
  end-of-year over/under spend.

All functions are pure: given a list of asset dicts they return plain
serializable structures suitable for direct rendering in Streamlit, JSON
APIs, or PDF reports.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

logger = logging.getLogger("cafom.executive_kpis")


# Recommended action thresholds for overdue contracts (days past renewal).
# Aligns with the operational reality: short overdue → grace renewal still
# negotiable; deep overdue → vendor leverage is gone, replacement is cheaper.
_ACTION_THRESHOLDS = {
    "renew_immediately": 30,   # days_past <= 30 → "Renew immediately"
    "negotiate": 90,           # 30 < days_past <= 90 → "Negotiate"
    # > 90 → "Replace"
}


def _safe_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _parse_date(v: Any) -> date | None:
    """Parse a date from string/date/datetime; return None on failure."""
    if v is None:
        return None
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, str):
        try:
            return date.fromisoformat(v)
        except ValueError:
            return None
    return None


def _recommended_action(days_past: int) -> str:
    """Map days-past-renewal to a recommended remediation action."""
    if days_past <= _ACTION_THRESHOLDS["renew_immediately"]:
        return "Renew immediately"
    if days_past <= _ACTION_THRESHOLDS["negotiate"]:
        return "Negotiate"
    return "Replace"


def get_critical_gap_recovery(
    assets: list[dict[str, Any]],
    *,
    today: date | None = None,
) -> dict[str, Any]:
    """
    Score every overdue contract by financial exposure.

    For each asset whose renewal_date is strictly before today:
      - days_past_renewal: int (today - renewal_date)
      - daily_cost: annual_cost_usd / 365
      - financial_exposure: days_past_renewal * daily_cost
      - recommended_action: "Renew immediately" / "Negotiate" / "Replace"

    The list is sorted by financial_exposure descending so the largest
    accounting impact is on top.

    Args:
        assets: Portfolio assets.
        today: Reference date for testing (default = today).

    Returns:
        dict with keys:
        - items: list of recovery rows (sorted by exposure desc)
        - total_exposure: sum of financial_exposure across all items
        - count: number of overdue assets
    """
    if today is None:
        today = date.today()

    items: list[dict[str, Any]] = []
    for asset in assets:
        renewal = _parse_date(asset.get("renewal_date"))
        if renewal is None:
            continue
        days_past = (today - renewal).days
        if days_past <= 0:
            continue  # not overdue

        cost = _safe_float(asset.get("annual_cost_usd")) or 0.0
        daily_cost = cost / 365.0
        exposure = daily_cost * days_past

        items.append({
            "id": asset.get("id"),
            "product": asset.get("product"),
            "vendor": asset.get("vendor"),
            "category": asset.get("category"),
            "renewal_date": (
                renewal.isoformat() if isinstance(renewal, date) else str(renewal)
            ),
            "days_past_renewal": days_past,
            "annual_cost_usd": cost,
            "daily_cost": daily_cost,
            "financial_exposure": exposure,
            "recommended_action": _recommended_action(days_past),
        })

    items.sort(key=lambda r: r["financial_exposure"], reverse=True)

    return {
        "items": items,
        "total_exposure": sum(r["financial_exposure"] for r in items),
        "count": len(items),
    }


def get_days_of_coverage_lost(
    assets: list[dict[str, Any]],
    *,
    today: date | None = None,
) -> dict[str, Any]:
    """
    Aggregate the total "uncovered" exposure across all overdue assets.

    Returns:
        dict with keys:
        - total_days_lost: int — sum of days past renewal across all overdue
        - total_financial_risk: float — sum of financial exposure
        - by_category: dict[category] = {days_lost, financial_risk, asset_count}
        - assets_overdue: int — number of overdue assets
    """
    if today is None:
        today = date.today()

    total_days = 0
    total_risk = 0.0
    overdue_count = 0
    by_category: dict[str, dict[str, float]] = {}

    for asset in assets:
        renewal = _parse_date(asset.get("renewal_date"))
        if renewal is None:
            continue
        days_past = (today - renewal).days
        if days_past <= 0:
            continue

        cost = _safe_float(asset.get("annual_cost_usd")) or 0.0
        risk = (cost / 365.0) * days_past
        cat = asset.get("category", "Uncategorized") or "Uncategorized"

        total_days += days_past
        total_risk += risk
        overdue_count += 1

        if cat not in by_category:
            by_category[cat] = {
                "days_lost": 0,
                "financial_risk": 0.0,
                "asset_count": 0,
            }
        by_category[cat]["days_lost"] += days_past
        by_category[cat]["financial_risk"] += risk
        by_category[cat]["asset_count"] += 1

    return {
        "total_days_lost": total_days,
        "total_financial_risk": total_risk,
        "assets_overdue": overdue_count,
        "by_category": by_category,
    }


def get_monthly_burn_rate_trend(
    assets: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Monthly actual-vs-budget plus cumulative variance trajectory.

    Spreads each asset's annual_cost_usd evenly across 12 months as actual
    burn, and budget_annual_usd (or 7.5% above actual when missing) as
    monthly budget. Then computes cumulative running totals and the
    end-of-period cumulative variance %.

    Returns:
        dict with keys:
        - months: list of month names (Jan-Dec)
        - monthly_actual: list[float] — actual burn per month
        - monthly_budget: list[float] — budget per month
        - cumulative_actual: list[float] — running sum of actual
        - cumulative_budget: list[float] — running sum of budget
        - cumulative_variance: list[float] — actual - budget per month (running)
        - cumulative_variance_pct: list[float] — running (actual-budget)/budget %
        - end_of_year_variance_pct: float — final variance % at month 12
        - trending: "Under budget" | "Over budget" | "On budget"
    """
    months = [
        "Jan", "Feb", "Mar", "Apr", "May", "Jun",
        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    ]

    monthly_actual = [0.0] * 12
    monthly_budget = [0.0] * 12

    for asset in assets:
        cost = _safe_float(asset.get("annual_cost_usd")) or 0.0
        bud = _safe_float(asset.get("budget_annual_usd"))
        if bud is None:
            bud = cost * 1.075  # default 7.5% over actual

        m_actual = cost / 12.0
        m_budget = bud / 12.0
        for i in range(12):
            monthly_actual[i] += m_actual
            monthly_budget[i] += m_budget

    cumulative_actual: list[float] = []
    cumulative_budget: list[float] = []
    cumulative_variance: list[float] = []
    cumulative_variance_pct: list[float] = []

    running_actual = 0.0
    running_budget = 0.0
    for i in range(12):
        running_actual += monthly_actual[i]
        running_budget += monthly_budget[i]
        cumulative_actual.append(running_actual)
        cumulative_budget.append(running_budget)
        diff = running_actual - running_budget
        cumulative_variance.append(diff)
        if running_budget > 0:
            cumulative_variance_pct.append(diff / running_budget * 100.0)
        else:
            cumulative_variance_pct.append(0.0)

    eoy_variance = cumulative_variance_pct[-1] if cumulative_variance_pct else 0.0
    if abs(eoy_variance) < 0.5:
        trending = "On budget"
    elif eoy_variance < 0:
        trending = "Under budget"
    else:
        trending = "Over budget"

    return {
        "months": months,
        "monthly_actual": monthly_actual,
        "monthly_budget": monthly_budget,
        "cumulative_actual": cumulative_actual,
        "cumulative_budget": cumulative_budget,
        "cumulative_variance": cumulative_variance,
        "cumulative_variance_pct": cumulative_variance_pct,
        "end_of_year_variance_pct": eoy_variance,
        "trending": trending,
    }
