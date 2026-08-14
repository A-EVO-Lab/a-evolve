#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

# SWE-bench: Sonnet 4.5 baseline + Opus 4.7 curator=Sonnet4.5 retry
# Run sequentially to avoid Docker resource contention

OPUS47="us.anthropic.claude-opus-4-7"
SONNET45="us.anthropic.claude-sonnet-4-5-20250929-v1:0"
SELECTOR="us.anthropic.claude-sonnet-4-5-20250929-v1:0"

# ══════════════════════════════════════════════════════════════════════
# Both experiments in parallel
# ══════════════════════════════════════════════════════════════════════

echo "=== Starting both experiments in parallel ==="

# Exp 1: Sonnet 4.5 baseline (no skills, no evolve)
conda run -n mem --no-capture-output python examples/evolve_swe.py \
    --solver-model "$SONNET45" \
    --no-evolve --no-seed-skills \
    --batch-size 16 --workers 16 \
    --eval-timeout 600 \
    --output-dir outputs_47/swe_sonnet45_baseline_v2 &
PID1=$!

# Exp 2: Opus 4.7 evolve with Sonnet 4.5 curator (retry)
conda run -n mem --no-capture-output python examples/evolve_swe.py \
    --solver-model "$OPUS47" \
    --curator-model "$SONNET45" \
    --selector-model "$SELECTOR" \
    --batch-size 16 --workers 16 \
    --max-skills-per-topic 5 \
    --max-general-skills 5 \
    --shuffle --shuffle-seed 42 \
    --no-seed-skills \
    --eval-timeout 600 \
    --feedback-level standard \
    --output-dir outputs_47/swe_opus47_curator_sonnet45_v2 &
PID2=$!

wait $PID1
echo "=== Sonnet 4.5 baseline done ==="
wait $PID2
echo "=== Opus 4.7 + Sonnet 4.5 curator done ==="

echo "=== All done ==="
