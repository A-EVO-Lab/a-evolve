#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

CONDA_ENV="${CONDA_ENV:-mem}"
WEBARENA_DIR="${WEBARENA_DIR:-/fsx/tianxin/webarena-infinity}"

conda run -n "$CONDA_ENV" --no-capture-output python evo_harness/webarena_infinity.py \
    --webarena-dir "$WEBARENA_DIR" \
    --web-app superhuman-general \
    --difficulty hard \
    --solver-model 1 \
    --curator-model 1 \
    --selector-model 2 \
    --batch-size 8 --workers 8 \
    --max-skills-per-topic 5 \
    --max-general-skills 5 \
    --no-seed-skills \
    --max-steps 50 --timeout 2400 \
    --shuffle --shuffle-seed 42 \
    --feedback-level standard \
    --evolve-all \
    --output-dir outputs_check/webarena_v2_evolve_all_more_timeout_gate_reset
