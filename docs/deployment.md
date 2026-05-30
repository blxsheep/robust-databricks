# Deployment — Databricks Asset Bundle

The pipeline is defined as code via a **Databricks Asset Bundle (DAB)**. A single `databricks bundle deploy` replaces manual notebook clicking and makes the dependency graph explicit.

---

## What the bundle defines

`databricks.yml` (repo root) declares the bundle and targets. `resources/reliability_pipeline.job.yml` defines the job with a 4-task DAG:

```
generate_data
     │
ingest_bronze   ← schema sentinel runs here; halts on BREAKING change
     │
  dbt_run       ← silver.orders_cleaned + gold.daily_revenue (incremental)
     │
 sla_check      ← writes freshness / completeness / schema results to sla_check_log
```

Each arrow is an explicit `depends_on` — Databricks will not start a downstream task if the upstream fails.

---

## Prerequisites

### 1. Databricks CLI

Install the Databricks CLI (v0.200+):

```bash
brew tap databricks/tap
brew install databricks
```

Authenticate:

```bash
databricks auth login --host https://dbc-970ed03d-67bd.cloud.databricks.com
```

This writes a `DEFAULT` profile to `~/.databrickscfg`. Verify:

```bash
databricks auth env --profile DEFAULT
```

### 2. Workspace Git folder (one-time setup)

The notebook tasks reference scripts via `source: WORKSPACE`. This requires the repo to be cloned as a Git folder inside the Databricks workspace.

**In the Databricks UI:**

1. Open the workspace sidebar → **Workspace**
2. Navigate to `/Workspace/Users/c.voranipit@gmail.com/`
3. Click **Create** → **Git folder**
4. Paste the GitHub repo URL
5. Confirm the folder name is `robust-databricks` (final path: `/Workspace/Users/c.voranipit@gmail.com/robust-databricks`)

After setup, keep the Git folder in sync with the `scripts/databricks_force_sync.sh` script or by pulling from the Repos UI before each deployment:

```bash
./scripts/databricks_force_sync.sh
```

**Why this is required:** `notebook_task` with `source: WORKSPACE` looks up the path at runtime. The DAB deploys the job definition but does not upload the notebook files — they must already exist in the workspace via the Git folder.

---

## One-time: set the warehouse ID

The `dbt_run` task executes SQL via a Databricks SQL Warehouse. Find your Starter Warehouse ID:

**Databricks UI → SQL Warehouses → Starter Warehouse → Connection Details → HTTP path**

The warehouse ID is the last segment of the HTTP path (e.g. `abc123def456`).

Set it as a bundle variable override or export before deploying:

```bash
# Option 1 — override at deploy time
databricks bundle deploy --var="warehouse_id=e7d27934b2137b5b"

# Option 2 — pin it in databricks.yml under the dev target variables block
variables:
  warehouse_id: e7d27934b2137b5b
```

---

## Deploy

```bash
# Validate config (no workspace calls)
databricks bundle validate

# Deploy to dev (creates the job in your workspace, schedules paused)
databricks bundle deploy --target dev

# Deploy to prod (schedule active — runs every 6 hours)
databricks bundle deploy --target prod
```

On success you will see the job appear under **Workflows** in the Databricks UI.

---

## Run the pipeline manually

```bash
databricks bundle run reliability_pipeline --target dev
```

Or trigger it from the Workflows UI. The DAG view shows each task in dependency order, with per-task logs and status.

---

## Schedule

The job runs every 6 hours (`0 0 */6 * * ?` UTC).

- **dev target:** schedule is `PAUSED` — trigger manually.
- **prod target:** schedule is active.

To change the cadence, edit `quartz_cron_expression` in `resources/reliability_pipeline.job.yml`.

---

## On-failure behavior

The job sends email to `c.voranipit@gmail.com` on any task failure. Because tasks have explicit `depends_on`, a failure at `ingest_bronze` (e.g. a breaking schema change) automatically prevents `dbt_run` and `sla_check` from starting — no downstream corruption.

---

## Bundle file layout

```
databricks.yml                         # bundle root: name, targets, auth profile
resources/
└── reliability_pipeline.job.yml       # job DAG, schedule, environments
```

Variables declared in `databricks.yml`:

| Variable | Description | Default |
|---|---|---|
| `workspace_repo_path` | Absolute path to git-synced repo in workspace | `/Workspace/Users/c.voranipit@gmail.com/robust-databricks` |
| `warehouse_id` | SQL Warehouse ID for dbt task | _(must be set before deploy)_ |

---

## Demo: Three Schema Scenarios

The pipeline accepts a `schema_version` job parameter (`v1` / `v2` / `v3`) that drives the sentinel scenario without touching any config file on disk.

**Trigger from the Workflows UI:**
Workflows → Reliability Pipeline → Run now → Edit parameters → set `schema_version`

**Trigger from the CLI:**
```bash
databricks bundle run reliability_pipeline --target dev \
  --python-named-params "schema_version=v3"
```

---

### Scenario 1 — Baseline (`schema_version=v1`)

No schema changes. All 4 tasks run to completion.

**What to look for:**
```sql
SELECT * FROM reliability_engine.observability.sla_check_log ORDER BY checked_at DESC LIMIT 3;
```
All three SLA checks show `status = PASS`.

---

### Scenario 2 — Non-breaking change (`schema_version=v2`)

Upstream adds `delivery_partner` column. Sentinel logs it and lets the pipeline continue.

**What to look for:**
```sql
SELECT verdict, added_columns, evaluated_at
FROM reliability_engine.observability.schema_change_log
ORDER BY evaluated_at DESC LIMIT 1;
-- verdict = NON_BREAKING, added_columns = ['delivery_partner']
```

All 4 tasks still complete. Data written to Bronze with the extra column preserved.

---

### Scenario 3 — Breaking change (`schema_version=v3`)

Upstream removes `customer_id`. Sentinel raises `SchemaBreakingChangeError`. Pipeline halts at `ingest_bronze` — `dbt_run` and `sla_check` never start.

**What to look for:**
```sql
SELECT event, removed_columns, affected_pipelines, evaluated_at
FROM reliability_engine.observability.incident_log
ORDER BY evaluated_at DESC LIMIT 1;
-- event = PIPELINE_HALTED, removed_columns = ['customer_id']
```

In the DAG view: `generate_data` ✓, `ingest_bronze` ✗ (red), `dbt_run` and `sla_check` grey (skipped).
Bronze row count is unchanged — zero rows written.

---

### Reset to baseline

Run the job again with `schema_version=v1`. No config files need to be modified.

---

## Tearing down

```bash
databricks bundle destroy --target dev
```

Removes the Databricks job. Unity Catalog tables are unaffected.
