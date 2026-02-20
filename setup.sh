#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$ROOT_DIR/.venv"

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1"
    exit 1
  fi
}

check_python_version() {
  "$PYTHON_BIN" - <<'PY'
import sys
if sys.version_info < (3, 10):
    raise SystemExit("Python 3.10+ is required")
print(f"Python version OK: {sys.version.split()[0]}")
PY
}

check_node_version() {
  major="$(node -v | sed -E 's/^v([0-9]+).*/\1/')"
  if [[ -z "$major" || "$major" -lt 20 ]]; then
    echo "Node.js >= 20 is required (current: $(node -v))"
    exit 1
  fi
  echo "Node version OK: $(node -v)"
}

# ---------------------------------------------------------------------------
# Detect Python: conda env > existing .venv > create new .venv
# ---------------------------------------------------------------------------
detect_python() {
  # 1) If inside an active conda environment, use it directly
  if [[ -n "${CONDA_PREFIX:-}" ]]; then
    PYTHON_BIN="$CONDA_PREFIX/bin/python"
    if [[ -x "$PYTHON_BIN" ]]; then
      echo "Detected active conda env: $CONDA_PREFIX"
      USE_CONDA=1
      return
    fi
  fi

  # 2) Fall back to .venv
  USE_CONDA=0
  if [[ -d "$VENV_DIR" ]]; then
    PYTHON_BIN="$VENV_DIR/bin/python"
  else
    # Will create venv after version check
    PYTHON_BIN="$(command -v python3)"
  fi
}

detect_python

require_cmd npm
require_cmd node

check_python_version
check_node_version

# ---------------------------------------------------------------------------
# Set up Python environment
# ---------------------------------------------------------------------------
if [[ "$USE_CONDA" -eq 1 ]]; then
  echo "Using conda Python: $PYTHON_BIN"
else
  if [[ ! -d "$VENV_DIR" ]]; then
    echo "Creating virtual environment at .venv ..."
    "$PYTHON_BIN" -m venv "$VENV_DIR"
  fi
  PYTHON_BIN="$VENV_DIR/bin/python"
fi

echo "Installing Python dependencies ..."
"$PYTHON_BIN" -m pip install --upgrade pip
"$PYTHON_BIN" -m pip install -r "$ROOT_DIR/agentic-evolution/requirements.txt"
"$PYTHON_BIN" -m pip install -r "$ROOT_DIR/a-evolve-ui/server/requirements.txt"

# Save the resolved Python path so start.sh can reuse it
echo "$PYTHON_BIN" > "$ROOT_DIR/.python_path"

echo "Installing frontend dependencies ..."
(
  cd "$ROOT_DIR/a-evolve-ui"
  npm install
)

# ---------------------------------------------------------------------------
# Download AppWorld benchmark data (if not already present)
# ---------------------------------------------------------------------------
APPWORLD_DATA_DIR="$ROOT_DIR/agentic-evolution/data"
if [[ ! -d "$APPWORLD_DATA_DIR/tasks" ]]; then
  echo "Downloading AppWorld data ..."
  APPWORLD_BIN="$(dirname "$PYTHON_BIN")/appworld"
  if [[ -x "$APPWORLD_BIN" ]]; then
    "$APPWORLD_BIN" download data --root "$ROOT_DIR/agentic-evolution"
  else
    echo "⚠️  appworld CLI not found. Please run manually:"
    echo "    cd agentic-evolution && appworld download data"
  fi
else
  echo "AppWorld data already present, skipping download."
fi

if [[ ! -f "$ROOT_DIR/.env" ]]; then
  cp "$ROOT_DIR/.env.example" "$ROOT_DIR/.env"
  echo "Created .env from .env.example"
fi

echo ""
echo "Setup complete."
echo "Update .env with your API keys before running evolve jobs."
echo "Start the stack with: bash start.sh"
