#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

# WebArena Infinity with A-EVOLVE propose+curator algorithm
#
# Uses browser-use library with Bedrock models (ChatAWSBedrock)
# Each task runs in a browser instance against a WebArena web app server
# Evaluation uses WebArena Infinity's programmatic Python verifiers
#
# Prerequisites:
#   pip install "browser-use[aws]"
#   git clone https://github.com/web-arena-x/webarena-infinity.git
#   Servers auto-start per worker (one server per worker port)

WEBARENA_DIR="${WEBARENA_DIR:-/fsx/tianxin/webarena-infinity}"

# ── Previous experiments (commented out) ──────────────────────────
# # Opus baseline
# conda run -n mem --no-capture-output python examples/evolve_webarena_infinity.py \
#     --webarena-dir "$WEBARENA_DIR" \
#     --web-app superhuman-general --difficulty hard \
#     --solver-model 1 \
#     --no-evolve --no-seed-skills \
#     --batch-size 8 --workers 8 \
#     --max-steps 50 --timeout 1200 \
#     --output-dir outputs/webarena_superhuman_hard_baseline_opus
#
# # Opus evolve — minimal
# conda run -n mem --no-capture-output python examples/evolve_webarena_infinity.py \
#     --webarena-dir "$WEBARENA_DIR" \
#     --web-app superhuman-general --difficulty hard \
#     --solver-model 1 --curator-model 1 --selector-model 1 \
#     --batch-size 8 --workers 8 \
#     --max-skills-per-topic 5 --max-general-skills 5 \
#     --no-seed-skills \
#     --max-steps 50 --timeout 1200 \
#     --shuffle --shuffle-seed 42 \
#     --feedback-level minimal \
#     --output-dir outputs/webarena_superhuman_hard_evolve_opus_minimal
#
# # Opus evolve — standard
# conda run -n mem --no-capture-output python examples/evolve_webarena_infinity.py \
#     --webarena-dir "$WEBARENA_DIR" \
#     --web-app superhuman-general --difficulty hard \
#     --solver-model 1 --curator-model 1 --selector-model 1 \
#     --batch-size 8 --workers 8 \
#     --max-skills-per-topic 5 --max-general-skills 5 \
#     --no-seed-skills \
#     --max-steps 50 --timeout 1200 \
#     --shuffle --shuffle-seed 42 \
#     --feedback-level standard \
#     --output-dir outputs/webarena_superhuman_hard_evolve_opus_standard
#
# # Opus evolve — minimal, general only
# conda run -n mem --no-capture-output python examples/evolve_webarena_infinity.py \
#     --webarena-dir "$WEBARENA_DIR" \
#     --web-app superhuman-general --difficulty hard \
#     --solver-model 1 --curator-model 1 --selector-model 1 \
#     --batch-size 8 --workers 8 \
#     --max-skills-per-topic 0 --max-general-skills 5 \
#     --no-seed-skills \
#     --max-steps 50 --timeout 1200 \
#     --shuffle --shuffle-seed 42 \
#     --feedback-level minimal \
#     --output-dir outputs/webarena_superhuman_hard_evolve_opus_minimal_general

# ══════════════════════════════════════════════════════════════════════
# V2: With skill gating + generalization prompts
# ══════════════════════════════════════════════════════════════════════

# ── 1. Standard (propose only on failures) ────────────────────────
# conda run -n mem --no-capture-output python examples/evolve_webarena_infinity.py \
#     --webarena-dir "$WEBARENA_DIR" \
#     --web-app superhuman-general \
#     --difficulty hard \
#     --solver-model 1 \
#     --curator-model 1 \
#     --selector-model 2 \
#     --batch-size 8 --workers 8 \
#     --max-skills-per-topic 5 \
#     --max-general-skills 5 \
#     --no-seed-skills \
#     --max-steps 50 --timeout 1200 \
#     --shuffle --shuffle-seed 42 \
#     --feedback-level standard \
#     --output-dir outputs/webarena_v2_standard

# ── 2. Standard + evolve-all ──────────────
# conda run -n mem --no-capture-output python examples/evolve_webarena_infinity.py \
#     --webarena-dir "$WEBARENA_DIR" \
#     --web-app superhuman-general \
#     --difficulty hard \
#     --solver-model 1 \
#     --curator-model 1 \
#     --selector-model 2 \
#     --batch-size 8 --workers 8 \
#     --max-skills-per-topic 5 \
#     --max-general-skills 5 \
#     --no-seed-skills \
#     --max-steps 50 --timeout 1200 \
#     --shuffle --shuffle-seed 42 \
#     --feedback-level standard \
#     --evolve-all \
#     --output-dir outputs/webarena_v2_evolve_all

# ── 3. Topic only ────
# conda run -n mem --no-capture-output python examples/evolve_webarena_infinity.py \
#     --webarena-dir "$WEBARENA_DIR" \
#     --web-app superhuman-general \
#     --difficulty hard \
#     --solver-model 1 \
#     --curator-model 1 \
#     --selector-model 2 \
#     --batch-size 8 --workers 8 \
#     --max-skills-per-topic 5 \
#     --max-general-skills 0 \
#     --no-seed-skills \
#     --max-steps 50 --timeout 1200 \
#     --shuffle --shuffle-seed 42 \
#     --feedback-level standard \
#     --output-dir outputs/webarena_v2_topic_only

# ── 4. Baseline no-evolve + more timeout ────
# conda run -n mem --no-capture-output python examples/evolve_webarena_infinity.py \
#     --webarena-dir "$WEBARENA_DIR" \
#     --web-app superhuman-general --difficulty hard \
#     --solver-model 1 \
#     --no-evolve --no-seed-skills \
#     --batch-size 8 --workers 8 \
#     --max-steps 50 --timeout 2400 \
#     --output-dir outputs/webarena_baseline_opus_more_timeout

# ── Best: evolve-all + longer timeout (77.5%, 62/80) ────
conda run -n mem --no-capture-output python examples/evolve_webarena_infinity.py \
    --webarena-dir "$WEBARENA_DIR" \
    --web-app superhuman-general \
    --difficulty hard \
    --solver-model 1 \
    --curator-model 1 \
    --selector-model 2 \
    --batch-size 8 --workers 8 \
    --max-skills-per-topic 5 \
    --max-general-skills 5 \
    --no-seed-skills \
    --max-steps 50 --timeout 2400 \
    --shuffle --shuffle-seed 42 \
    --feedback-level standard \
    --evolve-all \
    --output-dir outputs_check/webarena_v2_evolve_all_more_timeout_gate_reset
