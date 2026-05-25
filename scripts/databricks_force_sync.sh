#!/usr/bin/env bash
# Force-syncs a Databricks workspace repo to the latest GitHub branch.
# Handles conflicts by deleting and re-cloning — workspace-only edits will be lost.
#
# Usage:
#   ./scripts/databricks_force_sync.sh
#   ./scripts/databricks_force_sync.sh --branch feature/my-branch
#   ./scripts/databricks_force_sync.sh --path /Users/other@email.com/robust-databricks

set -euo pipefail

REPO_PATH="/Users/c.voranipit@gmail.com/robust-databricks"
REPO_URL="https://github.com/blxsheep/robust-databricks.git"
REPO_PROVIDER="github"
BRANCH="main"

# Parse optional flags
while [[ $# -gt 0 ]]; do
  case $1 in
    --branch) BRANCH="$2"; shift 2 ;;
    --path)   REPO_PATH="$2"; shift 2 ;;
    *) echo "Unknown flag: $1"; exit 1 ;;
  esac
done

echo "==> Resolving repo at: $REPO_PATH"

# Get repo ID from workspace path
REPO_ID=$(databricks workspace get-status "$REPO_PATH" --output json 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin)['object_id'])")

if [[ -z "$REPO_ID" ]]; then
  echo "    Repo not found at path — cloning fresh from GitHub."
else
  echo "    Found repo ID: $REPO_ID"
  echo "==> Attempting pull to branch: $BRANCH"

  if databricks repos update "$REPO_ID" --branch "$BRANCH" 2>/dev/null; then
    HEAD=$(databricks repos get "$REPO_ID" --output json | python3 -c "import sys,json; print(json.load(sys.stdin)['head_commit_id'][:12])")
    echo "    Synced. Head commit: $HEAD"
    exit 0
  fi

  echo "    Conflict detected — workspace edits will be discarded."
  echo "==> Deleting conflicted repo (ID: $REPO_ID)"
  databricks repos delete "$REPO_ID"
fi

echo "==> Cloning from $REPO_URL (branch: $BRANCH)"
RESULT=$(databricks repos create "$REPO_URL" "$REPO_PROVIDER" --path "$REPO_PATH" --output json)
NEW_ID=$(echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['id'])")
HEAD=$(echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['head_commit_id'][:12])")

echo "    Done. Repo ID: $NEW_ID | Head: $HEAD | Branch: $BRANCH"
