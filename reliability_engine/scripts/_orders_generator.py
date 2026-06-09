"""
_orders_generator.py
Shared synthetic order data helpers. No notebook header — stays a plain importable module.
Imported by both generate_data.py (notebook task) and ingest_bronze.py (__main__ block).
"""

import random
import uuid
from datetime import datetime, timedelta, timezone

from pyspark.sql.types import (
    DoubleType, IntegerType, StringType, StructField, StructType, TimestampType,
)

STATUSES          = ["pending", "confirmed", "shipped", "delivered", "cancelled"]
PRODUCTS          = [f"prod_{i:04d}" for i in range(1, 51)]
CUSTOMERS         = [f"cust_{i:06d}" for i in range(1, 201)]
DELIVERY_PARTNERS = ["DHL", "FedEx", "UPS", "USPS", "OnTrac"]

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

# v2: upstream adds delivery_partner (non-breaking)
BRONZE_SCHEMA_V2 = StructType([
    StructField("order_id",          StringType(),    nullable=False),
    StructField("customer_id",       StringType(),    nullable=False),
    StructField("product_id",        StringType(),    nullable=False),
    StructField("quantity",          IntegerType(),   nullable=False),
    StructField("unit_price",        DoubleType(),    nullable=False),
    StructField("status",            StringType(),    nullable=False),
    StructField("created_at",        TimestampType(), nullable=False),
    StructField("updated_at",        TimestampType(), nullable=False),
    StructField("delivery_partner",  StringType(),    nullable=True),
])

# v3: upstream drops customer_id (breaking)
BRONZE_SCHEMA_V3 = StructType([
    StructField("order_id",   StringType(),    nullable=False),
    StructField("product_id", StringType(),    nullable=False),
    StructField("quantity",   IntegerType(),   nullable=False),
    StructField("unit_price", DoubleType(),    nullable=False),
    StructField("status",     StringType(),    nullable=False),
    StructField("created_at", TimestampType(), nullable=False),
    StructField("updated_at", TimestampType(), nullable=False),
])


def generate_orders(n: int, base_time: datetime = None) -> list[dict]:
    if base_time is None:
        base_time = datetime.now(timezone.utc)

    rows = []
    for _ in range(n):
        created = base_time - timedelta(hours=random.randint(0, 72))
        rows.append({
            "order_id":    str(uuid.uuid4()),
            "customer_id": random.choice(CUSTOMERS),
            "product_id":  random.choice(PRODUCTS),
            "quantity":    random.randint(1, 20),
            "unit_price":  round(random.uniform(5.0, 500.0), 2),
            "status":      random.choice(STATUSES),
            "created_at":  created,
            "updated_at":  created + timedelta(minutes=random.randint(0, 120)),
        })
    return rows


def generate_orders_v2(n: int, base_time: datetime = None) -> list[dict]:
    """Simulates non-breaking upstream change: delivery_partner column added."""
    if base_time is None:
        base_time = datetime.now(timezone.utc)
    rows = []
    for _ in range(n):
        created = base_time - timedelta(hours=random.randint(0, 72))
        rows.append({
            "order_id":         str(uuid.uuid4()),
            "customer_id":      random.choice(CUSTOMERS),
            "product_id":       random.choice(PRODUCTS),
            "quantity":         random.randint(1, 20),
            "unit_price":       round(random.uniform(5.0, 500.0), 2),
            "status":           random.choice(STATUSES),
            "created_at":       created,
            "updated_at":       created + timedelta(minutes=random.randint(0, 120)),
            "delivery_partner": random.choice(DELIVERY_PARTNERS),
        })
    return rows


def generate_orders_v3(n: int, base_time: datetime = None) -> list[dict]:
    """Simulates breaking upstream change: customer_id column dropped."""
    if base_time is None:
        base_time = datetime.now(timezone.utc)
    rows = []
    for _ in range(n):
        created = base_time - timedelta(hours=random.randint(0, 72))
        rows.append({
            "order_id":   str(uuid.uuid4()),
            "product_id": random.choice(PRODUCTS),
            "quantity":   random.randint(1, 20),
            "unit_price": round(random.uniform(5.0, 500.0), 2),
            "status":     random.choice(STATUSES),
            "created_at": created,
            "updated_at": created + timedelta(minutes=random.randint(0, 120)),
        })
    return rows
