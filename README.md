# Robust Databricks — Data Reliability Engine

> Catches schema drift, late-arriving data, and runaway compute costs **at the boundary** — before damage propagates downstream.
> Built on Databricks Free Edition. Open source. Reproducible.

[![CI/CD](https://github.com/blxsheep/robust-databricks/actions/workflows/cicd.yml/badge.svg)](https://github.com/blxsheep/robust-databricks/actions/workflows/cicd.yml)

---

## The problem

Three failures cause the majority of production data incidents. All three are typically discovered **after** the damage is done.

| Failure | How it usually goes |
|---|---|
| **Schema drift** | An upstream team renames a column. The pipeline keeps running. Silent nulls accumulate for hours before anyone notices. |
| **Late-arriving updates** | An order placed as `pending` transitions to `cancelled` six hours later. The daily revenue report already closed. The number is wrong and can't be corrected without a full reprocess. |
| **Runaway compute** | Every run reprocesses the entire dataset. Nobody notices until the cost chart arrives at the end of the month. |

This project handles all three **at the boundary**, before corrupt or expensive work enters the warehouse.

---

## What's in the box

| Component | What it does |
|---|---|
| **Schema Sentinel** | Classifies every incoming schema as `NON_BREAKING` or `BREAKING`. Halts ingestion on breaking — zero rows written. |
| **Bronze ingestion with metadata** | Every row gets `_ingested_at`, `_schema_version`, `_source`. No silent writes. |
| **Cost attribution log** | Every run records runtime, rows processed, estimated DBU + USD. Per-pipeline cost is queryable from SQL. |
| **dbt incremental merge models** | Silver and Gold use merge strategy. Late-arriving updates correct the record without reprocessing history. |
| **SLA monitor** | Three checks (freshness, completeness, schema consistency), each shipped with a `business_impact` label so the alert tells you what's at stake, not just what failed. |

Deployed as code via **Databricks Asset Bundle**. Tests + bundle validate run on every push. Deploy to dev runs on every push to `main` (gated on tests passing).

---

## Demo — three scenarios, one click each

After deploying the bundle, four jobs appear under Databricks Workflows. Run them in this order:

| Step | Job | What happens |
|---|---|---|
| 1 | `Reliability Pipeline — Reset` | Truncates all tables — clean slate. |
| 2 | `Reliability Pipeline — Baseline` | schema_v1. All 4 tasks green. `sla_check_log` shows 3 PASS rows. |
| 3 | `Reliability Pipeline — Non-Breaking` | schema_v2 (adds `delivery_partner`). All 4 tasks green. `schema_change_log` shows the new column. |
| 4 | `Reliability Pipeline — Breaking` | schema_v3 (removes `customer_id`). `ingest_bronze` red. `dbt_run` and `sla_check` skipped. `incident_log` shows `PIPELINE_HALTED`. Bronze row count unchanged — zero corruption. |

That's it. No file swaps, no config edits. Each scenario is a deployed job — anyone with the workspace can reproduce the result.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        INGESTION BOUNDARY                        │
│                                                                  │
│   generate_data.py                                               │
│        │                                                         │
│        ▼                                                         │
│   schema_sentinel.py  ◄── config/schema_v{1,2,3}.json           │
│        │                                                         │
│        ├── NON_BREAKING ──► schema_change_log                    │
│        │        └──► pipeline continues                          │
│        │                                                         │
│        └── BREAKING ──────► incident_log                         │
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
│   ingest_bronze.py ──► cost_attribution_log                      │
└─────────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│                     SILVER + GOLD  (dbt)                         │
│                                                                  │
│   silver.orders_cleaned    incremental merge on order_id         │
│   gold.daily_revenue       incremental merge on order_date       │
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

## Tech stack

- **Platform:** Databricks Free Edition (Unity Catalog, serverless compute, Jobs)
- **Storage:** Delta Lake (ACID transactions, schema enforcement, time travel)
- **Transformation:** dbt-databricks (incremental merge strategy)
- **Orchestration:** Databricks Asset Bundle (4 jobs deployed from `databricks.yml`)
- **Language:** Python 3.11, PySpark 3.5
- **Testing:** pytest (8 tests, 6 pure-Python scenarios + 2 PySpark idempotency)
- **CI/CD:** GitHub Actions — `test ⊥ validate → deploy` with explicit `needs` chain
- **Docs:** MkDocs Material

---

## How the system works

### Schema Sentinel

`reliability_engine/scripts/schema_sentinel.py`

Runs **before any data enters Bronze**. Compares the incoming DataFrame schema against a versioned config file. Classifies every change:

| Change | Verdict | Consequence |
|---|---|---|
| New column added | `NON_BREAKING` | Logged to `schema_change_log`, pipeline continues |
| Required column removed | `BREAKING` | Logged to `incident_log`, `SchemaBreakingChangeError` raised, **zero rows written** |
| Column type changed | `BREAKING` | Same as above |

The sentinel is **stateless by design** — reads config, classifies, routes. No shared state between invocations. Each call is independent, which allows horizontal scaling without coordination.

Type comparison uses a normalized alias map (`int` = `integer`, `long` = `integer`, `float` = `double`, etc.) so Spark's internal type names never trigger false positives.

```python
from schema_sentinel import SchemaBreakingChangeError, run
from pathlib import Path

# Non-breaking: new column is fine
result = run({"order_id": "string", ..., "delivery_partner": "string"},
             spark=None,
             config_path=Path("config/schema_v1.json"))
# result.verdict → "NON_BREAKING"
# result.added_columns → ["delivery_partner"]

# Breaking: removed column halts the pipeline
try:
    run({"order_id": "string"},   # customer_id, product_id, etc. missing
        spark=None,
        config_path=Path("config/schema_v1.json"))
except SchemaBreakingChangeError as e:
    # incident_log written, zero rows in Bronze
    ...
```

---

### Bronze Ingestion with Metadata

`reliability_engine/scripts/ingest_bronze.py`

Every row written to `reliability_engine.bronze.raw_orders` carries three system columns:

```
_ingested_at       timestamp of write
_schema_version    version from the schema config active at ingestion time
_source            pipeline identifier (ingest_bronze_v1)
```

After each write, a cost attribution row is appended to `observability.cost_attribution_log`:

```
pipeline_name | run_type | runtime_seconds | rows_processed | estimated_dbu | estimated_cost_usd | methodology
```

The `methodology` column makes the cost model explicit on every row — DBU proxy rate, Free Edition serverless, plus a note pointing at `system.billing.usage` for production replacement.

---

### Incremental dbt Models

`robust_etl_ecomm/models/`

E-commerce orders are mutable — an order placed as `pending` will transition through `confirmed`, `shipped`, `delivered`. An append-only model accumulates one row per state transition and cannot correct the past.

Both models use merge strategy:

**Silver — `orders_cleaned`**
Incremental merge on `order_id`, watermarked by `updated_at`. Only rows newer than the current watermark are processed per run. Late-arriving updates overwrite stale records — the Silver table always holds current state.

**Gold — `daily_revenue`**
Incremental merge on `order_date`. Re-aggregates the most recent date on each run to capture late arrivals.

Both models use `on_schema_change='fail'` — dbt errors rather than silently drifting.

---

### SLA Monitoring

`reliability_engine/scripts/sla_monitor.py`

Three checks per run. Each carries a `business_impact` label explaining *why* the check exists:

| Check | Threshold | `business_impact` |
|---|---|---|
| Freshness | 6 hours | `"Revenue reporting shows stale numbers."` |
| Completeness | 100 rows/run | `"Order fulfillment risk."` |
| Schema consistency | matches config | `"Downstream dbt models fail silently."` |

Thresholds live in `config/sla_config.json`. Changing SLA rules is a config edit, not a code change.

---

### Cost Governance

`reliability_engine/notebooks/cost_projection.py`

Answers: *what does it actually cost to reprocess everything versus processing only what changed?*

Run `dbt run --full-refresh`, record runtime. Run `dbt run`, record runtime. Paste both into the notebook — it projects 30 days of divergence at 4 runs/day and labels the gap in dollars.

```
Methodology: DBU proxy rate $0.22/DBU, Free Edition serverless.
In production: replace estimated_dbu with system.billing.usage.
```

---

## Observability tables

Four append-only Delta tables. Never truncated by production jobs — they are the audit trail.

| Table | Written by | Purpose |
|---|---|---|
| `observability.schema_change_log` | Sentinel | All NON_BREAKING schema changes |
| `observability.incident_log` | Sentinel | All BREAKING changes that halted the pipeline |
| `observability.sla_check_log` | SLA monitor | Freshness, completeness, schema consistency results |
| `observability.cost_attribution_log` | Ingestion | Runtime, rows, estimated DBU and USD per run |

(The `reset_data` demo job intentionally truncates these too — without it, demo cycles accumulate noise. Production scenarios never touch observability.)

---

## Tests and CI/CD

**Schema scenario tests** (`reliability_engine/tests/test_schema_scenarios.py`) — 6 tests covering baseline / non-breaking / breaking using the actual `schema_v{1,2,3}.json` configs. Pure Python, no Spark. Runs in 0.02s in CI.

**Idempotency tests** (`reliability_engine/tests/test_idempotency.py`) — PySpark + delta-spark, no Databricks connection required. Run the merge pipeline twice on identical input, assert row count unchanged + no duplicate order_ids.

```bash
poetry run pytest -v
```

**CI/CD** (`.github/workflows/cicd.yml`) — one workflow, three jobs:

```
push to any branch
  ├── test       (pytest, all branches)
  └── validate   (bundle validate, all branches)
              ↓  both must pass
           deploy (bundle deploy + workspace sync, main only)
```

`test` and `validate` run in parallel. `deploy` has `needs: [test, validate]` AND `if: github.ref == 'refs/heads/main'`. After deploying the bundle, the workspace Git folder is synced automatically via `databricks repos update`.

---

## Key design decisions

Full reasoning in [`reliability_engine/docs/ADR.md`](reliability_engine/docs/ADR.md).

**Delta over Parquet** — ACID transactions prevent partial writes from leaving tables in undefined states. Schema enforcement rejects corrupt files at write time. Time travel enables incident replay: restore any table to its exact state at the moment of failure.

**Detection at the ingestion boundary** — catching schema drift at the dbt transformation layer means bad data has already landed in Bronze. Downstream systems may have already read it. Catching it at ingestion means the damage radius is zero: no rows written, warehouse stays clean.

**Incremental merge over append** — mutable records (order status transitions) require a merge strategy. Append-only models cannot correct the past and force every aggregation to deduplicate first.

---

## Setup

### Prerequisites

- Databricks Free Edition workspace (`signup.databricks.com`)
- Databricks CLI authenticated (`databricks auth login`)
- Python 3.11, Poetry, Java 17 (for local pytest)
- GitHub repo secrets `DATABRICKS_HOST` and `DATABRICKS_TOKEN` (for CI/CD)

### Install

```bash
git clone https://github.com/blxsheep/robust-databricks.git
cd robust-databricks
poetry install --with dev
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

### Workspace Git folder (one-time)

In the Databricks UI: Workspace → Create → Git folder → paste this repo's GitHub URL → confirm the folder name. CI/CD pulls the latest `main` into this folder after every deploy.

### dbt profile

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
      catalog: reliability_engine
```

HTTP path: **SQL Warehouses → Starter Warehouse → Connection Details**

### Deploy the bundle

```bash
# Validate the YAML (no workspace deploy)
databricks bundle validate --profile DEFAULT

# Deploy to dev — creates the 4 jobs in your workspace
databricks bundle deploy --target dev

# Run a specific scenario
databricks bundle run scenario_baseline --target dev
databricks bundle run scenario_breaking --target dev
databricks bundle run reset_data --target dev
```

See [`docs/deployment.md`](docs/deployment.md) for the full deployment guide.

---

## Project structure

```
reliability_engine/
├── config/
│   ├── schema_v{1,2,3}.json    # Schema scenarios
│   └── sla_config.json
├── scripts/
│   ├── _orders_generator.py    # Shared data factory (plain module)
│   ├── generate_data.py        # Notebook task — write 500 synthetic orders
│   ├── ingest_bronze.py        # Notebook task — sentinel + Bronze write
│   ├── schema_sentinel.py      # Schema change classifier
│   ├── sla_monitor.py          # Notebook task — 3 SLA checks
│   └── reset_data.py           # Notebook task — TRUNCATE all tables (demo only)
├── tests/
│   ├── test_idempotency.py     # PySpark, requires Java 17
│   └── test_schema_scenarios.py  # Pure Python, no Spark
├── notebooks/
│   └── cost_projection.py      # 30-day cost divergence chart
└── docs/
    └── ADR.md                  # Architecture decision records

robust_etl_ecomm/               # dbt project
├── models/silver/              # orders_cleaned (incremental merge)
├── models/gold/                # daily_revenue (incremental aggregation)
└── macros/                     # Schema-name override macro

resources/                      # DAB job definitions — one per file
├── scenario_baseline.job.yml      # schema_v1 — baseline
├── scenario_non_breaking.job.yml  # schema_v2 — adds delivery_partner
├── scenario_breaking.job.yml      # schema_v3 — removes customer_id (halts)
└── reset_data.job.yml             # TRUNCATE all tables

databricks.yml                  # DAB bundle root
.github/workflows/cicd.yml      # CI/CD — test ⊥ validate → deploy
scripts/                        # Ops scripts (workspace sync)
docs/                           # MkDocs site source
```

---

## What is not in v1

Deliberately deferred — v1 solves the detection and governance layer:

- Streaming ingestion (Kafka / Structured Streaming)
- Real-time alerting (PagerDuty, Slack webhooks)
- Terraform for Unity Catalog provisioning
- Great Expectations / Soda integration
- Multi-workspace Unity Catalog federation

---

## License + contact

MIT. Open to senior data engineering roles, remote, on the Databricks stack.

GitHub: [`@blxsheep`](https://github.com/blxsheep) · DM on LinkedIn.
