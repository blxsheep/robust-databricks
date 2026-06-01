# Schema Config Reference

The schema config files live in `reliability_engine/config/`. The sentinel reads `schema_v{n}.json` based on the `schema_version` widget value (default `v1`). Each deployed scenario job has a different version hardcoded in its `base_parameters`. No config file is mutated at runtime.

---

## schema_v1.json — Baseline (8 columns)

The production-default expected schema. All columns required, no optional fields. This is what `Reliability Pipeline — Baseline` runs against.

```json
{
  "version": "v1",
  "description": "Baseline schema — 8 columns",
  "columns": [
    {"name": "order_id",     "type": "string",    "nullable": false},
    {"name": "customer_id",  "type": "string",    "nullable": false},
    {"name": "product_id",   "type": "string",    "nullable": false},
    {"name": "quantity",     "type": "integer",   "nullable": false},
    {"name": "unit_price",   "type": "double",    "nullable": false},
    {"name": "status",       "type": "string",    "nullable": false},
    {"name": "created_at",   "type": "timestamp", "nullable": false},
    {"name": "updated_at",   "type": "timestamp", "nullable": false}
  ]
}
```

---

## Schema version fixtures

### schema_v2.json — Non-breaking change

Adds `delivery_partner` (nullable). Demonstrates that adding an optional column is safe — existing consumers ignore it.

```json
{"name": "delivery_partner", "type": "string", "nullable": true}
```

Sentinel verdict: `NON_BREAKING` — pipeline continues, logs to `schema_change_log`.
Run via: `Reliability Pipeline — Non-Breaking` job.

### schema_v3.json — Breaking change

Removes `customer_id`. Any downstream consumer that expects this column (Silver, Gold, BI tools) will break.

Sentinel verdict: `BREAKING` — `SchemaBreakingChangeError` raised, zero rows written to Bronze, logs to `incident_log`.
Run via: `Reliability Pipeline — Breaking` job.

---

## Type reference

Types used in `schema_config.json` and how they map to Spark:

| Config type | Spark type | Notes |
|---|---|---|
| `string` | `StringType` | |
| `integer` | `IntegerType` | Spark returns `int` — normalized by ingestion layer |
| `double` | `DoubleType` | |
| `float` | `FloatType` | |
| `long` | `LongType` | Spark returns `bigint` — normalized by ingestion layer |
| `timestamp` | `TimestampType` | |
| `boolean` | `BooleanType` | |

---

## SLA config

**File:** `reliability_engine/config/sla_config.json`

```json
{
  "sla_checks": [
    {
      "check_name": "freshness",
      "threshold_hours": 6,
      "business_impact": "Revenue reporting shows stale numbers."
    },
    {
      "check_name": "completeness",
      "min_rows_per_run": 100,
      "business_impact": "Order fulfillment risk."
    },
    {
      "check_name": "schema_consistency",
      "business_impact": "Downstream dbt models fail silently."
    }
  ]
}
```

Adjusting thresholds requires only a config edit — no code changes.
