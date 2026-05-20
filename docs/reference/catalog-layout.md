# Unity Catalog Layout

All data lives in the `reliability_engine` catalog. Four schemas — Bronze, Silver, Gold, and Observability.

---

## Catalog structure

```sql
CATALOG: reliability_engine

  SCHEMA: bronze
    TABLE: raw_orders          ← all ingested orders (Delta, append)

  SCHEMA: silver
    TABLE: orders_cleaned      ← dbt incremental merge on order_id

  SCHEMA: gold
    TABLE: daily_revenue       ← dbt incremental merge on order_date

  SCHEMA: observability
    TABLE: schema_change_log   ← NON_BREAKING sentinel events (append-only)
    TABLE: incident_log        ← BREAKING sentinel events (append-only)
    TABLE: sla_check_log       ← SLA check results (append-only)
    TABLE: cost_attribution_log ← per-run cost estimates (append-only)
```

---

## Table schemas

### bronze.raw_orders

| Column | Type | Description |
|---|---|---|
| `order_id` | string | UUID |
| `customer_id` | string | Customer identifier |
| `product_id` | string | Product identifier |
| `quantity` | integer | Units ordered |
| `unit_price` | double | Price per unit |
| `status` | string | `pending`, `confirmed`, `shipped`, `delivered`, `cancelled` |
| `created_at` | timestamp | Order creation time |
| `updated_at` | timestamp | Last status update time |
| `_ingested_at` | timestamp | When this row was written to Bronze |
| `_schema_version` | string | `schema_config.json` version active at write time |
| `_source` | string | Pipeline identifier (`ingest_bronze_v1`) |

### silver.orders_cleaned

All Bronze columns plus:

| Column | Type | Description |
|---|---|---|
| `line_total` | double | `quantity * unit_price` |

System columns (`_ingested_at`, `_schema_version`, `_source`) are carried forward.

### gold.daily_revenue

| Column | Type | Description |
|---|---|---|
| `order_date` | date | Calendar date |
| `total_orders` | long | Distinct order count |
| `gross_revenue` | double | Sum of `line_total` |
| `avg_order_value` | double | Average `line_total` |
| `delivered_orders` | long | Orders with `status = 'delivered'` |
| `cancelled_orders` | long | Orders with `status = 'cancelled'` |

### observability.schema_change_log

| Column | Type |
|---|---|
| `evaluated_at` | string (ISO timestamp) |
| `verdict` | string (`NON_BREAKING`) |
| `added_columns` | string (Python list repr) |
| `removed_columns` | string |
| `type_changes` | string |
| `affected_pipelines` | string |

### observability.incident_log

Same as `schema_change_log` plus:

| Column | Type |
|---|---|
| `event` | string (`PIPELINE_HALTED`) |

### observability.sla_check_log

| Column | Type |
|---|---|
| `check_name` | string |
| `status` | string (`PASS` / `FAIL`) |
| `detail` | string |
| `business_impact` | string |
| `checked_at` | string (ISO timestamp) |

### observability.cost_attribution_log

| Column | Type |
|---|---|
| `pipeline_name` | string |
| `run_type` | string |
| `runtime_seconds` | double |
| `rows_processed` | long |
| `estimated_dbu` | double |
| `estimated_cost_usd` | double |
| `logged_at` | string (ISO timestamp) |
| `methodology` | string |

---

## Create the catalog and schemas

```sql
CREATE CATALOG IF NOT EXISTS reliability_engine;

CREATE SCHEMA IF NOT EXISTS reliability_engine.bronze;
CREATE SCHEMA IF NOT EXISTS reliability_engine.silver;
CREATE SCHEMA IF NOT EXISTS reliability_engine.gold;
CREATE SCHEMA IF NOT EXISTS reliability_engine.observability;
```

Tables are created automatically on first write (Delta format with `saveAsTable`). No `CREATE TABLE` DDL needed.

---

## Observability tables are append-only

!!! danger
    Do not truncate or delete from observability tables. They are the audit trail. Use Delta time travel to query historical state:

    ```sql
    SELECT * FROM reliability_engine.observability.incident_log
    TIMESTAMP AS OF '2026-05-01T00:00:00'
    ```
