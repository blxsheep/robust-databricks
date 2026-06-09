# Architecture Decision Records

Three decisions that define the shape of this system.

---

## ADR-001 — Delta over Parquet

**Decision:** All tables use Delta Lake format.

**Reasoning:**
Plain Parquet gives no ACID guarantees. A partial write during a crash leaves the table in an undefined state. Delta's write-ahead log makes every write atomic. More importantly, Delta's time travel lets us replay the state of any table at the exact moment of an incident — which is the only reliable way to root-cause a data quality failure after the fact.

Schema enforcement at the table level (not just the application level) means corrupt files are rejected at write time, not discovered downstream when a model breaks.

**Consequence:** Requires `delta-spark` in the test environment. A small dependency cost for a large reliability gain.

---

## ADR-002 — Detection at the ingestion boundary

**Decision:** Schema validation runs before any data enters Bronze. Corrupt or incompatible data never touches the warehouse.

**Reasoning:**
Catching schema drift at the transformation layer (dbt) means the bad data has already landed somewhere. It's in Bronze. Downstream systems may have already read it. Rollback requires identifying every consumer and replaying from a clean snapshot.

Catching it at ingestion means the damage radius is zero. The sentinel raises an exception, writes to `incident_log`, and the pipeline exits. No rows written. The warehouse stays clean.

**Baseline: live table schema, not a static config file.**
The sentinel compares incoming data against the *current schema of `bronze.raw_orders`*, not a pinned config. The live table is the last accepted contract: if a previous run wrote `delivery_partner`, the next run must include it — any removal is BREAKING. System columns (`_ingested_at`, `_schema_version`, `_source`) are stripped before comparison since they are pipeline metadata, not upstream contract columns.

On first run (table does not yet exist) the sentinel falls back to `config/schema_v1.json` as the seed. This is the only time the config file is authoritative. After the first successful write the table owns the contract.

This is more production-realistic than a static config: a static file requires a manual update every time a non-breaking change is accepted into the warehouse, and goes stale silently if that update is missed. The live table never goes stale — it *is* the current state.

**Trade-off:** Because the live table schema is the baseline, `reset_data.py` must DROP `bronze.raw_orders` (not just TRUNCATE) to reset the schema contract between demo runs. TRUNCATE clears rows but leaves columns — a `delivery_partner` column left over from a Non-Breaking run would cause the next Baseline run to flag it as removed (BREAKING). DROP forces the next run to reseed from `schema_v1.json`.

**Each invocation is independent** — no internal state is held between calls. The live table read happens at call time; the sentinel holds no reference to prior results.

**Consequence:** The ingestion layer is the single point of schema truth. The `schema_version` widget in `ingest_bronze` controls which scenario-shaped data is *generated and ingested*, not which config file the sentinel compares against. Each scenario job (baseline, non-breaking, breaking) has its own hardcoded version for data generation. The sentinel's baseline always comes from the live table (or the v1 seed on first run).

---

## ADR-003 — Incremental merge over append

**Decision:** Silver and gold models use `unique_key` merge strategy, not append.

**Reasoning:**
E-commerce orders are mutable. An order placed in a `pending` state will later become `confirmed`, `shipped`, `delivered`, or `cancelled`. An append-only model accumulates one row per status transition — which means any aggregation (daily revenue, fulfillment rate) must deduplicate first, and any late-arriving update creates a permanent inconsistency.

Incremental merge on `order_id` means the Silver table always holds the current state of each order. Late-arriving updates correct the record without reprocessing the entire history. The idempotency test (`tests/test_idempotency.py`) enforces this: two runs on identical input must produce identical output.

**Consequence:** dbt requires a SQL warehouse (not all-purpose cluster) for merge operations on Databricks. The Starter Warehouse in Free Edition is sufficient.
