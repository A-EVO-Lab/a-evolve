#!/usr/bin/env bash
# Prevent Python from using stale bytecode cache (.pyc files)
export PYTHONDONTWRITEBYTECODE=1
# Hypothesis experiments for CTF-Dojo cybersecurity evolution.
# Uses the CTF-Dojo challenge archive (data/ctf_archive.json).
#
# Usage:
#   bash ctf_dojo_hypothesis.sh                                           # run all (default)
#   bash ctf_dojo_hypothesis.sh H0                                        # single experiment
#   bash ctf_dojo_hypothesis.sh --batch-size 10 --solver-temp 0.3 H1      # with overrides
#   bash ctf_dojo_hypothesis.sh /path/to/custom.json H0                   # custom catalog + target
#
# Options:
#   --batch-size N       Batch size for evolution cycles (default: 5)
#   --evolver-temp T     Evolver LLM temperature (default: 0)
#   --solver-temp T      Solver LLM temperature (default: 0)
set -uo pipefail
cd "$(dirname "$0")/.."

# First arg is catalog path if it ends in .json, otherwise all args are targets.
CATALOG="data/ctf_archive.json"
if [ $# -gt 0 ]; then
  case "$1" in
    *.json) CATALOG="$1"; shift ;;
  esac
fi

# Parse named options from remaining args (with defaults)
BATCH_SIZE=20
EVOLVER_TEMP=0
SOLVER_TEMP=0
BRANCH_CONFIDENCE=0.7
SUFFIX=""
NO_INFRA_EVO=false
MAX_TASKS=0
# Solver + evolver inference profiles. Default: GLOBAL cross-region
# profiles. This isolates CTF-Dojo traffic from PolyBench / FutureX runs
# (which default to the US profile) so the two sets of experiments do not
# contend for the same cross-region Bedrock quota pool. Override with
# ``--model-id <solver-model-id>`` and
# ``--evolver-model <evolver-model-id>`` if needed.
MODEL_ID="${SOLVER_MODEL:-<solver-model-id>}"
EVOLVER_MODEL="${EVOLVER_MODEL:-<evolver-model-id>}"
TARGETS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --batch-size)          BATCH_SIZE="$2"; shift 2 ;;
    --evolver-temp)        EVOLVER_TEMP="$2"; shift 2 ;;
    --solver-temp)         SOLVER_TEMP="$2"; shift 2 ;;
    --branch-confidence)   BRANCH_CONFIDENCE="$2"; shift 2 ;;
    --suffix)              SUFFIX="$2"; shift 2 ;;
    --no-infra-evo)        NO_INFRA_EVO=true; shift ;;
    --limit)               MAX_TASKS="$2"; shift 2 ;;
    --model-id)            MODEL_ID="$2"; shift 2 ;;
    --evolver-model)       EVOLVER_MODEL="$2"; shift 2 ;;
    *)                     TARGETS+=("$1"); shift ;;
  esac
done

if [ ! -f "$CATALOG" ]; then
  echo "ERROR: catalog not found: $CATALOG" >&2
  echo "Prerequisites:" >&2
  echo "  1. Clone ctf-archive: git clone --depth 1 https://github.com/pwncollege/ctf-archive.git data/ctf-archive" >&2
  echo "  2. Build catalog: python scripts/build_ctf_catalog.py" >&2
  exit 1
fi

CHALLENGE_COUNT=$(python -c "import json; print(len(json.load(open('$CATALOG'))))" 2>/dev/null || echo "?")
echo "Catalog: $CATALOG ($CHALLENGE_COUNT challenges with flag hashes)"

# Build extra args from options
EXTRA_ARGS="--batch-size $BATCH_SIZE --evolver-temp $EVOLVER_TEMP --solver-temp $SOLVER_TEMP --branch-confidence $BRANCH_CONFIDENCE"
if [ "$MAX_TASKS" -gt 0 ]; then
  EXTRA_ARGS="$EXTRA_ARGS --limit $MAX_TASKS"
fi

# CTF challenges: 600s timeout captures ~93% of solves (6/22 pass after 600s).
# 50 max-turns prevents endless loops while still allowing complex multi-step solves.
# 5 workers balances Docker load — more workers just overwhelm the host.
COMMON="python solve_all_with_evolution.py
  --benchmark ctf_dojo
  --dataset $CATALOG
  --seed-workspace experiments/ctf_dojo/seed
  --evolver-prompt experiments/ctf_dojo/evolver_prompt.md
  --model-id $MODEL_ID
  --temporal-reveal
  --max-turns 50
  --task-timeout 600
  --workers 5
  $EXTRA_ARGS"
if [ -n "$EVOLVER_MODEL" ]; then
  COMMON="$COMMON --evolver-model $EVOLVER_MODEL"
fi
if [ "$NO_INFRA_EVO" = true ]; then
  COMMON="$COMMON --no-infra-evo"
fi

LOG_DIR="logs"
mkdir -p "$LOG_DIR"

run() {
  local id="$1"; shift
  local label="$1"; shift
  if [ ${#TARGETS[@]} -gt 0 ]; then
    local match=0
    for t in "${TARGETS[@]}"; do [ "$t" = "$id" ] && match=1; done
    [ "$match" -eq 0 ] && return 0
  fi
  local logfile="$LOG_DIR/${id}_ctf_dojo_${label}.log"
  echo "=== $id: $label === (logging to $logfile)"
  # shellcheck disable=SC2086
  $COMMON "$@" > "$logfile" 2>&1
  local rc=$?
  tail -5 "$logfile"
  echo "=== $id: exit $rc ==="
  return $rc
}

# ─── Experiments ──────────────────────────────────────────────────────

# H0: Baseline - no evolution (control)
run H0 baseline \
  --output-dir results/ctf_dojo_baseline \
  --config experiments/ctf_dojo/configs/baseline.yaml

# Cross-model baselines: run H0 under a different solver model via
#   bash ctf_dojo_hypothesis.sh --model-id <model> H0
# (or export SOLVER_MODEL). No dedicated targets — the solver model is a
# CLI/env choice, not a per-config setting.

# H1: Full evolution - all layers (prompts + skills + memory + tools)
run H1 full_evo \
  --output-dir results/ctf_dojo_full_evo \
  --config experiments/ctf_dojo/configs/full_evo.yaml

# H2: Early freeze - evolve first 40% of tasks, freeze workspace for the rest
run H2 early_freeze \
  --output-dir results/ctf_dojo_early_freeze \
  --config experiments/ctf_dojo/configs/early_freeze.yaml

# H3: Late start - skip evolution for first 60% of tasks, then evolve
run H3 late_start \
  --output-dir results/ctf_dojo_late_start \
  --config experiments/ctf_dojo/configs/late_start.yaml

# H4: Navigation - plan-driven branching + task routing (no multi-agent)
run H4 navigation \
  --navigation \
  --output-dir results/ctf_dojo_navigation \
  --config experiments/ctf_dojo/configs/navigation.yaml


# H4_multi: Structured evolution (4-phase: analyze → research → build → verify)
run H4_multi structured_evo \
  --output-dir results/ctf_dojo_structured_evo \
  --config experiments/ctf_dojo/configs/structured_evolution_evo.yaml

# H4_multi_nav: Structured evolution + navigation (4-phase + git branching)
run H4_multi_nav structured_nav \
  --navigation \
  --output-dir results/ctf_dojo_structured_nav \
  --config experiments/ctf_dojo/configs/structured_navigation_evo.yaml

# ─── Summary ──────────────────────────────────────────────────────────
echo ""
echo "=== All requested experiments complete ==="
