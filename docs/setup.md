# Setup

End-to-end setup from a fresh Databricks Free Edition workspace to a running pipeline.

---

## Prerequisites

- Python 3.10+
- A Databricks Free Edition workspace — sign up at `signup.databricks.com`
- `dbt-databricks` installed locally

```bash
pip install dbt-databricks mkdocs mkdocs-material
```

---

## Step 1 — Databricks workspace

Sign up at `signup.databricks.com`. The workspace provisions with:

- Unity Catalog enabled
- Serverless compute included
- A Starter SQL Warehouse (needed for dbt merge operations)

No DBFS. No Community Edition. Free Edition only.

---

## Step 2 — Create the Unity Catalog structure

Run in a Databricks notebook or SQL editor:

```sql
CREATE CATALOG IF NOT EXISTS reliability_engine;

CREATE SCHEMA IF NOT EXISTS reliability_engine.bronze;
CREATE SCHEMA IF NOT EXISTS reliability_engine.silver;
CREATE SCHEMA IF NOT EXISTS reliability_engine.gold;
CREATE SCHEMA IF NOT EXISTS reliability_engine.observability;
```

The observability tables are created automatically on first run (Delta append writes create the table if it doesn't exist).

---

## Step 3 — Configure dbt

Create `~/.dbt/profiles.yml`:

```yaml
robust_etl_ecomm:
  target: dev
  outputs:
    dev:
      type: databricks
      host: <your-workspace-url>          # e.g. adb-1234567890.12.azuredatabricks.net
      http_path: <starter-warehouse-http-path>  # SQL Warehouses → Starter → Connection Details
      token: <your-personal-access-token>
      schema: silver
      catalog: reliability_engine
```

Verify the connection:

```bash
cd robust_etl_ecomm
dbt debug
```

Fix any connection issues before running models.

---

## Step 4 — Run the ingestion pipeline

The scripts run on Databricks (in a Job, notebook, or via `databricks-connect`). Run from `reliability_engine/scripts/`:

```bash
# Generate 500 synthetic orders and ingest into Bronze
python ingest_bronze.py
```

This will:

1. Generate synthetic e-commerce orders via `generate_data.py`
2. Run the Schema Sentinel against `config/schema_config.json`
3. Write enriched rows to `reliability_engine.bronze.raw_orders`
4. Append a cost attribution row to `observability.cost_attribution_log`

---

## Step 5 — Run dbt transforms

```bash
cd robust_etl_ecomm

# First run — full refresh to establish baseline
dbt run --full-refresh

# Subsequent runs — incremental only
dbt run
```

Record the runtime of each run. These are the inputs for the cost projection notebook.

---

## Step 6 — Run SLA checks

```bash
python reliability_engine/scripts/sla_monitor.py
```

Results append to `observability.sla_check_log`. Each check includes a `business_impact` label.

---

## Step 7 — Cost projection (optional)

Open `reliability_engine/notebooks/cost_projection.py` as a Databricks notebook. Fill in the benchmark runtimes from Step 5:

```python
FULL_REFRESH_RUNTIME_S = 42   # seconds from `dbt run --full-refresh`
INCREMENTAL_RUNTIME_S  = 8    # seconds from `dbt run`
```

Run the notebook to generate the 30-day cost divergence chart.

---

## Testing schema change scenarios

The sentinel reads `config/schema_config.json` at ingestion time. Swap the active config to simulate schema changes:

=== "Non-breaking (new column)"

    ```bash
    cp reliability_engine/config/schema_v2.json reliability_engine/config/schema_config.json
    python reliability_engine/scripts/ingest_bronze.py
    # → observability.schema_change_log gets a NON_BREAKING entry
    # → data is written normally
    ```

=== "Breaking (removed column)"

    ```bash
    cp reliability_engine/config/schema_v3.json reliability_engine/config/schema_config.json
    python reliability_engine/scripts/ingest_bronze.py
    # → observability.incident_log gets PIPELINE_HALTED
    # → SchemaBreakingChangeError raised, zero rows written to Bronze
    ```

=== "Reset to baseline"

    ```bash
    cp reliability_engine/config/schema_v1.json reliability_engine/config/schema_config.json
    ```

---

## Run tests

```bash
pytest reliability_engine/tests/
```

Tests run locally with PySpark + delta-spark — no Databricks connection required. CI runs these on every push via `.github/workflows/test.yml`.

---

## Serve the docs locally

```bash
mkdocs serve
```

Visit `http://127.0.0.1:8000`.
