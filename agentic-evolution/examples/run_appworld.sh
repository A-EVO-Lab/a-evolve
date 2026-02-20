#!/bin/bash
set -euo pipefail

# Default AppWorld entrypoint.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

export APPWORLD_ROOT="${APPWORLD_ROOT:-$(dirname "$SCRIPT_DIR")}"

python -u run_appworld_claude.py \
  --api-key "${ANTHROPIC_API_KEY:-}" \
  --num-tasks 50 \
  --num-test-tasks 50 \
  --batch-size 2 \
  --main-model claude-sonnet-4-20250514 \
  --proposer-model claude-sonnet-4-5-20250929 \
  --no-eval-vanilla \
  --test-only \
  --session-id appworld_claude-sonnet-4-20250514_train_50_20260219_011927 \
  "$@"
