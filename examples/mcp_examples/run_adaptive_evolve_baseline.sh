#!/usr/bin/env bash
# Run MCP-Atlas adaptive no-evolution baseline.
#
# This is the no-evolution counterpart to adaptive_evolve_all.py /
# run_adaptive_evolve_split_unified.sh. It uses the same MCP agent family,
# code-execution workspace preparation, MCP-Atlas docker image, and Bedrock
# judge path, but never constructs an EvolutionEngine and never mutates the
# workspace after solving a batch.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

SOLVER_MODEL="${SOLVER_MODEL:-us.anthropic.claude-opus-4-6-v1}"
JUDGE_MODEL="${JUDGE_MODEL:-${EVAL_MODEL_ID:-us.anthropic.claude-sonnet-4-20250514-v1:0}}"
REGION="${REGION:-us-west-2}"
MAX_TOKENS="${MAX_TOKENS:-16384}"
LIMIT="${LIMIT:-500}"
BATCH_SIZE="${BATCH_SIZE:-30}"
WORKERS="${WORKERS:-5}"
SEED_WORKSPACE="${SEED_WORKSPACE:-${REPO_ROOT}/seed_workspaces/mcp}"
ENV_FILE="${ENV_FILE:-.env}"
DOCKER_IMAGE="${DOCKER_IMAGE:-ghcr.io/scaleapi/mcp-atlas:latest}"
EXTERNAL_CONTAINER_URL="${EXTERNAL_CONTAINER_URL:-}"
RUN_ID="${RUN_ID:-$(date -u +%Y%m%d_%H%M%S)_pid$$}"
WORK_DIR="${WORK_DIR:-${REPO_ROOT}/evolution_workdir/mcp_adaptive_baseline_${RUN_ID}}"
OUTPUT_DIR="${OUTPUT_DIR:-${REPO_ROOT}/logs/mcp_adaptive_baseline_${RUN_ID}}"

export BEDROCK_RETRY_MAX_ATTEMPTS="${BEDROCK_RETRY_MAX_ATTEMPTS:-15}"
export BEDROCK_READ_TIMEOUT_SEC="${BEDROCK_READ_TIMEOUT_SEC:-600}"
export BEDROCK_CONNECT_TIMEOUT_SEC="${BEDROCK_CONNECT_TIMEOUT_SEC:-30}"

mkdir -p "${OUTPUT_DIR}"

echo "=== MCP Adaptive Baseline (No Evolution) ==="
echo "Run ID:        ${RUN_ID}"
echo "Output dir:    ${OUTPUT_DIR}"
echo "Workspace:     ${WORK_DIR}"
echo "Tasks:         ${LIMIT:-all}"
echo "Batch size:    ${BATCH_SIZE}"
echo "Workers:       ${WORKERS}"
echo "Solver model:  ${SOLVER_MODEL}"
echo "Judge model:   ${JUDGE_MODEL}"
echo "Docker image:  ${DOCKER_IMAGE:-<none>}"
echo "Env file:      ${ENV_FILE:-<none>}"
echo "Region:        ${REGION}"
echo ""

if [[ -n "${VIRTUAL_ENV:-}" ]]; then
    PY_CMD=(python)
else
    PY_CMD=(env UV_CACHE_DIR=/tmp/uv_cache uv run python)
fi

cmd=(
  "${PY_CMD[@]}"
  "${REPO_ROOT}/examples/mcp_examples/adaptive_evolve_baseline.py"
  --solver-model "${SOLVER_MODEL}"
  --judge-model "${JUDGE_MODEL}"
  --region "${REGION}"
  --max-tokens "${MAX_TOKENS}"
  --limit "${LIMIT}"
  --batch-size "${BATCH_SIZE}"
  --workers "${WORKERS}"
  --seed-workspace "${SEED_WORKSPACE}"
  --work-dir "${WORK_DIR}"
  --output-dir "${OUTPUT_DIR}"
)
[[ -n "${ENV_FILE}" ]] && cmd+=(--env-file "${ENV_FILE}")
[[ -n "${DOCKER_IMAGE}" ]] && cmd+=(--docker-image "${DOCKER_IMAGE}")
[[ -n "${EXTERNAL_CONTAINER_URL}" ]] && cmd+=(--external-container-url "${EXTERNAL_CONTAINER_URL}")

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
echo "=== MCP adaptive baseline completed ==="
echo "  Exit code: ${exit_code}"
echo "  Summary:   ${OUTPUT_DIR}/summary.csv"
echo "  Log:       ${LOG}"
exit "${exit_code}"
