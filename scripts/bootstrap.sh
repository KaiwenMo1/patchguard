#!/usr/bin/env bash
set -euo pipefail

WITH_DOCKER=false
INSTALL_FRONTEND=false

usage() {
  cat <<'EOF'
Usage: ./scripts/bootstrap.sh [--no-docker] [--with-docker] [--frontend]

Sets up PatchGuard for local use.

Options:
  --no-docker     Install Python dependencies only. This is the default.
  --with-docker   Also build the Docker sandbox image for full evidence runs.
  --frontend      Also install frontend npm dependencies.
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
    --frontend)
      INSTALL_FRONTEND=true
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

if ! command -v python >/dev/null 2>&1; then
  echo "python was not found. Install Python 3.11+ first." >&2
  exit 1
fi

if [ ! -d .venv ]; then
  python -m venv .venv
fi

if [ -f .venv/bin/activate ]; then
  . .venv/bin/activate
elif [ -f .venv/Scripts/activate ]; then
  . .venv/Scripts/activate
else
  echo "Could not find the virtualenv activation script under .venv." >&2
  exit 1
fi

python -m pip install --upgrade pip
python -m pip install -e ".[dev]"

if [ "$WITH_DOCKER" = true ]; then
  if ! command -v docker >/dev/null 2>&1; then
    echo "Docker was requested but the docker command was not found." >&2
    exit 1
  fi
  docker build -t patchguard-python-sandbox:latest -f sandbox/python/Dockerfile sandbox/python
else
  echo "Skipping Docker sandbox build. Use --with-docker for full test and scan evidence."
fi

if [ "$INSTALL_FRONTEND" = true ]; then
  if ! command -v npm >/dev/null 2>&1; then
    echo "npm was requested but was not found." >&2
    exit 1
  fi
  (cd frontend && npm ci)
fi

cat <<'EOF'

PatchGuard setup complete.

Try a no-Docker demo:
  . .venv/bin/activate
  ./scripts/demo.sh --no-docker

Try a full Docker evidence demo:
  . .venv/bin/activate
  ./scripts/demo.sh --with-docker
EOF
