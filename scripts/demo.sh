#!/usr/bin/env bash
set -euo pipefail

WITH_DOCKER=false
REPORT_PATH=".patchguard/quickstart/demo_security_bug.json"

usage() {
  cat <<'EOF'
Usage: ./scripts/demo.sh [--no-docker] [--with-docker] [--out PATH]

Runs a controlled PatchGuard demo without OpenAI credits.

Options:
  --no-docker     Skip Docker execution and write a partial report. This is the default.
  --with-docker   Run Docker-based tests and Ruff/Bandit scans.
  --out PATH      Report path. Defaults to .patchguard/quickstart/demo_security_bug.json.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --no-docker)
      WITH_DOCKER=false
      ;;
    --with-docker)
      WITH_DOCKER=true
      ;;
    --out)
      shift
      if [ "$#" -eq 0 ]; then
        echo "--out requires a path." >&2
        exit 2
      fi
      REPORT_PATH="$1"
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

if ! command -v patchguard >/dev/null 2>&1; then
  echo "patchguard was not found. Run ./scripts/bootstrap.sh first, then activate .venv." >&2
  exit 1
fi

mkdir -p "$(dirname "$REPORT_PATH")"

args=(
  analyze-demo
  examples/demo_security_bug
  --out "$REPORT_PATH"
  --skip-llm
)

if [ "$WITH_DOCKER" = false ]; then
  args+=(--skip-docker)
fi

env -u OPENAI_API_KEY patchguard "${args[@]}"

echo
echo "Demo report written to $REPORT_PATH"
if [ "$WITH_DOCKER" = false ]; then
  echo "Docker was skipped, so test and scan evidence is marked partial/skipped."
else
  echo "Docker evidence was collected from the controlled demo repository."
fi
