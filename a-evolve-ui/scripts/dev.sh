#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SERVER_DIR="$ROOT_DIR/server"

# Prefer Homebrew "node" formula (modern Node) over node@18 if both exist.
if [[ -d "/opt/homebrew/opt/node/bin" ]]; then
  export PATH="/opt/homebrew/opt/node/bin:$PATH"
elif [[ -d "/opt/homebrew/bin" ]]; then
  export PATH="/opt/homebrew/bin:$PATH"
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required but not found."
  exit 1
fi

if ! command -v npm >/dev/null 2>&1; then
  echo "npm is required but not found."
  exit 1
fi

NODE_MAJOR="$(node -v | sed -E 's/^v([0-9]+).*/\1/')"
if [[ -z "$NODE_MAJOR" || "$NODE_MAJOR" -lt 20 ]]; then
  echo "Node.js >= 20 is required (current: $(node -v))."
  echo "Tip: brew install node"
  exit 1
fi

if [[ ! -d "$ROOT_DIR/node_modules" ]]; then
  echo "Installing frontend dependencies..."
  (cd "$ROOT_DIR" && npm install)
fi

if [[ -f "$SERVER_DIR/requirements.txt" ]]; then
  echo "Ensuring backend dependencies..."
  python3 -m pip install -r "$SERVER_DIR/requirements.txt"
fi

# Check for API keys and print helpful warnings
missing_keys=()
[[ -z "${ANTHROPIC_API_KEY:-}" ]] && missing_keys+=("ANTHROPIC_API_KEY")
[[ -z "${OPENAI_API_KEY:-}" ]]    && missing_keys+=("OPENAI_API_KEY")
[[ -z "${GOOGLE_API_KEY:-}" ]]    && missing_keys+=("GOOGLE_API_KEY")
if [[ ${#missing_keys[@]} -gt 0 ]]; then
  echo ""
  echo "WARNING: The following API keys are NOT set in your shell:"
  for k in "${missing_keys[@]}"; do
    echo "  - $k"
  done
  echo ""
  echo "The 'Start Evolve' feature requires at least one key."
  echo "Set them before running, e.g.:"
  echo "  export ANTHROPIC_API_KEY=sk-ant-..."
  echo ""
fi

echo "Starting backend on http://localhost:8000 ..."
(cd "$SERVER_DIR" && python3 -m uvicorn main:app --reload --port 8000) &
BACKEND_PID=$!

echo "Starting frontend on http://localhost:5173 ..."
(cd "$ROOT_DIR" && npm run dev) &
FRONTEND_PID=$!

cleanup() {
  echo ""
  echo "Stopping services..."
  kill "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || true
}

trap cleanup EXIT INT TERM

echo "A-Evolve Visual UI is running."
echo "Press Ctrl+C to stop both services."
wait
