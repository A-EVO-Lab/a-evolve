#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

# Tau-bench with A-EVOLVE propose+curator evolution loop
#
# Tau-bench: LLM-simulated user ↔ tool-calling agent ↔ simulated DB
# Domains: airline (50 tasks), retail (115 tasks), both = 165 tasks
# Evaluation: DB state hash + output string matching (binary reward)
#
# Prerequisites:
#   pip install -e /fsx/tianxin/tau-bench
#   AWS credentials configured (Bedrock access)

# ── Previous experiments (commented out) ──────────────────────────
# # Sonnet baseline
# conda run -n mem --no-capture-output python examples/evolve_tau_bench.py \
#     --env both --task-split test \
#     --solver-model 2 --user-model 2 \
#     --batch-size 10 --workers 10 \
#     --no-evolve \
#     --output-dir outputs/tau_bench_both_baseline_sonnet
#
# # Opus baseline (user=opus)
# conda run -n mem --no-capture-output python examples/evolve_tau_bench.py \
#     --env both --task-split test \
#     --solver-model 1 --user-model 1 \
#     --batch-size 10 --workers 10 \
#     --no-evolve \
#     --output-dir outputs/tau_bench_both_baseline_opus_user_opus
#
# # Opus evolve — minimal (user=opus)
# conda run -n mem --no-capture-output python examples/evolve_tau_bench.py \
#     --env both --task-split test \
#     --solver-model 1 --user-model 1 \
#     --curator-model 1 --selector-model 1 \
#     --batch-size 10 --workers 10 \
#     --max-skills-per-topic 5 --max-general-skills 5 \
#     --feedback-level minimal \
#     --shuffle --shuffle-seed 42 \
#     --output-dir outputs/tau_bench_both_evolve_opus_minimal_user_opus
#
# # Opus evolve — standard (user=opus)
# conda run -n mem --no-capture-output python examples/evolve_tau_bench.py \
#     --env both --task-split test \
#     --solver-model 1 --user-model 1 \
#     --curator-model 1 --selector-model 1 \
#     --batch-size 10 --workers 10 \
#     --max-skills-per-topic 5 --max-general-skills 5 \
#     --feedback-level standard \
#     --shuffle --shuffle-seed 42 \
#     --output-dir outputs/tau_bench_both_evolve_opus_standard_user_opus
#
# # Opus evolve — standard, general only (user=opus)
# conda run -n mem --no-capture-output python examples/evolve_tau_bench.py \
#     --env both --task-split test \
#     --solver-model 1 --user-model 1 \
#     --curator-model 1 --selector-model 1 \
#     --batch-size 10 --workers 10 \
#     --max-skills-per-topic 0 --max-general-skills 5 \
#     --feedback-level standard \
#     --shuffle --shuffle-seed 42 \
#     --output-dir outputs/tau_bench_both_evolve_opus_standard_user_opus_general

# ══════════════════════════════════════════════════════════════════════
# V2: With skill gating + generalization prompts
# ══════════════════════════════════════════════════════════════════════

# ── 1. Standard (propose only on failures) ────────────────────────
# conda run -n mem --no-capture-output python examples/evolve_tau_bench.py \
#     --env both --task-split test \
#     --solver-model 1 --user-model 2 \
#     --curator-model 1 --selector-model 2 \
#     --batch-size 10 --workers 10 \
#     --max-skills-per-topic 5 \
#     --max-general-skills 5 \
#     --feedback-level minimal \
#     --shuffle --shuffle-seed 42 \
#     --output-dir outputs/tau_bench_v2_minimal

# ── 2. Standard + evolve-all + general only ──────────────
# conda run -n mem --no-capture-output python examples/evolve_tau_bench.py \
#     --env both --task-split test \
#     --solver-model 1 --user-model 2 \
#     --curator-model 1 --selector-model 2 \
#     --batch-size 10 --workers 10 \
#     --max-skills-per-topic 0 \
#     --max-general-skills 5 \
#     --feedback-level standard \
#     --shuffle --shuffle-seed 42 \
#     --evolve-all \
#     --output-dir outputs/tau_bench_v2_evolve_all_general_only

# ── 3. General only + user=opus ────
# conda run -n mem --no-capture-output python examples/evolve_tau_bench.py \
#     --env both --task-split test \
#     --solver-model 1 --user-model 1 \
#     --curator-model 1 --selector-model 2 \
#     --batch-size 10 --workers 10 \
#     --max-skills-per-topic 0 \
#     --max-general-skills 5 \
#     --feedback-level standard \
#     --shuffle --shuffle-seed 42 \
#     --output-dir outputs/tau_bench_v2_general_only_user_opus

# ── Best: General only + user=Sonnet (77.0%, 127/165) ────
conda run -n mem --no-capture-output python examples/evolve_tau_bench.py \
    --env both --task-split test \
    --solver-model 1 --user-model 2 \
    --curator-model 1 --selector-model 2 \
    --batch-size 10 --workers 10 \
    --max-skills-per-topic 0 \
    --max-general-skills 5 \
    --feedback-level standard \
    --shuffle --shuffle-seed 42 \
    --output-dir outputs_check/tau_bench_v2_general_only_gate_reset
