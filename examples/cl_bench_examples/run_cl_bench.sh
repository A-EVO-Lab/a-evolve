#!/usr/bin/env bash
set -euo pipefail

# CL-bench with A-EVOLVE propose+curator algorithm
#
# CL-bench: continual-learning benchmark with rubric-guided evaluation
# Tasks grouped by context; skills organized per-context + general.
#
# Requires: CL-bench-grouped.jsonl and CL-bench.jsonl data files

GROUPED_PATH="/path/to/CL-bench-grouped.jsonl"
RAW_PATH="/path/to/CL-bench.jsonl"

# ── Baseline: no evolution ────────────────────────────────────────────

# python examples/cl_bench_examples/evolve_cl_bench.py \
#     --grouped-path "$GROUPED_PATH" \
#     --raw-path "$RAW_PATH" \
#     --max-samples 500 \
#     --max-evolve-turns 0 \
#     --solver-model 1 \
#     --batch-size 16 \
#     --batch-workers 16 \
#     --output-dir outputs/cl_bench_baseline

# ── Evolve: propose+curator ──────────────────────────────────────────

python examples/cl_bench_examples/evolve_cl_bench.py \
    --grouped-path "$GROUPED_PATH" \
    --raw-path "$RAW_PATH" \
    --max-samples 500 \
    --max-evolve-turns 1 \
    --no-retest \
    --solver-model 1 \
    --curator-model 1 \
    --max-skills-per-context 5 \
    --max-general-skills 5 \
    --batch-size 16 \
    --batch-workers 16 \
    --output-dir outputs/cl_bench_evolve
