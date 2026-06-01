#!/usr/bin/env bash
# Prevent Python from using stale bytecode cache (.pyc files)
export PYTHONDONTWRITEBYTECODE=1
# Hypothesis experiments for FutureX temporal prediction evolution.
#
# Search modes:
#   strict (default): Wikipedia revision API — guaranteed pre-cutoff content,
#                     zero label leakage. Uses historical page snapshots.
#   live:             DuckDuckGo with date filtering — for online benchmark
#                     tasks. May have some leakage from updated pages.
#
# Usage:
#   bash futurex_hypothesis.sh                                           # run all
#   bash futurex_hypothesis.sh H0b                                       # single experiment
#   bash futurex_hypothesis.sh --search-mode live H0b                    # with live search
#   bash futurex_hypothesis.sh --batch-size 20 --solver-temp 0.0 H1      # with overrides
#
# Experiments:
#   H0a  Baseline — no evolution, NO web search (pure LLM reasoning)
#   H0b  Baseline — no evolution, WITH web search (strict mode)
#   H1   Full evolution — all layers with web search
#   H2   Early freeze — evolve first 40%, freeze rest
#   H3   Late start — skip evolution for first 60%, then evolve
#
# Options:
#   --batch-size N       Batch size for evolution cycles (default: 20)
#   --evolver-temp T     Evolver LLM temperature (default: 0.0)
#   --solver-temp T      Solver LLM temperature (default: 0.0)
#   --search-mode MODE   Search mode: strict or live (default: strict)
set -uo pipefail
cd "$(dirname "$0")/.."

# Parse named options from args (with defaults)
BATCH_SIZE=20
EVOLVER_TEMP=0.0
SOLVER_TEMP=0.0
SEARCH_MODE=strict
BRANCH_CONFIDENCE=0.7
SUFFIX=""
NO_INFRA_EVO=true
MAX_TASKS=0
TARGETS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --batch-size)          BATCH_SIZE="$2"; shift 2 ;;
    --evolver-temp)        EVOLVER_TEMP="$2"; shift 2 ;;
    --solver-temp)         SOLVER_TEMP="$2"; shift 2 ;;
    --search-mode)         SEARCH_MODE="$2"; shift 2 ;;
    --branch-confidence)   BRANCH_CONFIDENCE="$2"; shift 2 ;;
    --suffix)              SUFFIX="$2"; shift 2 ;;
    --no-infra-evo)        NO_INFRA_EVO=true; shift ;;
    --limit)               MAX_TASKS="$2"; shift 2 ;;
    *)                     TARGETS+=("$1"); shift ;;
  esac
done

# Validate search mode
if [ "$SEARCH_MODE" != "strict" ] && [ "$SEARCH_MODE" != "live" ]; then
  echo "ERROR: --search-mode must be 'strict' or 'live' (got: $SEARCH_MODE)" >&2
  exit 1
fi

# Validate FutureX data availability
echo "Checking FutureX dataset availability..."
TASK_COUNT=$(python3 -c "
import logging
logging.getLogger().setLevel(logging.ERROR)
try:
    from agent_evolve.benchmarks.futurex.data_loader import FutureXDataLoader
    loader = FutureXDataLoader()
    past_tasks, online_tasks = loader.load_datasets()
    print(len(past_tasks))
except Exception as e:
    print('0')
" 2>/dev/null || echo "0")

if [ "$TASK_COUNT" -eq 0 ]; then
  echo "ERROR: FutureX dataset not found or empty" >&2
  echo "Ensure FutureX data is downloaded in data/futurex/" >&2
  exit 1
fi

echo "Dataset: FutureX-Past ($TASK_COUNT temporal prediction tasks)"
echo "Search mode: $SEARCH_MODE"

# Build extra args from options
EXTRA_ARGS="--batch-size $BATCH_SIZE --evolver-temp $EVOLVER_TEMP --solver-temp $SOLVER_TEMP --branch-confidence $BRANCH_CONFIDENCE"
if [ "$MAX_TASKS" -gt 0 ]; then
  EXTRA_ARGS="$EXTRA_ARGS --limit $MAX_TASKS"
fi

# Workers: 3 for strict mode (DDGS needs serialized access via file lock).
# More workers just queue up behind the lock, wasting threads.
WORKERS=10

COMMON="python solve_all_with_evolution.py
  --benchmark futurex
  --seed-workspace experiments/futurex/seed
  --evolver-prompt experiments/futurex/evolver_prompt.md
  --temporal-reveal
  --task-timeout 300
  --workers $WORKERS
  $EXTRA_ARGS"
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
  local logfile="$LOG_DIR/${id}_futurex_${label}.log"
  echo "=== $id: $label === (logging to $logfile)"
  # shellcheck disable=SC2086
  $COMMON "$@" > "$logfile" 2>&1 &
  local pid=$!
  echo "Started experiment $id (PID: $pid)"
  wait $pid
  local rc=$?
  echo ""
  echo "=== $id: Experiment Summary ==="
  tail -10 "$logfile" | head -5
  echo "=== $id: exit $rc ==="
  echo ""
  return $rc
}

# ─── Experiments ──────────────────────────────────────────────────────

# H0a: Baseline - no evolution, NO web search (pure LLM reasoning)
run H0a baseline_no_search \
  --max-turns 50 \
  --output-dir results/futurex_baseline_no_search \
  --config experiments/futurex/configs/baseline_no_search.yaml

# H0b: Baseline - no evolution, strict search (Wikipedia revision API, zero leakage)
run H0b baseline_strict_search \
  --max-turns 50 \
  --output-dir results/futurex_baseline \
  --config experiments/futurex/configs/baseline.yaml

# H0c: Baseline - no evolution, live search (DDGS, may have some leakage)
run H0c baseline_live_search \
  --max-turns 50 \
  --output-dir results/futurex_baseline_live_search \
  --config experiments/futurex/configs/baseline_live_search.yaml

# Cross-model baselines: the futurex solver reads the solver model from the
# config's ``model_name`` key (see experiments/futurex/configs/baseline_*.yaml).
# Run H0c against such a config to evaluate a different solver model; no
# dedicated DeepSeek/Kimi targets are hardcoded here.

# H1: Full evolution, strict search (Wikipedia)
run H1 full_evo_strict \
  --max-turns 80 \
  --output-dir results/futurex_full_evo \
  --config experiments/futurex/configs/full_evo.yaml

# H1b: Full evolution, live search (DuckDuckGo)
# Reduced workers to 5: Docker sandbox + DDGS subprocess per task overloads at 10
run H1b full_evo_live \
  --max-turns 50 --workers 5 \
  --output-dir results/futurex_full_evo_live \
  --config experiments/futurex/configs/live.yaml

# H2: Early freeze (40%), strict search
run H2 early_freeze_strict \
  --max-turns 50 \
  --output-dir results/futurex_early_freeze \
  --config experiments/futurex/configs/early_freeze.yaml

# H2b: Early freeze (40%), live search
run H2b early_freeze_live \
  --max-turns 50 --workers 5 \
  --output-dir results/futurex_early_freeze_live \
  --config experiments/futurex/configs/early_freeze_live.yaml

# H3: Late start (last ~56 tasks), strict search
run H3 late_start_strict \
  --max-turns 50 \
  --output-dir results/futurex_late_start \
  --config experiments/futurex/configs/late_start.yaml

# H3b: Late start (last ~56 tasks), live search
run H3b late_start_live \
  --max-turns 50 --workers 5 \
  --output-dir results/futurex_late_start_live \
  --config experiments/futurex/configs/late_start_live.yaml

# H4: Online evaluation — current FutureX-Online tasks (no ground truth)
# Downloads fresh weekly data from HuggingFace on each run.
# Uses evolved workspace from H1b for best-available skills/memory/tools.
run H4 online_eval \
  --max-turns 50 \
  --split online \
  --seed-workspace results/futurex_full_evo_live/evolved_workspace \
  --output-dir results/futurex_online_eval \
  --config experiments/futurex/configs/baseline.yaml

# H5_nav: Navigation - plan-driven branching + task routing (strict search, no multi-agent)
run H5_nav navigation_strict \
  --max-turns 80 \
  --navigation \
  --batch-size 40 \
  --evolver-prompt experiments/futurex/evolver_prompt_nav.md \
  --output-dir results/futurex_navigation \
  --config experiments/futurex/configs/navigation.yaml

# H5_nav_varA: Navigation variant A — aggressive branching
run H5_nav_varA nav_varA \
  --max-turns 80 \
  --navigation \
  --evolver-prompt experiments/futurex/evolver_prompt_nav_varA.md \
  --output-dir results/futurex_nav_varA \
  --config experiments/futurex/configs/navigation.yaml

# H5_nav_varB: Navigation variant B — protect main, never remove
run H5_nav_varB nav_varB \
  --max-turns 80 \
  --navigation \
  --evolver-prompt experiments/futurex/evolver_prompt_nav_varB.md \
  --output-dir results/futurex_nav_varB \
  --config experiments/futurex/configs/navigation.yaml

# H5_nav_varC: Navigation variant C — main=infra, branch=strategy
run H5_nav_varC nav_varC \
  --max-turns 80 \
  --navigation \
  --evolver-prompt experiments/futurex/evolver_prompt_nav_varC.md \
  --output-dir results/futurex_nav_varC \
  --config experiments/futurex/configs/navigation.yaml

# H5_multi: Structured evolution (4-phase: analyze → research → build → verify)
COMMON_SAVE="$COMMON"
COMMON="${COMMON//--no-infra-evo/}"
run H5_multi structured_evo \
  --max-turns 80 \
  --evolver-prompt experiments/futurex/evolver_prompt.md \
  --output-dir results/futurex_structured_evo \
  --config experiments/futurex/configs/structured_evolution_evo.yaml
COMMON="$COMMON_SAVE"

# H5_multi_nav: Navigation + multi-agent orchestrated evolution (strict search)
COMMON_SAVE="$COMMON"
COMMON="${COMMON//--no-infra-evo/}"
run H5_multi_nav navigation_multi_strict \
  --max-turns 80 \
  --navigation \
  --output-dir results/futurex_navigation_multi \
  --config experiments/futurex/configs/navigation_multi.yaml
COMMON="$COMMON_SAVE"

# H5_struct_nav: Structured evolution + navigation (4-phase + git branching)
COMMON_SAVE="$COMMON"
COMMON="${COMMON//--no-infra-evo/}"
run H5_struct_nav structured_nav \
  --max-turns 80 \
  --navigation \
  --output-dir results/futurex_structured_nav \
  --config experiments/futurex/configs/structured_navigation_evo.yaml
COMMON="$COMMON_SAVE"

# ─── Summary ──────────────────────────────────────────────────────────
echo ""
echo "=== All requested experiments complete ==="
echo ""
echo "Search mode: $SEARCH_MODE"
echo "  strict = Wikipedia revision API (guaranteed pre-cutoff, zero leakage)"
echo "  live   = DuckDuckGo with date filter (for online tasks)"
echo ""
echo "Experiments:"
echo "  H0a  No search            Pure LLM reasoning baseline"
echo "  H0b  Strict search        Wikipedia revision API (zero leakage)"
echo "  H0c  Live search          DuckDuckGo unrestricted"
echo "  H1   Full evo+strict      Evolution + Wikipedia"
echo "  H1b  Full evo+live        Evolution + DuckDuckGo"
echo "  H2   Freeze+strict        Evolve 40%, freeze + Wikipedia"
echo "  H2b  Freeze+live          Evolve 40%, freeze + DuckDuckGo"
echo "  H3   Late start+strict    Last ~56 tasks, evo + Wikipedia"
echo "  H3b  Late start+live      Last ~56 tasks, evo + DuckDuckGo"
echo "  H4   Online eval          FutureX-Online (no GT, evolved workspace)"
echo "  H5   Navigation           Inline branching + task routing"
echo "  H5_multi  Nav+multi-agent Plan-driven orchestrated navigation"
echo ""
echo "Key Comparisons:"
echo "  H0c vs H0a:      Value of live search"
echo "  H0b vs H0a:      Value of strict search (zero leakage)"
echo "  H1  vs H0b:      Value of evolution (strict)"
echo "  H1b vs H0c:      Value of evolution (live)"
echo "  H2b vs H1b:      Early freeze effect"
echo "  H3b vs H0c:      Late start on last 56 tasks"
echo "  H5  vs H1:       Value of navigation (branching)"
echo "  H5_multi vs H5:  Value of multi-agent orchestration"
echo ""
echo "Analysis:"
echo "  grep -h 'SUMMARY' logs/H*_futurex_*.log"
