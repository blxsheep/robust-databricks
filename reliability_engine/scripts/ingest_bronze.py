# Databricks notebook source

"""
ingest_bronze.py
Ingestion pipeline: validates schema via sentinel, then writes to
reliability_engine.bronze.raw_orders with system metadata columns.

System columns appended to every row:
  _ingested_at      — timestamp of write
  _schema_version   — schema_config.json version active at ingestion
  _source           — pipeline identifier

Run directly to generate + ingest 500 synthetic orders:
  python ingest_bronze.py
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from pyspark.sql import SparkSession, functions as F
from _orders_generator import BRONZE_SCHEMA, generate_orders
from schema_sentinel import SchemaBreakingChangeError, run as sentinel_run
from _config import cfg

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

spark = SparkSession.builder.getOrCreate()

try:
    _SCRIPTS_DIR = Path(__file__).parent
except NameError:
    _SCRIPTS_DIR = Path("/Workspace/Users/c.voranipit@gmail.com/robust-databricks/reliability_engine/scripts")

_CONFIG_DIR = _SCRIPTS_DIR.parent / "config"


def resolve_schema_config_path(scenario: str) -> Path:
    """Resolve the on-disk path for a given schema_version value.

    The scenario value is the literal job parameter ('v1' | 'v2' | 'v3') —
    it ALREADY contains the 'v' prefix. Do not prepend another one.
    """
    return _CONFIG_DIR / f"schema_{scenario}.json"


# schema_version widget: v1 (baseline), v2 (non-breaking), v3 (breaking).
# Set via Databricks job parameter or dbutils.widgets for manual runs.
# Defaults to v1 (production baseline) so scheduled runs are never affected.
try:
    _scenario = dbutils.widgets.get("schema_version")  # noqa: F821
except Exception:
    _scenario = "v1"

SCHEMA_CONFIG_PATH = resolve_schema_config_path(_scenario)

TARGET_TABLE = cfg["BRONZE_TABLE"]
COST_LOG     = cfg["COST_LOG"]
PIPELINE_ID  = cfg["PIPELINE_ID"]


def _schema_version() -> str:
    with open(SCHEMA_CONFIG_PATH) as f:
        return json.load(f).get("version", _scenario)


def _df_to_schema_dict(df) -> dict[str, str]:
    """Returns {column_name: raw_spark_type} from a Spark DataFrame.

    Type normalization (e.g. "int" → "integer") is handled by schema_sentinel,
    which owns the canonical type alias map.
    """
    return {f.name: f.dataType.simpleString() for f in df.schema.fields}


def ingest(df) -> int:
    """
    Validates schema before ingestion, then attaches metadata columns and writes to Bronze.

    Raises:
        SchemaBreakingChangeError: incoming schema has a removed column or type change.
        FileNotFoundError: schema_config.json is missing (infrastructure failure).
        json.JSONDecodeError: schema_config.json is malformed (infrastructure failure).
    Returns the number of rows written.
    """
    start = datetime.now(timezone.utc)

    # 1. Pre-ingestion schema validation — no data is written if this raises.
    #    SchemaBreakingChangeError → genuine schema change, logged to incident_log.
    #    FileNotFoundError / JSONDecodeError → infrastructure failure, propagates as-is.
    incoming_schema = _df_to_schema_dict(df)
    sentinel_run(
        incoming_schema,
        spark=spark,
        target_table=TARGET_TABLE,
        fallback_config_path=_CONFIG_DIR / "schema_v1.json",
    )

    # 2. Attach system metadata columns
    schema_ver  = _schema_version()
    ingested_at = datetime.now(timezone.utc)

    df_enriched = (
        df
        .withColumn("_ingested_at",    F.lit(ingested_at).cast("timestamp"))
        .withColumn("_schema_version", F.lit(schema_ver))
        .withColumn("_source",         F.lit(PIPELINE_ID))
    )
    df_enriched.write.format("delta").mode("append").option("mergeSchema", "true").saveAsTable(TARGET_TABLE)

    rows_written = df_enriched.count()
    runtime      = (datetime.now(timezone.utc) - start).total_seconds()

    logger.info(
        "Ingestion complete. rows=%d runtime=%.2fs schema_version=%s",
        rows_written, runtime, schema_ver,
    )

    # 3. Append cost attribution row
    _log_cost(runtime, rows_written, run_type="incremental")

    return rows_written


def _log_cost(runtime_seconds: float, rows_processed: int, run_type: str):
    DBU_RATE_USD = float(cfg["DBU_RATE_USD"])
    estimated_dbu = runtime_seconds / 3600 * int(cfg["DBU_PER_HOUR"])
    entry = {
        "pipeline_name":      PIPELINE_ID,
        "run_type":           run_type,
        "runtime_seconds":    runtime_seconds,
        "rows_processed":     rows_processed,
        "estimated_dbu":      round(estimated_dbu, 6),
        "estimated_cost_usd": round(estimated_dbu * DBU_RATE_USD, 6),
        "logged_at":          datetime.now(timezone.utc).isoformat(),
        "methodology":        "DBU proxy $0.22/DBU. Free Edition serverless. In prod: use system.billing.usage",
    }
    spark.createDataFrame([entry]).write.format("delta").mode("append").saveAsTable(COST_LOG)


if __name__ == "__main__":
    if _scenario == "v2":
        from _orders_generator import BRONZE_SCHEMA_V2, generate_orders_v2
        orders = generate_orders_v2(500)
        df = spark.createDataFrame(orders, schema=BRONZE_SCHEMA_V2)
    elif _scenario == "v3":
        from _orders_generator import BRONZE_SCHEMA_V3, generate_orders_v3
        orders = generate_orders_v3(500)
        df = spark.createDataFrame(orders, schema=BRONZE_SCHEMA_V3)
    else:
        orders = generate_orders(500)
        df = spark.createDataFrame(orders, schema=BRONZE_SCHEMA)
    rows = ingest(df)
    print(f"Ingested {rows} rows into {TARGET_TABLE}")
