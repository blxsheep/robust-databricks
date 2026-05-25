# CLAUDE.md

> Context for AI contributors working in this repository.  
> Read this before touching any file.

---

## What this is

A data reliability platform for pipelines that cannot afford to fail silently.

The core problem: upstream schema changes, late-arriving data, and runaway compute costs are the three most common causes of production data incidents. They are also the three problems most data platforms handle reactively — discovered after the damage is done.

This project handles all three at the boundary, before corrupt or expensive work propagates downstream.

Built on **Databricks Free Edition** (Unity Catalog, serverless compute, Jobs). Designed to map cleanly to production infrastructure with minimal delta.

---

## Platform constraints

**Databricks Community Edition was retired end of 2025. This project uses Free Edition only.**  
`signup.databricks.com` — workspace spins up with serverless compute included.

**DBFS is deprecated and unavailable in Free Edition.**  
Every path in this codebase uses Unity Catalog managed tables and volumes. If you see a `dbfs:/` path, it is wrong.

Production billing uses `system.billing.usage`. Free Edition does not expose this — the cost attribution module uses a DBU proxy rate with an explicit methodology note. That note exists because the honest version of a cost model is more useful than a silent one.

---

## Repository structure

```
reliability_engine/
├── config/
│   ├── schema_v1.json          # Baseline schema (8 columns)
│   ├── schema_v2.json          # Adds delivery_partner (non-breaking)
│   ├── schema_v3.json          # Removes customer_id (breaking)
│   ├── schema_config.json      # Active expected schema — sentinel reads this
│   └── sla_config.json         # SLA thresholds and business impact labels
├── scripts/
│   ├── generate_data.py        # Synthetic order data → UC managed table
│   ├── ingest_bronze.py        # Ingestion with validation and metadata
│   ├── schema_sentinel.py      # Schema change classifier and router
│   └── sla_monitor.py          # Three SLA checks → sla_check_log
├── tests/
│   └── test_idempotency.py     # Incremental pipeline run twice → identical output
├── notebooks/
│   └── cost_projection.py      # 30-day cost divergence chart
└── docs/
    └── ADR.md                  # Three architecture decisions, one page

robust_etl_ecomm/               # dbt project (profile: robust_etl_ecomm)
├── models/silver/
│   └── orders_cleaned.sql      # Incremental merge on order_id
├── models/gold/
│   └── daily_revenue.sql       # Daily revenue aggregation
└── dbt_project.yml

scripts/                        # Ops / admin scripts (not pipeline code)
└── databricks_force_sync.sh    # Force-sync Databricks workspace repo from GitHub

docs/                           # MkDocs site source (mkdocs serve to preview)
├── components/                 # Per-component deep-dives
├── reference/                  # ADR, catalog layout, schema config
└── architecture.md             # System diagram

.github/
└── workflows/test.yml          # CI: pyspark + delta-spark + pytest on push
mkdocs.yml                      # MkDocs Material theme config
```

---

## Catalog layout

```sql
CATALOG:  reliability_engine
  SCHEMA: bronze        → raw_orders (managed table), raw_files (volume)
  SCHEMA: silver        → orders_cleaned (incremental dbt)
  SCHEMA: gold          → daily_revenue (incremental dbt)
  SCHEMA: observability → schema_change_log
                          incident_log
                          sla_check_log
                          cost_attribution_log
```

Observability tables are append-only. They are the audit trail — do not truncate them.

---

## How the system works

### Schema Sentinel

`scripts/schema_sentinel.py`

Runs at the ingestion boundary. Compares incoming schema against `config/schema_config.json` before any data enters Bronze.

**Classification:**

| Change | Verdict | Consequence |
|---|---|---|
| New column added | `NON_BREAKING` | Log to `schema_change_log`, pipeline continues |
| Required column removed | `BREAKING` | Log to `incident_log` with `affected_pipelines`, raise `SchemaBreakingChangeError`, zero rows written |
| Column type changed | `BREAKING` | Same |

The sentinel is stateless by design — it reads config, compares, routes. No internal state. Each invocation is independent. This is documented in the code with an explicit comment because the design choice matters for horizontal scaling.

To test the two scenarios (run from `reliability_engine/`):

```bash
# Non-breaking
cp config/schema_v2.json config/schema_config.json
python scripts/ingest_bronze.py
# schema_change_log → NON_BREAKING, pipeline continued, data written

# Breaking
cp config/schema_v3.json config/schema_config.json
python scripts/ingest_bronze.py
# incident_log → PIPELINE_HALTED, SchemaBreakingChangeError raised, Bronze row count unchanged

# Reset
cp config/schema_v1.json config/schema_config.json
```

---

### Cost governance

`dbt/models/`, `notebooks/cost_projection.py`

The question this module answers: *what does it actually cost to reprocess everything versus processing only what changed?*

**dbt models** (run from `robust_etl_ecomm/`):
- `silver/orders_cleaned` — incremental, `unique_key='order_id'`, `updated_at` watermark, merge strategy
- `gold/daily_revenue` — incremental aggregation; columns: `order_date`, `total_orders`, `gross_revenue`, `avg_order_value`, `delivered_orders`, `cancelled_orders`

Both models use `on_schema_change='fail'` — dbt will error rather than silently drift.

Run sequence:

```bash
cd robust_etl_ecomm
dbt run --full-refresh   # baseline: full reprocessing cost
dbt run                  # incremental: cost at steady state
```

Record runtime and rows processed for each run. The ratio between these two numbers is the benchmark.

**Cost attribution log:**  
Every pipeline run appends a row to `observability.cost_attribution_log`:

```
pipeline_name, run_type, runtime_seconds, rows_processed, estimated_dbu, estimated_cost_usd
```

**30-day projection:**  
`notebooks/cost_projection.py` — real benchmark ratio, `4 runs/day × 30 days`, two diverging lines. The gap is labelled in dollars.

Methodology note in the notebook:
> *DBU proxy rate: $0.22/DBU. Free Edition serverless. Ratio holds at scale. In production: replace `estimated_dbu` with `system.billing.usage`.*

---

### SLA monitoring

`scripts/sla_monitor.py`, `config/sla_config.json`

Three checks. Each carries a `business_impact` label that explains why the check exists, not just whether it passed.

| Check | `business_impact` |
|---|---|
| Freshness | `"Revenue reporting shows stale numbers."` |
| Completeness | `"Order fulfillment risk."` |
| Schema consistency | `"Downstream dbt models fail silently."` |

Each run appends to `observability.sla_check_log`: `check_name`, `status`, `business_impact`.

Thresholds and impact strings live in `config/sla_config.json`. Changing SLA rules is a config edit, not a code change.

---

### Ingestion metadata

Every row written to Bronze includes three system columns:

```
_ingested_at       timestamp of write
_schema_version    which schema_config.json was active at ingestion time
_source            pipeline identifier
```

Structured logging throughout `ingest_bronze.py`. No silent failures.

---

## Tests and CI

**Idempotency** (`reliability_engine/tests/test_idempotency.py`):
- Run incremental pipeline twice on identical input
- Assert row count is identical on both runs
- Assert no duplicate `order_id` values

```bash
pytest tests/
```

**CI** (`.github/workflows/test.yml`):
- Trigger: push to any branch
- Steps: install `pyspark`, `delta-spark`, `pytest` → run `pytest tests/`

---

## dbt setup

The dbt project is `robust_etl_ecomm/`. Profile name must match `dbt_project.yml`.

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

HTTP path: SQL Warehouses → Starter Warehouse → Connection Details.

Run `dbt debug` from `robust_etl_ecomm/` before `dbt run`. Fix any connection issues before touching models.

---

## Ops scripts

`scripts/` at the repo root holds admin and ops utilities — not pipeline code.

| Script | Purpose |
|---|---|
| `databricks_force_sync.sh` | Force-syncs the Databricks workspace repo to GitHub. Tries a normal pull first; on conflict, deletes and re-clones. Workspace-only edits will be lost. |

```bash
# Default: syncs /Users/c.voranipit@gmail.com/robust-databricks to main
./scripts/databricks_force_sync.sh

# Override branch or workspace path
./scripts/databricks_force_sync.sh --branch feature/my-branch
./scripts/databricks_force_sync.sh --path /Users/other@email.com/robust-databricks
```

Requires the Databricks CLI authenticated (`databricks auth login`).

---

## Docs site

Source: `docs/` — built with MkDocs Material.

```bash
mkdocs serve    # preview at localhost:8000
mkdocs build    # output to site/
```

`site/` is a build artifact — do not commit it.

---

## What is not in v1

Explicitly out of scope for this version:

- Streaming ingestion (Kafka / Structured Streaming)
- Multi-workspace Unity Catalog federation
- Real-time alerting (PagerDuty, Slack)
- Great Expectations / Soda integration
- Terraform for Unity Catalog provisioning

These are deferred, not missing. v1 solves the detection and governance layer. Alerting and IaC are v2.

---

## Architecture decisions

Full reasoning in `reliability_engine/docs/ADR.md`. Summary:

1. **Delta over Parquet** — ACID transactions, schema enforcement, and time travel for incident replay.
2. **Detection at ingestion boundary** — corrupt or incompatible data never enters the warehouse. Catching it at transformation means it already landed somewhere.
3. **Incremental merge over append** — late-arriving updates are handled without full reprocessing. Append-only models cannot correct the past.