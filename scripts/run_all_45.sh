#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

# Run CL-bench, WebArena, Tau-bench with Opus 4.5 (model 3)
# (SWE + Terminal run separately to avoid Docker conflicts)
#
# Phase 0: Baseline (no evolution)
# Phase 1: Evolution

# ══════════════════════════════════════════════════════════════════════
# Phase 0: Baseline (no evolution)
# ══════════════════════════════════════════════════════════════════════

echo "=== Phase 0: Baseline (Opus 4.5, no evolution) ==="

# CL-bench baseline (background)
conda run -n mem --no-capture-output python examples/evolve_cl_bench.py \
    --grouped-path /fsx/tianxin/CL-bench/CL-bench-grouped.jsonl \
    --raw-path /fsx/tianxin/CL-bench/CL-bench.jsonl \
    --max-samples 500 \
    --max-evolve-turns 0 \
    --no-retest \
    --solver-model 3 \
    --curator-model 3 \
    --max-skills-per-context 0 \
    --max-general-skills 0 \
    --feedback-level 2 \
    --batch-size 16 \
    --batch-workers 16 \
    --output-dir outputs/cl_bench_baseline_opus45 &
CL_BASE_PID=$!

# WebArena baseline (background)
WEBARENA_DIR="${WEBARENA_DIR:-/fsx/tianxin/webarena-infinity}"
conda run -n mem --no-capture-output python examples/evolve_webarena_infinity.py \
    --webarena-dir "$WEBARENA_DIR" \
    --web-app superhuman-general \
    --difficulty hard \
    --solver-model 3 \
    --no-evolve --no-seed-skills \
    --batch-size 8 --workers 8 \
    --max-steps 50 --timeout 2400 \
    --base-port 8001 \
    --output-dir outputs/webarena_baseline_opus45 &
WEB_BASE_PID=$!

wait $CL_BASE_PID
echo "=== CL-bench baseline done ==="
wait $WEB_BASE_PID
echo "=== WebArena baseline done ==="

# Tau-bench baseline
conda run -n mem --no-capture-output python examples/evolve_tau_bench.py \
    --env both --task-split test \
    --solver-model 3 --user-model 2 \
    --no-evolve \
    --batch-size 10 --workers 10 \
    --output-dir outputs/tau_bench_baseline_opus45
echo "=== Tau-bench baseline done ==="

echo "=== Phase 0 complete ==="

# ══════════════════════════════════════════════════════════════════════
# Phase 1: Evolution
# ══════════════════════════════════════════════════════════════════════

echo "=== Phase 1: Evolution (Opus 4.5) ==="

# CL-bench evolve (background)
conda run -n mem --no-capture-output python examples/evolve_cl_bench.py \
    --grouped-path /fsx/tianxin/CL-bench/CL-bench-grouped.jsonl \
    --raw-path /fsx/tianxin/CL-bench/CL-bench.jsonl \
    --max-samples 500 \
    --max-evolve-turns 1 \
    --no-retest \
    --solver-model 3 \
    --curator-model 3 \
    --max-skills-per-context 5 \
    --max-general-skills 0 \
    --feedback-level 2 \
    --batch-size 16 \
    --batch-workers 16 \
    --output-dir outputs/cl_bench_v14_opus45 &
CL_PID=$!

# WebArena evolve (background)
conda run -n mem --no-capture-output python examples/evolve_webarena_infinity.py \
    --webarena-dir "$WEBARENA_DIR" \
    --web-app superhuman-general \
    --difficulty hard \
    --solver-model 3 \
    --curator-model 3 \
    --selector-model 2 \
    --batch-size 8 --workers 8 \
    --max-skills-per-topic 5 \
    --max-general-skills 5 \
    --no-seed-skills \
    --max-steps 50 --timeout 2400 \
    --shuffle --shuffle-seed 42 \
    --feedback-level standard \
    --evolve-all \
    --base-port 8001 \
    --output-dir outputs/webarena_v2_opus45 &
WEB_PID=$!

wait $CL_PID
echo "=== CL-bench evolve done ==="
wait $WEB_PID
echo "=== WebArena evolve done ==="

# Tau-bench evolve
conda run -n mem --no-capture-output python examples/evolve_tau_bench.py \
    --env both --task-split test \
    --solver-model 3 --user-model 2 \
    --curator-model 3 --selector-model 2 \
    --batch-size 10 --workers 10 \
    --max-skills-per-topic 0 \
    --max-general-skills 5 \
    --feedback-level standard \
    --shuffle --shuffle-seed 42 \
    --output-dir outputs/tau_bench_v2_opus45
echo "=== Tau-bench evolve done ==="

echo "=== All done (CL + WebArena + Tau) ==="
