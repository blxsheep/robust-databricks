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

## Step 4 — Deploy the pipeline via Asset Bundle

The pipeline is orchestrated as a Databricks Asset Bundle (DAB). One deploy replaces all manual notebook steps.

```bash
# Validate the bundle config
databricks bundle validate

# Deploy to dev (job schedule is paused)
databricks bundle deploy --target dev --var="warehouse_id=<your-warehouse-id>"

# Trigger a manual run
databricks bundle run reliability_pipeline --target dev
```

The job runs all four tasks in order: `generate_data → ingest_bronze → dbt_run → sla_check`.

See [Deployment (DAB)](deployment.md) for full instructions including how to find your warehouse ID and set up auth.

---

## Running steps manually (without the bundle)

If you want to run individual steps outside the job:

**Ingestion:**

```bash
# From reliability_engine/scripts/
python ingest_bronze.py
```

**dbt transforms:**

```bash
cd robust_etl_ecomm
dbt run --full-refresh   # baseline
dbt run                  # incremental
```

**SLA checks:**

```bash
python reliability_engine/scripts/sla_monitor.py
```

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
