#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

# SWE-bench: Cross-model skill transfer experiments
#
# Exp 1: Opus 4.7 evolves on first 50 tasks → Sonnet 4.5 uses those skills on next 50
# Exp 2: Sonnet 4.5 evolves on first 50 tasks → Opus 4.7 uses those skills on next 50

OPUS47="us.anthropic.claude-opus-4-7"
SONNET45="us.anthropic.claude-sonnet-4-5-20250929-v1:0"
SELECTOR="us.anthropic.claude-sonnet-4-5-20250929-v1:0"

# Use consistent shuffle for both experiments
SHUFFLE_ARGS="--shuffle --shuffle-seed 42"

# ══════════════════════════════════════════════════════════════════════
# Phase A: Both evolve experiments in parallel (first 50 tasks each)
# ══════════════════════════════════════════════════════════════════════

echo "=== Phase A: Evolve (parallel) ==="

# Exp 1-A: Opus 4.7 evolve on first 50 tasks
conda run -n mem --no-capture-output python examples/evolve_swe.py \
    --solver-model "$OPUS47" \
    --curator-model "$SONNET45" \
    --selector-model "$SELECTOR" \
    --batch-size 16 --workers 16 \
    --max-skills-per-topic 5 \
    --max-general-skills 5 \
    $SHUFFLE_ARGS \
    --no-seed-skills \
    --limit 64 \
    --eval-timeout 600 \
    --feedback-level standard \
    --output-dir outputs_47/swe_cross_opus47_evolve_50 &
PID_A1=$!

# Exp 2-A: Sonnet 4.5 evolve on first 50 tasks
conda run -n mem --no-capture-output python examples/evolve_swe.py \
    --solver-model "$SONNET45" \
    --curator-model "$OPUS47" \
    --selector-model "$SELECTOR" \
    --batch-size 16 --workers 16 \
    --max-skills-per-topic 5 \
    --max-general-skills 5 \
    $SHUFFLE_ARGS \
    --no-seed-skills \
    --limit 64 \
    --eval-timeout 600 \
    --feedback-level standard \
    --output-dir outputs_47/swe_cross_sonnet45_evolve_50 &
PID_A2=$!

wait $PID_A1
echo "=== Exp 1-A done: Opus 4.7 evolve ==="
wait $PID_A2
echo "=== Exp 2-A done: Sonnet 4.5 evolve ==="

# ══════════════════════════════════════════════════════════════════════
# Phase B: Both transfer experiments in parallel (next 50 tasks each)
# ══════════════════════════════════════════════════════════════════════

echo "=== Phase B: Transfer (parallel) ==="

# Exp 1-B: Sonnet 4.5 uses Opus 4.7's evolved skills on next 50 tasks
conda run -n mem --no-capture-output python examples/evolve_swe.py \
    --solver-model "$SONNET45" \
    --selector-model "$SELECTOR" \
    --no-evolve --no-seed-skills \
    --seed-workspace outputs_47/swe_cross_opus47_evolve_50/workspace \
    --batch-size 16 --workers 16 \
    --max-skills-per-topic 5 \
    --max-general-skills 5 \
    $SHUFFLE_ARGS \
    --offset 64 --limit 64 \
    --eval-timeout 600 \
    --output-dir outputs_47/swe_cross_sonnet45_use_opus47_skills &
PID_B1=$!

# Exp 2-B: Opus 4.7 uses Sonnet 4.5's evolved skills on next 50 tasks
conda run -n mem --no-capture-output python examples/evolve_swe.py \
    --solver-model "$OPUS47" \
    --selector-model "$SELECTOR" \
    --no-evolve --no-seed-skills \
    --seed-workspace outputs_47/swe_cross_sonnet45_evolve_50/workspace \
    --batch-size 16 --workers 16 \
    --max-skills-per-topic 5 \
    --max-general-skills 5 \
    $SHUFFLE_ARGS \
    --offset 64 --limit 64 \
    --eval-timeout 600 \
    --output-dir outputs_47/swe_cross_opus47_use_sonnet45_skills &
PID_B2=$!

wait $PID_B1
echo "=== Exp 1-B done: Sonnet 4.5 with Opus skills ==="
wait $PID_B2
echo "=== Exp 2-B done: Opus 4.7 with Sonnet skills ==="

# ══════════════════════════════════════════════════════════════════════
# Phase C: Sonnet 4.5 curator experiments (parallel)
# ══════════════════════════════════════════════════════════════════════

echo "=== Phase C: Sonnet 4.5 curator experiments (parallel) ==="

# C1: Sonnet 4.5 solver + S4.5 curator (full 300 tasks)
conda run -n mem --no-capture-output python examples/evolve_swe.py \
    --solver-model "$SONNET45" \
    --curator-model "$SONNET45" \
    --selector-model "$SELECTOR" \
    --batch-size 16 --workers 16 \
    --max-skills-per-topic 5 \
    --max-general-skills 5 \
    $SHUFFLE_ARGS \
    --no-seed-skills \
    --eval-timeout 600 \
    --feedback-level standard \
    --output-dir outputs_47/swe_sonnet45_curator_sonnet45_v2 &
PID_C1=$!

# C2: Sonnet 4.5 solver + Opus 4.7 curator v2 (full 300 tasks)
conda run -n mem --no-capture-output python examples/evolve_swe.py \
    --solver-model "$SONNET45" \
    --curator-model "$OPUS47" \
    --selector-model "$SELECTOR" \
    --batch-size 16 --workers 16 \
    --max-skills-per-topic 5 \
    --max-general-skills 5 \
    $SHUFFLE_ARGS \
    --no-seed-skills \
    --eval-timeout 600 \
    --feedback-level standard \
    --output-dir outputs_47/swe_sonnet45_curator_opus47_v2 &
PID_C2=$!

wait $PID_C1
echo "=== C1 done: Sonnet 4.5 + S4.5 curator ==="
wait $PID_C2
echo "=== C2 done: Sonnet 4.5 + Opus 4.7 curator v2 ==="

echo "=== All cross-transfer experiments done ==="
