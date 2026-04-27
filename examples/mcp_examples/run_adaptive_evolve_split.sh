#!/usr/bin/env bash
# MCP-Atlas legacy train/test split wrapper.
#
# Two-phase orchestrator (single-process) around
# examples/mcp_examples/adaptive_evolve_split.py:
#
#   Phase 1 (TRAIN): evolve on first $EVOLVE_LIMIT tasks with batches of
#                    $BATCH_SIZE, using AdaptiveEvolveEngine (legacy MCP).
#   Phase 2 (TEST):  evaluate $EVAL_LIMIT remaining tasks (or all remaining
#                    when EVAL_LIMIT is empty) with the evolved workspace,
#                    no further evolution.
#
# Mirrors examples/swe_examples/run_swe_evolve_split.sh shape but uses the
# legacy MCP engine (NOT UnifiedEngine — for that, see run_adaptive_evolve_split_unified.sh).
#
# All knobs are env-var configurable.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# ---------------------------------------------------------------------------
# Split / batch defaults
# ---------------------------------------------------------------------------
LIMIT="${LIMIT:-500}"                    # Total tasks (train + test cap; 500 = full MCP-Atlas)
EVOLVE_LIMIT="${EVOLVE_LIMIT:-100}"      # Phase 1 train tasks
EVAL_LIMIT="${EVAL_LIMIT:-}"             # Phase 2 test tasks ('' = all remaining)
BATCH_SIZE="${BATCH_SIZE:-25}"

# ---------------------------------------------------------------------------
# Model / region
# ---------------------------------------------------------------------------
SOLVER_MODEL="${SOLVER_MODEL:-${MODEL_ID:-us.anthropic.claude-opus-4-6-v1}}"
EVOLVER_MODEL="${EVOLVER_MODEL:-${EVOLVER_MODEL_ID:-${SOLVER_MODEL}}}"
JUDGE_MODEL="${JUDGE_MODEL:-us.anthropic.claude-sonnet-4-20250514-v1:0}"
REGION="${REGION:-us-west-2}"
MAX_TOKENS="${MAX_TOKENS:-16384}"
export BEDROCK_RETRY_MAX_ATTEMPTS="${BEDROCK_RETRY_MAX_ATTEMPTS:-15}"
export BEDROCK_READ_TIMEOUT_SEC="${BEDROCK_READ_TIMEOUT_SEC:-600}"
export BEDROCK_CONNECT_TIMEOUT_SEC="${BEDROCK_CONNECT_TIMEOUT_SEC:-30}"

# ---------------------------------------------------------------------------
# MCP runtime
# ---------------------------------------------------------------------------
DOCKER_IMAGE="${DOCKER_IMAGE:-ghcr.io/scaleapi/mcp-atlas:latest}"
EXTERNAL_CONTAINER_URL="${EXTERNAL_CONTAINER_URL:-}"
ENV_FILE="${ENV_FILE:-${REPO_ROOT}/.env}"
SEED_WORKSPACE="${SEED_WORKSPACE:-${REPO_ROOT}/seed_workspaces/mcp}"
RUN_ID="${RUN_ID:-$(date -u +%Y%m%d_%H%M%S)_pid$$}"
WORK_DIR="${WORK_DIR:-${REPO_ROOT}/evolution_workdir/mcp_split_${RUN_ID}}"
OUTPUT_DIR="${OUTPUT_DIR:-${REPO_ROOT}/logs/mcp_split_${RUN_ID}}"
export MCP_CONTAINER_NAME="${MCP_CONTAINER_NAME:-mcp-atlas-split-${RUN_ID}}"

mkdir -p "$(dirname "${OUTPUT_DIR}")"
mkdir -p "${OUTPUT_DIR}"

# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------
echo "============================================================"
echo "  MCP-Atlas Train/Test Split (legacy AdaptiveEvolveEngine)"
echo "  Run ID:        ${RUN_ID}"
echo "  Output dir:    ${OUTPUT_DIR}"
echo "  Workspace:     ${WORK_DIR}"
echo "  Phase 1 (train): ${EVOLVE_LIMIT} tasks, batch ${BATCH_SIZE}"
echo "  Phase 2 (test):  ${EVAL_LIMIT:-all remaining} tasks"
echo "  Total cap:     ${LIMIT} tasks"
echo "  Solver model:  ${SOLVER_MODEL}"
echo "  Evolver model: ${EVOLVER_MODEL}"
echo "  Judge model:   ${JUDGE_MODEL}"
echo "  Docker image:  ${DOCKER_IMAGE:-<external>}"
echo "  External URL:  ${EXTERNAL_CONTAINER_URL:-<none>}"
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
  "${REPO_ROOT}/examples/mcp_examples/adaptive_evolve_split.py"
  --solver-model  "${SOLVER_MODEL}"
  --evolver-model "${EVOLVER_MODEL}"
  --judge-model   "${JUDGE_MODEL}"
  --region        "${REGION}"
  --max-tokens    "${MAX_TOKENS}"
  --limit         "${LIMIT}"
  --evolve-limit  "${EVOLVE_LIMIT}"
  --batch-size    "${BATCH_SIZE}"
  --seed-workspace "${SEED_WORKSPACE}"
  --work-dir      "${WORK_DIR}"
  --output-dir    "${OUTPUT_DIR}"
)
[[ -n "${EVAL_LIMIT}" ]]              && cmd+=(--eval-limit "${EVAL_LIMIT}")
[[ -n "${ENV_FILE}" ]]                && cmd+=(--env-file "${ENV_FILE}")
[[ -n "${DOCKER_IMAGE}" ]]            && cmd+=(--docker-image "${DOCKER_IMAGE}")
[[ -n "${EXTERNAL_CONTAINER_URL}" ]]  && cmd+=(--external-container-url "${EXTERNAL_CONTAINER_URL}")

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
echo "  MCP split run completed"
echo "  Exit code:  ${exit_code}"
echo "  Train:      ${OUTPUT_DIR}/results.train.jsonl"
echo "  Test:       ${OUTPUT_DIR}/results.test.jsonl"
echo "  Combined:   ${OUTPUT_DIR}/results.jsonl"
echo "  Metrics:    ${OUTPUT_DIR}/results.metrics.json"
echo "  Log:        ${LOG}"
echo "============================================================"
exit "${exit_code}"
