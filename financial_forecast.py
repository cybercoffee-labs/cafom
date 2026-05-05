"""CAFOM financial forecast — 12-month cost projection.

Projects annual costs month-by-month by spreading annual costs and adding
any contracts that renew in each month (assuming re-up at same cost).
"""

from __future__ import annotations

import logging
from calendar import monthrange
from datetime import date, timedelta
from typing import Any

logger = logging.getLogger("cafom.financial_forecast")


def project_12_months(assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Project monthly costs for the next 12 months.

    For each month, sums:
    - (annual_cost_usd / 12) for all active assets
    - full annual_cost_usd for any assets renewing in that month (assume re-up at same cost)

    Args:
        assets: List of asset dicts (must have 'annual_cost_usd', 'renewal_date', 'status')

    Returns:
        List of dicts with keys: year, month, month_name, projected_cost, renewals_count
    """
    today = date.today()
    forecast = []

    # Iterate 12 months starting from next month
    current = date(today.year, today.month, 1)
    for i in range(12):
        # Move to next month
        if current.month == 12:
            next_month = date(current.year + 1, 1, 1)
        else:
            next_month = date(current.year, current.month + 1, 1)

        month_start = current
        month_end = date(
            next_month.year,
            next_month.month,
            monthrange(next_month.year, next_month.month)[1],
        )
        if i == 0:
            # First month: start from today
            month_start = today

        monthly_cost = 0.0
        renewals_in_month = 0

        for asset in assets:
            # Skip inactive assets
            if asset.get("status") == "Decommissioned":
                continue

            try:
                annual_cost = float(asset.get("annual_cost_usd", 0) or 0)
            except (ValueError, TypeError):
                annual_cost = 0.0

            # Add monthly allocation
            monthly_cost += annual_cost / 12.0

            # Check if asset renews this month
            renewal_str = asset.get("renewal_date")
            if renewal_str:
                try:
                    if isinstance(renewal_str, str):
                        renewal_date = date.fromisoformat(renewal_str)
                    else:
                        renewal_date = renewal_str

                    if month_start <= renewal_date <= month_end:
                        monthly_cost += annual_cost  # Add full cost for renewal
                        renewals_in_month += 1
                except (ValueError, TypeError):
                    pass

        month_names = [
            "January", "February", "March", "April", "May", "June",
            "July", "August", "September", "October", "November", "December",
        ]
        month_name = month_names[current.month - 1]

        forecast.append({
            "year": current.year,
            "month": current.month,
            "month_name": month_name,
            "projected_cost": monthly_cost,
            "renewals_count": renewals_in_month,
        })

        current = next_month

    return forecast
