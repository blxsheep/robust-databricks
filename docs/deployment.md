# Deployment — Databricks Asset Bundle

The pipeline is defined as code via a **Databricks Asset Bundle (DAB)**. A single `databricks bundle deploy` creates four jobs in the Databricks workspace — no manual notebook clicking.

---

## What the bundle defines

`databricks.yml` (repo root) declares the bundle and targets. The `resources/` directory holds one job per file:

| File | Job name in Databricks UI | What it does |
|---|---|---|
| `scenario_baseline.job.yml` | `Reliability Pipeline — Baseline` | Runs schema_v1 — baseline, all 4 tasks succeed |
| `scenario_non_breaking.job.yml` | `Reliability Pipeline — Non-Breaking` | Runs schema_v2 — sentinel logs NON_BREAKING, pipeline continues |
| `scenario_breaking.job.yml` | `Reliability Pipeline — Breaking` | Runs schema_v3 — sentinel halts at ingest_bronze, dbt + sla skipped |
| `reset_data.job.yml` | `Reliability Pipeline — Reset` | Truncates all tables — run before each demo cycle |

Each scenario job has the same 4-task DAG. The only difference is the `schema_version` value in the `ingest_bronze` task's `base_parameters`:

```
generate_data
     │
ingest_bronze   ← schema sentinel runs here; halts on BREAKING
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

After this one-time setup, CI/CD keeps the folder in sync automatically — every push to `main` triggers `databricks repos update` after the bundle deploys. To force-sync manually:

```bash
./scripts/databricks_force_sync.sh
```

**Why this is required:** `notebook_task` with `source: WORKSPACE` looks up the path at runtime. The DAB deploys the job definition but does not upload the notebook files — they must already exist in the workspace via the Git folder.

---

## Deploy

```bash
# Validate config (no workspace deploy)
databricks bundle validate --profile DEFAULT

# Deploy to dev — creates the 4 jobs in your workspace
databricks bundle deploy --target dev

# Deploy to prod (when promoting)
databricks bundle deploy --target prod
```

On success you will see four jobs appear under **Workflows** in the Databricks UI, all prefixed with `[dev …]` in dev mode.

**Auth resolution:**
- **Local:** uses `~/.databrickscfg`. If multiple profiles match the workspace host, pass `--profile DEFAULT`.
- **CI:** uses `DATABRICKS_HOST` and `DATABRICKS_TOKEN` env vars (GitHub secrets). No profile needed.

---

## Run jobs manually

```bash
# Reset all tables before a demo
databricks bundle run reset_data --target dev

# Run a scenario
databricks bundle run scenario_baseline --target dev
databricks bundle run scenario_non_breaking --target dev
databricks bundle run scenario_breaking --target dev
```

Or trigger from the Workflows UI. The DAG view shows each task in dependency order, with per-task logs and status.

---

## Demo — Three Schema Scenarios

This is the demo flow. Run each step in order; screenshot the DAG view + the resulting observability table rows after each.

### Step 0 — Reset

Run `Reliability Pipeline — Reset` first. All tables empty. Clean slate.

### Step 1 — Baseline (`scenario_baseline`)

All 4 tasks green. The pipeline ran end-to-end on `schema_v1` — the production baseline.

**Verify:**
```sql
SELECT * FROM reliability_engine.observability.sla_check_log
ORDER BY checked_at DESC LIMIT 3;
```
All three SLA checks show `status = PASS`.

```sql
SELECT count(*) FROM reliability_engine.bronze.raw_orders;
-- 500 rows
```

### Step 2 — Non-Breaking change (`scenario_non_breaking`)

Upstream adds `delivery_partner`. The sentinel logs it. Pipeline continues. All 4 tasks still green.

**Verify:**
```sql
SELECT verdict, added_columns, evaluated_at
FROM reliability_engine.observability.schema_change_log
ORDER BY evaluated_at DESC LIMIT 1;
-- verdict = NON_BREAKING, added_columns = ['delivery_partner']
```

```sql
SELECT count(*) FROM reliability_engine.bronze.raw_orders;
-- 1000 rows (500 from baseline + 500 from this run)
```

### Step 3 — Breaking change (`scenario_breaking`)

Upstream removes `customer_id`. Sentinel raises `SchemaBreakingChangeError`. Pipeline halts at `ingest_bronze`. `dbt_run` and `sla_check` never start (grey in the DAG view).

**Verify:**
```sql
SELECT event, removed_columns, affected_pipelines, evaluated_at
FROM reliability_engine.observability.incident_log
ORDER BY evaluated_at DESC LIMIT 1;
-- event = PIPELINE_HALTED, removed_columns = ['customer_id']
```

```sql
SELECT count(*) FROM reliability_engine.bronze.raw_orders;
-- 1000 rows — unchanged. Zero rows written from this run.
```

In the DAG view: `generate_data` ✓ (green), `ingest_bronze` ✗ (red), `dbt_run` and `sla_check` grey (skipped).

This is the headline behavior — corrupt data never enters the warehouse.

---

## On-failure behavior

Each scenario job sends email to `c.voranipit@gmail.com` on any task failure (except `reset_data` which is a manual utility). Because tasks have explicit `depends_on`, a failure at `ingest_bronze` (e.g. a breaking schema change) automatically prevents `dbt_run` and `sla_check` from starting — no downstream corruption.

The `Breaking` scenario job is **expected to fail** at `ingest_bronze`. That's the demo. The email notification is left in place to demonstrate the on-failure hook.

---

## CI/CD

`.github/workflows/cicd.yml` is the single workflow file.

```
push to any branch
  ├── test       (pytest, all branches)
  └── validate   (bundle validate, all branches)
              ↓  both must pass
           deploy (bundle deploy + workspace sync, main only)
```

- `test` and `validate` run in parallel — they don't depend on each other and neither writes to the workspace.
- `deploy` has `needs: [test, validate]` AND `if: github.ref == 'refs/heads/main'`.
- After deploy, the workspace Git folder is synced via `databricks repos update`.

GitHub repo secrets required:

| Secret | Value |
|---|---|
| `DATABRICKS_HOST` | `https://dbc-970ed03d-67bd.cloud.databricks.com` |
| `DATABRICKS_TOKEN` | Databricks PAT (Settings → Developer → Access tokens) |

---

## Bundle file layout

```
databricks.yml                          # bundle root: name, targets, variables
resources/
├── scenario_baseline.job.yml           # schema_v1
├── scenario_non_breaking.job.yml       # schema_v2
├── scenario_breaking.job.yml           # schema_v3
└── reset_data.job.yml                  # TRUNCATE all tables
```

Variables declared in `databricks.yml`:

| Variable | Description | Default |
|---|---|---|
| `workspace_repo_path` | Absolute path to git-synced repo in workspace | `/Workspace/Users/c.voranipit@gmail.com/robust-databricks` |
| `warehouse_id` | SQL Warehouse ID for `dbt_task` | `e7d27934b2137b5b` (Starter Warehouse, set in `dev` target) |

---

## Tearing down

```bash
databricks bundle destroy --target dev
```

Removes all four jobs. Unity Catalog tables are unaffected. To wipe tables, run the `reset_data` job once before destroying.
