# dbt Models

**Project:** `robust_etl_ecomm/`

Two incremental models transform raw Bronze data into analytics-ready Silver and Gold tables. Both use merge strategy — orders are mutable, so append-only is not an option.

---

## Silver — `orders_cleaned`

**File:** `robust_etl_ecomm/models/silver/orders_cleaned.sql`  
**Target:** `reliability_engine.silver.orders_cleaned`

```sql
{{ config(
    materialized='incremental',
    unique_key='order_id',
    incremental_strategy='merge',
    on_schema_change='fail'
) }}

select
    order_id, customer_id, product_id,
    quantity, unit_price,
    quantity * unit_price   as line_total,
    status, created_at, updated_at,
    _ingested_at, _schema_version, _source
from {{ source('bronze', 'raw_orders') }}

{% if is_incremental() %}
    where updated_at > (select max(updated_at) from {{ this }})
{% endif %}
```

### How the incremental works

On each run, the watermark `(select max(updated_at) from silver.orders_cleaned)` is computed first. Only Bronze rows newer than that watermark are pulled. These are merged into Silver on `order_id`:

- **Matched** → update all columns (handles status changes: pending → shipped → delivered)
- **Not matched** → insert new order

This means Silver always holds **current state** per `order_id`. No deduplication needed downstream.

### What it adds

`line_total = quantity * unit_price` — computed at Silver so Gold and any other consumers don't repeat the calculation.

### `on_schema_change='fail'`

If the upstream Bronze schema changes unexpectedly, dbt halts rather than silently producing a broken model. Combined with the Schema Sentinel, schema drift is caught twice — once at ingestion (no rows written) and once at transformation.

---

## Gold — `daily_revenue`

**File:** `robust_etl_ecomm/models/gold/daily_revenue.sql`  
**Target:** `reliability_engine.gold.daily_revenue`

```sql
{{ config(
    materialized='incremental',
    unique_key='order_date',
    incremental_strategy='merge',
    on_schema_change='fail'
) }}

select
    cast(created_at as date)                                   as order_date,
    count(distinct order_id)                                   as total_orders,
    sum(line_total)                                            as gross_revenue,
    avg(line_total)                                            as avg_order_value,
    count(case when status = 'delivered' then 1 end)           as delivered_orders,
    count(case when status = 'cancelled' then 1 end)           as cancelled_orders
from {{ ref('orders_cleaned') }}

{% if is_incremental() %}
    where cast(created_at as date) >= (select max(order_date) from {{ this }})
{% endif %}

group by cast(created_at as date)
```

### How the incremental works

Watermark is `max(order_date)` in Gold. On each run, only Silver rows from the most recent date onwards are aggregated and merged. This handles late-arriving orders for the current day without reprocessing historical dates.

Unique key is `order_date` — each date has exactly one row, updated on every run that touches that date.

### Output schema

| Column | Description |
|---|---|
| `order_date` | Calendar date |
| `total_orders` | Distinct orders placed |
| `gross_revenue` | Sum of all `line_total` values |
| `avg_order_value` | Average order `line_total` |
| `delivered_orders` | Orders with `status = 'delivered'` |
| `cancelled_orders` | Orders with `status = 'cancelled'` |

---

## Running the models

```bash
cd robust_etl_ecomm

# Full refresh — reprocesses everything from Bronze
# Use for: first run, schema migrations, baseline cost benchmark
dbt run --full-refresh

# Incremental — processes only new/changed rows
# Use for: all subsequent runs
dbt run

# Run a specific model only
dbt run --select orders_cleaned
dbt run --select daily_revenue

# Validate
dbt test
```

---

## Cost benchmark

The ratio of `full-refresh runtime / incremental runtime` is the core cost metric this platform tracks. Run both and record:

```bash
# Time the full refresh
time dbt run --full-refresh

# Time the incremental
time dbt run
```

Feed these into `reliability_engine/notebooks/cost_projection.py` to project 30-day cost divergence.

---

## dbt profiles setup

`~/.dbt/profiles.yml`:

```yaml
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

!!! note
    dbt merge operations on Databricks require a **SQL Warehouse**, not an all-purpose cluster. The Starter Warehouse in Free Edition is sufficient.
