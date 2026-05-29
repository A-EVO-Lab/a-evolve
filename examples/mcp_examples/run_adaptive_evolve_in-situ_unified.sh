#!/usr/bin/env bash
# Run MCP-Atlas evolution via UnifiedEngine (Phase 1).
#
# Unified counterpart to examples/mcp_examples/adaptive_evolve_all.py.
# Engine-level parity with AdaptiveEvolveEngine — see
# docs/algorithms/unified-equivalence-audit.md and
# docs/mcp-atlas-demo-unified.md.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

CYCLES="${CYCLES:-}"
PASSES="${PASSES:-}"
CYCLE_PER_BATCH="${CYCLE_PER_BATCH:-}"
BATCH_SIZE="${BATCH_SIZE:-30}"
PARALLEL="${PARALLEL:-1}"
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
OUTPUT_DIR="${OUTPUT_DIR:-${REPO_ROOT}/logs/unified_mcp_${RUN_ID}}"

mkdir -p "$(dirname "${OUTPUT_DIR}")"

echo "=== MCP-Atlas Unified Run ==="
echo "Run ID:        ${RUN_ID}"
echo "Output dir:    ${OUTPUT_DIR}"
echo "Cycles:        ${CYCLES:-full sweep}"
echo "Batch size:    ${BATCH_SIZE}"
echo "Parallel:      ${PARALLEL} (${PARALLEL_BACKEND})"
echo "Limit:         ${LIMIT}"
echo "Dataset:       ${DATASET}"
echo "Solver model:  ${SOLVER_MODEL}"
echo "Evolver model: ${EVOLVER_MODEL:-<same as solver>}"
echo "Judge model:   ${JUDGE_MODEL}"
echo "Docker image:  ${DOCKER_IMAGE:-<none>}"
echo "Region:        ${REGION}"
echo "Skill mode:    $([ "${MCP_SKILL_LAZY:-1}" = "0" ] && echo "eager (keyword top-3 inline)" || echo "lazy (read_skill tool, all listed)")"
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
  "${REPO_ROOT}/examples/mcp_examples/run_adaptive_evolve_all_unified.py"
  --batch-size "${BATCH_SIZE}"
  --parallel "${PARALLEL}"
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
[[ -n "${CYCLES}" ]] && cmd+=(--cycles "${CYCLES}")
[[ -n "${EVOLVER_MODEL}" ]] && cmd+=(--evolver-model "${EVOLVER_MODEL}")
[[ -n "${ENV_FILE}" ]]      && cmd+=(--env-file "${ENV_FILE}")
[[ -n "${DOCKER_IMAGE}" ]]  && cmd+=(--docker-image "${DOCKER_IMAGE}")
# Unified pass / cycle knobs (when set, overrides --cycles via formula).
[[ -n "${PASSES}" ]]          && cmd+=(--passes "${PASSES}")
[[ -n "${CYCLE_PER_BATCH}" ]] && cmd+=(--cycle-per-batch "${CYCLE_PER_BATCH}")
# Resume: when START_CYCLE > 1, runner skips workspace seeding, validates
# HEAD is at evo-{N-1}, loads score_history from existing results.metrics.json,
# advances bench cursor by (N-1)*batch_size, and runs cycles N..max.
# `:-` default keeps `set -u` happy when START_CYCLE is not exported.
[[ -n "${START_CYCLE:-}" ]]   && cmd+=(--start-cycle "${START_CYCLE}")

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
echo "=== MCP unified run completed ==="
echo "  Exit code:  ${exit_code}"
echo "  Results:    ${OUTPUT_DIR}/results.jsonl"
echo "  Metrics:    ${OUTPUT_DIR}/results.metrics.json"
echo "  Log:        ${LOG}"
exit "${exit_code}"
