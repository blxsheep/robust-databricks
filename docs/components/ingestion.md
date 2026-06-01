# Ingestion — Bronze Layer

**File:** `reliability_engine/scripts/ingest_bronze.py`

The ingestion script validates incoming data via the Schema Sentinel, attaches audit metadata, writes to the Bronze Delta table, and logs cost attribution.

**Target table:** `reliability_engine.bronze.raw_orders`

---

## What it does

```
generate_orders(500)
      │
      ▼
_df_to_schema_dict(df)        ← extract raw Spark types from DataFrame schema
      │
      ▼
sentinel_run(incoming_schema) ← pre-ingestion validation; raises SchemaBreakingChangeError
      │                          on BREAKING change (no data written). Infrastructure
      │                          failures (FileNotFoundError, JSONDecodeError) propagate as-is.
      ▼
df.withColumn(...)            ← attach _ingested_at, _schema_version, _source
      │
      ▼
df.write.format("delta").mode("append").saveAsTable(TARGET_TABLE)
      │
      ▼
_log_cost(...)                ← append to cost_attribution_log
```

---

## System metadata columns

Every row written to Bronze includes three audit columns:

| Column | Type | Value |
|---|---|---|
| `_ingested_at` | timestamp | UTC timestamp of the write |
| `_schema_version` | string | `version` field from the active `schema_v{n}.json` (driven by the `schema_version` widget) |
| `_source` | string | `ingest_bronze_v1` |

These columns enable:

- **Freshness checks** — SLA monitor filters on `_ingested_at`
- **Schema change tracing** — correlate a row with the schema version active when it was written
- **Source lineage** — identify which pipeline wrote a row

---

## Type normalization

Spark and `schema_config.json` use slightly different type names (e.g. `"int"` vs `"integer"`). Normalization is owned entirely by `schema_sentinel.py` via its `_normalize_type()` function and `_TYPE_ALIASES` map. `ingest_bronze.py` passes raw Spark type strings — the sentinel resolves aliases on both sides before comparing.

This keeps the single source of truth for type aliases in the sentinel, not split across two files.

---

## Cost attribution

After every write, a row is appended to `reliability_engine.observability.cost_attribution_log`:

| Field | Description |
|---|---|
| `pipeline_name` | `ingest_bronze_v1` |
| `run_type` | `incremental` |
| `runtime_seconds` | Wall-clock time for the full ingest |
| `rows_processed` | Rows written to Bronze |
| `estimated_dbu` | `runtime_seconds / 3600 * 2` (2 DBU/hr serverless proxy) |
| `estimated_cost_usd` | `estimated_dbu * $0.22` |
| `methodology` | Explanatory note about the proxy rate |

!!! note "Methodology transparency"
    The `methodology` field exists because the honest version of a cost model is more useful than a silent one. In production, replace `estimated_dbu` with actual values from `system.billing.usage`.

---

## Running it

```bash
# From reliability_engine/scripts/
python ingest_bronze.py
```

Generates 500 synthetic orders, validates schema, writes to Bronze.

To ingest custom data:

```python
from ingest_bronze import ingest
from pyspark.sql import SparkSession

spark = SparkSession.builder.getOrCreate()
df = spark.read.format("csv").option("header", True).load("your_data.csv")
rows_written = ingest(df)
```

`ingest()` returns the number of rows written.

Raises `SchemaBreakingChangeError` on a BREAKING schema change (removed column or type change). Raises `FileNotFoundError` or `json.JSONDecodeError` on infrastructure failures. Callers can catch these separately.

---

## Data generation

**File:** `reliability_engine/scripts/generate_data.py`

Produces synthetic e-commerce orders:

| Field | Values |
|---|---|
| `order_id` | UUID |
| `customer_id` | `cust_000001` … `cust_000200` |
| `product_id` | `prod_0001` … `prod_0050` |
| `quantity` | 1–20 |
| `unit_price` | $5.00–$500.00 |
| `status` | `pending`, `confirmed`, `shipped`, `delivered`, `cancelled` |
| `created_at` | Within last 72 hours |
| `updated_at` | `created_at` + up to 120 minutes |

The base time is configurable for deterministic test data:

```python
from _orders_generator import generate_orders
from datetime import datetime

orders = generate_orders(n=100, base_time=datetime(2026, 1, 1))
```

`generate_orders` lives in `_orders_generator.py` (a plain Python module without the `# Databricks notebook source` header) so it can be imported from both `generate_data.py` and the `__main__` block of `ingest_bronze.py`. The notebook header is a one-way door — files with it cannot be imported as modules.
