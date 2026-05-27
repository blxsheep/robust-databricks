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
from pyspark.sql.types import (
    DoubleType, IntegerType, StringType, StructField, StructType, TimestampType,
)
from schema_sentinel import SchemaBreakingChangeError, run as sentinel_run

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

spark = SparkSession.builder.getOrCreate()

try:
    CONFIG_PATH = Path(__file__).parent.parent / "config" / "schema_config.json"
except NameError:
    # Running in notebook/REPL where __file__ is not defined
    CONFIG_PATH = Path("/Workspace/Users/c.voranipit@gmail.com/robust-databricks/reliability_engine/config/schema_config.json")

TARGET_TABLE = "reliability_engine.bronze.raw_orders"
COST_LOG     = "reliability_engine.observability.cost_attribution_log"
PIPELINE_ID  = "ingest_bronze_v1"

# Explicit schema matching schema_config.json v1.
# Passed to createDataFrame() to prevent Spark from inferring Python int as LongType.
BRONZE_SCHEMA = StructType([
    StructField("order_id",    StringType(),    nullable=False),
    StructField("customer_id", StringType(),    nullable=False),
    StructField("product_id",  StringType(),    nullable=False),
    StructField("quantity",    IntegerType(),   nullable=False),
    StructField("unit_price",  DoubleType(),    nullable=False),
    StructField("status",      StringType(),    nullable=False),
    StructField("created_at",  TimestampType(), nullable=False),
    StructField("updated_at",  TimestampType(), nullable=False),
])

def _schema_version() -> str:
    with open(CONFIG_PATH) as f:
        return json.load(f).get("version", "unknown")


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
    sentinel_run(incoming_schema, spark=spark)

    # 2. Attach system metadata columns
    schema_ver  = _schema_version()
    ingested_at = datetime.now(timezone.utc)

    df_enriched = (
        df
        .withColumn("_ingested_at",    F.lit(ingested_at).cast("timestamp"))
        .withColumn("_schema_version", F.lit(schema_ver))
        .withColumn("_source",         F.lit(PIPELINE_ID))
    )
    df_enriched.write.format("delta").mode("append").saveAsTable(TARGET_TABLE)

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
    DBU_RATE_USD = 0.22
    estimated_dbu = runtime_seconds / 3600 * 2  # ~2 DBU/hr serverless
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
    from generate_data import generate_orders
    orders = generate_orders(500)
    df = spark.createDataFrame(orders, schema=BRONZE_SCHEMA)
    rows = ingest(df)
    print(f"Ingested {rows} rows into {TARGET_TABLE}")
