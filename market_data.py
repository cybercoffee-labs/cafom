"""CAFOM market data — public cybersecurity vendor stock prices.

Pulls historical price data from Yahoo Finance for the subset of CAFOM
vendors that are publicly traded (8 of 12). Splunk (acquired by Cisco),
Proofpoint (taken private 2021), Darktrace (acquired by Thoma Bravo 2024),
and Wiz (private) are deliberately excluded — no tickers fabricated.

Builds an equal-weighted "Cybersecurity Public Vendor Index" from available
tickers and exposes per-vendor trends. All network calls degrade gracefully:
unavailable tickers are skipped, never crashing the caller.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

logger = logging.getLogger("cafom.market_data")

# Public tickers for CAFOM vendors. Private/acquired vendors deliberately omitted:
# - Splunk: acquired by Cisco in 2024 (no clean ticker)
# - Proofpoint: taken private by Thoma Bravo in 2021
# - Darktrace: acquired by Thoma Bravo in 2024
# - Wiz: still private as of 2026
_VENDOR_TICKERS: dict[str, str] = {
    "CrowdStrike": "CRWD",
    "Palo Alto Networks": "PANW",
    "Zscaler": "ZS",
    "Okta": "OKTA",
    "SentinelOne": "S",
    "Microsoft": "MSFT",
    "Tenable": "TENB",
    "Qualys": "QLYS",
}

_DEFAULT_START = "2023-01-01"
_DEFAULT_END = "2026-04-30"

_INDEX_LABEL = "Cybersecurity Public Vendor Index (8 of 12 CAFOM vendors)"


def _try_import_yfinance():
    """Lazy yfinance import; returns None if unavailable."""
    try:
        import yfinance as yf
        return yf
    except ImportError:
        logger.warning("yfinance not installed; market data unavailable")
        return None


def _download_prices(
    tickers: list[str],
    start: str = _DEFAULT_START,
    end: str = _DEFAULT_END,
) -> dict[str, Any]:
    """
    Download adjusted close prices for given tickers.

    Returns a dict mapping ticker -> pandas.Series of closes.
    Tickers that fail to download are silently skipped.
    """
    yf = _try_import_yfinance()
    if yf is None:
        return {}

    result: dict[str, Any] = {}
    for ticker in tickers:
        try:
            data = yf.download(
                ticker,
                start=start,
                end=end,
                progress=False,
                auto_adjust=True,
                threads=False,
            )
            if data is None or data.empty:
                logger.info("No data returned for %s", ticker)
                continue
            # Get the close series — handle both single-level and multi-level columns
            if "Close" in data.columns:
                close = data["Close"]
                # If multi-ticker download produced a DataFrame, extract column
                if hasattr(close, "columns"):
                    close = close.iloc[:, 0]
            else:
                logger.info("No 'Close' column for %s", ticker)
                continue
            close = close.dropna()
            if close.empty:
                continue
            result[ticker] = close
        except Exception as exc:
            logger.warning("Failed to download %s: %s", ticker, exc)
            continue
    return result


def get_vendor_trends(
    start: str = _DEFAULT_START,
    end: str = _DEFAULT_END,
) -> dict[str, dict[str, float]]:
    """
    Per-vendor price trend summary.

    Returns:
        dict mapping vendor display name to {ticker, start_price, end_price,
        pct_change}. Vendors whose tickers fail to load are omitted entirely.
    """
    tickers = list(_VENDOR_TICKERS.values())
    prices = _download_prices(tickers, start=start, end=end)

    # Reverse lookup ticker → vendor display name
    inv = {v: k for k, v in _VENDOR_TICKERS.items()}

    trends: dict[str, dict[str, float]] = {}
    for ticker, series in prices.items():
        try:
            vendor = inv.get(ticker, ticker)
            start_price = float(series.iloc[0])
            end_price = float(series.iloc[-1])
            pct_change = (
                (end_price - start_price) / start_price * 100.0
                if start_price > 0 else 0.0
            )
            trends[vendor] = {
                "ticker": ticker,
                "start_price": start_price,
                "end_price": end_price,
                "pct_change": pct_change,
            }
        except (IndexError, ValueError, ZeroDivisionError) as exc:
            logger.warning("Trend computation failed for %s: %s", ticker, exc)
            continue
    return trends


def get_sector_index(
    start: str = _DEFAULT_START,
    end: str = _DEFAULT_END,
) -> dict[str, Any]:
    """
    Build an equal-weighted Cybersecurity Public Vendor Index.

    For each trading day, normalizes each ticker's price by its starting
    price (so all start at 1.0) and averages across all available tickers.
    The index thus represents "growth multiple of an equal-weighted basket"
    relative to the start date.

    Returns:
        dict with keys:
        - label: human-readable index name
        - tickers_used: list of tickers actually included
        - tickers_missing: list of tickers we tried but couldn't load
        - dates: list of ISO date strings
        - values: list of index values (1.0 = baseline, e.g. 1.5 = +50%)
    """
    tickers = list(_VENDOR_TICKERS.values())
    prices = _download_prices(tickers, start=start, end=end)

    used = sorted(prices.keys())
    missing = sorted(set(tickers) - set(used))

    if not prices:
        return {
            "label": _INDEX_LABEL,
            "tickers_used": [],
            "tickers_missing": missing,
            "dates": [],
            "values": [],
        }

    try:
        import pandas as pd
    except ImportError:
        logger.warning("pandas not installed; cannot build sector index")
        return {
            "label": _INDEX_LABEL,
            "tickers_used": used,
            "tickers_missing": missing,
            "dates": [],
            "values": [],
        }

    # Build DataFrame, normalize each column to its first value, then average.
    df = pd.concat(prices, axis=1)
    df.columns = list(prices.keys())
    df = df.dropna(how="all")
    if df.empty:
        return {
            "label": _INDEX_LABEL,
            "tickers_used": used,
            "tickers_missing": missing,
            "dates": [],
            "values": [],
        }

    # Normalize each ticker by its first observed value
    normalized = df.div(df.bfill().iloc[0])
    # Equal-weighted mean across tickers (skip NaN columns per row)
    index_series = normalized.mean(axis=1, skipna=True)

    return {
        "label": _INDEX_LABEL,
        "tickers_used": used,
        "tickers_missing": missing,
        "dates": [d.strftime("%Y-%m-%d") for d in index_series.index],
        "values": [float(v) for v in index_series.values],
    }
