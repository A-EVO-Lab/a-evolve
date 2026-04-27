#!/usr/bin/env bash
# SkillBench legacy train/test split wrapper.
#
# Two-phase orchestrator (single-process) around
# examples/skillbench_examples/skillbench_evolve_split.py:
#
#   Phase 1 (TRAIN): solve first $EVOLVE_LIMIT tasks in batches of
#                    $BATCH_SIZE; after each batch, run one AEvolveEngine
#                    evolve step.
#   Phase 2 (TEST):  solve the next $EVAL_LIMIT tasks once each on the
#                    evolved workspace — no engine, no per-task retry.
#
# Mirrors examples/swe_examples/run_swe_evolve_split.sh shape but uses the
# legacy SkillBench engine (NOT UnifiedEngine — for that, see
# run_skillbench_evolve_split_unified.sh).
#
# Native mode only (harbor mode not wired here). All knobs are env-var
# configurable.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# ---------------------------------------------------------------------------
# Split / batch defaults
# ---------------------------------------------------------------------------
LIMIT="${LIMIT:-}"                       # Total task cap ('' = all tasks)
EVOLVE_LIMIT="${EVOLVE_LIMIT:-20}"       # Phase 1 train tasks
EVAL_LIMIT="${EVAL_LIMIT:-}"             # Phase 2 test tasks ('' = all remaining)
BATCH_SIZE="${BATCH_SIZE:-1}"
# Phase 1 parallel workers (effective = min(TRAIN_PARALLEL, BATCH_SIZE)).
TRAIN_PARALLEL="${TRAIN_PARALLEL:-${MAX_WORKERS:-1}}"
# Phase 2 parallel workers (whole test set in one pool, no batch boundary).
TEST_PARALLEL="${TEST_PARALLEL:-${MAX_WORKERS:-8}}"
# Legacy backstop: MAX_WORKERS is used as the default for both knobs above
# when set; if neither TRAIN_PARALLEL nor TEST_PARALLEL is given, both fall
# back to MAX_WORKERS (default 1 for train, 8 for test if MAX_WORKERS unset).
# >1 + EVOLVE_MEMORY=true is rejected by the python guard.

# ---------------------------------------------------------------------------
# SkillBench knobs
# ---------------------------------------------------------------------------
USE_SKILLS="${USE_SKILLS:-false}"
SPLIT_SEED="${SPLIT_SEED:-42}"
NATIVE_PROFILE="${NATIVE_PROFILE:-terminus2}"
SCORE_MODE="${SCORE_MODE:-dual}"
FEEDBACK_LEVEL="${FEEDBACK_LEVEL:-tests}"
CATEGORY="${CATEGORY:-}"
DIFFICULTY="${DIFFICULTY:-}"
TASKS_DIR_WITH_SKILLS="${TASKS_DIR_WITH_SKILLS:-}"
TASKS_DIR_WITHOUT_SKILLS="${TASKS_DIR_WITHOUT_SKILLS:-}"

# ---------------------------------------------------------------------------
# Evolve scope
# ---------------------------------------------------------------------------
EVOLVE_SKILLS="${EVOLVE_SKILLS:-true}"
EVOLVE_MEMORY="${EVOLVE_MEMORY:-false}"
EVOLVE_PROMPTS="${EVOLVE_PROMPTS:-false}"
EVOLVE_TOOLS="${EVOLVE_TOOLS:-false}"

# ---------------------------------------------------------------------------
# Model / region
# ---------------------------------------------------------------------------
MODEL_ID="${MODEL_ID:-us.anthropic.claude-opus-4-6-v1}"
EVOLVER_MODEL_ID="${EVOLVER_MODEL_ID:-}"
REGION="${REGION:-us-west-2}"
MAX_TOKENS="${MAX_TOKENS:-64000}"
RETRY_MAX="${RETRY_MAX:-}"
RETRY_MIN_WAIT_SEC="${RETRY_MIN_WAIT_SEC:-1.0}"
RETRY_MAX_WAIT_SEC="${RETRY_MAX_WAIT_SEC:-150.0}"
export BEDROCK_RETRY_MAX_ATTEMPTS="${BEDROCK_RETRY_MAX_ATTEMPTS:-15}"
export BEDROCK_READ_TIMEOUT_SEC="${BEDROCK_READ_TIMEOUT_SEC:-600}"
export BEDROCK_CONNECT_TIMEOUT_SEC="${BEDROCK_CONNECT_TIMEOUT_SEC:-30}"

# ---------------------------------------------------------------------------
# Workspace / output
# ---------------------------------------------------------------------------
SEED_WORKSPACE="${SEED_WORKSPACE:-${REPO_ROOT}/seed_workspaces/skillbench}"
RUN_ID="${RUN_ID:-$(date -u +%Y%m%d_%H%M%S)_pid$$}"
RUN_DIR="${RUN_DIR:-${REPO_ROOT}/logs/sb_split_${RUN_ID}}"

mkdir -p "$(dirname "${RUN_DIR}")"
mkdir -p "${RUN_DIR}"

# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------
echo "============================================================"
echo "  SkillBench Train/Test Split (legacy AEvolveEngine)"
echo "  Run ID:        ${RUN_ID}"
echo "  Run dir:       ${RUN_DIR}"
echo "  Phase 1 (train): ${EVOLVE_LIMIT} tasks, batch ${BATCH_SIZE}, train_parallel ${TRAIN_PARALLEL}"
echo "  Phase 2 (test):  ${EVAL_LIMIT:-all remaining} tasks, test_parallel ${TEST_PARALLEL}"
echo "  Total cap:     ${LIMIT:-all tasks}"
echo "  Use skills:    ${USE_SKILLS}"
echo "  Split seed:    ${SPLIT_SEED}"
echo "  Native profile/score: ${NATIVE_PROFILE} / ${SCORE_MODE}"
echo "  Feedback level: ${FEEDBACK_LEVEL}"
echo "  Evolve scope:  skills=${EVOLVE_SKILLS} memory=${EVOLVE_MEMORY} prompts=${EVOLVE_PROMPTS} tools=${EVOLVE_TOOLS}"
echo "  Model:         ${MODEL_ID}"
echo "  Evolver model: ${EVOLVER_MODEL_ID:-<same as solver>}"
echo "  Region:        ${REGION}"
echo "  Max tokens:    ${MAX_TOKENS}"
echo "============================================================"
echo ""

# ---------------------------------------------------------------------------
# Choose python (respect active venv, otherwise uv).
# ---------------------------------------------------------------------------
if [[ -n "${VIRTUAL_ENV:-}" ]]; then
    PY_CMD=(python)
else
    PY_CMD=(env UV_CACHE_DIR=/tmp/uv_cache uv run python)
fi

cmd=(
  "${PY_CMD[@]}"
  "${REPO_ROOT}/examples/skillbench_examples/skillbench_evolve_split.py"
  --evolve-limit "${EVOLVE_LIMIT}"
  --batch-size   "${BATCH_SIZE}"
  --train-parallel "${TRAIN_PARALLEL}"
  --test-parallel  "${TEST_PARALLEL}"
  --use-skills   "${USE_SKILLS}"
  --split-seed   "${SPLIT_SEED}"
  --native-profile "${NATIVE_PROFILE}"
  --score-mode   "${SCORE_MODE}"
  --feedback-level "${FEEDBACK_LEVEL}"
  --model-id     "${MODEL_ID}"
  --region       "${REGION}"
  --max-tokens   "${MAX_TOKENS}"
  --retry-min-wait-sec "${RETRY_MIN_WAIT_SEC}"
  --retry-max-wait-sec "${RETRY_MAX_WAIT_SEC}"
  --evolve-skills  "${EVOLVE_SKILLS}"
  --evolve-memory  "${EVOLVE_MEMORY}"
  --evolve-prompts "${EVOLVE_PROMPTS}"
  --evolve-tools   "${EVOLVE_TOOLS}"
  --seed-workspace "${SEED_WORKSPACE}"
  --run-dir      "${RUN_DIR}"
  -v
)
[[ -n "${EVAL_LIMIT}" ]]               && cmd+=(--eval-limit "${EVAL_LIMIT}")
[[ -n "${LIMIT}" ]]                    && cmd+=(--limit "${LIMIT}")
[[ -n "${RETRY_MAX}" ]]                && cmd+=(--retry-max "${RETRY_MAX}")
[[ -n "${EVOLVER_MODEL_ID}" ]]         && cmd+=(--evolver-model-id "${EVOLVER_MODEL_ID}")
[[ -n "${CATEGORY}" ]]                 && cmd+=(--category "${CATEGORY}")
[[ -n "${DIFFICULTY}" ]]               && cmd+=(--difficulty "${DIFFICULTY}")
[[ -n "${TASKS_DIR_WITH_SKILLS}" ]]    && cmd+=(--tasks-dir-with-skills "${TASKS_DIR_WITH_SKILLS}")
[[ -n "${TASKS_DIR_WITHOUT_SKILLS}" ]] && cmd+=(--tasks-dir-without-skills "${TASKS_DIR_WITHOUT_SKILLS}")

LOG="${RUN_DIR}/evolve.log"
echo "Running: ${cmd[*]}"
echo "Log: ${LOG}"
echo ""

set +e
if command -v stdbuf >/dev/null 2>&1; then
  stdbuf -oL -eL "${cmd[@]}" 2>&1 | tee "${LOG}"
else
  "${cmd[@]}" 2>&1 | tee "${LOG}"
fi
exit_code=${PIPESTATUS[0]}
set -e

echo ""
echo "============================================================"
echo "  SkillBench split run completed"
echo "  Exit code:  ${exit_code}"
echo "  Train:      ${RUN_DIR}/results.train.jsonl"
echo "  Test:       ${RUN_DIR}/results.test.jsonl"
echo "  Combined:   ${RUN_DIR}/results.jsonl"
echo "  Metrics:    ${RUN_DIR}/results.metrics.json"
echo "  Log:        ${LOG}"
echo "============================================================"
exit "${exit_code}"
