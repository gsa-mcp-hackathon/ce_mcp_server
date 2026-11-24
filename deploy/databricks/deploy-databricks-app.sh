#!/usr/bin/env bash
# Deploy this MCP server to Databricks Apps.
#
# Flow:
#   1. Ensure the Databricks App exists (create-or-reuse).
#   2. Sync the repo source to a workspace path (SOURCE_CODE_PATH).
#   3. `databricks apps deploy` from that path.
#
# Databricks builds a container from the synced source + app.yaml, injects
# DATABRICKS_APP_PORT at runtime, and app.py serves MCP over streamable-HTTP at
# :$DATABRICKS_APP_PORT/mcp with a health check at /health.
#
# Prerequisites:
#   - Databricks CLI (v0.205+):  https://docs.databricks.com/dev-tools/cli/install.html
#   - Authenticated:             databricks auth login --host <workspace-url>
#                                (or a configured profile / DATABRICKS_TOKEN)
#   - Apps enabled on your workspace.
#   - Config:                    cp .env.example .env && edit
#
# Usage:
#   set -a; source deploy/databricks/.env; set +a
#   bash deploy/databricks/deploy-databricks-app.sh
#
# Idempotent: creates the app on first run, redeploys on subsequent runs.
set -euo pipefail

# --- Validate config ----------------------------------------------------------
: "${APP_NAME:?set APP_NAME (source deploy/databricks/.env)}"
: "${SOURCE_CODE_PATH:?set SOURCE_CODE_PATH (absolute /Workspace path)}"

# Optional passthroughs to the CLI.
PROFILE_ARGS=()
if [[ -n "${DATABRICKS_CONFIG_PROFILE:-}" ]]; then
  PROFILE_ARGS=(--profile "${DATABRICKS_CONFIG_PROFILE}")
fi
if [[ -n "${DATABRICKS_HOST:-}" ]]; then
  export DATABRICKS_HOST
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

echo "=== Target ==="
echo "  app name:         ${APP_NAME}"
echo "  source code path: ${SOURCE_CODE_PATH}"
echo "  profile:          ${DATABRICKS_CONFIG_PROFILE:-DEFAULT}"
echo "  repo root:        ${REPO_ROOT}"
if [[ -n "${RECORD_WAREHOUSE_ID:-}" ]]; then
  echo "  record store:     Delta tables in ${RECORD_CATALOG:-?}.${RECORD_SCHEMA:-?}"
else
  echo "  record store:     files in the container (lost on restart)"
fi
echo ""

# --- Confirm the CLI is installed and authenticated ---------------------------
if ! command -v databricks >/dev/null 2>&1; then
  echo "FATAL: 'databricks' CLI not found." >&2
  echo "       Install: https://docs.databricks.com/dev-tools/cli/install.html" >&2
  exit 1
fi
if ! databricks "${PROFILE_ARGS[@]}" current-user me >/dev/null 2>&1; then
  echo "FATAL: not authenticated. Run:" >&2
  echo "       databricks auth login --host <workspace-url>" >&2
  exit 1
fi

# --- app.yaml must sit at the source root Databricks deploys from -------------
# Databricks Apps reads app.yaml from the deployed source directory. We keep the
# canonical copy in this kit and stage it at the repo root for the sync so the
# deploy sees it without polluting the repo. (Removed after deploy.)
STAGED_APP_YAML="${REPO_ROOT}/app.yaml"
CLEANUP_APP_YAML=0
if [[ ! -f "${STAGED_APP_YAML}" ]]; then
  cp "${SCRIPT_DIR}/app.yaml" "${STAGED_APP_YAML}"
  CLEANUP_APP_YAML=1
fi
cleanup() {
  if [[ "${CLEANUP_APP_YAML}" == "1" ]]; then
    rm -f "${STAGED_APP_YAML}"
  fi
}
trap cleanup EXIT

# --- Record store: Delta tables behind a SQL warehouse (optional) -------------
# With RECORD_WAREHOUSE_ID set, the staged app.yaml gets the settings that keep
# request, resolved and outcome records in Delta tables. Without it, records
# are files in the app's container and are lost when the app restarts.
RECORD_STORE=0
if [[ -n "${RECORD_WAREHOUSE_ID:-}" ]]; then
  : "${RECORD_CATALOG:?set RECORD_CATALOG (the catalog of the record tables)}"
  : "${RECORD_SCHEMA:?set RECORD_SCHEMA (the schema of the record tables)}"
  RECORD_TABLE_PREFIX="${RECORD_TABLE_PREFIX-ce_mcp_}"
  for value in "${RECORD_WAREHOUSE_ID}" "${RECORD_CATALOG}" "${RECORD_SCHEMA}" \
    "${RECORD_TABLE_PREFIX}"; do
    if [[ ! "${value}" =~ ^[A-Za-z0-9_-]*$ ]]; then
      echo "FATAL: the RECORD_* values must be plain names." >&2
      exit 1
    fi
  done
  if [[ "${CLEANUP_APP_YAML}" == "1" ]]; then
    cat >>"${STAGED_APP_YAML}" <<EOF

env:
  - name: CE_CAPTURE_STORE
    value: "delta"
  - name: CE_CAPTURE_WAREHOUSE_ID
    value: "${RECORD_WAREHOUSE_ID}"
  - name: CE_CAPTURE_STORE_CATALOG
    value: "${RECORD_CATALOG}"
  - name: CE_CAPTURE_STORE_SCHEMA
    value: "${RECORD_SCHEMA}"
  - name: CE_CAPTURE_STORE_PREFIX
    value: "${RECORD_TABLE_PREFIX}"
EOF
    RECORD_STORE=1
  else
    echo "WARNING: ${STAGED_APP_YAML} exists already, so the RECORD_* settings" >&2
    echo "         were not added to it. Add the CE_CAPTURE_STORE_* values" >&2
    echo "         there yourself, or move the file away." >&2
  fi
fi

# --- Ensure the app exists (create-or-reuse) ----------------------------------
echo "=== Ensuring Databricks App '${APP_NAME}' ==="
if databricks "${PROFILE_ARGS[@]}" apps get "${APP_NAME}" >/dev/null 2>&1; then
  echo "  app exists — reusing."
else
  echo "  app not found — creating '${APP_NAME}'..."
  databricks "${PROFILE_ARGS[@]}" apps create "${APP_NAME}"
fi

# --- Sync the repo source to the workspace path -------------------------------
# `databricks sync` uploads the repo (respecting .gitignore) to SOURCE_CODE_PATH.
# data/parquet is git-ignored, so it is included by name: without it the app
# starts but run_request reports that no data is loaded.
echo "=== Syncing source to ${SOURCE_CODE_PATH} ==="
if [[ ! -d "${REPO_ROOT}/data/parquet" ]]; then
  echo "  WARNING: data/parquet not found; run 'ce-ingest load' first," >&2
  echo "           or run_request will report that no data is loaded." >&2
fi
databricks "${PROFILE_ARGS[@]}" sync --include "data/parquet/**" \
  "${REPO_ROOT}" "${SOURCE_CODE_PATH}"

# --- Deploy -------------------------------------------------------------------
echo "=== Deploying app '${APP_NAME}' from ${SOURCE_CODE_PATH} ==="
databricks "${PROFILE_ARGS[@]}" apps deploy "${APP_NAME}" \
  --source-code-path "${SOURCE_CODE_PATH}"

# --- Report the app URL -------------------------------------------------------
APP_URL="$(databricks "${PROFILE_ARGS[@]}" apps get "${APP_NAME}" \
  --output json 2>/dev/null | grep -o '"url"[^,]*' | head -1 | cut -d'"' -f4 || true)"
echo ""
echo "=== Deployed ==="
if [[ -n "${APP_URL}" ]]; then
  echo "  App URL:      ${APP_URL}"
  echo "  MCP endpoint: ${APP_URL}/mcp"
  echo "  Health:       ${APP_URL}/health"
  echo ""
  echo "Verify with:"
  echo "  bash deploy/databricks/smoke-test.sh ${APP_URL}"
else
  echo "  Deployed. Get the URL with:"
  echo "    databricks ${PROFILE_ARGS[*]} apps get ${APP_NAME}"
fi

# --- The record tables and the app's access to them ---------------------------
if [[ "${RECORD_STORE}" == "1" ]]; then
  SP_ID="$(databricks "${PROFILE_ARGS[@]}" apps get "${APP_NAME}" \
    --output json 2>/dev/null | grep -o '"service_principal_client_id"[^,]*' \
    | head -1 | cut -d'"' -f4 || true)"
  echo ""
  echo "=== Record store ==="
  echo "  Once, create the tables and let the app read and write them:"
  echo "    CE_CAPTURE_DATABRICKS_HOST=<workspace-url> \\"
  echo "    CE_CAPTURE_DATABRICKS_PROFILE=${DATABRICKS_CONFIG_PROFILE:-DEFAULT} \\"
  echo "    CE_CAPTURE_WAREHOUSE_ID=${RECORD_WAREHOUSE_ID} \\"
  echo "    uv run ce-capture store-ddl --apply \\"
  echo "      --catalog ${RECORD_CATALOG} --schema ${RECORD_SCHEMA} \\"
  echo "      --prefix '${RECORD_TABLE_PREFIX}' \\"
  echo "      --grant-to ${SP_ID:-<service principal client ID>}"
  echo "  The app's service principal also needs 'Can use' on the warehouse:"
  echo "  see deploy/databricks/README.md, 'Record store'."
fi
