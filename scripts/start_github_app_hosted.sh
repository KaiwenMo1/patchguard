#!/usr/bin/env bash
set -euo pipefail

DB_PATH="${PATCHGUARD_APP_DB_PATH:-.patchguard/github_app/patchguard-app.db}"
MEMORY_DB="${PATCHGUARD_MEMORY_DB:-.patchguard/memory/patchguard-memory.db}"
PORT_VALUE="${PORT:-8000}"
WORKER_INTERVAL="${PATCHGUARD_WORKER_INTERVAL:-10}"

mkdir -p "$(dirname "$DB_PATH")" "$(dirname "$MEMORY_DB")" .patchguard/app_reports .patchguard/app_workspaces

publish_checks=()
if [[ "${PATCHGUARD_PUBLISH_CHECKS:-true}" == "true" ]]; then
  publish_checks=(--publish-checks)
fi

skip_docker=()
if [[ "${PATCHGUARD_SKIP_DOCKER:-true}" == "true" ]]; then
  skip_docker=(--skip-docker)
fi

enable_llm=()
if [[ "${PATCHGUARD_ENABLE_LLM:-false}" == "true" ]]; then
  enable_llm=(--enable-llm)
fi

use_memory=()
if [[ "${PATCHGUARD_USE_MEMORY:-true}" == "true" ]]; then
  use_memory=(--use-memory --memory-db "$MEMORY_DB")
fi

compare_base=()
if [[ "${PATCHGUARD_COMPARE_BASE:-false}" == "true" ]]; then
  compare_base=(--compare-base)
fi

python -m uvicorn patchguard.api_app:app --host 0.0.0.0 --port "$PORT_VALUE" &
api_pid=$!

patchguard app-worker \
  --db-path "$DB_PATH" \
  --poll \
  --interval "$WORKER_INTERVAL" \
  "${publish_checks[@]}" \
  "${skip_docker[@]}" \
  "${enable_llm[@]}" \
  "${use_memory[@]}" \
  "${compare_base[@]}" &
worker_pid=$!

cleanup() {
  kill "$api_pid" "$worker_pid" 2>/dev/null || true
}
trap cleanup INT TERM

wait -n "$api_pid" "$worker_pid"
