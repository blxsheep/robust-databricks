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

The sentinel is stateless — it reads config, compares, routes. This is an explicit design constraint: a stateful sentinel introduces shared state that complicates horizontal scaling and makes the classifier harder to test in isolation.

**Consequence:** The ingestion layer is the single point of schema truth. `schema_config.json` must be kept current. A schema migration requires updating the config file before the next ingestion run.

---

## ADR-003 — Incremental merge over append

**Decision:** Silver and gold models use `unique_key` merge strategy, not append.

**Reasoning:**
E-commerce orders are mutable. An order placed in a `pending` state will later become `confirmed`, `shipped`, `delivered`, or `cancelled`. An append-only model accumulates one row per status transition — which means any aggregation (daily revenue, fulfillment rate) must deduplicate first, and any late-arriving update creates a permanent inconsistency.

Incremental merge on `order_id` means the Silver table always holds the current state of each order. Late-arriving updates correct the record without reprocessing the entire history. The idempotency test (`tests/test_idempotency.py`) enforces this: two runs on identical input must produce identical output.

**Consequence:** dbt requires a SQL warehouse (not all-purpose cluster) for merge operations on Databricks. The Starter Warehouse in Free Edition is sufficient.
