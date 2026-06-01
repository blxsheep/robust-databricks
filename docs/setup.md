# Setup

End-to-end setup from a fresh Databricks Free Edition workspace to a running pipeline.

---

## Prerequisites

- Python 3.11 + [Poetry](https://python-poetry.org/)
- Java 17 (required by PySpark 3.5 — see [Testing](testing.md) if you have a later Java)
- A Databricks Free Edition workspace — sign up at `signup.databricks.com`
- Databricks CLI v0.200+ (`brew install databricks/tap/databricks`)
- (For CI/CD) GitHub repo secrets `DATABRICKS_HOST` and `DATABRICKS_TOKEN`

```bash
git clone https://github.com/blxsheep/robust-databricks.git
cd robust-databricks
poetry install --with dev
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

Tables are created automatically on first write (Delta append creates the table if it doesn't exist).

---

## Step 3 — Databricks CLI auth

```bash
databricks auth login --host https://<your-workspace-url>
```

This writes a `DEFAULT` profile to `~/.databrickscfg`. Verify:

```bash
databricks auth env --profile DEFAULT
```

---

## Step 4 — Workspace Git folder (one-time)

The `notebook_task` resources reference scripts at a workspace path. You need to create a Git folder in the workspace first.

**In the Databricks UI:**

1. Workspace sidebar → navigate to `/Users/<your-email>/`
2. Click **Create → Git folder**
3. Paste the GitHub repo URL
4. Confirm the folder name is `robust-databricks`

After this one-time setup, CI/CD keeps the folder in sync — every push to `main` triggers `databricks repos update` automatically. To force-sync manually:

```bash
./scripts/databricks_force_sync.sh
```

---

## Step 5 — dbt profile

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

## Step 6 — Deploy the bundle

```bash
# Validate the YAML (no workspace deploy)
databricks bundle validate --profile DEFAULT

# Deploy to dev — creates 4 jobs in your workspace
databricks bundle deploy --target dev
```

After deploy, you'll see four jobs under **Workflows**:

| Job name | Schema scenario |
|---|---|
| `Reliability Pipeline — Baseline` | schema_v1 (production default) |
| `Reliability Pipeline — Non-Breaking` | schema_v2 (adds `delivery_partner`) |
| `Reliability Pipeline — Breaking` | schema_v3 (removes `customer_id`) |
| `Reliability Pipeline — Reset` | Truncates all tables for clean demo cycles |

Trigger a manual run:

```bash
databricks bundle run scenario_baseline --target dev
```

See [Deployment (DAB)](deployment.md) for the full deployment guide including the demo flow.

---

## Step 7 — Local runs (without the bundle)

If you want to run individual scripts outside the deployed bundle:

```bash
# Ingest 500 synthetic orders end-to-end
poetry run python reliability_engine/scripts/ingest_bronze.py

# Run dbt transforms
cd robust_etl_ecomm
dbt run --full-refresh   # baseline (also captures the full-refresh runtime)
dbt run                  # incremental (captures the steady-state runtime)

# Run SLA checks
poetry run python reliability_engine/scripts/sla_monitor.py
```

These require a live Databricks connection (Spark session config inherits from the active profile).

---

## Step 8 — Cost projection (optional)

Open `reliability_engine/notebooks/cost_projection.py` as a Databricks notebook. Fill in the benchmark runtimes from Step 7:

```python
FULL_REFRESH_RUNTIME_S = 42   # seconds from `dbt run --full-refresh`
INCREMENTAL_RUNTIME_S  = 8    # seconds from `dbt run`
```

Run the notebook to generate the 30-day cost divergence chart.

---

## Testing the three schema scenarios

After Step 6, the three scenarios are deployed jobs — trigger them from the Workflows UI or via the CLI:

=== "Baseline (schema_v1)"

    ```bash
    databricks bundle run scenario_baseline --target dev
    # All 4 tasks succeed. sla_check_log → 3 PASS rows.
    ```

=== "Non-breaking (schema_v2)"

    ```bash
    databricks bundle run scenario_non_breaking --target dev
    # All 4 tasks succeed.
    # schema_change_log → verdict=NON_BREAKING, added_columns=['delivery_partner']
    ```

=== "Breaking (schema_v3)"

    ```bash
    databricks bundle run scenario_breaking --target dev
    # generate_data ✓, ingest_bronze ✗, dbt_run + sla_check skipped.
    # incident_log → event=PIPELINE_HALTED, removed_columns=['customer_id']
    # Bronze row count unchanged — zero rows written.
    ```

=== "Reset between cycles"

    ```bash
    databricks bundle run reset_data --target dev
    # All tables (bronze, silver, gold, observability) truncated.
    # Ready for the next demo cycle.
    ```

No config file mutation. Each scenario is a deployed job with `schema_version` hardcoded in `base_parameters`.

---

## Run tests locally

```bash
poetry run pytest -v
```

Two test files:

- `test_schema_scenarios.py` — 6 sentinel scenario tests (pure Python, no Spark needed)
- `test_idempotency.py` — incremental merge idempotency (PySpark + delta-spark, requires Java 17)

See [Testing](testing.md) for the Java 17 setup if `JAVA_GATEWAY_EXITED` appears.

---

## Serve the docs locally

```bash
mkdocs serve
```

Visit `http://127.0.0.1:8000`.
