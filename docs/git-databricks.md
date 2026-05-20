# Git Versioning for Databricks

How this repository connects to Databricks and how notebooks are versioned in Git.

---

## How It Works

Databricks has a built-in feature called **Git Folders** (Workspace → Git Folders). You link this GitHub repo directly — Databricks clones it into your workspace and you can pull/push from the UI or trigger syncs via the API.

```
GitHub repo  ←──── push / pull ────→  Databricks Git Folder
     ↑                                          ↓
 local edit                           runs as notebooks or Jobs
```

Changes flow in one direction during normal development: edit locally → commit → push → pull in Databricks. You can also edit notebooks directly in Databricks and push back to GitHub, but keeping local as the source of truth is cleaner.

---

## Notebook File Format

Databricks reads plain `.py` files as notebooks when they include a magic header. Every cell is separated by a sentinel comment.

```python
# Databricks notebook source        ← marks this file as a notebook

# COMMAND ----------                 ← cell boundary

your code here

# COMMAND ----------                 ← next cell

more code
```

When Databricks opens a file with this header, it renders each `# COMMAND ----------` block as an interactive cell — exactly like a `.ipynb` notebook, but stored as plain text that diffs cleanly in Git.

`reliability_engine/notebooks/cost_projection.py` already uses this format correctly.

!!! tip "Why `.py` over `.ipynb`"
    `.ipynb` files embed cell outputs and metadata as JSON. Diffs are noisy and conflicts are painful to resolve. `.py` with Databricks cell markers gives you the same interactive experience with clean, reviewable Git history.

---

## Repo Structure for Git Folders

The project separates **notebooks** (what Databricks runs) from **scripts** (pure Python logic that notebooks import):

```
robust-databricks/
├── notebooks/                          ← Databricks notebooks — synced via Git Folder
│   ├── ingestion/
│   │   ├── 01_generate_data.py         # Databricks notebook source
│   │   └── 02_ingest_bronze.py         # Databricks notebook source
│   ├── monitoring/
│   │   └── 03_sla_monitor.py           # Databricks notebook source
│   └── cost/
│       └── 04_cost_projection.py       # Databricks notebook source
│
├── reliability_engine/
│   ├── scripts/                        ← Pure Python modules — imported by notebooks
│   │   ├── schema_sentinel.py          # stateless logic, no SparkSession
│   │   └── generate_data.py            # data generation logic only
│   ├── config/
│   └── tests/
│
└── robust_etl_ecomm/                   ← dbt project — runs from local, not Databricks
```

**The rule:** notebooks orchestrate and display results; scripts contain the logic. This keeps notebooks thin and makes the logic testable locally with `pytest` without a live Databricks connection.

---

## What Runs Where

| Layer | File | Runs on |
|---|---|---|
| Data generation | `notebooks/ingestion/01_generate_data.py` | Databricks (Spark cluster) |
| Ingestion + validation | `notebooks/ingestion/02_ingest_bronze.py` | Databricks (Spark cluster) |
| SLA monitoring | `notebooks/monitoring/03_sla_monitor.py` | Databricks (Spark cluster) |
| Cost projection | `notebooks/cost/04_cost_projection.py` | Databricks (interactive notebook) |
| Schema sentinel logic | `reliability_engine/scripts/schema_sentinel.py` | Imported by notebooks |
| dbt transforms | `robust_etl_ecomm/models/` | Local machine → Databricks SQL Warehouse |
| Unit + idempotency tests | `reliability_engine/tests/` | Local (PySpark + delta-spark, no cluster needed) |

---

## Setting Up the Git Folder in Databricks

**Step 1 — Link your GitHub account**

Databricks UI → top-right avatar → **Settings → Linked Accounts → GitHub** → connect.

**Step 2 — Create the Git Folder**

Workspace → **Git Folders → Add Git Folder** → paste the GitHub repo URL → **Create Git Folder**.

Databricks clones the repo. Your `notebooks/` directory appears as runnable notebooks in the workspace.

**Step 3 — Pull updates**

After pushing changes from local:

```
Databricks UI → Git Folder → Pull
```

Or automate it via the Databricks REST API in CI.

---

## Importing Shared Logic in Notebooks

Notebooks import shared logic from `reliability_engine/scripts/` using `sys.path`:

```python
# Databricks notebook source
# COMMAND ----------

import sys
sys.path.insert(0, "/Workspace/Git/robust-databricks/reliability_engine/scripts")

from schema_sentinel import run as sentinel_run
from generate_data import generate_orders
```

The path `/Workspace/Git/robust-databricks/` is where Databricks mounts the Git Folder. Adjust the last segment to match the folder name you gave when creating the Git Folder.

---

## Branch Strategy for Environment Promotion

Databricks Jobs reference a Git Folder at a specific **branch or tag**, not just a file path. This gives you environment promotion without duplicating any notebooks.

| Branch | Job target | Purpose |
|---|---|---|
| `feature/*` | — | Local development, run notebooks manually |
| `main` | dev Job | Integration testing against dev catalog |
| Git tag (e.g. `v1.0`) | prod Job | Production — pinned, stable |

To configure this in a Databricks Job:

1. Create a new Job → **Add Task → Notebook**
2. Source: **Git provider** → select your repo
3. Set **Git branch** or **Git tag**
4. The job always runs the notebook at the specified ref — branch-based jobs track `HEAD`, tag-based jobs are pinned

This means promoting to production is a tag, not a file copy.

---

## Git Workflow Summary

```bash
# 1. Edit notebook or script locally
vim notebooks/ingestion/02_ingest_bronze.py

# 2. Commit
git add notebooks/ingestion/02_ingest_bronze.py
git commit -m "feat: add retry logic to ingest step"

# 3. Push
git push origin main

# 4. In Databricks — pull the Git Folder
# Workspace → Git Folders → robust-databricks → Pull

# 5. Run the notebook or trigger the Job
```

---

## Notebook Naming Convention

Prefix notebooks with a number to make execution order explicit:

```
01_generate_data.py     ← run first
02_ingest_bronze.py     ← run second
03_sla_monitor.py       ← run after ingestion
04_cost_projection.py   ← run after dbt benchmarks
```

This is purely a naming convention — Databricks does not enforce order. The Job DAG defines actual execution order.
