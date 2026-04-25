"""
generate_data.py
Generates synthetic e-commerce order data and writes it to
Unity Catalog managed table: reliability_engine.bronze.raw_orders
"""

import random
import uuid
from datetime import datetime, timedelta

from pyspark.sql import SparkSession

spark = SparkSession.builder.getOrCreate()

STATUSES  = ["pending", "confirmed", "shipped", "delivered", "cancelled"]
PRODUCTS  = [f"prod_{i:04d}" for i in range(1, 51)]
CUSTOMERS = [f"cust_{i:06d}" for i in range(1, 201)]

TARGET_TABLE = "reliability_engine.bronze.raw_orders"
NUM_ROWS = 500


def generate_orders(n: int, base_time: datetime = None) -> list[dict]:
    if base_time is None:
        base_time = datetime.utcnow()

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


def main():
    orders = generate_orders(NUM_ROWS)
    df = spark.createDataFrame(orders)
    df.write.format("delta").mode("append").saveAsTable(TARGET_TABLE)
    print(f"Written {df.count()} rows to {TARGET_TABLE}")


if __name__ == "__main__":
    main()
