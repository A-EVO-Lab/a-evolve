#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

# CL-bench baseline with Opus 4.6 (no evolution, no skills)

conda run -n mem --no-capture-output python examples/evolve_cl_bench.py \
    --grouped-path /fsx/tianxin/CL-bench/CL-bench-grouped.jsonl \
    --raw-path /fsx/tianxin/CL-bench/CL-bench.jsonl \
    --max-samples 500 \
    --max-evolve-turns 0 \
    --no-retest \
    --solver-model 1 \
    --curator-model 1 \
    --max-skills-per-context 0 \
    --max-general-skills 0 \
    --feedback-level 2 \
    --batch-size 16 \
    --batch-workers 16 \
    --output-dir outputs/cl_bench_baseline_opus46

# Terminal-Bench baseline with Opus 4.6 (no evolution, no skills)
conda run -n mem --no-capture-output python examples/evolve_terminal.py \
    --solver-model 1 \
    --no-evolve --no-seed-skills \
    --batch-size 5 --workers 5 \
    --output-dir outputs/terminal_baseline_opus46

conda run -n mem --no-capture-output python examples/evolve_swe.py \
    --solver-model 1 \
    --no-evolve --no-seed-skills \
    --batch-size 5 --workers 5 \
    --output-dir outputs/swe_baseline_opus46_retry
