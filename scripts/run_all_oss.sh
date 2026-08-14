#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

# Run CL-bench, WebArena, Tau-bench with GPT-OSS 120B
# (SWE + Terminal run separately to avoid Docker conflicts)

SOLVER="openai.gpt-oss-120b-1:0"
CURATOR="openai.gpt-oss-120b-1:0"
SELECTOR="us.anthropic.claude-sonnet-4-5-20250929-v1:0"
USER_MODEL="us.anthropic.claude-sonnet-4-5-20250929-v1:0"
WEBARENA_DIR="${WEBARENA_DIR:-/fsx/tianxin/webarena-infinity}"

# ══════════════════════════════════════════════════════════════════════
# Phase 0: Baseline (no evolution)
# ══════════════════════════════════════════════════════════════════════

echo "=== Phase 0: CL-bench & WebArena baselines already done, skipping ==="

# conda run -n mem --no-capture-output python examples/evolve_cl_bench.py \
#     --grouped-path /fsx/tianxin/CL-bench/CL-bench-grouped.jsonl \
#     --raw-path /fsx/tianxin/CL-bench/CL-bench.jsonl \
#     --max-samples 500 \
#     --max-evolve-turns 0 \
#     --no-retest \
#     --solver-model "$SOLVER" \
#     --curator-model "$CURATOR" \
#     --max-skills-per-context 0 \
#     --max-general-skills 0 \
#     --feedback-level 2 \
#     --batch-size 16 \
#     --batch-workers 16 \
#     --output-dir outputs_oss/cl_bench_baseline_oss &
# CL_BASE_PID=$!

# conda run -n mem --no-capture-output python examples/evolve_webarena_infinity.py \
#     --webarena-dir "$WEBARENA_DIR" \
#     --web-app superhuman-general \
#     --difficulty hard \
#     --solver-model "$SOLVER" \
#     --no-evolve --no-seed-skills \
#     --batch-size 8 --workers 8 \
#     --max-steps 50 --timeout 2400 \
#     --base-port 8201 \
#     --output-dir outputs_oss/webarena_baseline_oss &
# WEB_BASE_PID=$!

# wait $CL_BASE_PID
# echo "=== CL-bench baseline done ==="
# wait $WEB_BASE_PID
# echo "=== WebArena baseline done ==="

# conda run -n mem --no-capture-output python examples/evolve_tau_bench.py \
#     --env both --task-split test \
#     --solver-model "$SOLVER" --user-model "$USER_MODEL" \
#     --no-evolve \
#     --batch-size 10 --workers 10 \
#     --output-dir outputs_oss/tau_bench_baseline_oss
# echo "=== Tau-bench baseline done ==="

# echo "=== Phase 0 complete ==="

# # ══════════════════════════════════════════════════════════════════════
# # Phase 1: Evolution
# # ══════════════════════════════════════════════════════════════════════

# echo "=== Phase 1: Evolution (GPT-OSS 120B) ==="

# conda run -n mem --no-capture-output python examples/evolve_cl_bench.py \
#     --grouped-path /fsx/tianxin/CL-bench/CL-bench-grouped.jsonl \
#     --raw-path /fsx/tianxin/CL-bench/CL-bench.jsonl \
#     --max-samples 500 \
#     --max-evolve-turns 1 \
#     --no-retest \
#     --solver-model "$SOLVER" \
#     --curator-model "$CURATOR" \
#     --max-skills-per-context 5 \
#     --max-general-skills 0 \
#     --feedback-level 2 \
#     --batch-size 16 \
#     --batch-workers 16 \
#     --output-dir outputs_oss/cl_bench_v14_oss &
# CL_PID=$!

# conda run -n mem --no-capture-output python examples/evolve_webarena_infinity.py \
#     --webarena-dir "$WEBARENA_DIR" \
#     --web-app superhuman-general \
#     --difficulty hard \
#     --solver-model "$SOLVER" \
#     --curator-model "$CURATOR" \
#     --selector-model "$SELECTOR" \
#     --batch-size 8 --workers 8 \
#     --max-skills-per-topic 5 \
#     --max-general-skills 5 \
#     --no-seed-skills \
#     --max-steps 50 --timeout 2400 \
#     --shuffle --shuffle-seed 42 \
#     --feedback-level standard \
#     --evolve-all \
#     --base-port 8201 \
#     --output-dir outputs_oss/webarena_v2_oss &
# WEB_PID=$!

# wait $CL_PID
# echo "=== CL-bench evolve done ==="
# wait $WEB_PID
# echo "=== WebArena evolve done ==="

conda run -n mem --no-capture-output python examples/evolve_tau_bench.py \
    --env both --task-split test \
    --solver-model "$SOLVER" --user-model "$USER_MODEL" \
    --curator-model "$CURATOR" --selector-model "$SELECTOR" \
    --batch-size 10 --workers 10 \
    --max-skills-per-topic 0 \
    --max-general-skills 5 \
    --feedback-level standard \
    --shuffle --shuffle-seed 42 \
    --output-dir outputs_oss/tau_bench_v2_oss
echo "=== Tau-bench evolve done ==="

echo "=== All done (GPT-OSS 120B) ==="
