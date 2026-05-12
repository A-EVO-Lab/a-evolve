#!/usr/bin/env bash
# Run Terminal-Bench 2.0 train/test split via UnifiedEngine.
#
# Two-phase wrapper around examples/tb_examples/batch_evolve_terminal_split_unified.py
# (the unified counterpart to the legacy run_evolution.sh):
#
#   Phase 1 (TRAIN): evolve on first $EVOLVE_LIMIT tasks in train batches
#                    of $BATCH_SIZE (max skills $MAX_SKILLS).
#   Phase 2 (TEST):  evaluate $EVAL_LIMIT remaining tasks with the evolved
#                    workspace (no engine, no further evolution).
#
# Defaults follow the legacy run_evolution.sh shape:
#   EVOLVE_LIMIT=20 BATCH_SIZE=5 MAX_SKILLS=6 EVAL_LIMIT=""(all remaining)
#
# Usage:
#   bash examples/tb_examples/run_evolve_split_unified.sh <RUN_NAME>
#   EVOLVE_LIMIT=30 EVAL_LIMIT=20 BATCH_SIZE=5 \
#     bash examples/tb_examples/run_evolve_split_unified.sh <RUN_NAME>
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

RUN_NAME="${1:?Usage: $0 <RUN_NAME> [--evolve-limit N] [--eval-limit N] [--batch-size N] [--max-skills N]}"
shift || true

EVOLVE_LIMIT="${EVOLVE_LIMIT:-20}"
EVAL_LIMIT="${EVAL_LIMIT:-}"
BATCH_SIZE="${BATCH_SIZE:-5}"
# Train: max parallel solve workers in each Phase 1 batch.
# Effective parallelism is min(TRAIN_PARALLEL, BATCH_SIZE).
TRAIN_PARALLEL="${TRAIN_PARALLEL:-${PARALLEL:-6}}"
# Test: explicit worker count for Phase 2 (no evolve, fully parallelizable).
TEST_PARALLEL="${TEST_PARALLEL:-6}"
PARALLEL_BACKEND="${PARALLEL_BACKEND:-thread}"
MAX_SKILLS="${MAX_SKILLS:-6}"
MODEL_ID="${MODEL_ID:-us.anthropic.claude-opus-4-6-v1}"
SOLVER="${SOLVER:-react}"
EVOLVER_MODEL_ID="${EVOLVER_MODEL_ID:-}"
export BEDROCK_RETRY_MAX_ATTEMPTS="${BEDROCK_RETRY_MAX_ATTEMPTS:-15}"
export BEDROCK_READ_TIMEOUT_SEC="${BEDROCK_READ_TIMEOUT_SEC:-600}"
export BEDROCK_CONNECT_TIMEOUT_SEC="${BEDROCK_CONNECT_TIMEOUT_SEC:-30}"
REGION="${REGION:-us-west-2}"
MAX_TOKENS="${MAX_TOKENS:-16384}"
CHALLENGES_DIR="${CHALLENGES_DIR:-}"
SEED_WORKSPACE="${SEED_WORKSPACE:-${REPO_ROOT}/seed_workspaces/terminal}"
LOG_DIR="${LOG_DIR:-${REPO_ROOT}/logs/unified_tb_split_${RUN_NAME}}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --evolve-limit)   EVOLVE_LIMIT="$2";       shift 2 ;;
        --eval-limit)     EVAL_LIMIT="$2";         shift 2 ;;
        --batch-size)     BATCH_SIZE="$2";         shift 2 ;;
        --max-skills)     MAX_SKILLS="$2";         shift 2 ;;
        --model-id)       MODEL_ID="$2";           shift 2 ;;
        --solver)         SOLVER="$2";             shift 2 ;;
        --region)         REGION="$2";             shift 2 ;;
        --challenges-dir) CHALLENGES_DIR="$2";     shift 2 ;;
        *)                echo "Unknown flag: $1"; exit 1 ;;
    esac
done

mkdir -p "${LOG_DIR}"

echo "=== Terminal-Bench Train/Test Split (Unified): ${RUN_NAME} ==="
echo "Log dir:       ${LOG_DIR}"
echo "Phase1 evolve: ${EVOLVE_LIMIT} tasks, batch ${BATCH_SIZE}, max-skills ${MAX_SKILLS}"
echo "Phase2 eval:   ${EVAL_LIMIT:-all remaining}"
echo "Train parallel: ${TRAIN_PARALLEL} (effective=min(${TRAIN_PARALLEL},${BATCH_SIZE}))"
echo "Test parallel:  ${TEST_PARALLEL}"
echo "Parallel backend: ${PARALLEL_BACKEND}"
echo "Evolve flags:  trajectory-only, skills-only, protect-skills"
echo "Solver:        ${SOLVER}"
echo "Model:         ${MODEL_ID}"
echo "Evolver model: ${EVOLVER_MODEL_ID:-<same as solver>}"
echo "Region:        ${REGION}"
[[ -n "${CHALLENGES_DIR}" ]] && echo "Challenges:    ${CHALLENGES_DIR}"
echo ""

# Choose python runner: respect an already-active venv; otherwise fall
# back to `uv run python` (matches legacy run_evolution.sh / run_baseline.sh).
if [[ -n "${VIRTUAL_ENV:-}" ]]; then
    PY_CMD=(python)
else
    PY_CMD=(env UV_CACHE_DIR=/tmp/uv_cache uv run python)
fi

cmd=(
  "${PY_CMD[@]}"
  "${REPO_ROOT}/examples/tb_examples/batch_evolve_terminal_split_unified.py"
  "${RUN_NAME}"
  --evolve-limit "${EVOLVE_LIMIT}"
  --batch-size "${BATCH_SIZE}"
  --train-parallel "${TRAIN_PARALLEL}"
  --test-parallel "${TEST_PARALLEL}"
  --parallel-backend "${PARALLEL_BACKEND}"
  --max-skills "${MAX_SKILLS}"
  --model-id "${MODEL_ID}"
  --solver "${SOLVER}"
  --region "${REGION}"
  --max-tokens "${MAX_TOKENS}"
  --seed-workspace "${SEED_WORKSPACE}"
  --log-dir "${LOG_DIR}"
  -v
)
[[ -n "${EVAL_LIMIT}" ]]        && cmd+=(--eval-limit "${EVAL_LIMIT}")
[[ -n "${EVOLVER_MODEL_ID}" ]] && cmd+=(--evolver-model-id "${EVOLVER_MODEL_ID}")
[[ -n "${CHALLENGES_DIR}" ]]   && cmd+=(--challenges-dir "${CHALLENGES_DIR}")

LOG="${LOG_DIR}/evolve.log"
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
echo "=== Terminal-Bench split run completed ==="
echo "  Exit code:  ${exit_code}"
echo "  Train:      ${LOG_DIR}/results.train.jsonl"
echo "  Test:       ${LOG_DIR}/results.test.jsonl"
echo "  Combined:   ${LOG_DIR}/results.jsonl"
echo "  Metrics:    ${LOG_DIR}/results.metrics.json"
echo "  Log:        ${LOG}"
exit "${exit_code}"
