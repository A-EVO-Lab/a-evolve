#!/bin/bash
# Run AppWorld with GPT (GPT-5 or GPT-5-mini)

cd "$(dirname "$0")"

# Configuration
export OPENAI_API_KEY="${OPENAI_API_KEY:-}"

# Model options:
# - gpt-5 (default)
# - gpt-5-mini
MODEL="${MODEL:-gpt-5}"
NUM_TASKS="${NUM_TASKS:-50}"

echo "=============================================="
echo "Running AppWorld with OpenAI GPT"
echo "Model: $MODEL"
echo "Tasks: $NUM_TASKS"
echo "=============================================="

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
export APPWORLD_ROOT="${APPWORLD_ROOT:-$(dirname "$SCRIPT_DIR")}"

python -u run_appworld_gpt.py \
  --api-key "$OPENAI_API_KEY" \
  --num-tasks $NUM_TASKS \
  --num-test-tasks $NUM_TASKS \
  --test-dataset test_normal \
  --batch-size 2 \
  --main-model "$MODEL" \
  --proposer-model claude-sonnet-4-5-20250929 \
  --no-eval-vanilla \
  "$@"