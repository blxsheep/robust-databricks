# Robust Databricks — Reliability Engine

A data reliability platform for pipelines that cannot afford to fail silently.

---

## The problem

Three failure modes cause the majority of production data incidents:

| Failure mode | How it's usually discovered |
|---|---|
| Upstream schema changes | A dbt model fails, or worse — silently produces wrong numbers |
| Late-arriving / stale data | A stakeholder notices revenue reports look off |
| Runaway compute costs | The cloud bill arrives |

All three are discovered **after** the damage is done.

This platform handles all three **at the boundary** — before corrupt or expensive work propagates downstream.

---

## What it does

```
Incoming data
     │
     ▼
Schema Sentinel ──► NON_BREAKING → pipeline continues
     │               BREAKING    → exception raised, zero rows written
     ▼
Bronze (raw_orders)   ← audit columns on every row
     │
     ▼
dbt Silver (orders_cleaned)   ← incremental merge, late updates handled
     │
     ▼
dbt Gold (daily_revenue)      ← daily aggregation
     │
     ▼
SLA Monitor + Cost Attribution  ← observability layer
```

---

## Built on

- **Databricks Free Edition** — Unity Catalog, serverless compute, Jobs
- **Delta Lake** — ACID transactions, time travel, schema enforcement
- **dbt** — incremental merge models with watermark-based processing
- **Python + PySpark** — ingestion, sentinel, SLA checks

---

## Platform constraints

!!! warning "Databricks Community Edition was retired end of 2025"
    This project uses **Free Edition only** (`signup.databricks.com`). Workspace spins up with serverless compute included.

!!! danger "DBFS is deprecated and unavailable in Free Edition"
    Every path uses Unity Catalog managed tables and volumes. If you see a `dbfs:/` path, it is wrong.

---

## Quick links

- [Setup guide](setup.md) — get the stack running end-to-end
- [Architecture](architecture.md) — system diagram and data flow
- [Schema Sentinel](components/schema-sentinel.md) — how schema changes are caught at the boundary
- [ADR](reference/adr.md) — the three decisions that define the shape of the system
