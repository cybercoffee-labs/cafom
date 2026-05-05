# CAFOM — Construction Plan

**Cyber Asset Financial Operations Manager** — a Streamlit dashboard + FastAPI service that tracks the lifecycle, vendor health, renewal exposure, and CAPEX/OPEX forecast of an enterprise's cybersecurity tooling portfolio. The artifact demonstrates the exact skill mix in a "Cybersecurity Analyst II — Financial Operations Specialist" role: SQLite-backed asset ledger, append-only audit trail, vendor health probing, threshold-based renewal alerts, and anomaly detection on annual cost.

## Build sequence (strict order, halt on first test failure)

1. `dual_writer.py` — JSONL-always + Postgres-optional sink (foundation for every other module).
2. `schemas.py` — Pydantic v2 `CyberAssetModel` (validates everything written by all modules).
3. `asset_tracker.py` — SQLite schema + queries; consumes JSONL, exposes read API.
4. `vendor_health.py` — TTL-cached HTTP health probes per vendor URL.
5. `renewal_alerts.py` — date-threshold gate (GREEN/YELLOW/RED/CRITICAL) on renewal_date.
6. `api.py` — FastAPI surface (`/healthz`, `/assets`, `/vendors/health`, `/renewals/alerts`).
7. `app.py` — Streamlit dashboard (4 tabs + KPI strip).

Side modules built alongside (#7 only): `anomaly_detector.py`, `financial_forecast.py`.

---

## Module-by-module spec (Phase 3 reference)

### 1. `dual_writer.py`  (adapts `Finance2026/.../core/dual_writer.py`)
- **Public API**: `log_asset(asset: dict, *, log_to_file: bool = True) -> bool`, `log_vendor_check(vendor: str, status: str, response_ms: float, status_code: int | None) -> bool`, `log_portfolio_refresh(summary: dict) -> bool`, `pg_available() -> bool` (cached).
- **KEEP from source**: JSONL-always semantics, `_check_pg()` cached availability gate, `json.dumps(..., default=str)` for type coercion, append-only `with open(path, "a")`, exception handling that returns `True` only when JSONL write succeeded.
- **CHANGE**: `log_opportunity` → `log_asset`; file `opportunities.jsonl` → `assets.jsonl`; field set tailored to CyberAsset (id, product, vendor, renewal_date, status); add `log_vendor_check()` for health-probe records → `vendor_checks.jsonl`; add `log_portfolio_refresh()` for daily snapshot → `portfolio_snapshots.jsonl`.
- **REMOVE**: scanner_type / scanner_name / opps_found / viable_found, cycle_id, every reference to scanners or engine_runs.
- **Postgres path**: stays optional; CAFOM defaults to `pg_available() == False` (no DB required for v1).
- **Test plan** — `tests/test_dual_writer.py`:
  - `test_log_asset_writes_jsonl` — temp dir, monkeypatch `_LOG_DIR`, assert file exists, line is valid JSON, has expected keys.
  - `test_log_asset_returns_true` — happy path.
  - `test_log_asset_returns_false_when_path_unwritable` — point at `/dev/null/xxx`, expect False + warning logged.
  - `test_pg_available_returns_false_when_no_module` — sufficient because no PG installed in CI.
  - `test_log_vendor_check_writes_separate_file` — confirms multi-stream pattern.
- **Verify**: `pytest tests/test_dual_writer.py -v`.

### 2. `schemas.py`  (adapts `contable_bot/core/schemas.py`)
- **Public API**: `class CyberAssetModel(BaseModel)`, `class AssetPortfolioModel(BaseModel)`, `validate_asset(any) -> CyberAssetModel`, `validate_portfolio(Iterable[dict]) -> list[CyberAssetModel]`.
- **KEEP**: Pydantic v2, `model_config = ConfigDict(extra="allow", str_strip_whitespace=True)`, `@field_validator(... mode="before") @classmethod` decorator pattern, `_as_mapping()` helper, aggregated-error pattern from `validate_consolidated_operations()`.
- **CHANGE / ADD**: replace `BankMovementModel` with `CyberAssetModel`; fields:
  ```
  id: str (regex AST-\d{3,})
  product: str
  vendor: str
  category: str
  purchase_date: date
  renewal_date: date
  contract_term_months: int (ge=1)
  annual_cost_usd: Decimal (ge=0)
  capex_opex: Literal["CAPEX", "OPEX"]
  owner: str
  status: Literal["Active","Expired","Renewed","Decommissioned"]
  health_check_url: HttpUrl
  ```
- **New validators**:
  - `_renewal_after_purchase(self) -> Self` (model_validator, mode="after") — raise `ValueError("renewal_date must be > purchase_date")` if violated.
  - `_id_format(cls, v)` — must match `AST-\d{3,}`.
- **REMOVE**: `BankMovementModel`, `ConsolidatedOperationModel`, IVA/comision validators.
- **Test plan** — `tests/test_schemas.py`:
  - `test_valid_asset_parses` — full minimal asset dict.
  - `test_renewal_before_purchase_rejected` — expects `ValidationError`.
  - `test_negative_cost_rejected`, `test_invalid_capex_opex_rejected`.
  - `test_extra_fields_allowed` (per `extra="allow"`).
  - `test_validate_portfolio_aggregates_errors` — bad row mixed with good row, error contains both row indices.
- **Verify**: `pytest tests/test_schemas.py -v`.

### 3. `asset_tracker.py`  (adapts `Finance2026/.../core/harvey.py`)
- **Public API**: `init_db(db_path: Path) -> None`, `ingest_assets(jsonl_path: Path, db_path: Path) -> dict[str, int]`, `get_assets(db_path: Path, *, status: str | None = None) -> list[dict]`, `get_renewals_within(db_path: Path, days: int) -> list[dict]`, `daily_renewal_exposure_usd(db_path: Path, days: int = 30) -> float`.
- **KEEP**: `_get_conn()` factory + `sqlite3.Row` row_factory, `_ensure_schema()` idempotent CREATE, `INSERT OR IGNORE` dedup pattern, append-only JSONL ingestion via `_load_assets()`, structured logging.
- **CHANGE**:
  - Drop `signals` table; add `assets` table with the CyberAsset fields above (id PRIMARY KEY).
  - Drop `_derive_edge`, `get_asset_statistics` (crypto), `get_venue_statistics`, `get_signal_type_statistics`, `get_top_edges`, `get_scanner_performance`.
  - Add `get_assets()` with optional status filter.
  - Replace `daily_exposure(fiat, date)` with `daily_renewal_exposure_usd(days=30)` — sum of `annual_cost_usd` for assets with `renewal_date BETWEEN today AND today+days`.
- **REMOVE**: P&L, daily_pnl, scanner_stats, exchange/venue references.
- **Test plan** — `tests/test_asset_tracker.py`:
  - `test_init_db_creates_assets_table` — sqlite_master query confirms.
  - `test_ingest_writes_then_dedupes` — ingest twice, count stays the same.
  - `test_get_assets_filters_by_status`.
  - `test_renewals_within_returns_only_in_window`.
  - `test_daily_renewal_exposure_sums_costs`.
- **Verify**: `pytest tests/test_asset_tracker.py -v`.

### 4. `vendor_health.py`  (adapts `Finance2026/.../core/aquaman.py`)
- **Public API**: `class VendorHealthChecker`, `check_vendor(url: str, *, timeout_sec: float = 5.0) -> dict`, `check_all(urls: Iterable[str]) -> dict[str, dict]`.
- **KEEP**: TTL cache pattern (`self._cache: dict[str, tuple[float, dict]]`), `threading.Lock()` for thread safety, "never cache errors" rule (errors bypass cache so retries probe live), `_error_result(reason: str) -> dict` helper.
- **CHANGE**: replace CCXT with `requests`. `check_vendor(url)` performs `requests.get(url, timeout=5)`:
  - `200 ≤ status < 400` → `{status: "Healthy", status_code, response_ms, checked_at}`.
  - `requests.Timeout` → `{status: "Degraded", reason: "timeout", response_ms: 5000, checked_at}`.
  - any other Exception / 4xx / 5xx → `{status: "Down", reason: str(exc), checked_at}`.
- **REMOVE**: orderbook depth, `_estimate_slippage`, `_depth_within_band`, `_infer_exchange_id`, all CCXT imports.
- **Test plan** — `tests/test_vendor_health.py`:
  - `test_check_vendor_healthy_when_200` — monkeypatch `requests.get` returning fake 200 response.
  - `test_check_vendor_degraded_on_timeout` — monkeypatch raising `requests.Timeout`.
  - `test_check_vendor_down_on_500`.
  - `test_cache_hits_for_repeated_url` — confirm `requests.get` called once across two calls within TTL.
  - `test_errors_never_cached` — second call after error re-probes.
- **Verify**: `pytest tests/test_vendor_health.py -v`.

### 5. `renewal_alerts.py`  (adapts `Finance2026/.../core/gordon.py`)
- **Public API**: `RenewalLevel = Literal["GREEN","YELLOW","RED","CRITICAL"]`, `classify(renewal_date: date, *, today: date | None = None) -> RenewalLevel`, `evaluate_portfolio(assets: list[dict]) -> dict` returning `{counts: {GREEN, YELLOW, RED, CRITICAL}, alerts: [...]}`.
- **KEEP**: configurable thresholds (top of module constants), structured `{status, checks, blocked_by, warnings}` result shape, append-only audit JSONL via `dual_writer.log_portfolio_refresh()`, `_utc_timestamp()` helper.
- **CHANGE / threshold logic**:
  ```
  days = (renewal_date - today).days
  CRITICAL: days <= -30        # 30+ days overdue
  RED:      -30 < days <= 0    # expired today or recently
  YELLOW:   0 < days <= 30     # urgent
  GREEN:    days > 30          # safe (within window) OR > 60 (per spec)
  ```
  Per spec: GREEN strictly when `> 60`; days in `(30, 60]` we'll classify as YELLOW (closest to user intent). I'll surface this in the doc comment for review.
- **REMOVE**: `is_kill_switch_active()`, `check_runtime_guard()`, dq_score gate, regime/PANIC, circuit breakers, kill_switch.flag handling.
- **Test plan** — `tests/test_renewal_alerts.py`:
  - `test_classify_green_far_future`, `test_classify_yellow_30d`, `test_classify_red_today`, `test_classify_critical_60d_overdue`.
  - `test_classify_boundary_60d` (must be GREEN).
  - `test_evaluate_portfolio_counts_each_level`.
  - `test_evaluate_portfolio_emits_alerts_for_red_and_critical`.
- **Verify**: `pytest tests/test_renewal_alerts.py -v`.

### 6. `api.py`  (adapts `contable_bot/api.py`)
- **Public API**: `create_app() -> FastAPI`. Endpoints:
  - `GET /healthz` → `{status: "ok", service: "cafom"}`.
  - `GET /assets?status=...` → `list[CyberAssetModel]`.
  - `POST /assets` → body `CyberAssetModel` → persists via `dual_writer.log_asset()` + `asset_tracker.ingest_assets()`; returns `{id, status: "created"}`.
  - `GET /vendors/health` → runs `VendorHealthChecker.check_all()` over distinct vendor URLs in DB; returns `{healthy, degraded, down, vendors: {url: {...}}}`.
  - `GET /renewals/alerts` → calls `renewal_alerts.evaluate_portfolio()`; returns the structured result.
- **KEEP**: `create_app()` factory, Pydantic v2 request/response models, FastAPI `response_model=`, structured `HTTPException(status, detail)`, `ValueError → 400`, generic `Exception → 500` mapping.
- **REMOVE**: `/pipeline/run`, `/rag/*`, `SystemOrchestrator`, `MODE_CORE`/`MODE_FULL`, all RAG models and lazy imports.
- **Test plan** — `tests/test_api.py` (TestClient):
  - `test_healthz_ok`.
  - `test_get_assets_empty` (fresh temp DB).
  - `test_post_then_get_roundtrip`.
  - `test_post_invalid_asset_returns_422` (Pydantic validation).
  - `test_renewals_alerts_returns_structured_response`.
  - `test_vendors_health_uses_mocked_check_vendor` (monkeypatch).
- **Verify**: `pytest tests/test_api.py -v`.

### 7. `app.py` — Streamlit dashboard (no source file; net-new, but stays thin)
- **KPI strip** (top): Total Assets · Annual Spend (sum) · Renewals Next 30 Days · Vendors Healthy.
- **Tab 1 — Asset Inventory**: `st.dataframe(get_assets(...))` + filter widgets (vendor, category, status); add/edit/delete form posting to `dual_writer.log_asset()`.
- **Tab 2 — Vendor Health**: table + "Run All" button → `VendorHealthChecker.check_all()`; color status with `st.column_config` or `pandas.Styler`.
- **Tab 3 — Renewal Calendar**: `evaluate_portfolio()` result rendered as 4 colored sections (green/yellow/red/critical); upcoming renewals table sorted by date.
- **Tab 4 — Financial Forecast**:
  - Bar chart by vendor (`st.bar_chart` from `pandas.DataFrame`).
  - Pie chart CAPEX vs OPEX (matplotlib via `st.pyplot`).
  - 12-month forecast table (`financial_forecast.project_12_months()`).
  - Anomaly badge (`anomaly_detector.flag_outliers()` — assets with `annual_cost > category_mean + 2σ`).
- **Verify**: `streamlit run app.py --server.headless true --server.port 8765` boots without exception within 5s; we don't validate visuals, just the boot.

### Side modules
- **`anomaly_detector.py`** — `flag_outliers(assets: list[dict]) -> list[dict]`. Group by `category`, compute mean + std of `annual_cost_usd`, flag rows where `cost > mean + 2σ`. Pure function, ~30 lines. Tests in `tests/test_anomaly.py`.
- **`financial_forecast.py`** — `project_12_months(assets: list[dict]) -> list[dict]`. For each month in next 12, sum `annual_cost_usd / 12` for active assets + cost of any contracts renewing that month (assume re-up at same cost). Tests in `tests/test_forecast.py`.

---

## Data file (created right after `schemas.py` lands)

`/Volumes/Sonnet/Finance2026/cafom/data/cyber_assets.json` — JSON array of 12 entries with realistic 2026 dates, per-vendor URLs, OPEX-heavy split, and at least 2 assets in each renewal bucket so the dashboard has content for every alert level.

Vendors / products: CrowdStrike Falcon, Splunk Enterprise, Palo Alto NGFW, Okta Identity, Tenable Nessus, Qualys VMDR, Zscaler Internet Access, Proofpoint Email, SentinelOne Singularity, Darktrace DETECT, Wiz Cloud Security, Microsoft Sentinel.

---

## Top-level deliverables

```
/Volumes/Sonnet/Finance2026/cafom/
├── PLAN.md                  (this file)
├── README.md                (overview + quickstart)
├── Dockerfile               (python:3.13-slim, EXPOSE 8501, CMD streamlit run app.py)
├── requirements.txt         (streamlit, pydantic>=2, fastapi, uvicorn, requests)
├── app.py                   (Streamlit dashboard)
├── api.py                   (FastAPI surface)
├── asset_tracker.py
├── vendor_health.py
├── renewal_alerts.py
├── dual_writer.py
├── schemas.py
├── anomaly_detector.py
├── financial_forecast.py
├── data/
│   └── cyber_assets.json    (12 seed assets)
└── tests/
    ├── test_dual_writer.py
    ├── test_schemas.py
    ├── test_asset_tracker.py
    ├── test_vendor_health.py
    ├── test_renewal_alerts.py
    ├── test_anomaly.py
    ├── test_forecast.py
    └── test_api.py
```

---

## Final verification (after all 7 modules pass their unit tests)

```bash
cd /Volumes/Sonnet/Finance2026/cafom
python -m pip install -r requirements.txt
pytest tests/ -v                                # full green suite
streamlit run app.py --server.headless true \
    --server.port 8765 &                        # boot smoke
sleep 4 && curl -fsS http://localhost:8765 \
    && echo "OK"                                # 200 = pass
kill %1
uvicorn api:create_app --factory --port 8000 &  # API smoke
sleep 2 && curl -fsS http://localhost:8000/healthz
kill %1
```

---

## Open decisions (please confirm or override before Phase 3 begins)

1. **Persistence** — SQLite local DB at `data/cafom.db` is fine? Postgres remains optional via `dual_writer.pg_available()` but defaults off.
2. **Renewal threshold spec** — your spec mentions GREEN `> 60d` and YELLOW `≤ 30d`. The window `30d < days ≤ 60d` is undefined; I plan to classify it as **YELLOW** (the conservative choice). Acceptable?
3. **Vendor health endpoint** — your `health_check_url` field per asset is the URL we probe. OK to dedupe by URL across assets so each vendor is probed once per refresh?
4. **No live network in tests** — vendor_health tests will all monkeypatch `requests.get`. The Streamlit "Run All" button is the only path that hits the network. Fine?
5. **Pyright not installed locally** — happy for me to skip the Pyright LSP gate for Phase 0 and rely on pytest + Pydantic runtime validation? I'll still add type annotations everywhere.

Once you confirm (or override) these, I'll start with `dual_writer.py` and proceed module-by-module per the order above, halting on the first test failure as specified.
