#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

# SWE-bench Lite: Curator ablation study
# 1. Opus 4.7 solver + Sonnet 4.5 curator
# 2. Sonnet 4.5 solver + Sonnet 4.5 curator
# 3. Sonnet 4.5 solver + Opus 4.7 curator

OPUS47="us.anthropic.claude-opus-4-7"
SONNET45="us.anthropic.claude-sonnet-4-5-20250929-v1:0"
SELECTOR="us.anthropic.claude-sonnet-4-5-20250929-v1:0"

# ══════════════════════════════════════════════════════════════════════
# Exp 1: Opus 4.7 solver + Sonnet 4.5 curator
# ══════════════════════════════════════════════════════════════════════

echo "=== Exp 1: Opus 4.7 solver + Sonnet 4.5 curator ==="

conda run -n mem --no-capture-output python examples/evolve_swe.py \
    --solver-model "$OPUS47" \
    --curator-model "$SONNET45" \
    --selector-model "$SELECTOR" \
    --batch-size 16 --workers 16 \
    --max-skills-per-topic 5 \
    --max-general-skills 5 \
    --shuffle --shuffle-seed 42 \
    --no-seed-skills \
    --eval-timeout 300 \
    --feedback-level standard \
    --output-dir outputs_47/swe_opus47_curator_sonnet45

echo "=== Exp 1 done ==="

# ══════════════════════════════════════════════════════════════════════
# Exp 2: Sonnet 4.5 solver + Sonnet 4.5 curator
# ══════════════════════════════════════════════════════════════════════

echo "=== Exp 2: Sonnet 4.5 solver + Sonnet 4.5 curator ==="

conda run -n mem --no-capture-output python examples/evolve_swe.py \
    --solver-model "$SONNET45" \
    --curator-model "$SONNET45" \
    --selector-model "$SELECTOR" \
    --batch-size 16 --workers 16 \
    --max-skills-per-topic 5 \
    --max-general-skills 5 \
    --shuffle --shuffle-seed 42 \
    --no-seed-skills \
    --eval-timeout 300 \
    --feedback-level standard \
    --output-dir outputs_47/swe_sonnet45_curator_sonnet45

echo "=== Exp 2 done ==="

# ══════════════════════════════════════════════════════════════════════
# Exp 3: Sonnet 4.5 solver + Opus 4.7 curator
# ══════════════════════════════════════════════════════════════════════

echo "=== Exp 3: Sonnet 4.5 solver + Opus 4.7 curator ==="

conda run -n mem --no-capture-output python examples/evolve_swe.py \
    --solver-model "$SONNET45" \
    --curator-model "$OPUS47" \
    --selector-model "$SELECTOR" \
    --batch-size 16 --workers 16 \
    --max-skills-per-topic 5 \
    --max-general-skills 5 \
    --shuffle --shuffle-seed 42 \
    --no-seed-skills \
    --eval-timeout 300 \
    --feedback-level standard \
    --output-dir outputs_47/swe_sonnet45_curator_opus47

echo "=== Exp 3 done ==="

echo "=== All curator ablation experiments done ==="
