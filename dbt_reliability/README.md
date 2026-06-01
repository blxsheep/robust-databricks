# dbt_reliability

The dbt project for the [Robust Databricks — Data Reliability Engine](../README.md).

Transforms `reliability_engine.bronze.raw_orders` into:

- `reliability_engine.silver.orders_cleaned` — incremental merge on `order_id`, watermark on `updated_at`
- `reliability_engine.gold.daily_revenue` — incremental aggregation on `order_date`

Both models use `on_schema_change='fail'` — dbt errors rather than silently drifting.

---

## Setup

```yaml
# ~/.dbt/profiles.yml
dbt_reliability:
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

Verify the connection:

```bash
cd dbt_reliability
dbt debug
```

## Run

```bash
dbt run --full-refresh   # baseline — captures full-refresh runtime
dbt run                  # incremental — captures steady-state runtime
```

The ratio of `full-refresh runtime / incremental runtime` is the headline number for the cost projection notebook.

## Notes

- `macros/generate_schema_name.sql` overrides dbt's default `{target.schema}_{custom_schema}` concatenation so models land in `silver.orders_cleaned` and `gold.daily_revenue` directly (not `silver_silver.…` / `silver_gold.…`). Do not remove.
- Schemas (`silver`, `gold`) and catalog (`reliability_engine`) are configured in `dbt_project.yml`, not in profiles.yml.
- Free Edition Starter Warehouse is sufficient for these merge operations.
