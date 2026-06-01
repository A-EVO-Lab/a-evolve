#!/usr/bin/env bash
# Hypothesis experiments for PolyBench prediction-market evolution.
# Uses the real PolyBench dataset (polymarket_analysis.db).
#
# Usage:
#   bash poly_hypothesis.sh                                           # run all (default)
#   bash poly_hypothesis.sh H0                                        # single experiment
#   bash poly_hypothesis.sh --batch-size 50 --solver-temp 0.3 H1      # with overrides
#   bash poly_hypothesis.sh /path/to/custom.db H0                     # custom DB + target
#
# Options:
#   --suffix S           Suffix appended to result folder names (e.g. _run2)
#   --batch-size N       Batch size for evolution cycles (default: 100)
#   --evolver-temp T     Evolver LLM temperature (default: 0)
#   --solver-temp T      Solver LLM temperature (default: 0)
set -uo pipefail
cd "$(dirname "$0")/.."

# First arg is DB path if it ends in .db/.sqlite, otherwise all args are targets.
DB_PATH="data/polymarket_analysis.db"
if [ $# -gt 0 ]; then
  case "$1" in
    *.db|*.sqlite) DB_PATH="$1"; shift ;;
  esac
fi

# Parse named options from remaining args (with defaults)
BATCH_SIZE=100
EVOLVER_TEMP=0
SOLVER_TEMP=0
BRANCH_CONFIDENCE=0.7
SUFFIX=""
NO_INFRA_EVO=true
MAX_TASKS=0
# Solver/evolver models default to the SOLVER_MODEL/EVOLVER_MODEL env vars.
# Override per-run with --model-id / --evolver-model (e.g. to run a baseline
# under a different solver model).
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

if [ ! -f "$DB_PATH" ]; then
  echo "ERROR: database not found: $DB_PATH" >&2
  echo "Download from the PolyBench repo and place at $DB_PATH" >&2
  exit 1
fi

TASK_COUNT=$(sqlite3 "$DB_PATH" "
  SELECT COUNT(*) FROM market_snapshots ms
  JOIN resolutions r ON ms.market_id = r.market_id
  WHERE ms.ready_for_analysis = 1;
" 2>/dev/null || echo "?")
echo "Database: $DB_PATH ($TASK_COUNT resolved markets)"

# Build extra args from options
EXTRA_ARGS="--batch-size $BATCH_SIZE --evolver-temp $EVOLVER_TEMP --solver-temp $SOLVER_TEMP --branch-confidence $BRANCH_CONFIDENCE"
if [ "$MAX_TASKS" -gt 0 ]; then
  EXTRA_ARGS="$EXTRA_ARGS --limit $MAX_TASKS"
fi

# PolyBench is pure reasoning (no Docker), so lower timeouts and more workers.
COMMON="python solve_all_with_evolution.py
  --benchmark polybench
  --dataset $DB_PATH
  --seed-workspace experiments/polybench/seed
  --evolver-prompt experiments/polybench/evolver_prompt.md
  --model-id $MODEL_ID
  --temporal-reveal
  --max-turns 30
  --task-timeout 300
  --workers 24
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
  local logfile="$LOG_DIR/${id}_polybench_${label}${SUFFIX}.log"
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
  --output-dir "results/polybench_baseline${SUFFIX}" \
  --config experiments/polybench/configs/baseline.yaml

# Cross-model baselines: run H0 under a different solver model via
#   bash poly_hypothesis.sh --model-id <model> H0
# (or export SOLVER_MODEL). No dedicated targets — the solver model is a
# CLI/env choice, not a per-config setting.

# H1: Full evolution - all layers (prompts + skills + memory + tools)
run H1 full_evo \
  --output-dir "results/polybench_full_evo${SUFFIX}" \
  --config experiments/polybench/configs/full_evo.yaml

# H2: Early freeze - evolve first 40% of tasks, freeze workspace for the rest
run H2 early_freeze \
  --output-dir "results/polybench_early_freeze${SUFFIX}" \
  --config experiments/polybench/configs/early_freeze.yaml

# H3: Late start - skip evolution for first 60% of tasks, then evolve
run H3 late_start \
  --output-dir "results/polybench_late_start${SUFFIX}" \
  --config experiments/polybench/configs/late_start.yaml

# H4: Navigation - plan-driven branching + task routing (no multi-agent)
run H4 navigation \
  --navigation \
  --evolver-prompt experiments/polybench/evolver_prompt_nav.md \
  --output-dir "results/polybench_navigation${SUFFIX}" \
  --config experiments/polybench/configs/navigation.yaml

# H4_multi: Structured evolution (4-phase: analyze → research → build → verify)
run H4_multi structured_evo \
  --output-dir "results/polybench_structured_evo${SUFFIX}" \
  --config experiments/polybench/configs/structured_evolution_evo.yaml 

# H4_multi_nav: Structured evolution + navigation (4-phase + git branching)
run H4_multi_nav structured_nav \
  --navigation \
  --output-dir "results/polybench_structured_nav${SUFFIX}" \
  --config experiments/polybench/configs/structured_navigation_evo.yaml 

# ─── Summary ──────────────────────────────────────────────────────────
echo ""
echo "=== All requested experiments complete ==="
