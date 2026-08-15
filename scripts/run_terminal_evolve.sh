#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

CONDA_ENV="${CONDA_ENV:-mem}"

conda run -n "$CONDA_ENV" --no-capture-output python evo_harness/terminal_bench.py \
    --solver-model 1 \
    --curator-model 1 \
    --batch-size 10 \
    --shuffle --shuffle-seed 42 \
    --workers 10 \
    --max-general-skills 5 \
    --max-skills-per-topic 0 \
    --feedback-level minimal \
    --output-dir outputs/terminal_v6_standard_retry
