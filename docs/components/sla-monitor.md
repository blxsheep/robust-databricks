# SLA Monitor

**File:** `reliability_engine/scripts/sla_monitor.py`  
**Config:** `reliability_engine/config/sla_config.json`  
**Target:** `reliability_engine.observability.sla_check_log`

Runs three checks against the Bronze table. Each check carries a `business_impact` label that explains why it exists — not just whether it passed.

---

## Design principle

Thresholds and `business_impact` strings live in `sla_config.json`. Changing SLA rules is a **config edit, not a code change**.

---

## The three checks

### Freshness

> "Revenue reporting shows stale numbers."

```python
def check_freshness(threshold_hours: int) -> tuple[str, str | None]:
    latest = spark.table(BRONZE_TABLE).agg(F.max("updated_at")).collect()[0][0]
    age_hours = (datetime.utcnow() - latest).total_seconds() / 3600
    if age_hours > threshold_hours:
        return "FAIL", f"Data is {age_hours:.1f}h old, threshold={threshold_hours}h"
    return "PASS", None
```

**Default threshold:** 6 hours  
**Checks:** `max(updated_at)` in Bronze vs. current UTC time  
**Fails when:** The most recent row is older than 6 hours — pipeline likely stalled

### Completeness

> "Order fulfillment risk."

```python
def check_completeness(min_rows: int) -> tuple[str, str | None]:
    today = str(datetime.utcnow().date())
    count = spark.table(BRONZE_TABLE).filter(F.to_date("_ingested_at") == today).count()
    if count < min_rows:
        return "FAIL", f"Row count {count} below minimum {min_rows}"
    return "PASS", None
```

**Default minimum:** 100 rows per run  
**Checks:** Rows ingested today (filtered on `_ingested_at`)  
**Fails when:** Today's ingestion count is below the minimum — data may have been dropped at source

### Schema consistency

> "Downstream dbt models fail silently."

```python
def check_schema_consistency() -> tuple[str, str | None]:
    expected = load_expected_schema()
    actual = {
        f.name: f.dataType.simpleString()
        for f in spark.table(BRONZE_TABLE).schema.fields
        if not f.name.startswith("_")    # exclude system columns
    }
    result = classify(actual, expected)
    if result.verdict == "BREAKING":
        return "FAIL", f"removed={result.removed_columns} type_changes={result.type_changes}"
    return "PASS", None
```

**Checks:** Live Bronze table schema vs. `schema_config.json`  
**Fails when:** A column was removed from or changed in the live table — even if the sentinel passed at ingestion time

---

## Log output

Each run appends one row per check to `observability.sla_check_log`:

| Field | Type | Example |
|---|---|---|
| `check_name` | string | `freshness` |
| `status` | string | `PASS` or `FAIL` |
| `detail` | string | `"Data is 8.2h old, threshold=6h"` or `""` |
| `business_impact` | string | `"Revenue reporting shows stale numbers."` |
| `checked_at` | string (ISO) | `2026-05-19T08:30:00.000000` |

The `business_impact` field survives into dashboards and alerts — whoever is on-call sees not just a status but the reason it matters.

---

## SLA config

`reliability_engine/config/sla_config.json`:

```json
{
  "sla_checks": [
    {
      "check_name": "freshness",
      "description": "Bronze table must have data within the last N hours",
      "threshold_hours": 6,
      "business_impact": "Revenue reporting shows stale numbers."
    },
    {
      "check_name": "completeness",
      "description": "Row count must not drop below expected minimum per run",
      "min_rows_per_run": 100,
      "business_impact": "Order fulfillment risk."
    },
    {
      "check_name": "schema_consistency",
      "description": "Active schema must match schema_config.json at every run",
      "business_impact": "Downstream dbt models fail silently."
    }
  ]
}
```

To adjust a threshold, edit the JSON. No code changes needed.

---

## Running

```bash
python reliability_engine/scripts/sla_monitor.py
```

Output:

```
freshness: PASS
completeness: PASS
schema_consistency: PASS
```

Or on failure:

```
freshness: FAIL  Data is 8.2h old, threshold=6h
```

Results are always written to `sla_check_log` regardless of pass/fail.
