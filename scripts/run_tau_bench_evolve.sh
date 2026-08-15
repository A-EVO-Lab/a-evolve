#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

CONDA_ENV="${CONDA_ENV:-mem}"

conda run -n "$CONDA_ENV" --no-capture-output python evo_harness/tau_bench.py \
    --env both --task-split test \
    --solver-model 1 --user-model 2 \
    --curator-model 1 --selector-model 2 \
    --batch-size 10 --workers 10 \
    --max-skills-per-topic 0 \
    --max-general-skills 5 \
    --feedback-level standard \
    --shuffle --shuffle-seed 42 \
    --output-dir outputs_check/tau_bench_v2_general_only_gate_reset
