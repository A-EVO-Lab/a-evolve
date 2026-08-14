#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

# Terminal-Bench 2.0 with A-EVOLVE propose+curator algorithm
#
# Skills organized by self-assigned topic (no metadata category).
# Topic selection via Sonnet, full body injection by default.

# Baseline (no evolution, no skills)
# conda run -n mem --no-capture-output python examples/evolve_terminal.py \
#     --solver-model 1 --no-evolve \
#     --batch-size 10 --workers 10 \
#     --output-dir outputs/terminal_baseline

# V1: propose+curator, full injection, topic selection via Sonnet
# conda run -n mem --no-capture-output python examples/evolve_terminal.py \
#     --solver-model 1 \
#     --curator-model 1 \
#     --selector-model 1 \
#     --batch-size 10 \
#     --shuffle \
#     --workers 10 \
#     --max-skills-per-topic 5 \
#     --max-general-skills 5 \
#     --lazy-load \
#     --output-dir outputs/terminal_evolve_v1_full_batch_10_seed_lazy

# --evolve-all \
# V2: evolve-all (propose+curate for all tasks, not just failed) # --lazy-load \
# conda run -n mem --no-capture-output python examples/evolve_terminal.py \
#     --solver-model 1 \
#     --curator-model 1 \
#     --selector-model 1 \
#     --batch-size 10 \
#     --shuffle --shuffle-seed 42 \
#     --workers 10 \
#     --max-skills-per-topic 0 \
#     --max-general-skills 5 \
#     --lazy-load \
#     --output-dir outputs/terminal_evolve_v2_all_general_5_evolve_42

# V3: agent-evolve (evolver agent with bash tool, aggregates batch info, writes skills directly)
# Like old a-evolve: no per-task propose, single evolver sees all batch results
# conda run -n mem --no-capture-output python examples/evolve_terminal.py \
#     --solver-model 1 \
#     --curator-model 1 \
#     --batch-size 10 \
#     --shuffle --shuffle-seed 42 \
#     --workers 10 \
#     --max-skills-per-topic 0 \
#     --max-general-skills 5 \
#     --agent-evolve \
#     --output-dir outputs/terminal_evolve_v2_agent_seedskill_42

# V4: hybrid — per-task propose + agent curator with bash tool
# Flat skills/evolved/ structure, max-skills-per-topic = total evolved skill limit
# conda run -n mem --no-capture-output python examples/evolve_terminal.py \
#     --solver-model 1 \
#     --curator-model 1 \
#     --batch-size 10 \
#     --shuffle --shuffle-seed 42 \
#     --workers 10 \
#     --max-skills-per-topic 10 \
#     --lazy-load \
#     --agent-curate \
#     --output-dir outputs/terminal_evolve_v4_hybrid_lazy_seedskill_42

# V5: flat agent-curate, no seed skills, evolve-all
# conda run -n mem --no-capture-output python examples/evolve_terminal.py \
#     --solver-model 1 \
#     --curator-model 1 \
#     --batch-size 10 \
#     --shuffle --shuffle-seed 42 \
#     --no-seed-skills \
#     --workers 10 \
#     --max-skills-per-topic 10 \
#     --lazy-load \
#     --evolve-all \
#     --agent-curate \
#     --output-dir outputs/terminal_evolve_v5_flat_noseed_evolveall_42

# V5 with seed skills
# conda run -n mem --no-capture-output python examples/evolve_terminal.py \
#     --solver-model 1 \
#     --curator-model 1 \
#     --selector-model 1 \
#     --batch-size 10 \
#     --shuffle --shuffle-seed 42 \
#     --workers 10 \
#     --max-general-skills 5 \
#     --max-skills-per-topic 0 \
#     --feedback-level "minimal" \
#     --output-dir outputs/terminal_evolve_seedskill_42_minimal_feedback_no_topic

# conda run -n mem --no-capture-output python examples/evolve_terminal.py \
#     --solver-model 1 \
#     --curator-model 1 \
#     --selector-model 1 \
#     --batch-size 10 \
#     --shuffle --shuffle-seed 42 \
#     --workers 10 \
#     --max-general-skills 5 \
#     --no-seed-skills \
#     --max-skills-per-topic 0 \
#     --feedback-level "minimal" \
#     --output-dir outputs/terminal_evolve_noseedskill_42_minimal_feedback_no_topic

# conda run -n mem --no-capture-output python examples/evolve_terminal.py \
#     --solver-model 1 \
#     --curator-model 1 \
#     --selector-model 1 \
#     --batch-size 10 \
#     --shuffle --shuffle-seed 42 \
#     --workers 10 \
#     --max-general-skills 5 \
#     --max-skills-per-topic 5 \
#     --feedback-level "standard" \
#     --output-dir outputs/terminal_evolve_seedskill_42_standard_feedback

# V6: minimal feedback + general only + skill gating
conda run -n mem --no-capture-output python examples/evolve_terminal.py \
    --solver-model 1 \
    --curator-model 1 \
    --batch-size 10 \
    --shuffle --shuffle-seed 42 \
    --workers 10 \
    --no-seed-skills \
    --max-general-skills 5 \
    --max-skills-per-topic 0 \
    --feedback-level "minimal" \
    --output-dir outputs/terminal_v6_standard_retry
