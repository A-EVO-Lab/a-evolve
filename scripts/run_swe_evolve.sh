#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

CONDA_ENV="${CONDA_ENV:-mem}"

conda run -n "$CONDA_ENV" --no-capture-output python evo_harness/swe_bench.py \
    --solver-model 1 \
    --curator-model 1 \
    --batch-size 16 --workers 16 \
    --max-skills-per-topic 5 \
    --max-general-skills 5 \
    --shuffle --shuffle-seed 42 \
    --eval-timeout 300 \
    --feedback-level standard \
    --output-dir outputs/swe_v2_minimal_feedback_gating
