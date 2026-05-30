# Databricks notebook source

"""
reset_data.py
Truncates all pipeline tables for a clean demo re-run.
Table schemas are preserved — only rows are deleted.
"""

from pyspark.sql import SparkSession

spark = SparkSession.builder.getOrCreate()

TABLES = [
    "reliability_engine.bronze.raw_orders",
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

for table in TABLES:
    try:
        spark.sql(f"TRUNCATE TABLE {table}")
        print(f"  cleared  {table}")
    except Exception as e:
        print(f"  skipped  {table}  ({e})")

print("=" * 50)
print("Reset complete. Ready for a clean demo run.")
print("=" * 50)
