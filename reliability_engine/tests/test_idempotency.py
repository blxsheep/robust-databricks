"""
test_idempotency.py
Asserts that running the incremental pipeline twice on identical input
produces identical output — same row count, no duplicate order_ids.

Uses PySpark + delta-spark locally (no Databricks connection required).
"""

import pytest
from datetime import datetime
from pyspark.sql import SparkSession
from pyspark.sql import functions as F
from delta import configure_spark_with_delta_pip


@pytest.fixture(scope="session")
def spark():
    builder = (
        SparkSession.builder
        .master("local[2]")
        .appName("idempotency_test")
        .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
        .config("spark.sql.catalog.spark_catalog", "org.apache.spark.sql.delta.catalog.DeltaCatalog")
    )
    session = configure_spark_with_delta_pip(builder).getOrCreate()
    session.sparkContext.setLogLevel("ERROR")
    yield session
    session.stop()


@pytest.fixture
def sample_orders(spark):
    now = datetime.utcnow()
    data = [
        ("ord-001", "cust-001", "prod-001", 2, 49.99, "confirmed",  now, now),
        ("ord-002", "cust-002", "prod-002", 1, 199.0, "shipped",    now, now),
        ("ord-003", "cust-003", "prod-003", 5, 12.50, "delivered",  now, now),
    ]
    schema = [
        "order_id", "customer_id", "product_id",
        "quantity", "unit_price", "status", "created_at", "updated_at",
    ]
    return spark.createDataFrame(data, schema)


def _run_incremental(spark, input_df, target_path: str):
    """Simulates the incremental merge on order_id."""
    from delta.tables import DeltaTable

    if DeltaTable.isDeltaTable(spark, target_path):
        dt = DeltaTable.forPath(spark, target_path)
        (
            dt.alias("target")
            .merge(input_df.alias("source"), "target.order_id = source.order_id")
            .whenMatchedUpdateAll()
            .whenNotMatchedInsertAll()
            .execute()
        )
    else:
        input_df.write.format("delta").save(target_path)


def test_incremental_idempotent(spark, sample_orders, tmp_path):
    target = str(tmp_path / "silver_orders")

    # Run 1
    _run_incremental(spark, sample_orders, target)
    count_after_run1 = spark.read.format("delta").load(target).count()

    # Run 2 — identical input
    _run_incremental(spark, sample_orders, target)
    count_after_run2 = spark.read.format("delta").load(target).count()

    assert count_after_run1 == count_after_run2, (
        f"Row count changed between runs: {count_after_run1} → {count_after_run2}"
    )


def test_no_duplicate_order_ids(spark, sample_orders, tmp_path):
    target = str(tmp_path / "silver_orders_dedup")

    _run_incremental(spark, sample_orders, target)
    _run_incremental(spark, sample_orders, target)

    df = spark.read.format("delta").load(target)
    duplicates = (
        df.groupBy("order_id")
        .count()
        .filter(F.col("count") > 1)
    )
    assert duplicates.count() == 0, "Duplicate order_ids found after two incremental runs"
