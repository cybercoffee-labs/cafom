"""CAFOM anomaly detection — flag outlier asset costs.

Identifies assets with unusual annual costs relative to their category by
computing mean + std per category and flagging those exceeding mean + 2σ.
"""

from __future__ import annotations

import logging
from statistics import mean, stdev
from typing import Any

logger = logging.getLogger("cafom.anomaly_detector")


def flag_outliers(assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Flag assets with outlier annual costs within their category.

    Groups assets by category, computes mean and std of annual_cost_usd,
    and marks those exceeding mean + 2σ as anomalies.

    Args:
        assets: List of asset dicts (must have 'category' and 'annual_cost_usd')

    Returns:
        List of flagged assets (subset of input) marked with is_outlier=True
    """
    if not assets:
        return []

    # Group by category
    by_category: dict[str, list[dict[str, Any]]] = {}
    for asset in assets:
        cat = asset.get("category", "Uncategorized")
        if cat not in by_category:
            by_category[cat] = []
        by_category[cat].append(asset)

    # Compute stats per category and flag outliers
    flagged = []
    for category, cat_assets in by_category.items():
        costs = []
        for asset in cat_assets:
            try:
                cost = float(asset.get("annual_cost_usd", 0) or 0)
                costs.append(cost)
            except (ValueError, TypeError):
                pass

        if len(costs) < 2:
            # Can't compute std with < 2 values
            continue

        try:
            m = mean(costs)
            s = stdev(costs)
            threshold = m + 2 * s

            for asset in cat_assets:
                try:
                    cost = float(asset.get("annual_cost_usd", 0) or 0)
                    if cost > threshold:
                        flagged_asset = dict(asset)
                        flagged_asset["is_outlier"] = True
                        flagged_asset["category_mean"] = m
                        flagged_asset["category_std"] = s
                        flagged_asset["threshold"] = threshold
                        flagged.append(flagged_asset)
                except (ValueError, TypeError):
                    pass
        except Exception as exc:
            logger.warning("Failed to compute stats for category %s: %s", category, exc)

    return flagged
