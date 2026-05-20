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
_df_to_schema_dict(df)       ← normalize Spark types to schema_config types
      │
      ▼
sentinel_run(incoming_schema) ← raises RuntimeError on BREAKING change
      │
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
| `_schema_version` | string | `version` field from active `schema_config.json` |
| `_source` | string | `ingest_bronze_v1` |

These columns enable:

- **Freshness checks** — SLA monitor filters on `_ingested_at`
- **Schema change tracing** — correlate a row with the schema version active when it was written
- **Source lineage** — identify which pipeline wrote a row

---

## Type normalization

Spark and `schema_config.json` use slightly different type names. The ingestion layer normalizes before passing to the sentinel:

```python
_SPARK_TYPE_MAP = {
    "int":    "integer",
    "bigint": "long",
    "float":  "float",
}
```

Without this, `integer` columns would always appear as a type mismatch.

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

`ingest()` returns the number of rows written, or raises `RuntimeError` on a BREAKING schema change.

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
from generate_data import generate_orders
from datetime import datetime

orders = generate_orders(n=100, base_time=datetime(2026, 1, 1))
```
