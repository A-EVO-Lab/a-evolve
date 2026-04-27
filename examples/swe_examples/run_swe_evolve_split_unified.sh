#!/usr/bin/env bash
# Run SWE-bench Verified train/test split via UnifiedEngine.
#
# Two-phase wrapper around examples/swe_examples/evolve_sequential_split_unified.py:
#
#   Phase 1 (TRAIN): evolve on first $EVOLVE_LIMIT tasks in train batches
#                    of $BATCH_SIZE.
#   Phase 2 (TEST):  evaluate $EVAL_LIMIT remaining tasks with the evolved
#                    workspace (no engine).
#
# Defaults follow the v32g full recipe (matches run_swe_evolve_split.sh):
#   --solver-proposes --verification-focus --efficiency-prompt
#   --feedback none --max-steps 140 --window-size 70
#   --batch-size 20 --parallel 20
#   dataset = princeton-nlp/SWE-bench_Verified
#   total LIMIT = 500 (split: EVOLVE_LIMIT=100 train + 400 test)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

EVOLVE_LIMIT="${EVOLVE_LIMIT:-100}"
EVAL_LIMIT="${EVAL_LIMIT:-}"
LIMIT="${LIMIT:-500}"
BATCH_SIZE="${BATCH_SIZE:-20}"
# Train: max parallel solve workers in each Phase 1 batch.
# Effective parallelism is min(TRAIN_PARALLEL, BATCH_SIZE).
TRAIN_PARALLEL="${TRAIN_PARALLEL:-${PARALLEL:-20}}"
# Test: explicit worker count for Phase 2 (no evolve, fully parallelizable).
TEST_PARALLEL="${TEST_PARALLEL:-20}"
PARALLEL_BACKEND="${PARALLEL_BACKEND:-process}"
FEEDBACK="${FEEDBACK:-none}"
SOLVER_PROPOSES="${SOLVER_PROPOSES:-true}"
VERIFICATION_FOCUS="${VERIFICATION_FOCUS:-true}"
EFFICIENCY_PROMPT="${EFFICIENCY_PROMPT:-true}"
MODEL_ID="${MODEL_ID:-us.anthropic.claude-opus-4-6-v1}"
EVOLVER_MODEL_ID="${EVOLVER_MODEL_ID:-}"
export BEDROCK_RETRY_MAX_ATTEMPTS="${BEDROCK_RETRY_MAX_ATTEMPTS:-15}"
export BEDROCK_READ_TIMEOUT_SEC="${BEDROCK_READ_TIMEOUT_SEC:-600}"
export BEDROCK_CONNECT_TIMEOUT_SEC="${BEDROCK_CONNECT_TIMEOUT_SEC:-30}"
REGION="${REGION:-us-west-2}"
MAX_TOKENS="${MAX_TOKENS:-16384}"
MAX_STEPS="${MAX_STEPS:-140}"
WINDOW_SIZE="${WINDOW_SIZE:-70}"
DATASET="${DATASET:-princeton-nlp/SWE-bench_Verified}"
SEED_WORKSPACE="${SEED_WORKSPACE:-${REPO_ROOT}/seed_workspaces/swe}"
RUN_ID="${RUN_ID:-$(date -u +%Y%m%d_%H%M%S)_pid$$}"
OUTPUT_DIR="${OUTPUT_DIR:-${REPO_ROOT}/logs/unified_swe_split_${RUN_ID}}"

mkdir -p "$(dirname "${OUTPUT_DIR}")"

echo "=== SWE Train/Test Split (Unified) ==="
echo "Run ID:        ${RUN_ID}"
echo "Output dir:    ${OUTPUT_DIR}"
echo "Phase1 evolve: ${EVOLVE_LIMIT} tasks, batch ${BATCH_SIZE}"
echo "Phase2 eval:   ${EVAL_LIMIT:-all remaining}"
echo "Total cap:     ${LIMIT:-all tasks}"
echo "Train parallel: ${TRAIN_PARALLEL} (effective=min(${TRAIN_PARALLEL},${BATCH_SIZE}))"
echo "Test parallel:  ${TEST_PARALLEL}"
echo "Parallel backend: ${PARALLEL_BACKEND}"
echo "Feedback:      ${FEEDBACK}"
echo "Solver proposes: ${SOLVER_PROPOSES}"
echo "Dataset:       ${DATASET}"
echo "Model:         ${MODEL_ID}"
echo "Evolver model: ${EVOLVER_MODEL_ID:-<same as solver>}"
echo "Region:        ${REGION}"
echo ""

# Choose python runner: respect an already-active venv; otherwise fall
# back to `uv run python` (matches legacy TB run_evolution.sh / run_baseline.sh).
if [[ -n "${VIRTUAL_ENV:-}" ]]; then
    PY_CMD=(python)
else
    PY_CMD=(env UV_CACHE_DIR=/tmp/uv_cache uv run python)
fi

cmd=(
  "${PY_CMD[@]}"
  "${REPO_ROOT}/examples/swe_examples/evolve_sequential_split_unified.py"
  --evolve-limit "${EVOLVE_LIMIT}"
  --batch-size "${BATCH_SIZE}"
  --train-parallel "${TRAIN_PARALLEL}"
  --test-parallel "${TEST_PARALLEL}"
  --parallel-backend "${PARALLEL_BACKEND}"
  --feedback "${FEEDBACK}"
  --model-id "${MODEL_ID}"
  --region "${REGION}"
  --max-tokens "${MAX_TOKENS}"
  --max-steps "${MAX_STEPS}"
  --window-size "${WINDOW_SIZE}"
  --dataset "${DATASET}"
  --seed-workspace "${SEED_WORKSPACE}"
  --output-dir "${OUTPUT_DIR}"
  -v
)
[[ -n "${EVAL_LIMIT}" ]]        && cmd+=(--eval-limit "${EVAL_LIMIT}")
[[ -n "${LIMIT}" ]]             && cmd+=(--limit "${LIMIT}")
[[ -n "${EVOLVER_MODEL_ID}" ]] && cmd+=(--evolver-model-id "${EVOLVER_MODEL_ID}")
[[ "${SOLVER_PROPOSES}" == "true" ]] && cmd+=(--solver-proposes)
[[ "${VERIFICATION_FOCUS}" == "true" ]] && cmd+=(--verification-focus)
[[ "${EFFICIENCY_PROMPT}" == "true" ]] && cmd+=(--efficiency-prompt)

LOG="${OUTPUT_DIR}/evolve.log"
mkdir -p "${OUTPUT_DIR}"
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
echo "=== SWE split run completed ==="
echo "  Exit code:  ${exit_code}"
echo "  Train:      ${OUTPUT_DIR}/results.train.jsonl"
echo "  Test:       ${OUTPUT_DIR}/results.test.jsonl"
echo "  Combined:   ${OUTPUT_DIR}/results.jsonl"
echo "  Metrics:    ${OUTPUT_DIR}/results.metrics.json"
echo "  Log:        ${LOG}"
exit "${exit_code}"
