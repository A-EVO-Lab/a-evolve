#!/usr/bin/env bash
# Run MCP-Atlas without evolution.
#
# This thin wrapper gives the harness-disentangling dispatchers the same
# env-var driven surface as the unified evolve wrappers, while delegating the
# actual work to the existing Python baseline runner.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

SOLVER_MODEL="${SOLVER_MODEL:-us.anthropic.claude-opus-4-6-v1}"
JUDGE_MODEL="${JUDGE_MODEL:-us.anthropic.claude-sonnet-4-6}"
REGION="${REGION:-us-west-2}"
MAX_TOKENS="${MAX_TOKENS:-16384}"
LIMIT="${LIMIT:-500}"
BATCH_SIZE="${BATCH_SIZE:-30}"
WORKERS="${WORKERS:-5}"
SEED_WORKSPACE="${SEED_WORKSPACE:-${REPO_ROOT}/seed_workspaces/mcp}"
WORK_DIR="${WORK_DIR:-${REPO_ROOT}/evolution_workdir/adaptive_baseline}"
OUTPUT_DIR="${OUTPUT_DIR:-${REPO_ROOT}/logs/mcp_baseline_${RUN_ID:-manual}}"
DOCKER_IMAGE="${DOCKER_IMAGE:-ghcr.io/scaleapi/mcp-atlas:latest}"
ENV_FILE="${ENV_FILE:-.env}"

export BEDROCK_RETRY_MAX_ATTEMPTS="${BEDROCK_RETRY_MAX_ATTEMPTS:-15}"
export BEDROCK_READ_TIMEOUT_SEC="${BEDROCK_READ_TIMEOUT_SEC:-600}"
export BEDROCK_CONNECT_TIMEOUT_SEC="${BEDROCK_CONNECT_TIMEOUT_SEC:-30}"

mkdir -p "${OUTPUT_DIR}"

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
  --seed-workspace "${SEED_WORKSPACE}"
  --work-dir "${WORK_DIR}"
  --output-dir "${OUTPUT_DIR}"
)
[[ -n "${DOCKER_IMAGE}" ]] && cmd+=(--docker-image "${DOCKER_IMAGE}")
[[ -n "${ENV_FILE}" ]] && cmd+=(--env-file "${ENV_FILE}")

LOG="${OUTPUT_DIR}/baseline.log"
echo "=== MCP-Atlas Baseline ==="
echo "Output dir:   ${OUTPUT_DIR}"
echo "Solver model: ${SOLVER_MODEL}"
echo "Judge model:  ${JUDGE_MODEL}"
echo "Region:       ${REGION}"
echo "Limit:        ${LIMIT}"
echo "Batch size:   ${BATCH_SIZE}"
echo "Workers:      ${WORKERS}"
echo "Running: ${cmd[*]}"

set +e
if command -v stdbuf >/dev/null 2>&1; then
  stdbuf -oL -eL "${cmd[@]}" 2>&1 | tee "${LOG}"
else
  "${cmd[@]}" 2>&1 | tee "${LOG}"
fi
exit_code=${PIPESTATUS[0]}
set -e

if [[ "${exit_code}" -eq 0 ]]; then
  python3 - "$OUTPUT_DIR/RUN_COMPLETE.json" <<'PY'
import json
import sys
from pathlib import Path

Path(sys.argv[1]).write_text(json.dumps({"status": "complete"}, indent=2) + "\n")
PY
fi

exit "${exit_code}"
