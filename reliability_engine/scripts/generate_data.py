# Databricks notebook source

"""
generate_data.py
Generates 500 synthetic e-commerce orders and appends them to
reliability_engine.bronze.raw_orders.
"""

from pyspark.sql import SparkSession
from _orders_generator import BRONZE_SCHEMA, generate_orders
from _config import cfg

spark = SparkSession.builder.getOrCreate()

TARGET_TABLE = cfg["BRONZE_TABLE"]
NUM_ROWS     = int(cfg["NUM_ROWS"])


def main():
    orders = generate_orders(NUM_ROWS)
    df = spark.createDataFrame(orders, schema=BRONZE_SCHEMA)
    df.write.format("delta").mode("append").saveAsTable(TARGET_TABLE)
    print(f"Written {df.count()} rows to {TARGET_TABLE}")


if __name__ == "__main__":
    main()
