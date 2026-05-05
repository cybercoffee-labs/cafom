# 🛡️ CAFOM — Cyber Asset Financial Operations Manager

[![Tests](https://img.shields.io/badge/tests-74%20passing-brightgreen.svg)](#tests)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![Streamlit](https://img.shields.io/badge/streamlit-1.x-FF4B4B.svg)](https://streamlit.io/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.x-009688.svg)](https://fastapi.tiangolo.com/)
[![Pydantic](https://img.shields.io/badge/pydantic-v2-E92063.svg)](https://docs.pydantic.dev/)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](#license)

> A unified cybersecurity tooling portfolio platform that joins **financial
> operations** with **security operations**: tracks asset lifecycle, vendor
> health, renewal exposure, CAPEX/OPEX forecasts, and cost anomalies.

---

## Why This Matters for Cybersecurity Financial Operations

Most security teams manage their tooling portfolio across spreadsheets,
ticketing systems, and a vendor-by-vendor patchwork of dashboards. CAFOM
consolidates the **financial** and **operational** dimensions of a security
toolset into one auditable surface:

- **Renewals don't slip** — date-threshold alerts at 60 / 30 / 0 / -30 days.
- **Vendor outages stop being a Slack thread** — a single TTL-cached health
  probe across all `health_check_url` endpoints, exposed via API and dashboard.
- **CFO-ready financial reporting** — CAPEX/OPEX split, 12-month forecast,
  cost anomalies (mean + 2σ per category), exportable PDF.
- **Auditable trail** — append-only JSONL writes for every asset mutation,
  vendor probe, and portfolio refresh; optional Postgres mirror.
- **Composable** — Streamlit dashboard for analysts, FastAPI surface for
  integrations, library-quality core for embedding.

---

## Architecture

```
                     ┌──────────────────────────────────────────────┐
                     │            Streamlit Dashboard (app.py)      │
                     │  KPI strip + 4 tabs (Inventory / Health /    │
                     │     Renewals / Forecast + PDF export)        │
                     └───────────────────┬──────────────────────────┘
                                         │
              ┌──────────────────────────┼──────────────────────────┐
              │                          │                          │
              ▼                          ▼                          ▼
       ┌─────────────┐           ┌──────────────┐           ┌───────────────┐
       │  api.py     │           │ report_gen.  │           │ Side modules  │
       │  FastAPI    │ ────────► │  fpdf2       │           │ • anomaly     │
       │  6 endpts   │           │  PDF report  │           │ • forecast    │
       └──────┬──────┘           └──────────────┘           └───────┬───────┘
              │                                                     │
              ▼                                                     │
   ┌──────────────────────────────────────────────────┐             │
   │             Domain layer (pure logic)            │             │
   │  ┌────────────┐  ┌───────────────┐  ┌────────────┴───────┐
   │  │ schemas.py │  │ vendor_health │  │ renewal_alerts.py  │
   │  │ Pydantic   │  │ TTL-cached    │  │ GREEN/YELLOW/RED/  │
   │  │ validation │  │ HTTP probes   │  │   CRITICAL gate    │
   │  └────────────┘  └───────────────┘  └────────────────────┘
   │                       │
   │           ┌───────────▼───────────┐
   │           │   asset_tracker.py    │
   │           │   SQLite ledger +     │
   │           │   ingestion + queries │
   │           └───────────┬───────────┘
   └───────────────────────┼─────────────────────────────────────┐
                           ▼                                     │
                    ┌─────────────────┐                          │
                    │  dual_writer.py │                          │
                    │  JSONL (always) │ ◄────────────────────────┘
                    │  + PG (optional)│
                    └─────────────────┘
```

**Data flow:** every asset mutation is dual-written (JSONL → Postgres
optional). The SQLite tracker ingests JSONL via `INSERT OR IGNORE` for
idempotent dedup. Query APIs (status filter, renewal window, daily exposure)
power both the FastAPI service and the Streamlit dashboard. Vendor health
results are cached per URL with a 5-minute TTL; errors and degraded
responses bypass cache so retries probe live endpoints.

---

## Module → Job Description Mapping

CAFOM is built to demonstrate the full skill mix expected of a
**Cybersecurity Analyst II — Financial Operations Specialist**. Each module
maps to one or more job-description responsibilities:

| Module | Cybersecurity Analyst II Responsibility |
|--------|-----------------------------------------|
| `dual_writer.py` | *Maintain immutable audit trails of security tool changes* — append-only JSONL with optional Postgres mirror; supports compliance review and retroactive incident analysis. |
| `schemas.py` | *Enforce data integrity for the security tooling inventory* — Pydantic v2 validation: regex IDs, capex/opex enum, renewal-after-purchase invariant, HTTP URL strictness. |
| `asset_tracker.py` | *Maintain the cybersecurity tools portfolio (inventory, status, contract terms)* — SQLite-backed ledger with status filtering, renewal-window queries, and aggregate cost reporting. |
| `vendor_health.py` | *Monitor SaaS security vendor availability and incident response readiness* — TTL-cached HTTP health probes; status taxonomy (Healthy/Degraded/Down) drives both alerts and SLA reporting. |
| `renewal_alerts.py` | *Proactively manage contract renewals to avoid lapses in protection* — date-threshold gate (CRITICAL/RED/YELLOW/GREEN) with structured alerts sorted by urgency. |
| `anomaly_detector.py` | *Detect outliers in security spending against category baselines* — flag annual costs exceeding `mean + 2σ` per category to support budget reviews. |
| `financial_forecast.py` | *Provide 12-month CAPEX/OPEX forecasts to security leadership* — monthly allocation + renewal spikes; informs annual budget cycles and reforecasting. |
| `api.py` | *Expose security operations data to upstream systems (BI tools, SIEM dashboards, finance ERP)* — FastAPI service with REST endpoints for inventory, health, alerts, and PDF reporting. |
| `app.py` | *Deliver a single pane of glass for security operations + finance partnership* — Streamlit dashboard combining KPI strip, inventory, vendor health, renewal calendar, and forecast. |
| `report_generator.py` | *Produce executive-ready portfolio reports for CISO/CFO review* — fpdf2 PDF with cover, executive summary, inventory, alerts, forecast, and anomalies. |

---

## Quickstart

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Initialize database & ingest seed data
python -c "
import asset_tracker, json
from pathlib import Path
asset_tracker.init_db()
data = json.loads(Path('data/cyber_assets.json').read_text())
jsonl = Path('data/seed.jsonl')
jsonl.write_text('\n'.join(json.dumps(a) for a in data))
asset_tracker.ingest_assets(jsonl)
"

# 3. Run dashboard (dark theme via .streamlit/config.toml)
streamlit run app.py

# 4. (Optional) Run API
uvicorn api:create_app --factory --port 8000
```

### Key endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/healthz` | Liveness probe |
| `GET` | `/assets?status=Active` | Filtered inventory |
| `POST` | `/assets` | Create asset (Pydantic-validated) |
| `GET` | `/vendors/health` | Vendor health summary |
| `GET` | `/renewals/alerts` | Renewal alert breakdown |
| `GET` | `/report?format=pdf` | Streaming PDF portfolio report |

---

## Tests

```bash
pytest tests/ -v
```

**74 tests across 6 test files** — all passing:

| File | Tests | Coverage |
|------|-------|----------|
| `test_dual_writer.py` | 8 | JSONL append, Postgres availability, multi-stream |
| `test_schemas.py` | 9 | Pydantic validators, regex IDs, date ordering |
| `test_asset_tracker.py` | 13 | Schema, dedup, filters, renewal queries, sums |
| `test_vendor_health.py` | 15 | Status taxonomy, cache TTL, error bypass |
| `test_renewal_alerts.py` | 20 | Boundary conditions, evaluation, sorting |
| `test_api.py` | 9 | All endpoints, validation errors |

---

## Theme

CAFOM ships with a dark cybersecurity-themed Streamlit config at
`.streamlit/config.toml`:
- **Background:** `#0e1117` (deep near-black blue, terminal-inspired)
- **Sidebar/cards:** `#1a1c23`
- **Accent:** `#00ff41` (matrix/SOC green) for primary buttons and highlights
- **Font:** monospace (SOC/terminal feel)

---

## Project Layout

```
cafom/
├── .streamlit/config.toml           # Dark cybersecurity theme
├── data/
│   ├── cafom.db                     # SQLite ledger (created on first run)
│   └── cyber_assets.json            # 12 seed assets
├── tests/                           # 74 unit tests
│   ├── test_dual_writer.py
│   ├── test_schemas.py
│   ├── test_asset_tracker.py
│   ├── test_vendor_health.py
│   ├── test_renewal_alerts.py
│   └── test_api.py
├── dual_writer.py                   # JSONL + Postgres logging
├── schemas.py                       # Pydantic v2 models
├── asset_tracker.py                 # SQLite ledger
├── vendor_health.py                 # TTL-cached health probes
├── renewal_alerts.py                # Date-threshold alerts
├── anomaly_detector.py              # Outlier detection
├── financial_forecast.py            # 12-month projection
├── report_generator.py              # fpdf2 PDF reports
├── api.py                           # FastAPI surface
├── app.py                           # Streamlit dashboard
├── PLAN.md                          # Build plan & specs
├── requirements.txt
└── README.md
```

---

## Limitations

- Vendor health probes are HTTP GET only; no auth, no body inspection.
- 12-month forecast assumes contract re-up at same cost — no negotiation modeling.
- Anomaly detection is per-category; small categories (< 2 assets) skip.
- PDF report uses Latin-1 fonts; non-ASCII characters fall back to ASCII equivalents.
- No multi-tenancy — single-portfolio scope.

---

## License

MIT — see [LICENSE](LICENSE) for details.

---

> Built with care for security teams who want their tooling portfolio to be
> as observable as the systems it protects.
