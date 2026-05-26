# Testing

---

## Idempotency tests

**File:** `reliability_engine/tests/test_idempotency.py`

Tests that running the incremental pipeline twice on identical input produces identical output. This is the core contract of an incremental merge model.

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

### Sample data

```python
data = [
    ("ord-001", "cust-001", "prod-001", 2, 49.99, "confirmed",  now, now),
    ("ord-002", "cust-002", "prod-002", 1, 199.0, "shipped",    now, now),
    ("ord-003", "cust-003", "prod-003", 5, 12.50, "delivered",  now, now),
]
```

Three orders, each with a unique `order_id`. After two merges, still three rows, zero duplicates.

### Run locally

Tests use PySpark + delta-spark in local mode — no Databricks connection required.

**Java 17 is required.** PySpark 3.5 is incompatible with Java 21+. Running against Java 25 (the current Homebrew default) causes `JAVA_GATEWAY_EXITED` at test startup.

Install Java 17 if not present:

```bash
brew install openjdk@17
```

Run with Poetry (recommended):

```bash
JAVA_HOME=/opt/homebrew/opt/openjdk@17 poetry run pytest -v
```

To avoid passing `JAVA_HOME` every time, add to `~/.zshrc`:

```bash
export JAVA_HOME=/opt/homebrew/opt/openjdk@17
```

Then just:

```bash
poetry run pytest
```

---

## CI

**File:** `.github/workflows/test.yml`

Runs on every push to any branch:

```yaml
steps:
  - Install pyspark, delta-spark, pytest
  - pytest reliability_engine/tests/
```

The test suite is designed to run in CI without credentials or a live Databricks workspace.

---

## Schema sentinel smoke test

The sentinel can be tested locally without Spark:

```bash
python reliability_engine/scripts/schema_sentinel.py
```

Runs a NON_BREAKING classification against `schema_v2` schema (adds `delivery_partner`). Prints the verdict and skips UC writes since no Spark session is available.

**Note:** `__file__` is undefined in Databricks notebooks and the REPL. All scripts that resolve config paths use a `try/except NameError` fallback to an absolute workspace path. This is applied in `ingest_bronze.py`, `schema_sentinel.py`, and `sla_monitor.py` — no action needed when running locally via the CLI.

---

## Manual scenario tests

| Scenario | Command | Expected result |
|---|---|---|
| Non-breaking schema change | `cp config/schema_v2.json config/schema_config.json && python ingest_bronze.py` | `schema_change_log` gets NON_BREAKING entry, data written |
| Breaking schema change | `cp config/schema_v3.json config/schema_config.json && python ingest_bronze.py` | `incident_log` gets PIPELINE_HALTED, RuntimeError raised, zero rows written |
| Reset | `cp config/schema_v1.json config/schema_config.json` | Sentinel passes on next run |
| SLA freshness fail | Don't run ingestion for 7+ hours, then run `sla_monitor.py` | `sla_check_log` entry with `status=FAIL` for freshness |
