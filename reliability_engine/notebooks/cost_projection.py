# Databricks notebook source
# cost_projection.py
# 30-day cost divergence chart: full-refresh vs incremental.
#
# Methodology note:
#   DBU proxy rate: $0.22/DBU. Free Edition serverless.
#   Ratio holds at scale. In production: replace `estimated_dbu`
#   with actual values from system.billing.usage.
#
# TODO: datasource — populate FULL_REFRESH_RUNTIME_S and INCREMENTAL_RUNTIME_S
#   by running:
#     dbt run --full-refresh   → record runtime
#     dbt run                  → record runtime
#   Then paste observed values below.

# COMMAND ----------

# Parameters — fill in from observed dbt benchmark runs
FULL_REFRESH_RUNTIME_S = None   # TODO: datasource — seconds from `dbt run --full-refresh`
INCREMENTAL_RUNTIME_S  = None   # TODO: datasource — seconds from `dbt run`

RUNS_PER_DAY = 4
DAYS = 30
DBU_RATE_USD = 0.22
DBU_PER_HOUR_SERVERLESS = 2.0   # approximate Free Edition serverless rate

# COMMAND ----------

def runtime_to_cost_usd(runtime_seconds: float) -> float:
    dbu = (runtime_seconds / 3600) * DBU_PER_HOUR_SERVERLESS
    return dbu * DBU_RATE_USD


def project_costs(runtime_s: float, runs_per_day: int, days: int) -> list[float]:
    daily_cost = runtime_to_cost_usd(runtime_s) * runs_per_day
    return [daily_cost * d for d in range(1, days + 1)]


# COMMAND ----------

if FULL_REFRESH_RUNTIME_S is None or INCREMENTAL_RUNTIME_S is None:
    print("TODO: Set FULL_REFRESH_RUNTIME_S and INCREMENTAL_RUNTIME_S from benchmark runs.")
    print("Run `dbt run --full-refresh` and `dbt run`, record runtimes, then rerun this notebook.")
else:
    import matplotlib.pyplot as plt

    full_refresh_costs  = project_costs(FULL_REFRESH_RUNTIME_S, RUNS_PER_DAY, DAYS)
    incremental_costs   = project_costs(INCREMENTAL_RUNTIME_S,  RUNS_PER_DAY, DAYS)
    days_axis = list(range(1, DAYS + 1))

    savings_30d = full_refresh_costs[-1] - incremental_costs[-1]

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(days_axis, full_refresh_costs,  label="Full refresh",  color="#d62728", linewidth=2)
    ax.plot(days_axis, incremental_costs,   label="Incremental",   color="#2ca02c", linewidth=2)
    ax.annotate(
        f"${savings_30d:.2f} saved",
        xy=(DAYS, full_refresh_costs[-1]),
        xytext=(DAYS - 8, (full_refresh_costs[-1] + incremental_costs[-1]) / 2),
        arrowprops=dict(arrowstyle="->"),
        fontsize=11,
    )
    ax.set_xlabel("Day")
    ax.set_ylabel("Cumulative cost (USD)")
    ax.set_title(f"30-day cost divergence: {RUNS_PER_DAY} runs/day @ ${DBU_RATE_USD}/DBU")
    ax.legend()
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.show()

    print(f"30-day full-refresh total : ${full_refresh_costs[-1]:.4f}")
    print(f"30-day incremental total  : ${incremental_costs[-1]:.4f}")
    print(f"Savings                   : ${savings_30d:.4f}")
