# Testing

Two test files. Eight tests. All run in CI on every push.

```bash
poetry run pytest -v
```

---

## Schema scenario tests

**File:** `reliability_engine/tests/test_schema_scenarios.py` (6 tests)

Tests the schema sentinel against the three scenarios that drive the demo: baseline (v1), non-breaking change (v2), and breaking change (v3). The tests load the actual config files from `reliability_engine/config/` and call `schema_sentinel.run(spark=None, config_path=…)` directly — no Spark session, no Databricks credentials, no Java required.

| Test | Scenario | Asserts |
|---|---|---|
| `test_baseline_schema_passes` | v1 incoming matches v1 expected | `verdict == NON_BREAKING`, no changes |
| `test_non_breaking_schema_logged_and_continues` | v2 (adds `delivery_partner`) vs v1 | `verdict == NON_BREAKING`, `delivery_partner` in `added_columns` |
| `test_non_breaking_does_not_raise` | Same | No `SchemaBreakingChangeError` raised |
| `test_breaking_schema_halts_pipeline` | v3 (removes `customer_id`) vs v1 | `SchemaBreakingChangeError` raised |
| `test_breaking_schema_identifies_removed_column` | Same | `customer_id` in `removed_columns` |
| `test_breaking_schema_lists_affected_pipelines` | Same | `affected_pipelines` is non-empty |

Runtime in CI: ~0.02s.

These tests double as the **executable specification** for the sentinel — if the contract ever drifts, the suite fails immediately.

---

## Idempotency tests

**File:** `reliability_engine/tests/test_idempotency.py` (2 tests)

Tests that running the incremental dbt-style merge pipeline twice on identical input produces identical output. This is the core contract of an incremental merge model.

### Two assertions

**`test_incremental_idempotent`** — row count must be identical after run 1 and run 2:

```python
_run_incremental(spark, sample_orders, target)
count_after_run1 = spark.read.format("delta").load(target).count()

_run_incremental(spark, sample_orders, target)
count_after_run2 = spark.read.format("delta").load(target).count()

assert count_after_run1 == count_after_run2
```

**`test_no_duplicate_order_ids`** — no `order_id` appears more than once after two runs:

```python
duplicates = (
    df.groupBy("order_id")
    .count()
    .filter(F.col("count") > 1)
)
assert duplicates.count() == 0
```

### What the incremental merge simulates

```python
def _run_incremental(spark, input_df, target_path: str):
    if DeltaTable.isDeltaTable(spark, target_path):
        dt = DeltaTable.forPath(spark, target_path)
        (
            dt.alias("target")
            .merge(input_df.alias("source"), "target.order_id = source.order_id")
            .whenMatchedUpdateAll()
            .whenNotMatchedInsertAll()
            .execute()
        )
    else:
        input_df.write.format("delta").save(target_path)
```

First call: table doesn't exist → write. Second call: table exists → merge on `order_id`.

These tests use PySpark + delta-spark in local mode — no Databricks connection required, but they DO require Java 17.

---

## Running locally

### Schema scenario tests — fast, no Java required

```bash
poetry run pytest reliability_engine/tests/test_schema_scenarios.py -v
```

### Idempotency tests — require Java 17

**PySpark 3.5 is incompatible with Java 21+.** Running against Java 25 (the current Homebrew default) causes `JAVA_GATEWAY_EXITED` at test startup.

Install Java 17 if not present:

```bash
brew install openjdk@17
```

Run with Java 17 explicitly:

```bash
JAVA_HOME=/opt/homebrew/opt/openjdk@17 poetry run pytest -v
```

Or set it permanently in `~/.zshrc`:

```bash
export JAVA_HOME=/opt/homebrew/opt/openjdk@17
```

Then just `poetry run pytest`.

### The `pythonpath` trick

`pyproject.toml` adds `reliability_engine/scripts/` to pytest's pythonpath:

```toml
[tool.pytest.ini_options]
testpaths = ["reliability_engine/tests"]
pythonpath = ["reliability_engine/scripts"]
```

This lets the scenario tests do `from schema_sentinel import …` without sys.path hacking. CI picks this up automatically.

---

## CI/CD

**File:** `.github/workflows/cicd.yml`

```
push to any branch
  ├── test       (pytest -v, all branches)
  └── validate   (databricks bundle validate, all branches)
              ↓  both must pass
           deploy (databricks bundle deploy + workspace sync, main only)
```

The `test` job:
- Sets up Java 17 explicitly (so idempotency tests pass)
- Sets up Python 3.11 + Poetry
- Runs `poetry install --with dev` then `poetry run pytest -v`

Tests are designed to run without credentials or a live Databricks workspace. The 6 scenario tests are pure Python. The 2 idempotency tests use PySpark in local mode.

---

## Schema sentinel CLI smoke test

The sentinel can also be invoked directly without a test runner:

```bash
python reliability_engine/scripts/schema_sentinel.py
```

This runs a NON_BREAKING classification against `schema_v2` (adds `delivery_partner`). Prints the verdict and skips UC writes since no Spark session is available. Useful for quick sanity checks; the scenario tests are the actual contract.

**Note on `__file__` in Databricks:** `__file__` is undefined in Databricks notebooks. All scripts that resolve config paths use a `try/except NameError` fallback to an absolute workspace path. This is applied in `ingest_bronze.py`, `schema_sentinel.py`, and `sla_monitor.py` — no action needed when running locally via the CLI.

---

## Manual scenario verification

After deploying the bundle, the three scenarios are deployed jobs. Trigger them from the Workflows UI or CLI:

| Scenario | Command | Expected result |
|---|---|---|
| Baseline | `databricks bundle run scenario_baseline --target dev` | 4 tasks green, 3 PASS rows in `sla_check_log` |
| Non-breaking | `databricks bundle run scenario_non_breaking --target dev` | 4 tasks green, NON_BREAKING row in `schema_change_log` |
| Breaking | `databricks bundle run scenario_breaking --target dev` | `ingest_bronze` red, PIPELINE_HALTED in `incident_log`, Bronze row count unchanged |
| Reset | `databricks bundle run reset_data --target dev` | All tables truncated, ready for next cycle |

See [Deployment](deployment.md) for the full demo flow and the SQL queries that verify each scenario.
