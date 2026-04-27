#!/usr/bin/env bash
# SWE legacy train/test split wrapper.
#
# Two-phase orchestrator (single-process) around
# examples/swe_examples/evolve_sequential_split.py:
#
#   Phase 1 (TRAIN): evolve on first $EVOLVE_LIMIT tasks with batches of
#                    $BATCH_SIZE, using GuidedSynthesisEngine.
#   Phase 2 (TEST):  evaluate $EVAL_LIMIT remaining tasks (or all remaining
#                    when EVAL_LIMIT is empty) with the evolved workspace,
#                    no further evolution.
#
# Defaults follow docs/algorithms/guided-synth.md recommendations:
#   --solver-proposes --verification-focus --efficiency-prompt
#   --feedback none --max-steps 140 --window-size 70
#   --batch-size 20 --parallel 20
#   dataset = princeton-nlp/SWE-bench_Verified
#   total LIMIT = 500 (split: EVOLVE_LIMIT=100 train + 400 test)
#
# Usage:
#   bash examples/swe_examples/run_swe_evolve_split.sh                  # all defaults
#   EVOLVE_LIMIT=50 LIMIT=200 bash examples/swe_examples/run_swe_evolve_split.sh
#   nohup bash examples/swe_examples/run_swe_evolve_split.sh &
#
# All knobs are env-var configurable (no CLI parsing here -- mirrors TB's
# run_evolution.sh shape).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# ---------------------------------------------------------------------------
# Split / batch defaults
# ---------------------------------------------------------------------------
LIMIT="${LIMIT:-500}"                    # Total tasks (train + test cap)
EVOLVE_LIMIT="${EVOLVE_LIMIT:-100}"      # Phase 1 train tasks
EVAL_LIMIT="${EVAL_LIMIT:-}"             # Phase 2 test tasks ('' = all remaining)
BATCH_SIZE="${BATCH_SIZE:-20}"

# Parallelism: Phase 1 effective = min(TRAIN_PARALLEL, BATCH_SIZE).
TRAIN_PARALLEL="${TRAIN_PARALLEL:-${PARALLEL:-20}}"
TEST_PARALLEL="${TEST_PARALLEL:-20}"

# ---------------------------------------------------------------------------
# Agent / evolver knobs (guided-synth recommended setting)
# ---------------------------------------------------------------------------
FEEDBACK="${FEEDBACK:-none}"
SOLVER_PROPOSES="${SOLVER_PROPOSES:-true}"
VERIFICATION_FOCUS="${VERIFICATION_FOCUS:-true}"
EFFICIENCY_PROMPT="${EFFICIENCY_PROMPT:-true}"
MAX_STEPS="${MAX_STEPS:-140}"
WINDOW_SIZE="${WINDOW_SIZE:-70}"

# ---------------------------------------------------------------------------
# Model / region
# ---------------------------------------------------------------------------
MODEL_ID="${MODEL_ID:-us.anthropic.claude-opus-4-6-v1}"
EVOLVER_MODEL_ID="${EVOLVER_MODEL_ID:-}"
REGION="${REGION:-us-west-2}"
MAX_TOKENS="${MAX_TOKENS:-16384}"
export BEDROCK_RETRY_MAX_ATTEMPTS="${BEDROCK_RETRY_MAX_ATTEMPTS:-15}"
export BEDROCK_READ_TIMEOUT_SEC="${BEDROCK_READ_TIMEOUT_SEC:-600}"
export BEDROCK_CONNECT_TIMEOUT_SEC="${BEDROCK_CONNECT_TIMEOUT_SEC:-30}"

# ---------------------------------------------------------------------------
# Dataset / workspace / output
# ---------------------------------------------------------------------------
DATASET="${DATASET:-princeton-nlp/SWE-bench_Verified}"
SEED_WORKSPACE="${SEED_WORKSPACE:-${REPO_ROOT}/seed_workspaces/swe}"
RUN_ID="${RUN_ID:-$(date -u +%Y%m%d_%H%M%S)_pid$$}"
OUTPUT_DIR="${OUTPUT_DIR:-${REPO_ROOT}/logs/swe_split_${RUN_ID}}"

mkdir -p "$(dirname "${OUTPUT_DIR}")"
mkdir -p "${OUTPUT_DIR}"

# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------
echo "============================================================"
echo "  SWE Train/Test Split (legacy GuidedSynthesisEngine)"
echo "  Run ID:        ${RUN_ID}"
echo "  Output dir:    ${OUTPUT_DIR}"
echo "  Phase 1 (train): ${EVOLVE_LIMIT} tasks, batch ${BATCH_SIZE}, parallel ${TRAIN_PARALLEL}"
echo "  Phase 2 (test):  ${EVAL_LIMIT:-all remaining} tasks, parallel ${TEST_PARALLEL}"
echo "  Total cap:     ${LIMIT} tasks"
echo "  Dataset:       ${DATASET}"
echo "  Feedback:      ${FEEDBACK}"
echo "  Solver-proposes:    ${SOLVER_PROPOSES}"
echo "  Verification-focus: ${VERIFICATION_FOCUS}"
echo "  Efficiency-prompt:  ${EFFICIENCY_PROMPT}"
echo "  Max steps / window: ${MAX_STEPS} / ${WINDOW_SIZE}"
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
  "${REPO_ROOT}/examples/swe_examples/evolve_sequential_split.py"
  --evolve-limit "${EVOLVE_LIMIT}"
  --limit "${LIMIT}"
  --batch-size "${BATCH_SIZE}"
  --train-parallel "${TRAIN_PARALLEL}"
  --test-parallel "${TEST_PARALLEL}"
  --feedback "${FEEDBACK}"
  --max-steps "${MAX_STEPS}"
  --window-size "${WINDOW_SIZE}"
  --model-id "${MODEL_ID}"
  --region "${REGION}"
  --max-tokens "${MAX_TOKENS}"
  --dataset "${DATASET}"
  --seed-workspace "${SEED_WORKSPACE}"
  --output-dir "${OUTPUT_DIR}"
  -v
)
[[ -n "${EVAL_LIMIT}" ]]               && cmd+=(--eval-limit "${EVAL_LIMIT}")
[[ -n "${EVOLVER_MODEL_ID}" ]]         && cmd+=(--evolver-model-id "${EVOLVER_MODEL_ID}")
[[ "${SOLVER_PROPOSES}" == "true" ]]    && cmd+=(--solver-proposes)
[[ "${VERIFICATION_FOCUS}" == "true" ]] && cmd+=(--verification-focus)
[[ "${EFFICIENCY_PROMPT}" == "true" ]]  && cmd+=(--efficiency-prompt)

LOG="${OUTPUT_DIR}/evolve.log"
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
echo "  SWE split run completed"
echo "  Exit code:  ${exit_code}"
echo "  Train:      ${OUTPUT_DIR}/results.train.jsonl"
echo "  Test:       ${OUTPUT_DIR}/results.test.jsonl"
echo "  Combined:   ${OUTPUT_DIR}/results.jsonl"
echo "  Metrics:    ${OUTPUT_DIR}/results.metrics.json"
echo "  Log:        ${LOG}"
echo "============================================================"
exit "${exit_code}"
