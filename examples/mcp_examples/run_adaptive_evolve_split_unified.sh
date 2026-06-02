#!/usr/bin/env bash
# Run MCP-Atlas train/test split via UnifiedEngine.
#
# Two-phase wrapper around examples/mcp_examples/run_adaptive_evolve_all_split_unified.py:
#
#   Phase 1 (TRAIN): evolve on first $EVOLVE_LIMIT tasks in train batches
#                    of $BATCH_SIZE.
#   Phase 2 (TEST):  evaluate $EVAL_LIMIT remaining tasks with the evolved
#                    workspace (no engine).
#
# LIMIT is a global cap applied before the train/test split: first keep at
# most $LIMIT ordered MCP tasks, then train on $EVOLVE_LIMIT and evaluate the
# remaining slice.
#
# Defaults match run_adaptive_evolve_split.sh:
#           EVOLVE_LIMIT=100, BATCH_SIZE=25, LIMIT=500,
#           EVAL_LIMIT=""(all remaining after train).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

EVOLVE_LIMIT="${EVOLVE_LIMIT:-100}"
EVAL_LIMIT="${EVAL_LIMIT:-}"
BATCH_SIZE="${BATCH_SIZE:-25}"
# Train: max parallel solve workers in each Phase 1 batch.
# Effective parallelism is min(TRAIN_PARALLEL, BATCH_SIZE).
TRAIN_PARALLEL="${TRAIN_PARALLEL:-${PARALLEL:-1}}"
# Test: explicit worker count for Phase 2 (no evolve, fully parallelizable).
TEST_PARALLEL="${TEST_PARALLEL:-5}"
PARALLEL_BACKEND="${PARALLEL_BACKEND:-thread}"
LIMIT="${LIMIT:-500}"
SOLVER_MODEL="${SOLVER_MODEL:-us.anthropic.claude-opus-4-6-v1}"
EVOLVER_MODEL="${EVOLVER_MODEL:-${EVOLVER_MODEL_ID:-}}"
REGION="${REGION:-us-west-2}"
MAX_TOKENS="${MAX_TOKENS:-16384}"
export BEDROCK_RETRY_MAX_ATTEMPTS="${BEDROCK_RETRY_MAX_ATTEMPTS:-15}"
export BEDROCK_READ_TIMEOUT_SEC="${BEDROCK_READ_TIMEOUT_SEC:-600}"
export BEDROCK_CONNECT_TIMEOUT_SEC="${BEDROCK_CONNECT_TIMEOUT_SEC:-30}"
JUDGE_MODEL="${JUDGE_MODEL:-${EVAL_MODEL_ID:-us.anthropic.claude-sonnet-4-6}}"
DATASET="${DATASET:-ScaleAI/MCP-Atlas}"
SEED_WORKSPACE="${SEED_WORKSPACE:-${REPO_ROOT}/seed_workspaces/mcp}"
ENV_FILE="${ENV_FILE:-.env}"        # path to .env with MCP API keys; matches legacy usage
DOCKER_IMAGE="${DOCKER_IMAGE:-ghcr.io/scaleapi/mcp-atlas:latest}"
RUN_ID="${RUN_ID:-$(date -u +%Y%m%d_%H%M%S)_pid$$}"
OUTPUT_DIR="${OUTPUT_DIR:-${REPO_ROOT}/logs/unified_mcp_split_${RUN_ID}}"

mkdir -p "$(dirname "${OUTPUT_DIR}")"

echo "=== MCP-Atlas Train/Test Split (Unified) ==="
echo "Run ID:        ${RUN_ID}"
echo "Output dir:    ${OUTPUT_DIR}"
echo "Phase1 evolve: ${EVOLVE_LIMIT} tasks, batch ${BATCH_SIZE}"
echo "Phase2 eval:   ${EVAL_LIMIT:-all remaining}"
echo "Train parallel: ${TRAIN_PARALLEL} (effective=min(${TRAIN_PARALLEL},${BATCH_SIZE})) backend=${PARALLEL_BACKEND}"
echo "Test parallel:  ${TEST_PARALLEL}"
echo "Global cap:    ${LIMIT} tasks before train/test split"
echo "Dataset:       ${DATASET}"
echo "Solver model:  ${SOLVER_MODEL}"
echo "Evolver model: ${EVOLVER_MODEL:-<same as solver>}"
echo "Judge model:   ${JUDGE_MODEL}"
echo "Docker image:  ${DOCKER_IMAGE:-<none>}"
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
  "${REPO_ROOT}/examples/mcp_examples/run_adaptive_evolve_all_split_unified.py"
  --evolve-limit "${EVOLVE_LIMIT}"
  --batch-size "${BATCH_SIZE}"
  --train-parallel "${TRAIN_PARALLEL}"
  --test-parallel "${TEST_PARALLEL}"
  --parallel-backend "${PARALLEL_BACKEND}"
  --limit "${LIMIT}"
  --solver-model "${SOLVER_MODEL}"
  --region "${REGION}"
  --max-tokens "${MAX_TOKENS}"
  --judge-model "${JUDGE_MODEL}"
  --dataset "${DATASET}"
  --seed-workspace "${SEED_WORKSPACE}"
  --output-dir "${OUTPUT_DIR}"
  -v
)
[[ -n "${EVAL_LIMIT}" ]]    && cmd+=(--eval-limit "${EVAL_LIMIT}")
[[ -n "${EVOLVER_MODEL}" ]] && cmd+=(--evolver-model "${EVOLVER_MODEL}")
[[ -n "${ENV_FILE}" ]]      && cmd+=(--env-file "${ENV_FILE}")
[[ -n "${DOCKER_IMAGE}" ]]  && cmd+=(--docker-image "${DOCKER_IMAGE}")

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
echo "=== MCP split run completed ==="
echo "  Exit code:  ${exit_code}"
echo "  Train:      ${OUTPUT_DIR}/results.train.jsonl"
echo "  Test:       ${OUTPUT_DIR}/results.test.jsonl"
echo "  Combined:   ${OUTPUT_DIR}/results.jsonl"
echo "  Metrics:    ${OUTPUT_DIR}/results.metrics.json"
echo "  Log:        ${LOG}"
exit "${exit_code}"
