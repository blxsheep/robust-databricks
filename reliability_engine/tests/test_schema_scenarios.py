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


# ── Regression: DAB job parameters resolve to existing config files ──────
#
# The bug this guards against: ingest_bronze.py used f"schema_v{scenario}.json"
# while the DAB job passed scenario="v1" (already prefixed) — producing
# "schema_vv1.json" which doesn't exist. CI passed because the scenario tests
# above use a different helper ("1"/"2"/"3" + manual f-string) and never
# exercised the path-construction code in ingest_bronze.py.
#
# This parametrized test crosses the boundary: it uses the exact string
# values from resources/scenario_*.job.yml AND calls into ingest_bronze.py.

@pytest.mark.parametrize("scenario", ["v1", "v2", "v3"])
def test_dab_job_parameter_resolves_to_existing_config(scenario):
    """Each schema_version value used in resources/scenario_*.job.yml must
    resolve to a real on-disk config file via ingest_bronze.resolve_schema_config_path().
    """
    from ingest_bronze import resolve_schema_config_path
    path = resolve_schema_config_path(scenario)
    assert path.exists(), (
        f"schema_version={scenario!r} resolves to {path}, which does not exist. "
        f"This means the DAB job will fail at runtime with FileNotFoundError."
    )


# ── Filesystem references audit ──────────────────────────────────────────
#
# Every workspace path string in the repo — YAML notebook references AND
# the hardcoded /Workspace/.../robust-databricks/... fallbacks in Python
# scripts — must map to a real file or directory in the local repo. The
# Databricks workspace Git folder clones this repo, so if a path exists
# locally it will exist in the workspace; if it doesn't, the pipeline
# fails at runtime.
#
# This catches: renamed/moved files that didn't update all callers,
# typos in path construction, removed directories still referenced.

import re

REPO_ROOT = Path(__file__).parent.parent.parent
RESOURCES_DIR = REPO_ROOT / "resources"
SCRIPTS_DIR = REPO_ROOT / "reliability_engine" / "scripts"

# Matches ${var.workspace_repo_path}/some/path or /Workspace/Users/.../robust-databricks/some/path
_DAB_VAR_RE = re.compile(r"\$\{var\.workspace_repo_path\}/(\S+?)(?:\s|$|\")")
_WORKSPACE_LITERAL_RE = re.compile(
    r'"(/Workspace/Users/[^/]+/robust-databricks/([^"]+))"'
)


def _path_exists_for_notebook_or_dir(rel_path: str) -> bool:
    """notebook_path references omit the .py suffix; dbt_task and other
    references point at directories. Accept either a file (with optional
    .py) or a directory."""
    candidate = REPO_ROOT / rel_path
    if candidate.exists():
        return True
    return candidate.with_suffix(".py").exists()


def test_all_dab_workspace_paths_resolve():
    """Every ${var.workspace_repo_path}/... reference in resources/*.yml
    must point at a real file (with or without .py) or directory in the repo.
    """
    missing = []
    for yml_file in sorted(RESOURCES_DIR.glob("*.yml")):
        for match in _DAB_VAR_RE.finditer(yml_file.read_text()):
            ref = match.group(1).rstrip(",")
            if not _path_exists_for_notebook_or_dir(ref):
                missing.append(f"{yml_file.name} → {ref}")

    assert not missing, (
        "DAB resource files reference paths that don't exist in the repo:\n  "
        + "\n  ".join(missing)
        + "\n\nThis means the pipeline will fail at runtime with "
          "'Unable to access the notebook' or similar."
    )


def test_python_fallback_paths_match_local_files():
    """Every hardcoded /Workspace/Users/.../robust-databricks/... path in
    reliability_engine/scripts/*.py (the try/except NameError fallbacks)
    must map to a real local file or directory.

    These fallback paths are only used when __file__ is undefined (i.e. in
    Databricks notebooks), so they're never exercised by other tests.
    """
    missing = []
    for py_file in sorted(SCRIPTS_DIR.glob("*.py")):
        for match in _WORKSPACE_LITERAL_RE.finditer(py_file.read_text()):
            suffix = match.group(2)  # the part after robust-databricks/
            candidate = REPO_ROOT / suffix
            if not candidate.exists():
                missing.append(f"{py_file.name} → {suffix}")

    assert not missing, (
        "Python fallback paths reference files that don't exist in the repo:\n  "
        + "\n  ".join(missing)
        + "\n\nThese paths only fire in Databricks notebooks (__file__ undefined). "
          "If they break, the failure only surfaces at workspace runtime — never locally."
    )
