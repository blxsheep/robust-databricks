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

---

## One-time: set the warehouse ID

The `dbt_run` task executes SQL via a Databricks SQL Warehouse. Find your Starter Warehouse ID:

**Databricks UI → SQL Warehouses → Starter Warehouse → Connection Details → HTTP path**

The warehouse ID is the last segment of the HTTP path (e.g. `abc123def456`).

Set it as a bundle variable override or export before deploying:

```bash
# Option 1 — override at deploy time
databricks bundle deploy --var="warehouse_id=abc123def456"

# Option 2 — set it in databricks.yml under the dev target variables block
variables:
  warehouse_id: abc123def456
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

## Tearing down

```bash
databricks bundle destroy --target dev
```

Removes the Databricks job. Unity Catalog tables are unaffected.
