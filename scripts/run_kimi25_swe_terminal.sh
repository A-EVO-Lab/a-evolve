#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

# Terminal-Bench + SWE-bench with Kimi K2.5
# Baseline parallel, then evolve parallel

SOLVER="moonshotai.kimi-k2.5"
CURATOR="moonshotai.kimi-k2.5"
SELECTOR="us.anthropic.claude-sonnet-4-5-20250929-v1:0"

# ══════════════════════════════════════════════════════════════════════
# Phase 0: Baselines (parallel)
# ══════════════════════════════════════════════════════════════════════

echo "=== Phase 0: Baselines (Kimi K2.5) ==="

conda run -n mem --no-capture-output python examples/evolve_terminal.py \
    --solver-model "$SOLVER" \
    --no-evolve --no-seed-skills \
    --batch-size 10 --workers 10 \
    --output-dir outputs_kimi25/terminal_baseline_kimi25 &
TERM_BASE_PID=$!

conda run -n mem --no-capture-output python examples/evolve_swe.py \
    --solver-model "$SOLVER" \
    --no-evolve --no-seed-skills \
    --batch-size 16 --workers 16 \
    --eval-timeout 300 \
    --output-dir outputs_kimi25/swe_baseline_kimi25 &
SWE_BASE_PID=$!

wait $TERM_BASE_PID
echo "=== Terminal baseline done ==="
wait $SWE_BASE_PID
echo "=== SWE baseline done ==="

# ══════════════════════════════════════════════════════════════════════
# Phase 1: Evolution (parallel)
# ══════════════════════════════════════════════════════════════════════

echo "=== Phase 1: Evolution (Kimi K2.5) ==="

conda run -n mem --no-capture-output python examples/evolve_terminal.py \
    --solver-model "$SOLVER" \
    --curator-model "$CURATOR" \
    --selector-model "$SELECTOR" \
    --batch-size 10 \
    --shuffle --shuffle-seed 42 \
    --workers 10 \
    --no-seed-skills \
    --max-general-skills 5 \
    --max-skills-per-topic 0 \
    --feedback-level minimal \
    --output-dir outputs_kimi25/terminal_v6_kimi25 &
TERM_EVO_PID=$!

conda run -n mem --no-capture-output python examples/evolve_swe.py \
    --solver-model "$SOLVER" \
    --curator-model "$CURATOR" \
    --selector-model "$SELECTOR" \
    --batch-size 16 --workers 16 \
    --max-skills-per-topic 5 \
    --max-general-skills 5 \
    --shuffle --shuffle-seed 42 \
    --no-seed-skills \
    --eval-timeout 300 \
    --feedback-level standard \
    --output-dir outputs_kimi25/swe_v2_kimi25 &
SWE_EVO_PID=$!

wait $TERM_EVO_PID
echo "=== Terminal evolve done ==="
wait $SWE_EVO_PID
echo "=== SWE evolve done ==="

echo "=== All done (Terminal + SWE, Kimi K2.5) ==="
