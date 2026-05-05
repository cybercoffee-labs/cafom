"""CAFOM — Dual Writer.

Adapted from ``batman_flow_engine/core/dual_writer.py``. Writes asset,
vendor-health, and portfolio-refresh records to JSONL files (always)
and Postgres (only when available).

KEEP from source:
* JSONL-always semantics — local file is authoritative.
* Cached PostgreSQL availability gate via ``pg_available()``.
* ``json.dumps(..., default=str)`` for type coercion (dates, Decimals).
* Append-only ``with open(path, "a")``.
* Returns ``True`` only when the JSONL write succeeded.

CHANGE vs source:
* ``log_opportunity`` → ``log_asset``.
* ``opportunities.jsonl`` → ``assets.jsonl``.
* Drops scanner/engine run loggers; adds ``log_vendor_check`` and
  ``log_portfolio_refresh`` for the new operational events.

REMOVE from source: scanner_type / scanner_name / opps_found /
viable_found, cycle_id, every reference to scanners or engine_runs.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("cafom.writer")

# Module-level log directory. Tests monkeypatch this attribute to point
# at a temp directory; the file getters below resolve fresh paths each
# call so the patch is honored.
_LOG_DIR: Path = Path(__file__).resolve().parent / "data" / "logs"

_pg_available: bool | None = None


# ---------------------------------------------------------------------------
# Path helpers — resolve from current ``_LOG_DIR`` so tests can monkeypatch.
# ---------------------------------------------------------------------------
def _assets_log() -> Path:
    return _LOG_DIR / "assets.jsonl"


def _vendor_checks_log() -> Path:
    return _LOG_DIR / "vendor_checks.jsonl"


def _portfolio_log() -> Path:
    return _LOG_DIR / "portfolio_snapshots.jsonl"


# ---------------------------------------------------------------------------
# Postgres availability — cached. CAFOM defaults to JSONL-only.
# ---------------------------------------------------------------------------
def _check_pg() -> bool:
    """Probe whether PostgreSQL is reachable. Result is cached forever."""
    global _pg_available
    if _pg_available is None:
        try:
            from database.postgres import check_connection  # type: ignore[import-not-found]

            result = check_connection()
            _pg_available = bool(result.get("status") == "ok")
            if _pg_available:
                logger.info("PostgreSQL available — dual writing enabled")
            else:
                logger.info("PostgreSQL unavailable — JSONL only")
        except Exception:
            _pg_available = False
    return _pg_available


def pg_available() -> bool:
    """Public wrapper around :func:`_check_pg` (used by api/Streamlit)."""
    return _check_pg()


# ---------------------------------------------------------------------------
# Writers — same shape: write JSONL first, mirror to PG if available.
# ---------------------------------------------------------------------------
def _append_jsonl(path: Path, record: dict[str, Any]) -> bool:
    """Write a single JSON record + newline to ``path``. Returns success."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")
        return True
    except Exception as exc:  # pragma: no cover — defensive
        logger.error("JSONL write failed for %s: %s", path, exc)
        return False


def log_asset(asset: dict[str, Any], *, log_to_file: bool = True) -> bool:
    """Persist a CyberAsset record.

    Returns ``True`` only when the JSONL write succeeded. Postgres
    failures are logged but do not flip the return value — local
    JSONL is always authoritative.
    """
    success = False
    if log_to_file:
        success = _append_jsonl(_assets_log(), asset)

    if _check_pg():
        try:
            from database.postgres import save_asset  # type: ignore[import-not-found]

            save_asset(asset)
        except Exception as exc:
            logger.warning("PostgreSQL asset write failed (JSONL still saved): %s", exc)

    return success


def log_vendor_check(
    vendor_url: str,
    status: str,
    *,
    response_ms: float | None = None,
    status_code: int | None = None,
    reason: str | None = None,
) -> bool:
    """Append a vendor health-check result. JSONL is authoritative."""
    record = {
        "vendor_url": vendor_url,
        "status": status,
        "response_ms": response_ms,
        "status_code": status_code,
        "reason": reason,
    }
    success = _append_jsonl(_vendor_checks_log(), record)

    if _check_pg():
        try:
            from database.postgres import save_vendor_check  # type: ignore[import-not-found]

            save_vendor_check(record)
        except Exception as exc:
            logger.warning("PostgreSQL vendor_check write failed: %s", exc)

    return success


def log_portfolio_refresh(summary: dict[str, Any]) -> bool:
    """Append a daily portfolio snapshot (counts, totals, alert tallies)."""
    success = _append_jsonl(_portfolio_log(), summary)

    if _check_pg():
        try:
            from database.postgres import save_portfolio_snapshot  # type: ignore[import-not-found]

            save_portfolio_snapshot(summary)
        except Exception as exc:
            logger.warning("PostgreSQL portfolio snapshot write failed: %s", exc)

    return success
