"""
sla_monitor.py
Runs three SLA checks against the Bronze table and appends results
to reliability_engine.observability.sla_check_log.

Thresholds and business_impact strings are driven by config/sla_config.json.
Changing SLA rules is a config edit, not a code change.
"""

import json
import logging
from datetime import datetime
from pathlib import Path

from pyspark.sql import SparkSession, functions as F
from schema_sentinel import load_expected_schema, classify

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

spark = SparkSession.builder.getOrCreate()

CONFIG_PATH  = Path(__file__).parent.parent / "config" / "sla_config.json"
BRONZE_TABLE = "reliability_engine.bronze.raw_orders"
SLA_LOG      = "reliability_engine.observability.sla_check_log"


def load_config() -> list[dict]:
    with open(CONFIG_PATH) as f:
        return json.load(f)["sla_checks"]


def check_freshness(threshold_hours: int) -> tuple[str, str | None]:
    """Latest updated_at in Bronze must be within threshold_hours of now."""
    latest = spark.table(BRONZE_TABLE).agg(F.max("updated_at")).collect()[0][0]
    if latest is None:
        return "FAIL", "No data in Bronze table"
    age_hours = (datetime.utcnow() - latest).total_seconds() / 3600
    if age_hours > threshold_hours:
        return "FAIL", f"Data is {age_hours:.1f}h old, threshold={threshold_hours}h"
    return "PASS", None


def check_completeness(min_rows: int) -> tuple[str, str | None]:
    """Bronze row count for today's ingestion must meet minimum."""
    today = str(datetime.utcnow().date())
    count = (
        spark.table(BRONZE_TABLE)
        .filter(F.to_date("_ingested_at") == today)
        .count()
    )
    if count < min_rows:
        return "FAIL", f"Row count {count} below minimum {min_rows}"
    return "PASS", None


def check_schema_consistency() -> tuple[str, str | None]:
    """Live Bronze schema must still match schema_config.json."""
    expected = load_expected_schema()
    actual = {
        f.name: f.dataType.simpleString()
        for f in spark.table(BRONZE_TABLE).schema.fields
        if not f.name.startswith("_")
    }
    result = classify(actual, expected)
    if result.verdict == "BREAKING":
        return "FAIL", f"removed={result.removed_columns} type_changes={result.type_changes}"
    return "PASS", None


def run() -> list[dict]:
    config = load_config()
    check_map = {
        "freshness":          lambda c: check_freshness(c["threshold_hours"]),
        "completeness":       lambda c: check_completeness(c["min_rows_per_run"]),
        "schema_consistency": lambda c: check_schema_consistency(),
    }

    log_entries = []
    for check in config:
        name   = check["check_name"]
        impact = check["business_impact"]
        fn     = check_map.get(name)
        if fn is None:
            logger.warning("Unknown check: %s", name)
            continue

        status, detail = fn(check)
        entry = {
            "check_name":      name,
            "status":          status,
            "detail":          detail or "",
            "business_impact": impact,
            "checked_at":      datetime.utcnow().isoformat(),
        }
        log_entries.append(entry)
        logger.info("SLA [%s]: %s — %s", name, status, impact)

    spark.createDataFrame(log_entries).write.format("delta").mode("append").saveAsTable(SLA_LOG)
    return log_entries


if __name__ == "__main__":
    results = run()
    for r in results:
        print(f"{r['check_name']}: {r['status']}  {r['detail'] or ''}")
