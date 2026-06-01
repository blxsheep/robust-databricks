# Git Versioning for Databricks

How this repository connects to Databricks and how `.py` files become notebooks in the workspace.

---

## How it works

Databricks has a built-in feature called **Git folders** (Workspace → Git folders). You link this GitHub repo directly — Databricks clones it into your workspace at `/Workspace/Users/<email>/robust-databricks` and you can pull/push from the UI or via the REST API.

```
GitHub repo  ←──── push / pull ────→  Databricks Git folder
     ↑                                          ↓
 local edit                              runs as notebooks via Jobs
```

Changes flow in one direction during normal development: edit locally → commit → push → CI syncs the workspace. CI/CD calls `databricks repos update` after every successful deploy so the workspace folder always matches `main`.

You *can* edit files directly in Databricks and push back to GitHub, but keeping local as the source of truth is cleaner. The `scripts/databricks_force_sync.sh` script will reset workspace-only edits if there's a conflict — read the warning before running it.

---

## Notebook file format

Databricks reads plain `.py` files as **notebooks** only when the first line is the magic header:

```python
# Databricks notebook source

"""
your code goes here as if it were a normal Python file
"""

import ...
```

When a `.py` file in a Git folder has this header, Databricks registers it as a notebook object in the workspace registry. Without the header, it's just a plain text file — `notebook_task` will fail with "Unable to access the notebook" even though the file exists at the expected path.

The `# COMMAND ----------` separator can split the file into multiple cells if you want interactive editing in the workspace. None of the task scripts in this repo use cell markers — they're single-cell notebooks that run top-to-bottom.

!!! tip "Why `.py` over `.ipynb`"
    `.ipynb` files embed cell outputs and metadata as JSON. Diffs are noisy and conflicts are painful to resolve. `.py` with the Databricks notebook header gives you the same workspace-editor experience with clean, reviewable Git history.

---

## Which files have the notebook header (and which don't)

The header is a one-way door. Files with it cannot be imported as Python modules; files without it cannot be referenced by `notebook_task`.

| File | Header? | Why |
|---|---|---|
| `reliability_engine/scripts/generate_data.py` | ✓ Yes | Notebook task in scenario jobs |
| `reliability_engine/scripts/ingest_bronze.py` | ✓ Yes | Notebook task in scenario jobs |
| `reliability_engine/scripts/sla_monitor.py` | ✓ Yes | Notebook task in scenario jobs |
| `reliability_engine/scripts/reset_data.py` | ✓ Yes | Notebook task in the reset job |
| `reliability_engine/notebooks/cost_projection.py` | ✓ Yes | Interactive notebook (not in a job) |
| `reliability_engine/scripts/schema_sentinel.py` | ✗ No | **Imported as a module** by `ingest_bronze` and `sla_monitor` |
| `reliability_engine/scripts/_orders_generator.py` | ✗ No | **Imported as a module** by `generate_data` and `ingest_bronze` |

`_orders_generator.py` exists specifically because `generate_data.py` needs the notebook header (it's a notebook task) AND its `generate_orders()` function needs to be importable from `ingest_bronze.py`'s `__main__` block. A single file cannot do both — that was the lesson from a session where `from generate_data import generate_orders` started throwing `NotebookImportException` after the header was added.

---

## Setting up the Git folder in Databricks

**Step 1 — Link your GitHub account**

Databricks UI → top-right avatar → **Settings → Linked Accounts → GitHub** → connect.

**Step 2 — Create the Git folder**

Workspace → navigate to `/Users/<your-email>/` → **Create → Git folder** → paste the GitHub repo URL → confirm the folder name is `robust-databricks`.

Databricks clones the repo. All `.py` files with the notebook header become runnable notebooks in the workspace.

**Step 3 — Sync after each push**

After CI/CD has been set up (which is automatic in this repo via `cicd.yml`), the workspace folder syncs automatically on every push to `main`. To force-sync manually:

```bash
./scripts/databricks_force_sync.sh
```

Or from the UI: Git folder → Pull.

---

## Workspace path conventions

The DAB resource files reference notebooks via an absolute workspace path:

```yaml
notebook_task:
  notebook_path: ${var.workspace_repo_path}/reliability_engine/scripts/ingest_bronze
  source: WORKSPACE
```

`${var.workspace_repo_path}` is defined in `databricks.yml` and defaults to `/Workspace/Users/c.voranipit@gmail.com/robust-databricks`.

Note that the path does **not** include `.py` — Databricks resolves notebook paths by name, not file extension. The notebook registry maps the file at `…/scripts/ingest_bronze.py` to the workspace object at `…/scripts/ingest_bronze`.

---

## Imports between notebooks and modules

Notebooks add their own directory to `sys.path` automatically. From `ingest_bronze.py` (a notebook):

```python
from schema_sentinel import SchemaBreakingChangeError, run as sentinel_run
from _orders_generator import BRONZE_SCHEMA, generate_orders
```

Both imports resolve because `schema_sentinel.py` and `_orders_generator.py` are plain modules (no notebook header) in the same directory.

Importing a file *with* the notebook header throws `NotebookImportException` — that's the failure mode that drove the `_orders_generator.py` split.

---

## Branch strategy

The DAB targets are tied to deployment, not to Git branches. CI/CD deploys to `dev` from `main`. To deploy to `prod`, run `databricks bundle deploy --target prod` manually after testing in dev.

| Branch / event | What happens |
|---|---|
| Push to feature branch | `test` + `validate` jobs run (no deploy) |
| Push to `main` | `test` + `validate` run → `deploy` runs → workspace sync |
| Manual prod promotion | `databricks bundle deploy --target prod` locally |

CI/CD does not currently auto-deploy to prod — that's a deliberate choice to keep the prod cut as a manual decision.

---

## Git workflow summary

```bash
# 1. Edit locally
vim reliability_engine/scripts/ingest_bronze.py

# 2. Run tests
poetry run pytest -v

# 3. Validate bundle (optional — CI will do this anyway)
databricks bundle validate --profile DEFAULT

# 4. Commit + push
git add reliability_engine/scripts/ingest_bronze.py
git commit -m "fix: add structured error context to bronze ingestion"
git push

# 5. CI/CD runs: test ⊥ validate → deploy → workspace sync
#    See: github.com/blxsheep/robust-databricks/actions
```

No manual workspace pulls. No notebook UI edits. The repo is the source of truth.
