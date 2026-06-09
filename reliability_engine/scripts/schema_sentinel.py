"""
schema_sentinel.py
Schema change classifier.

Compares the incoming DataFrame schema against the CURRENT schema of the
Bronze target table — the last accepted contract — BEFORE any data enters
Bronze. Classifies as NON_BREAKING or BREAKING, routes accordingly, and
appends to observability tables.

On first run (target table does not yet exist) it falls back to
config/schema_v1.json as the seed contract. After that, the live table
schema is authoritative: columns accepted by a previous run become
required by the next one.

System columns (_ingested_at, _schema_version, _source) are stripped from
the table schema before comparison — they are pipeline metadata added at
write time, not part of the upstream contract being validated.

Each invocation is independent — no internal state is held between calls.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

try:
    CONFIG_PATH = Path(__file__).parent.parent / "config" / "schema_config.json"
except NameError:
    # Running in notebook/REPL where __file__ is not defined
    CONFIG_PATH = Path("/Workspace/Users/c.voranipit@gmail.com/robust-databricks/reliability_engine/config/schema_config.json")


class SchemaBreakingChangeError(Exception):
    """Raised when an incoming schema has a breaking change (removed column or type change)."""

SCHEMA_CHANGE_LOG = "reliability_engine.observability.schema_change_log"
INCIDENT_LOG      = "reliability_engine.observability.incident_log"


@dataclass
class SentinelResult:
    verdict: str                    # "NON_BREAKING" | "BREAKING"
    added_columns: list[str] = field(default_factory=list)
    removed_columns: list[str] = field(default_factory=list)
    type_changes: list[dict] = field(default_factory=list)
    affected_pipelines: list[str] = field(default_factory=list)


# Spark/Databricks type aliases that are semantically equivalent.
# Without this, "integer" vs "int" would be misclassified as a BREAKING type change.
# Spark/Databricks type aliases that are semantically equivalent.
# PySpark infers Python int as LongType ("long") by default, so long/bigint
# must normalize to the same canonical as "integer" — numeric widening is non-breaking.
_TYPE_ALIASES: dict[str, str] = {
    "int":           "integer",
    "long":          "integer",   # PySpark default for Python int
    "bigint":        "integer",   # SQL alias for long
    "float":         "double",
    "bool":          "boolean",
    "str":           "string",
    "varchar":       "string",
    "timestamp_ntz": "timestamp",
}


def _normalize_type(t: str) -> str:
    return _TYPE_ALIASES.get(t.lower(), t.lower())


def load_expected_schema(config_path: Path = CONFIG_PATH) -> dict[str, str]:
    """Returns {column_name: normalized_type} from a schema config JSON file.

    Used as the first-run seed when the target table does not yet exist.
    Raises FileNotFoundError if config is missing, json.JSONDecodeError if malformed.
    These are infrastructure failures — callers should not catch them as schema changes.
    """
    with open(config_path) as f:
        config = json.load(f)
    return {col["name"]: _normalize_type(col["type"]) for col in config["columns"]}


def load_expected_schema_from_table(spark, table_name: str) -> dict[str, str] | None:
    """Read the current Delta table schema as the expected baseline.

    The live table is the last accepted contract: columns that were written
    in a prior run are required by this run. Any removal is BREAKING.

    System columns (_ingested_at, _schema_version, _source) are excluded —
    they are added by the pipeline at write time and are not part of the
    upstream schema being validated.

    Returns None if the table does not yet exist (first run) or on any
    Spark error. Callers must fall back to load_expected_schema() in that case.
    """
    try:
        fields = spark.table(table_name).schema.fields
        return {
            f.name: _normalize_type(f.dataType.simpleString())
            for f in fields
            if not f.name.startswith("_")
        }
    except Exception:
        return None


def classify(incoming_schema: dict[str, str], expected_schema: dict[str, str]) -> SentinelResult:
    """
    incoming_schema: {column_name: type} from the incoming DataFrame
    expected_schema: {column_name: type} from schema_config.json

    Rules:
      - New column added          → NON_BREAKING
      - Required column removed   → BREAKING
      - Column type changed       → BREAKING (after type alias normalization)
    """
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

    return SentinelResult(
        verdict=verdict,
        added_columns=added,
        removed_columns=removed,
        type_changes=type_changes,
        affected_pipelines=affected,
    )


def run(
    incoming_schema: dict[str, str],
    spark=None,
    target_table: str = "reliability_engine.bronze.raw_orders",
    fallback_config_path: Path = CONFIG_PATH,
) -> SentinelResult:
    """
    Entry point. Call with the schema of the incoming DataFrame.

    spark: active SparkSession — required for live table lookup and UC writes.
           If None, falls back to fallback_config_path (local/test mode).
    target_table: the Bronze table whose current schema is the expected baseline.
    fallback_config_path: seed schema used on first run (table does not exist yet)
                          or when spark is unavailable.

    Schema resolution order:
      1. Live table schema (spark available + table exists) — production path
      2. fallback_config_path                              — first run or test mode
    """
    expected = None
    schema_source = fallback_config_path.name

    if spark is not None:
        expected = load_expected_schema_from_table(spark, target_table)
        if expected is not None:
            schema_source = f"live table: {target_table}"

    if expected is None:
        expected = load_expected_schema(fallback_config_path)

    logger.info("Schema baseline: %s", schema_source)
    result = classify(incoming_schema, expected)

    log_entry = {
        "evaluated_at":       datetime.now(timezone.utc).isoformat(),
        "verdict":            result.verdict,
        "added_columns":      str(result.added_columns),
        "removed_columns":    str(result.removed_columns),
        "type_changes":       str(result.type_changes),
        "affected_pipelines": str(result.affected_pipelines),
    }

    if result.verdict == "NON_BREAKING":
        logger.warning("Schema sentinel: NON_BREAKING — pipeline continues. added=%s", result.added_columns)
        _append_log(spark, SCHEMA_CHANGE_LOG, log_entry)

    elif result.verdict == "BREAKING":
        logger.warning(
            "Schema sentinel: BREAKING — pipeline halted. removed=%s type_changes=%s",
            result.removed_columns, result.type_changes,
        )
        _append_log(spark, INCIDENT_LOG, {**log_entry, "event": "PIPELINE_HALTED"})
        raise SchemaBreakingChangeError(
            f"BREAKING schema change detected. Removed: {result.removed_columns}. "
            f"Type changes: {result.type_changes}. Affected: {result.affected_pipelines}"
        )

    else:
        raise ValueError(f"Schema sentinel returned unexpected verdict: {result.verdict!r}")

    return result


def _append_log(spark, table: str, entry: dict):
    if spark is None:
        logger.debug("No spark session — skipping UC write for %s: %s", table, entry)
        return
    spark.createDataFrame([entry]).write.format("delta").mode("append").saveAsTable(table)


if __name__ == "__main__":
    # Quick local smoke test against schema_v2 (non-breaking) — no spark needed
    sample_incoming = {
        "order_id": "string", "customer_id": "string", "product_id": "string",
        "quantity": "integer", "unit_price": "double", "status": "string",
        "created_at": "timestamp", "updated_at": "timestamp",
        "delivery_partner": "string",  # new column
    }
    result = run(sample_incoming, spark=None)
    print(f"Verdict: {result.verdict}")
