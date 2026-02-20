#!/bin/bash
# Run AppWorld with Google Gemini
#
# Uses Gemini 3 Flash as main model and Claude Sonnet as proposer model

cd "$(dirname "$0")"

# API Keys - need both for multi-provider setup
export GOOGLE_API_KEY="${GOOGLE_API_KEY:-}"
export ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-}"

# Model options:
# - gemini-3-flash-preview (default)
# - gemini-2.0-flash-exp
# - gemini-1.5-pro
MODEL="${MODEL:-claude-haiku-4-5-20251001}"
PROPOSER_MODEL="${PROPOSER_MODEL:-gemini-3-flash-preview}"
NUM_TASKS="${NUM_TASKS:-50}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
export APPWORLD_ROOT="${APPWORLD_ROOT:-$(dirname "$SCRIPT_DIR")}"

echo "=============================================="
echo "Running AppWorld with Google Gemini"
echo "Main Model: $MODEL"
echo "Proposer Model: $PROPOSER_MODEL"
echo "Tasks: $NUM_TASKS"
echo "=============================================="

# Pick API key based on main model type
if [[ "$MODEL" == claude* ]]; then
  API_KEY="$ANTHROPIC_API_KEY"
else
  API_KEY="$GOOGLE_API_KEY"
fi

python -u run_appworld_gpt.py \
  --api-key "$API_KEY" \
  --num-tasks $NUM_TASKS \
  --num-test-tasks $NUM_TASKS \
  --test-dataset test_normal \
  --batch-size 2 \
  --main-model "$MODEL" \
  --proposer-model "$PROPOSER_MODEL" \
  --no-eval-vanilla \
  "$@"