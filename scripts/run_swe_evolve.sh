#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

# SWE-bench Lite with A-EVOLVE propose+curator algorithm
#
# 300 tasks from princeton-nlp/SWE-bench_Lite
# Each task runs in its own Docker container (swebench/sweb.eval.x86_64.*)
# Evaluation uses swebench test harness (FAIL_TO_PASS + PASS_TO_PASS grading)

# ── Baseline: no evolution, no seed skills ───────────────────────────

# Pure baseline (no skills, no evolution)
# conda run -n mem --no-capture-output python examples/evolve_swe.py \
#     --solver-model 1 \
#     --no-evolve --no-seed-skills \
#     --batch-size 16 --workers 16 \
#     --eval-timeout 300 \
#     --output-dir outputs/swe_baseline_pure

# Baseline with seed skills only (no evolution)
# conda run -n mem --no-capture-output python examples/evolve_swe.py \
#     --solver-model 1 \
#     --no-evolve \
#     --lazy-load \
#     --batch-size 10 --workers 10 \
#     --eval-timeout 300 \
#     --output-dir outputs/swe_baseline_seed

# ── Evolve V1: propose+curator, lazy load ────────────────────────────

# V1: Opus solver, Sonnet curator+selector
# conda run -n mem --no-capture-output python examples/evolve_swe.py \
#     --solver-model 1 \
#     --curator-model 1 \
#     --selector-model 1 \
#     --batch-size 16 --workers 16 \
#     --max-skills-per-topic 5 \
#     --max-general-skills 5 \
#     --shuffle --shuffle-seed 42 \
#     --eval-timeout 300 \
#     --output-dir outputs/swe_evolve_v1_shuffle_standard_feedback

# Minimal feedback (old, no gating)
# conda run -n mem --no-capture-output python examples/evolve_swe.py \
#     --solver-model 1 \
#     --curator-model 1 \
#     --selector-model 1 \
#     --batch-size 16 --workers 16 \
#     --max-skills-per-topic 0 \
#     --max-general-skills 5 \
#     --shuffle --shuffle-seed 42 \
#     --eval-timeout 300 \
#     --feedback-level minimal \
#     --output-dir outputs/swe_evolve_v1_shuffle_minimal_feedback_general

# ── Agent curate: flat skills/evolved/ ───────────────────────────────

# V3: agent-curate with seed skills, evolve-all
# conda run -n mem --no-capture-output python examples/evolve_swe.py \
#     --solver-model 1 \
#     --curator-model 1 \
#     --batch-size 10 \
#     --workers 10 \
#     --max-skills-per-topic 10 \
#     --lazy-load \
#     --shuffle --shuffle-seed 42 \
#     --evolve-all \
#     --agent-curate \
#     --eval-timeout 300 \
#     --output-dir outputs/swe_evolve_v3_flat_seed_evolveall

# ── Repo-specific runs ───────────────────────────────────────────────

# Django only
# conda run -n mem --no-capture-output python examples/evolve_swe.py \
#     --repo-filter django/django \
#     --solver-model 1 \
#     --no-evolve \
#     --batch-size 10 --workers 10 \
#     --eval-timeout 300 \
#     --output-dir outputs/swe_baseline_django

# Quick test (5 tasks)
# conda run -n mem --no-capture-output python examples/evolve_swe.py \
#     --limit 5 \
#     --solver-model 1 \
#     --no-evolve --no-seed-skills \
#     --batch-size 5 --workers 5 \
#     --eval-timeout 300 \
#     --output-dir outputs/swe_test_5

# V2: minimal feedback + skill gating
conda run -n mem --no-capture-output python examples/evolve_swe.py \
    --solver-model 1 \
    --curator-model 1 \
    --batch-size 16 --workers 16 \
    --max-skills-per-topic 5 \
    --max-general-skills 5 \
    --shuffle --shuffle-seed 42 \
    --no-seed-skills \
    --eval-timeout 300 \
    --feedback-level standard \
    --output-dir outputs/swe_v2_minimal_feedback_gating
