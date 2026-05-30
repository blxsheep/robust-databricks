"""
test_schema_scenarios.py
Tests the three schema sentinel scenarios end-to-end:

  Scenario 1 — Baseline (v1):   incoming schema matches expected exactly → pipeline runs clean
  Scenario 2 — Non-breaking (v2): upstream adds delivery_partner column → logged, pipeline continues
  Scenario 3 — Breaking (v3):   upstream removes customer_id → SchemaBreakingChangeError, pipeline halted

No SparkSession required — sentinel classifies in pure Python; Unity Catalog writes are
skipped when spark=None. These tests run in CI without Java or Databricks credentials.
"""

import json
import pytest
from pathlib import Path

from schema_sentinel import SchemaBreakingChangeError, run

CONFIG_DIR = Path(__file__).parent.parent / "config"
EXPECTED_CONFIG = CONFIG_DIR / "schema_v1.json"


def _incoming_from_config(version: str) -> dict[str, str]:
    """Derive an incoming DataFrame schema dict from a schema config file."""
    with open(CONFIG_DIR / f"schema_v{version}.json") as f:
        config = json.load(f)
    return {col["name"]: col["type"] for col in config["columns"]}


# ── Scenario 1: Baseline ───────────────────────────────────────────────────

def test_baseline_schema_passes():
    """v1 incoming matches v1 expected — no changes, pipeline runs clean."""
    result = run(_incoming_from_config("1"), spark=None, config_path=EXPECTED_CONFIG)
    assert result.verdict == "NON_BREAKING"
    assert result.added_columns == []
    assert result.removed_columns == []
    assert result.type_changes == []


# ── Scenario 2: Non-breaking change ───────────────────────────────────────

def test_non_breaking_schema_logged_and_continues():
    """v2 incoming adds delivery_partner — NON_BREAKING, no exception, pipeline continues."""
    result = run(_incoming_from_config("2"), spark=None, config_path=EXPECTED_CONFIG)
    assert result.verdict == "NON_BREAKING"
    assert "delivery_partner" in result.added_columns
    assert result.removed_columns == []
    assert result.type_changes == []


def test_non_breaking_does_not_raise():
    """v2 must not raise — a non-breaking change should never halt the pipeline."""
    try:
        run(_incoming_from_config("2"), spark=None, config_path=EXPECTED_CONFIG)
    except SchemaBreakingChangeError:
        pytest.fail("SchemaBreakingChangeError raised on a non-breaking schema change")


# ── Scenario 3: Breaking change ────────────────────────────────────────────

def test_breaking_schema_halts_pipeline():
    """v3 incoming removes customer_id — BREAKING, SchemaBreakingChangeError raised."""
    with pytest.raises(SchemaBreakingChangeError):
        run(_incoming_from_config("3"), spark=None, config_path=EXPECTED_CONFIG)


def test_breaking_schema_identifies_removed_column():
    """v3 must name the removed column so the incident_log has actionable detail."""
    from schema_sentinel import classify, load_expected_schema
    expected = load_expected_schema(EXPECTED_CONFIG)
    result = classify(_incoming_from_config("3"), expected)
    assert result.verdict == "BREAKING"
    assert "customer_id" in result.removed_columns


def test_breaking_schema_lists_affected_pipelines():
    """v3 must list affected pipelines so on-call knows blast radius immediately."""
    from schema_sentinel import classify, load_expected_schema
    expected = load_expected_schema(EXPECTED_CONFIG)
    result = classify(_incoming_from_config("3"), expected)
    assert len(result.affected_pipelines) > 0
