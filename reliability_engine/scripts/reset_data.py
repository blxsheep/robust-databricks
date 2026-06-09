# Databricks notebook source

"""
reset_data.py
Resets all pipeline tables for a clean demo re-run.

bronze.raw_orders is DROPPED (not truncated) so its schema is fully reset.
The schema sentinel uses the live table schema as its baseline — if
delivery_partner was accepted during a Non-Breaking run, TRUNCATE would
leave that column in the table and corrupt the next Baseline run's comparison.
DROP forces a fresh schema from the v1 seed config on the next ingest.

All other tables are TRUNCATED: their schemas are managed by dbt or are
append-only observability logs with stable schemas.
"""

from pyspark.sql import SparkSession

spark = SparkSession.builder.getOrCreate()

# DROP resets the schema contract back to zero; next run seeds from schema_v1.json.
BRONZE_TABLE = "reliability_engine.bronze.raw_orders"

# TRUNCATE clears rows while preserving dbt-managed or stable schemas.
TRUNCATE_TABLES = [
    "reliability_engine.silver.orders_cleaned",
    "reliability_engine.gold.daily_revenue",
    "reliability_engine.observability.schema_change_log",
    "reliability_engine.observability.incident_log",
    "reliability_engine.observability.sla_check_log",
    "reliability_engine.observability.cost_attribution_log",
]

print("=" * 50)
print("Resetting all pipeline tables...")
print("=" * 50)

try:
    spark.sql(f"DROP TABLE IF EXISTS {BRONZE_TABLE}")
    print(f"  dropped  {BRONZE_TABLE}  (schema + data reset)")
except Exception as e:
    print(f"  skipped  {BRONZE_TABLE}  ({e})")

for table in TRUNCATE_TABLES:
    try:
        spark.sql(f"TRUNCATE TABLE {table}")
        print(f"  cleared  {table}")
    except Exception as e:
        print(f"  skipped  {table}  ({e})")

print("=" * 50)
print("Reset complete. Ready for a clean demo run.")
print("=" * 50)
