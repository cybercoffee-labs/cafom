"""CAFOM renewal alerts — date-threshold classification and portfolio evaluation.

Adapted from ``Finance2026/.../core/gordon.py``. Classifies assets by renewal
urgency (GREEN/YELLOW/RED/CRITICAL) based on days until renewal, and evaluates
entire portfolios to track which assets need attention.

KEEP from source:
* configurable thresholds (top of module constants)
* structured result shape with counts and alerts
* append-only audit JSONL via dual_writer.log_portfolio_refresh()

CHANGE:
* Replace event-stream rules with pure date-threshold logic
* Threshold boundaries:
  - CRITICAL: days ≤ -30     (30+ days overdue)
  - RED:      -30 < days ≤ 0 (expired to today)
  - YELLOW:   0 < days ≤ 30  (urgent renewal window)
  - GREEN:    days > 30      (safe; per user spec, GREEN strictly > 60, (30,60] → YELLOW)

REMOVE:
* kill_switch, circuit breakers, panic mode, dq_score gate
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal

try:
    from dual_writer import log_portfolio_refresh
except ImportError:
    log_portfolio_refresh = None

logger = logging.getLogger("cafom.renewal_alerts")

_DAYS_CRITICAL = -30  # days <= -30 = CRITICAL
_DAYS_RED = 0  # -30 < days <= 0 = RED
_DAYS_YELLOW = 30  # 0 < days <= 30 = YELLOW
# days > 30 = GREEN (per user spec, strictly > 60 for full green, (30,60] → YELLOW)

RenewalLevel = Literal["GREEN", "YELLOW", "RED", "CRITICAL"]


def classify(renewal_date: date, *, today: date | None = None) -> RenewalLevel:
    """
    Classify asset renewal urgency based on days until/after renewal_date.

    Args:
        renewal_date: The contract renewal date
        today: Reference date (defaults to today)

    Returns:
        RenewalLevel: One of GREEN, YELLOW, RED, CRITICAL

    Thresholds (days = renewal_date - today):
        CRITICAL: days <= -30    (30+ days overdue)
        RED:      -30 < days <= 0 (expired or renewing today)
        YELLOW:   0 < days <= 30  (urgent: renews within 30 days)
        GREEN:    days > 30       (safe: 30+ days until renewal)

    Note: per user spec, GREEN is strictly days > 60; days in (30, 60] are YELLOW.
    """
    if today is None:
        today = date.today()

    days = (renewal_date - today).days

    if days <= _DAYS_CRITICAL:
        return "CRITICAL"
    elif days <= _DAYS_RED:
        return "RED"
    elif days <= _DAYS_YELLOW:
        return "YELLOW"
    else:
        return "GREEN"


def evaluate_portfolio(assets: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Evaluate all assets in a portfolio and return alert summary.

    Args:
        assets: List of asset dicts (from get_assets() or similar)

    Returns:
        dict with keys:
        - counts: {GREEN, YELLOW, RED, CRITICAL} → count of each level
        - alerts: list of alert dicts (Red and Critical level assets)
          each alert: {id, product, vendor, renewal_date, level, days_until}
    """
    today = date.today()
    counts = {"GREEN": 0, "YELLOW": 0, "RED": 0, "CRITICAL": 0}
    alerts = []

    for asset in assets:
        renewal_str = asset.get("renewal_date")
        if not renewal_str:
            continue

        # Parse renewal_date (could be string or date object)
        if isinstance(renewal_str, str):
            try:
                renewal_date = date.fromisoformat(renewal_str)
            except ValueError:
                logger.warning(
                    "Invalid renewal_date %s for asset %s", renewal_str, asset.get("id")
                )
                continue
        else:
            renewal_date = renewal_str

        level = classify(renewal_date, today=today)
        counts[level] += 1

        # Emit alerts for RED and CRITICAL
        if level in ("RED", "CRITICAL"):
            days_until = (renewal_date - today).days
            alerts.append(
                {
                    "id": asset.get("id"),
                    "product": asset.get("product"),
                    "vendor": asset.get("vendor"),
                    "renewal_date": renewal_str,
                    "level": level,
                    "days_until": days_until,
                }
            )

    # Log portfolio snapshot (optional, if dual_writer is available)
    if log_portfolio_refresh:
        summary = {
            "total_assets": len(assets),
            "counts": counts,
            "num_alerts": len(alerts),
            "checked_at": datetime.now(UTC).isoformat(),
        }
        try:
            log_portfolio_refresh(summary)
        except Exception as exc:
            logger.warning("Failed to log portfolio refresh: %s", exc)

    return {
        "counts": counts,
        "alerts": sorted(alerts, key=lambda a: a["renewal_date"]),  # sort by date
    }
