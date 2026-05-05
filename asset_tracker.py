"""CAFOM asset tracker — SQLite ledger + ingestion.

Adapted from ``batman_flow_engine/core/harvey.py``. Manages a SQLite
database of cyber assets, ingests from the JSONL log written by
dual_writer, and provides read APIs for queries (status filters,
renewal windows, cost aggregation).

KEEP from source:
* _get_conn() factory with sqlite3.Row row_factory
* _ensure_schema() idempotent CREATE TABLE IF NOT EXISTS
* _load_assets() append-only JSONL ingestion with malformed-line skipping
* INSERT OR IGNORE dedup pattern (by id PRIMARY KEY)

CHANGE:
* signals table → assets table; drop scanner_id, type, asset, venue, edge
* Add columns: id, product, vendor, category, purchase_date, renewal_date,
  contract_term_months, annual_cost_usd, capex_opex, owner, status, health_check_url
* Remove _derive_edge, get_asset_statistics, get_venue_statistics, etc.
* Add get_assets(status=None), get_renewals_within(days), daily_renewal_exposure_usd(days)
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger("cafom.asset_tracker")

_DB_PATH: Path = Path(__file__).resolve().parent / "data" / "cafom.db"


def _get_conn() -> sqlite3.Connection:
    """Get or create SQLite connection with Row factory."""
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Create tables if they don't exist."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS assets (
            id TEXT PRIMARY KEY,
            product TEXT,
            vendor TEXT,
            category TEXT,
            purchase_date TEXT,
            renewal_date TEXT,
            contract_term_months INTEGER,
            annual_cost_usd REAL,
            capex_opex TEXT,
            owner TEXT,
            status TEXT,
            health_check_url TEXT,
            last_health_check_at TEXT,
            vendor_contact_email TEXT,
            raw_json TEXT
        );
        """
    )


def init_db(db_path: Path | None = None) -> None:
    """Initialize SQLite database schema."""
    if db_path:
        global _DB_PATH
        _DB_PATH = db_path
    conn = _get_conn()
    _ensure_schema(conn)
    conn.close()


def _load_assets(jsonl_path: Path) -> list[dict[str, Any]]:
    """Load and parse assets.jsonl. Skip malformed lines."""
    if not jsonl_path.exists():
        logger.info("Assets log not found: %s", jsonl_path)
        return []
    assets = []
    with jsonl_path.open("r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("Skipping malformed JSON at line %d in %s", line_num, jsonl_path)
                continue
            if not isinstance(record, dict):
                logger.warning("Skipping non-dict at line %d in %s", line_num, jsonl_path)
                continue
            assets.append(record)
    return assets


def ingest_assets(jsonl_path: Path, db_path: Path | None = None) -> dict[str, int]:
    """Load assets from JSONL and insert into SQLite (dedup by id)."""
    if db_path:
        global _DB_PATH
        _DB_PATH = db_path
    assets = _load_assets(jsonl_path)
    conn = _get_conn()
    cursor = conn.cursor()
    scanned = len(assets)
    inserted = 0
    for asset in assets:
        asset_id = asset.get("id")
        if not asset_id:
            continue
        try:
            cursor.execute(
                """
                INSERT OR IGNORE INTO assets (
                    id, product, vendor, category, purchase_date, renewal_date,
                    contract_term_months, annual_cost_usd, capex_opex, owner,
                    status, health_check_url, last_health_check_at,
                    vendor_contact_email, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    asset.get("id"),
                    asset.get("product"),
                    asset.get("vendor"),
                    asset.get("category"),
                    asset.get("purchase_date"),
                    asset.get("renewal_date"),
                    asset.get("contract_term_months"),
                    asset.get("annual_cost_usd"),
                    asset.get("capex_opex"),
                    asset.get("owner"),
                    asset.get("status"),
                    str(asset.get("health_check_url")),
                    asset.get("last_health_check_at"),
                    asset.get("vendor_contact_email"),
                    json.dumps(asset, default=str),
                ),
            )
            inserted += cursor.rowcount
        except sqlite3.Error as e:
            logger.warning("Failed to insert asset %s: %s", asset_id, e)
    conn.commit()
    conn.close()
    return {"scanned": scanned, "inserted": inserted}


def get_assets(status: str | None = None) -> list[dict[str, Any]]:
    """Fetch all assets, optionally filtered by status."""
    conn = _get_conn()
    cursor = conn.cursor()
    if status:
        cursor.execute("SELECT * FROM assets WHERE status = ?", (status,))
    else:
        cursor.execute("SELECT * FROM assets")
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_renewals_within(days: int) -> list[dict[str, Any]]:
    """Fetch assets renewing within N days from today."""
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT * FROM assets
        WHERE renewal_date BETWEEN date('now') AND date('now', '+' || ? || ' days')
        ORDER BY renewal_date ASC
        """,
        (days,),
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def daily_renewal_exposure_usd(days: int = 30) -> float:
    """Sum annual_cost_usd for assets renewing within N days."""
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT COALESCE(SUM(annual_cost_usd), 0) as total
        FROM assets
        WHERE renewal_date BETWEEN date('now') AND date('now', '+' || ? || ' days')
        """,
        (days,),
    )
    result = cursor.fetchone()
    conn.close()
    return float(result["total"]) if result else 0.0
