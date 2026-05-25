# Robust Databricks — Data Reliability Engine

> A data reliability platform built on Databricks Free Edition.  
> Catches schema drift, late-arriving data, and runaway compute costs **at the boundary** — before damage propagates downstream.

---

## The Problem

Three failures cause most production data incidents:

| Failure | How it usually goes |
|---|---|
| **Schema drift** | An upstream team renames a column. The pipeline keeps running. Silent nulls accumulate for hours before anyone notices. |
| **Late-arriving updates** | An order placed as `pending` transitions to `cancelled` six hours later. The daily revenue report already closed. The number is wrong and can't be corrected without a full reprocess. |
| **Runaway compute** | Every run reprocesses the entire dataset. Nobody notices until the cost chart arrives at the end of the month. |

Most platforms catch these **reactively** — after the damage is done. This project catches them **at the boundary**, before corrupt or expensive work enters the warehouse.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        INGESTION BOUNDARY                        │
│                                                                  │
│   generate_data.py                                               │
│        │                                                         │
│        ▼                                                         │
│   schema_sentinel.py  ◄── config/schema_config.json             │
│        │                                                         │
│        ├── NON_BREAKING ──► schema_change_log (observability)    │
│        │        └──► pipeline continues                          │
│        │                                                         │
│        └── BREAKING ──────► incident_log (observability)         │
│                 └──► SchemaBreakingChangeError, zero rows written │
└─────────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│                          BRONZE LAYER                            │
│                                                                  │
│   reliability_engine.bronze.raw_orders                           │
│   + _ingested_at | _schema_version | _source                     │
│                                                                  │
│   ingest_bronze.py ──► cost_attribution_log (observability)      │
└─────────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│                     SILVER LAYER  (dbt)                          │
│                                                                  │
│   silver.orders_cleaned                                          │
│   incremental merge on order_id | watermark: updated_at          │
└─────────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│                       GOLD LAYER  (dbt)                          │
│                                                                  │
│   gold.daily_revenue                                             │
│   incremental merge on order_date                                │
└─────────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│                       OBSERVABILITY                              │
│                                                                  │
│   sla_monitor.py  →  sla_check_log                               │
│   cost_projection.py  →  30-day cost divergence chart            │
└─────────────────────────────────────────────────────────────────┘
```

---

## Tech Stack

- **Platform:** Databricks Free Edition (Unity Catalog, serverless compute)
- **Storage:** Delta Lake (ACID transactions, schema enforcement, time travel)
- **Transformation:** dbt-databricks (incremental merge strategy)
- **Orchestration:** Databricks Jobs
- **Language:** Python 3.11, PySpark 3.5
- **Testing:** pytest, delta-spark (local, no Databricks connection required)
- **Docs:** MkDocs Material
- **CI:** GitHub Actions

---

## What It Does

### 1. Schema Sentinel

`reliability_engine/scripts/schema_sentinel.py`

Runs **before any data enters Bronze**. Compares the incoming DataFrame schema against a versioned `schema_config.json` and classifies every change:

| Change | Verdict | Consequence |
|---|---|---|
| New column added | `NON_BREAKING` | Logged to `schema_change_log`, pipeline continues |
| Required column removed | `BREAKING` | Logged to `incident_log`, `SchemaBreakingChangeError` raised, **zero rows written** |
| Column type changed | `BREAKING` | Same as above |

The sentinel is **stateless by design** — reads config, classifies, routes. No shared state between invocations. Each call is independent, which allows horizontal scaling without coordination.

Type comparison uses a normalized alias map (`int` = `integer`, `long` = `integer`, `float` = `double`, etc.) so Spark's internal type names never trigger false positives.

```python
# Non-breaking: new column is fine
result = run({"order_id": "string", ..., "delivery_partner": "string"}, spark)
# result.verdict → "NON_BREAKING"

# Breaking: removed column halts the pipeline
try:
    run({"order_id": "string"}, spark)  # customer_id, product_id, etc. missing
except SchemaBreakingChangeError as e:
    # incident_log written, zero rows in Bronze
```

To test both scenarios locally:

```bash
cd reliability_engine

# Non-breaking (adds delivery_partner column)
cp config/schema_v2.json config/schema_config.json
python scripts/ingest_bronze.py

# Breaking (removes customer_id)
cp config/schema_v3.json config/schema_config.json
python scripts/ingest_bronze.py

# Reset
cp config/schema_v1.json config/schema_config.json
```

---

### 2. Bronze Ingestion with Metadata

`reliability_engine/scripts/ingest_bronze.py`

Every row written to `reliability_engine.bronze.raw_orders` carries three system columns:

```
_ingested_at       timestamp of write
_schema_version    version from schema_config.json active at ingestion time
_source            pipeline identifier (ingest_bronze_v1)
```

After each write, a cost attribution row is appended to `observability.cost_attribution_log`:

```
pipeline_name | run_type | runtime_seconds | rows_processed | estimated_dbu | estimated_cost_usd
```

No silent failures. Every run is observable.

---

### 3. Incremental dbt Models

`robust_etl_ecomm/models/`

E-commerce orders are mutable — an order placed as `pending` will transition through `confirmed`, `shipped`, `delivered`. An append-only model accumulates one row per state transition and cannot correct the past.

Both models use merge strategy:

**Silver — `orders_cleaned`**  
Incremental merge on `order_id`, watermarked by `updated_at`. Only rows newer than the current watermark are processed per run. Late-arriving updates overwrite stale records — the Silver table always holds current state.

**Gold — `daily_revenue`**  
Incremental merge on `order_date`. Re-aggregates the most recent date on each run to capture late arrivals.

```sql
-- Silver watermark
where updated_at > (select max(updated_at) from {{ this }})

-- Gold re-aggregation window
where cast(created_at as date) >= (select max(order_date) from {{ this }})
```

Both models use `on_schema_change='fail'` — dbt errors rather than silently drifting.

---

### 4. SLA Monitoring

`reliability_engine/scripts/sla_monitor.py`

Three checks per run. Each carries a `business_impact` label explaining *why* the check exists, not just whether it passed:

| Check | Threshold | `business_impact` |
|---|---|---|
| Freshness | 6 hours | `"Revenue reporting shows stale numbers."` |
| Completeness | 100 rows/run | `"Order fulfillment risk."` |
| Schema consistency | matches config | `"Downstream dbt models fail silently."` |

Thresholds live in `config/sla_config.json`. Changing SLA rules is a config edit, not a code change.

---

### 5. Cost Governance

`reliability_engine/notebooks/cost_projection.py`

Answers: *what does it actually cost to reprocess everything versus processing only what changed?*

Run `dbt run --full-refresh`, record runtime. Run `dbt run`, record runtime. Paste both into the notebook — it projects 30 days of divergence at 4 runs/day and labels the gap in dollars.

```
Methodology: DBU proxy rate $0.22/DBU, Free Edition serverless.
In production: replace estimated_dbu with system.billing.usage.
```

---

## Observability Tables

Four append-only Delta tables. Never truncate — they are the audit trail.

| Table | Written by | Purpose |
|---|---|---|
| `observability.schema_change_log` | Sentinel | All NON_BREAKING schema changes |
| `observability.incident_log` | Sentinel | All BREAKING changes that halted the pipeline |
| `observability.sla_check_log` | SLA monitor | Freshness, completeness, schema consistency results |
| `observability.cost_attribution_log` | Ingestion | Runtime, rows, estimated DBU and USD per run |

---

## Tests and CI

**Idempotency tests** run locally with no Databricks connection:

```bash
pytest reliability_engine/tests/
```

- `test_incremental_idempotent` — run the merge pipeline twice on identical input, assert row count is unchanged
- `test_no_duplicate_order_ids` — assert no duplicate `order_id` values after two runs

**CI** (`.github/workflows/test.yml`) — triggers on every push: installs `pyspark`, `delta-spark`, `pytest`, runs the full test suite.

---

## Key Design Decisions

Full reasoning in [`reliability_engine/docs/ADR.md`](reliability_engine/docs/ADR.md).

**Delta over Parquet** — ACID transactions prevent partial writes from leaving tables in undefined states. Schema enforcement rejects corrupt files at write time. Time travel enables incident replay: restore any table to its exact state at the moment of failure.

**Detection at the ingestion boundary** — catching schema drift at the dbt transformation layer means bad data has already landed in Bronze. Downstream systems may have already read it. Catching it at ingestion means the damage radius is zero: no rows written, warehouse stays clean.

**Incremental merge over append** — mutable records (order status transitions) require a merge strategy. Append-only models cannot correct the past and force every aggregation to deduplicate first.

---

## Setup

### Prerequisites

- Databricks Free Edition workspace (`signup.databricks.com`)
- Databricks CLI authenticated (`databricks auth login`)
- Python 3.11, Poetry

### Install

```bash
git clone https://github.com/blxsheep/robust-databricks.git
cd robust-databricks
poetry install
```

### Unity Catalog setup

Create the catalog and schemas in your Databricks workspace:

```sql
CREATE CATALOG IF NOT EXISTS reliability_engine;

CREATE SCHEMA IF NOT EXISTS reliability_engine.bronze;
CREATE SCHEMA IF NOT EXISTS reliability_engine.silver;
CREATE SCHEMA IF NOT EXISTS reliability_engine.gold;
CREATE SCHEMA IF NOT EXISTS reliability_engine.observability;
```

### dbt setup

```yaml
# ~/.dbt/profiles.yml
robust_etl_ecomm:
  target: dev
  outputs:
    dev:
      type: databricks
      host: <workspace-url>
      http_path: <starter-warehouse-http-path>
      token: <personal-access-token>
      schema: silver
```

HTTP path: **SQL Warehouses → Starter Warehouse → Connection Details**

```bash
cd robust_etl_ecomm
dbt debug    # verify connection before running
dbt run
```

### Run the pipeline

```bash
cd reliability_engine
python scripts/generate_data.py   # generate 500 synthetic orders
python scripts/ingest_bronze.py   # validate + write to Bronze
python scripts/sla_monitor.py     # run SLA checks
```

### Sync to Databricks workspace

```bash
./scripts/databricks_force_sync.sh
```

---

## Project Structure

```
reliability_engine/
├── config/                    # Schema versions and SLA thresholds
├── scripts/
│   ├── generate_data.py       # Synthetic order data
│   ├── ingest_bronze.py       # Ingestion with sentinel + metadata
│   ├── schema_sentinel.py     # Schema change classifier
│   └── sla_monitor.py         # SLA checks
├── notebooks/
│   └── cost_projection.py     # 30-day cost divergence chart
├── tests/
│   └── test_idempotency.py    # Incremental pipeline idempotency
└── docs/
    └── ADR.md                 # Architecture decision records

robust_etl_ecomm/              # dbt project
├── models/silver/             # orders_cleaned (incremental merge)
└── models/gold/               # daily_revenue (incremental aggregation)

scripts/                       # Ops utilities
└── databricks_force_sync.sh   # Force-sync workspace repo from GitHub

docs/                          # MkDocs site source
```

---

## What is not in v1

Deliberately deferred — v1 solves the detection and governance layer:

- Streaming ingestion (Kafka / Structured Streaming)
- Real-time alerting (PagerDuty, Slack webhooks)
- Terraform for Unity Catalog provisioning
- Great Expectations / Soda integration
- Multi-workspace Unity Catalog federation
