# Architecture

## System overview

The platform is structured as a linear pipeline with validation and observability at every stage.

```
┌─────────────────────────────────────────────────────────────────┐
│                        INGESTION BOUNDARY                        │
│                                                                  │
│   generate_data.py                                               │
│        │                                                         │
│        ▼                                                         │
│   schema_sentinel.py  ◄── config/schema_v{1,2,3}.json           │
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
│                          SILVER LAYER  (dbt)                     │
│                                                                  │
│   silver.orders_cleaned                                          │
│   incremental merge on order_id                                  │
│   watermark: updated_at                                          │
└─────────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│                          GOLD LAYER  (dbt)                       │
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

## Data flow detail

### 1. Data generation

`reliability_engine/scripts/generate_data.py` — creates synthetic e-commerce orders:

- 200 unique customers (`cust_000001` … `cust_000200`)
- 50 unique products (`prod_0001` … `prod_0050`)
- Orders with random `quantity`, `unit_price`, `status`, timestamps within the last 72 hours

This is the stand-in for an upstream source system. In production, replace with a Kafka consumer, API poller, or file landing zone.

### 2. Schema Sentinel

`reliability_engine/scripts/schema_sentinel.py` — runs before any data enters Bronze.

Compares the incoming DataFrame schema against a versioned schema config. The config path is parameterized: `ingest_bronze` reads a `schema_version` widget (default `v1`) and resolves to `config/schema_v{n}.json`. Each deployed scenario job has a different `schema_version` hardcoded in `base_parameters`.

Three possible changes, two outcomes:

| Change type | Verdict | What happens |
|---|---|---|
| New column added | `NON_BREAKING` | Log to `schema_change_log`, pipeline continues |
| Required column removed | `BREAKING` | Log to `incident_log`, `SchemaBreakingChangeError` raised, zero rows written |
| Column type changed | `BREAKING` | Same as above |

The sentinel is **stateless** — reads config, classifies, routes. No shared state between invocations. This is an explicit design constraint for horizontal scaling.

### 3. Bronze ingestion

`reliability_engine/scripts/ingest_bronze.py` — writes to `reliability_engine.bronze.raw_orders`.

Every row gets three system columns:

| Column | Value |
|---|---|
| `_ingested_at` | Timestamp of the write |
| `_schema_version` | Version field from the active `schema_config.json` |
| `_source` | Pipeline identifier (`ingest_bronze_v1`) |

After writing, appends a row to `observability.cost_attribution_log` with runtime and estimated DBU cost.

### 4. dbt Silver — `orders_cleaned`

Incremental merge on `order_id`, watermarked by `updated_at`:

```sql
where updated_at > (select max(updated_at) from {{ this }})
```

On each run, only rows newer than the current Silver watermark are processed. Late-arriving updates to existing orders are merged (not appended) — the Silver table always holds current state per `order_id`.

Adds a computed `line_total = quantity * unit_price` column.

### 5. dbt Gold — `daily_revenue`

Aggregates Silver to daily grain, incremental on `order_date`:

- `total_orders` — distinct order count
- `gross_revenue` — sum of `line_total`
- `avg_order_value`
- `delivered_orders` / `cancelled_orders` counts

### 6. Observability

Four append-only tables. Never truncate — they are the audit trail.

| Table | Written by | Purpose |
|---|---|---|
| `schema_change_log` | Sentinel | Records all NON_BREAKING schema changes |
| `incident_log` | Sentinel | Records all BREAKING changes that halted the pipeline |
| `sla_check_log` | SLA monitor | Freshness, completeness, schema consistency results |
| `cost_attribution_log` | Ingestion | Runtime, rows processed, estimated DBU and USD per run |

---

## Key design decisions

Three decisions that define the shape of the system — full reasoning in [ADR](reference/adr.md).

**Delta over Parquet** — ACID transactions, schema enforcement at the table level, time travel for incident replay.

**Detection at the ingestion boundary** — corrupt data never enters the warehouse. Catching schema drift at the dbt layer means it already landed in Bronze.

**Incremental merge over append** — e-commerce orders are mutable (pending → shipped → delivered). Append-only models accumulate one row per state transition and cannot correct the past.

---

## Deployed jobs

The bundle deploys four jobs to Databricks Workflows. Three are scenarios (same 4-task DAG, different `schema_version` parameter); one is a utility that wipes tables between demo cycles.

| Job | `schema_version` | Behavior |
|---|---|---|
| `Reliability Pipeline — Baseline` | `v1` | All 4 tasks succeed; data flows through to gold |
| `Reliability Pipeline — Non-Breaking` | `v2` | Sentinel logs to `schema_change_log`; all 4 tasks succeed |
| `Reliability Pipeline — Breaking` | `v3` | Sentinel halts at `ingest_bronze`; `dbt_run` and `sla_check` never start |
| `Reliability Pipeline — Reset` | — | Truncates all tables (Bronze, Silver, Gold, Observability) for a clean demo run |

Each scenario is a static, named, deployed job — no parameter selection at trigger time, no config file mutation. This is the production pattern: predictable jobs that always do the same thing.

See [Deployment](deployment.md) for the demo flow.
