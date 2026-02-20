#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON_PATH_FILE="$ROOT_DIR/.python_path"

# ---------------------------------------------------------------------------
# Resolve the Python binary that setup.sh recorded
# ---------------------------------------------------------------------------
if [[ -f "$PYTHON_PATH_FILE" ]]; then
  PYTHON_BIN="$(cat "$PYTHON_PATH_FILE")"
elif [[ -n "${CONDA_PREFIX:-}" && -x "$CONDA_PREFIX/bin/python" ]]; then
  PYTHON_BIN="$CONDA_PREFIX/bin/python"
elif [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
  PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
else
  echo "No Python environment found. Run: bash setup.sh"
  exit 1
fi

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Python binary not found at $PYTHON_BIN"
  echo "Run: bash setup.sh"
  exit 1
fi

cleanup() {
  echo ""
  echo "Stopping services ..."
  kill "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || true
}

trap cleanup EXIT INT TERM

echo "Using Python: $PYTHON_BIN"
echo "Starting backend at http://localhost:8000 ..."
(
  cd "$ROOT_DIR/a-evolve-ui/server"
  "$PYTHON_BIN" -m uvicorn main:app --reload --port 8000
) &
BACKEND_PID=$!

echo "Starting frontend at http://localhost:5173 ..."
(
  cd "$ROOT_DIR/a-evolve-ui"
  npm run dev
) &
FRONTEND_PID=$!

echo "A-EVOLVE is running. Press Ctrl+C to stop."
wait
