#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

CONDA_ENV="${CONDA_ENV:-mem}"
CL_BENCH_DIR="${CL_BENCH_DIR:-/fsx/tianxin/CL-bench}"

conda run -n "$CONDA_ENV" --no-capture-output python evo_harness/cl_bench.py \
    --grouped-path "$CL_BENCH_DIR/CL-bench-grouped.jsonl" \
    --raw-path "$CL_BENCH_DIR/CL-bench.jsonl" \
    --max-samples 500 \
    --max-evolve-turns 1 \
    --no-retest \
    --solver-model 1 \
    --curator-model 1 \
    --max-skills-per-context 5 \
    --max-general-skills 5 \
    --feedback-level 2 \
    --batch-size 16 \
    --batch-workers 16 \
    --output-dir outputs/cl_bench_v14_standard_feedback_all
