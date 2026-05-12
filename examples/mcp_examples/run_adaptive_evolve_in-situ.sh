#!/usr/bin/env bash
# Run MCP-Atlas evolution via legacy AdaptiveEvolveEngine.
#
# In-situ setting: solve tasks in batches and evolve the same workspace after
# each batch. This is the non-unified counterpart to
# run_adaptive_evolve_in-situ_unified.sh and follows docs/mcp-atlas-demo.md.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

BATCH_SIZE="${BATCH_SIZE:-30}"
LIMIT="${LIMIT:-500}"
SOLVER_MODEL="${SOLVER_MODEL:-us.anthropic.claude-opus-4-6-v1}"
EVOLVER_MODEL="${EVOLVER_MODEL:-${EVOLVER_MODEL_ID:-${SOLVER_MODEL}}}"
REGION="${REGION:-us-west-2}"
MAX_TOKENS="${MAX_TOKENS:-16384}"
export BEDROCK_RETRY_MAX_ATTEMPTS="${BEDROCK_RETRY_MAX_ATTEMPTS:-15}"
export BEDROCK_READ_TIMEOUT_SEC="${BEDROCK_READ_TIMEOUT_SEC:-600}"
export BEDROCK_CONNECT_TIMEOUT_SEC="${BEDROCK_CONNECT_TIMEOUT_SEC:-30}"
JUDGE_MODEL="${JUDGE_MODEL:-${EVAL_MODEL_ID:-us.anthropic.claude-sonnet-4-6}}"
SEED_WORKSPACE="${SEED_WORKSPACE:-${REPO_ROOT}/seed_workspaces/mcp}"
ENV_FILE="${ENV_FILE:-.env}"        # path to .env with MCP API keys; matches legacy usage
DOCKER_IMAGE="${DOCKER_IMAGE:-ghcr.io/scaleapi/mcp-atlas:latest}"
RUN_ID="${RUN_ID:-$(date -u +%Y%m%d_%H%M%S)_pid$$}"
WORK_DIR="${WORK_DIR:-${REPO_ROOT}/evolution_workdir/mcp_adaptive_${RUN_ID}}"
OUTPUT_DIR="${OUTPUT_DIR:-${REPO_ROOT}/logs/mcp_adaptive_${RUN_ID}}"
export MCP_CONTAINER_NAME="${MCP_CONTAINER_NAME:-mcp-atlas-adaptive-${RUN_ID}}"

mkdir -p "$(dirname "${OUTPUT_DIR}")"

echo "=== MCP-Atlas In-Situ Evolution (Legacy AdaptiveEvolveEngine) ==="
echo "Run ID:        ${RUN_ID}"
echo "Output dir:    ${OUTPUT_DIR}"
echo "Workspace:     ${WORK_DIR}"
echo "Batch size:    ${BATCH_SIZE}"
echo "Limit:         ${LIMIT}"
echo "Benchmark:     MCP-Atlas"
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
  "${REPO_ROOT}/examples/mcp_examples/adaptive_evolve_all.py"
  --solver-model "${SOLVER_MODEL}"
  --evolver-model "${EVOLVER_MODEL}"
  --judge-model "${JUDGE_MODEL}"
  --region "${REGION}"
  --max-tokens "${MAX_TOKENS}"
  --limit "${LIMIT}"
  --batch-size "${BATCH_SIZE}"
  --seed-workspace "${SEED_WORKSPACE}"
  --work-dir "${WORK_DIR}"
  --output-dir "${OUTPUT_DIR}"
)
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
echo "=== MCP legacy in-situ run completed ==="
echo "  Exit code:  ${exit_code}"
echo "  Summary:    ${OUTPUT_DIR}/summary.csv"
echo "  Log:        ${LOG}"
exit "${exit_code}"
