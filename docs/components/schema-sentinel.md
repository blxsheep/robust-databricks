# Schema Sentinel

**File:** `reliability_engine/scripts/schema_sentinel.py`

The schema sentinel runs at the ingestion boundary. It compares the incoming DataFrame schema against the active `config/schema_config.json` before any data enters Bronze. If the change is breaking, no rows are written — ever.

---

## Design principle

The sentinel is **stateless by design**. It reads config, compares schemas, routes the result. No internal state between invocations. Each call is fully independent.

This matters for horizontal scaling: multiple ingestion jobs can run concurrently and each will independently validate against the same config file without coordination overhead.

---

## Classification rules

| Change | Verdict | Consequence |
|---|---|---|
| New column added | `NON_BREAKING` | Log to `schema_change_log`, pipeline continues |
| Required column removed | `BREAKING` | Log to `incident_log`, `RuntimeError` raised, zero rows written |
| Column type changed | `BREAKING` | Same as above |

Adding columns is always safe — downstream consumers ignore unknown columns. Removing or changing a required column breaks every consumer silently if not caught.

---

## How it works

### 1. Load expected schema

```python
def load_expected_schema(config_path: Path = CONFIG_PATH) -> dict[str, str]:
    with open(config_path) as f:
        config = json.load(f)
    return {col["name"]: col["type"] for col in config["columns"]}
```

Returns `{column_name: type}` from the active `schema_config.json`.

### 2. Classify the incoming schema

```python
def classify(incoming_schema: dict[str, str], expected_schema: dict[str, str]) -> SentinelResult:
    added   = [c for c in incoming_schema if c not in expected_schema]
    removed = [c for c in expected_schema if c not in incoming_schema]
    type_changes = [
        {"column": c, "expected": expected_schema[c], "actual": incoming_schema[c]}
        for c in incoming_schema
        if c in expected_schema and incoming_schema[c] != expected_schema[c]
    ]

    is_breaking = bool(removed or type_changes)
    verdict = "BREAKING" if is_breaking else "NON_BREAKING"
    affected = ["bronze.raw_orders", "silver.orders_cleaned", "gold.daily_revenue"] if is_breaking else []
    ...
```

### 3. Route and log

- **NON_BREAKING** → appends to `reliability_engine.observability.schema_change_log`, returns `SentinelResult`
- **BREAKING** → appends to `reliability_engine.observability.incident_log` with `event: PIPELINE_HALTED`, raises `RuntimeError`

The `RuntimeError` propagates up through `ingest_bronze.py`, stopping the write. Zero rows reach Bronze.

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
from schema_sentinel import run

sample_incoming = {
    "order_id": "string", "customer_id": "string", "product_id": "string",
    "quantity": "integer", "unit_price": "double", "status": "string",
    "created_at": "timestamp", "updated_at": "timestamp",
    "delivery_partner": "string",  # new column — non-breaking
}
result = run(sample_incoming, spark=None)
print(result.verdict)  # NON_BREAKING
```

Or run the file directly:

```bash
python reliability_engine/scripts/schema_sentinel.py
```

---

## Updating the expected schema

`schema_config.json` is the single source of schema truth. Changing it is a config edit — no code changes required.

Before a schema migration:

1. Update `config/schema_config.json` to match the new upstream schema
2. Run `dbt run --full-refresh` if column types or names changed
3. Run the next ingestion — the sentinel will now pass the new schema

!!! warning
    Updating `schema_config.json` **before** the upstream sends the new schema will cause all current ingestions to fail with a BREAKING verdict (columns present in config but missing from incoming data). Coordinate the timing carefully.
