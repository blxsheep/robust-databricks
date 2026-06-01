# Schema Sentinel

**File:** `reliability_engine/scripts/schema_sentinel.py`

The schema sentinel runs at the ingestion boundary. It compares the incoming DataFrame schema against a versioned `config/schema_v{n}.json` (selected by the `schema_version` job parameter) before any data enters Bronze. If the change is breaking, no rows are written — ever.

---

## Design principle

The sentinel is **stateless by design**. It reads config, compares schemas, routes the result. No internal state between invocations. Each call is fully independent.

This matters for horizontal scaling: multiple ingestion jobs can run concurrently and each will independently validate against the same config file without coordination overhead.

---

## Classification rules

| Change | Verdict | Consequence |
|---|---|---|
| New column added | `NON_BREAKING` | Log to `schema_change_log`, pipeline continues |
| Required column removed | `BREAKING` | Log to `incident_log`, `SchemaBreakingChangeError` raised, zero rows written |
| Column type changed | `BREAKING` | Same as above |
| Any other verdict | — | `ValueError` raised — indicates a bug in `classify()`, not a schema event |

Adding columns is always safe — downstream consumers ignore unknown columns. Removing or changing a required column breaks every consumer silently if not caught.

Type comparison uses normalized canonical names before classifying. Spark and schema_config.json use slightly different aliases for the same types — without normalization, `"int"` vs `"integer"` would fire a false `BREAKING` verdict.

| Alias | Canonical | Note |
|---|---|---|
| `int` | `integer` | |
| `long`, `bigint` | `integer` | PySpark infers Python `int` as `LongType` by default — widening is non-breaking |
| `float` | `double` | |
| `bool` | `boolean` | |
| `str`, `varchar` | `string` | |
| `timestamp_ntz` | `timestamp` | |

---

## How it works

### 1. Load expected schema

```python
def load_expected_schema(config_path: Path = CONFIG_PATH) -> dict[str, str]:
    with open(config_path) as f:
        config = json.load(f)
    return {col["name"]: _normalize_type(col["type"]) for col in config["columns"]}
```

Returns `{column_name: normalized_type}` from the config file at `config_path` (typically `schema_v{n}.json` for the requested scenario). Raises `FileNotFoundError` if the config is missing or `json.JSONDecodeError` if malformed — these are infrastructure failures and propagate as-is, not as schema errors.

### 2. Classify the incoming schema

```python
def classify(incoming_schema: dict[str, str], expected_schema: dict[str, str]) -> SentinelResult:
    normalized_incoming = {c: _normalize_type(t) for c, t in incoming_schema.items()}

    added   = [c for c in normalized_incoming if c not in expected_schema]
    removed = [c for c in expected_schema if c not in normalized_incoming]
    type_changes = [
        {"column": c, "expected": expected_schema[c], "actual": normalized_incoming[c]}
        for c in normalized_incoming
        if c in expected_schema and normalized_incoming[c] != expected_schema[c]
    ]

    is_breaking = bool(removed or type_changes)
    verdict = "BREAKING" if is_breaking else "NON_BREAKING"
    affected = ["bronze.raw_orders", "silver.orders_cleaned", "gold.daily_revenue"] if is_breaking else []
    ...
```

Both sides are normalized before comparison — Spark type aliases are resolved to canonical names first.

### 3. Route and log

- **NON_BREAKING** → logs `WARNING`, appends to `reliability_engine.observability.schema_change_log`, returns `SentinelResult`
- **BREAKING** → logs `WARNING`, appends to `reliability_engine.observability.incident_log` with `event: PIPELINE_HALTED`, raises `SchemaBreakingChangeError`
- **Unexpected verdict** → raises `ValueError` — this is a code bug, not a schema event

`SchemaBreakingChangeError` propagates up through `ingest_bronze.py`, stopping the write before any data touches Bronze. Both `SchemaBreakingChangeError` and infrastructure errors (`FileNotFoundError`, `JSONDecodeError`) can be caught separately by callers.

---

## SentinelResult dataclass

```python
@dataclass
class SentinelResult:
    verdict: str                    # "NON_BREAKING" | "BREAKING"
    added_columns: list[str]
    removed_columns: list[str]
    type_changes: list[dict]        # {"column", "expected", "actual"}
    affected_pipelines: list[str]   # populated only on BREAKING
```

---

## Observability log entries

### schema_change_log (NON_BREAKING)

| Field | Example |
|---|---|
| `evaluated_at` | `2026-05-19T08:30:00.000000` |
| `verdict` | `NON_BREAKING` |
| `added_columns` | `['delivery_partner']` |
| `removed_columns` | `[]` |
| `type_changes` | `[]` |
| `affected_pipelines` | `[]` |

### incident_log (BREAKING)

| Field | Example |
|---|---|
| `evaluated_at` | `2026-05-19T08:30:00.000000` |
| `verdict` | `BREAKING` |
| `event` | `PIPELINE_HALTED` |
| `removed_columns` | `['customer_id']` |
| `type_changes` | `[]` |
| `affected_pipelines` | `['bronze.raw_orders', 'silver.orders_cleaned', 'gold.daily_revenue']` |

---

## Testing it locally

The sentinel can run without a Spark session (no UC writes, logs printed only):

```python
from schema_sentinel import SchemaBreakingChangeError, run

# Non-breaking: new column added
sample_incoming = {
    "order_id": "string", "customer_id": "string", "product_id": "string",
    "quantity": "integer", "unit_price": "double", "status": "string",
    "created_at": "timestamp", "updated_at": "timestamp",
    "delivery_partner": "string",  # new column — non-breaking
}
result = run(sample_incoming, spark=None)
print(result.verdict)  # NON_BREAKING

# Breaking: column removed
try:
    run({"order_id": "string"}, spark=None)  # missing required columns
except SchemaBreakingChangeError as e:
    print(f"Schema change: {e}")
except FileNotFoundError:
    print("Config missing — infrastructure issue, not a schema change")
```

Or run the file directly:

```bash
python reliability_engine/scripts/schema_sentinel.py
```

---

## Updating the expected schema

Each scenario job loads a specific `schema_v{n}.json` via the `schema_version` widget parameter. To evolve the production-baseline schema:

1. Add a new versioned config file (e.g. `schema_v4.json`) reflecting the new upstream schema
2. Update the `base_parameters` in `resources/scenario_baseline.job.yml` to point at the new version
3. Run `dbt run --full-refresh` if column types or names changed
4. Deploy: `databricks bundle deploy --target dev`
5. Trigger the baseline job — the sentinel will now validate against the new schema

The versioned files become the historical record of the production-baseline schema over time, queryable from Git.

!!! warning
    Promoting a new baseline **before** the upstream sends the new schema will cause all current ingestions to fail with a BREAKING verdict (columns present in config but missing from incoming data). Coordinate the timing carefully — promote the bundle change AFTER the upstream cutover.
