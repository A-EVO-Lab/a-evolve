#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

# Run CL-bench evolve, WebArena evolve, then Tau-bench (baseline + evolve) with Opus 4.7

SOLVER="us.anthropic.claude-opus-4-7"
CURATOR="us.anthropic.claude-opus-4-7"
SELECTOR="us.anthropic.claude-sonnet-4-5-20250929-v1:0"
USER_MODEL="us.anthropic.claude-sonnet-4-5-20250929-v1:0"
WEBARENA_DIR="${WEBARENA_DIR:-/fsx/tianxin/webarena-infinity}"

# ══════════════════════════════════════════════════════════════════════
# CL-bench evolve + WebArena evolve (parallel)
# ══════════════════════════════════════════════════════════════════════

echo "=== CL-bench + WebArena evolve (Opus 4.7) ==="

conda run -n mem --no-capture-output python examples/evolve_cl_bench.py \
    --grouped-path /fsx/tianxin/CL-bench/CL-bench-grouped.jsonl \
    --raw-path /fsx/tianxin/CL-bench/CL-bench.jsonl \
    --max-samples 500 \
    --max-evolve-turns 1 \
    --no-retest \
    --solver-model "$SOLVER" \
    --curator-model "$CURATOR" \
    --max-skills-per-context 5 \
    --max-general-skills 5 \
    --feedback-level 2 \
    --batch-size 16 \
    --batch-workers 16 \
    --output-dir outputs_47/cl_bench_v14_opus47 &
CL_PID=$!

conda run -n mem --no-capture-output python examples/evolve_webarena_infinity.py \
    --webarena-dir "$WEBARENA_DIR" \
    --web-app superhuman-general \
    --difficulty hard \
    --solver-model "$SOLVER" \
    --curator-model "$CURATOR" \
    --selector-model "$SELECTOR" \
    --batch-size 8 --workers 8 \
    --max-skills-per-topic 5 \
    --max-general-skills 5 \
    --no-seed-skills \
    --max-steps 50 --timeout 2400 \
    --shuffle --shuffle-seed 42 \
    --feedback-level standard \
    --evolve-all \
    --base-port 8301 \
    --output-dir outputs_47/webarena_v2_opus47 &
WEB_PID=$!

wait $CL_PID
echo "=== CL-bench evolve done ==="
wait $WEB_PID
echo "=== WebArena evolve done ==="

# ══════════════════════════════════════════════════════════════════════
# Tau-bench baseline + evolve (sequential, after WebArena frees resources)
# ══════════════════════════════════════════════════════════════════════

echo "=== Tau-bench Baseline (Opus 4.7) ==="

conda run -n mem --no-capture-output python examples/evolve_tau_bench.py \
    --env both --task-split test \
    --solver-model "$SOLVER" --user-model "$USER_MODEL" \
    --no-evolve \
    --batch-size 10 --workers 10 \
    --output-dir outputs_47/tau_bench_baseline_opus47

echo "=== Tau-bench baseline done ==="

echo "=== Tau-bench Evolution (Opus 4.7) ==="

conda run -n mem --no-capture-output python examples/evolve_tau_bench.py \
    --env both --task-split test \
    --solver-model "$SOLVER" --user-model "$USER_MODEL" \
    --curator-model "$CURATOR" --selector-model "$SELECTOR" \
    --batch-size 10 --workers 10 \
    --max-skills-per-topic 0 \
    --max-general-skills 5 \
    --feedback-level standard \
    --shuffle --shuffle-seed 42 \
    --output-dir outputs_47/tau_bench_v2_opus47

echo "=== Tau-bench evolve done ==="

echo "=== All done (Opus 4.7) ==="
