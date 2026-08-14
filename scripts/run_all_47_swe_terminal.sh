#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

# Terminal-Bench evolve with Opus 4.7

SOLVER="us.anthropic.claude-opus-4-7"
CURATOR="us.anthropic.claude-opus-4-7"
SELECTOR="us.anthropic.claude-sonnet-4-5-20250929-v1:0"

echo "=== Terminal-Bench Evolution (Opus 4.7) ==="

conda run -n mem --no-capture-output python examples/evolve_terminal.py \
    --solver-model "$SOLVER" \
    --curator-model "$CURATOR" \
    --selector-model "$SELECTOR" \
    --batch-size 10 \
    --shuffle --shuffle-seed 42 \
    --workers 10 \
    --no-seed-skills \
    --max-general-skills 5 \
    --max-skills-per-topic 0 \
    --feedback-level minimal \
    --output-dir outputs_47/terminal_v6_opus47

echo "=== Terminal-Bench evolve done ==="
