# Cost Governance

The platform answers one concrete question: **what does it actually cost to reprocess everything versus processing only what changed?**

---

## How cost is tracked

Every ingestion run appends a row to `reliability_engine.observability.cost_attribution_log`:

| Column | Description |
|---|---|
| `pipeline_name` | Identifies the pipeline |
| `run_type` | `full-refresh` or `incremental` |
| `runtime_seconds` | Wall-clock duration |
| `rows_processed` | Rows written in this run |
| `estimated_dbu` | `runtime_seconds / 3600 * 2` |
| `estimated_cost_usd` | `estimated_dbu * $0.22` |
| `methodology` | Explanatory note about proxy rate |
| `logged_at` | UTC timestamp |

### DBU proxy methodology

Free Edition does not expose `system.billing.usage`. The cost model uses a proxy:

```
DBU/hr = 2.0      (approximate Free Edition serverless compute rate)
rate   = $0.22    (per DBU)
cost   = (runtime_seconds / 3600) * 2 * 0.22
```

The `methodology` column makes this explicit in every row. In production, replace `estimated_dbu` with values from `system.billing.usage`.

---

## Benchmarking incremental vs. full-refresh

The benchmark is the ratio between two dbt runs:

```bash
# Baseline — reprocesses all Bronze rows
time dbt run --full-refresh

# Steady state — processes only new/changed rows
time dbt run
```

Record `runtime_seconds` for each. The ratio `full_refresh / incremental` is how much cheaper steady-state operation is relative to worst-case reprocessing.

---

## 30-day cost projection

**File:** `reliability_engine/notebooks/cost_projection.py`

Run as a Databricks notebook. Fill in the benchmark runtimes:

```python
FULL_REFRESH_RUNTIME_S = 42   # seconds from `dbt run --full-refresh`
INCREMENTAL_RUNTIME_S  = 8    # seconds from `dbt run`

RUNS_PER_DAY = 4
DAYS = 30
DBU_RATE_USD = 0.22
```

The notebook projects cumulative cost over 30 days for both run types and plots a divergence chart with the dollar gap labeled.

### What the chart shows

- **Red line** — full-refresh cost compounding at 4 runs/day for 30 days
- **Green line** — incremental cost at the same cadence
- **Annotation** — total savings over 30 days in USD

### Projection formula

```python
def runtime_to_cost_usd(runtime_seconds: float) -> float:
    dbu = (runtime_seconds / 3600) * DBU_PER_HOUR_SERVERLESS
    return dbu * DBU_RATE_USD

def project_costs(runtime_s: float, runs_per_day: int, days: int) -> list[float]:
    daily_cost = runtime_to_cost_usd(runtime_s) * runs_per_day
    return [daily_cost * d for d in range(1, days + 1)]
```

---

## Why this matters

A data team running full-refresh models at every pipeline trigger pays the reprocessing cost on every run. For small tables the gap is negligible. For large tables with frequent runs, the difference compounds into a meaningful budget line.

Incremental models process only what changed. The wider the ingestion window, the more expensive full-refresh becomes relative to incremental — and the more valuable the benchmark.
