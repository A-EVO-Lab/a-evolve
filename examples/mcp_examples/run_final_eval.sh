#!/usr/bin/env bash
# Run MCP-Atlas MetaHarness final-eval.
#
# Invokes examples/mcp_examples/run_final_eval.py — reads solver from
# config.extra.solver_model and runs N trials of the full task suite on
# a given workspace (typically seed_workspaces/mcp_mh for baseline).
# No EvolutionEngine, no workspace mutation.
#
# For the adaptive/unified MCP no-evolution baseline, use
# examples/mcp_examples/run_adaptive_evolve_baseline.sh instead. That path
# uses seed_workspaces/mcp and adaptive_evolve_baseline.py, matching
# adaptive_evolve_all.py / run_adaptive_evolve_split_unified.sh.
#
# Note: solver model is read from the CONFIG yaml (config.extra.solver_model).
# To override solver per-run (needed by EvolverBench's multi-solver sweep),
# either (a) generate a per-solver config file, or (b) patch run_final_eval.py
# to accept a --solver-model CLI override. See EvolverBench docs/plan_v1.md
# for follow-up tracking.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

CONFIG="${CONFIG:-${REPO_ROOT}/examples/configs/metaharness_mcp.yaml}"
WORKSPACE="${WORKSPACE:-${REPO_ROOT}/seed_workspaces/mcp_mh}"
TRIALS="${TRIALS:-1}"
WORKERS="${WORKERS:-20}"
export BEDROCK_RETRY_MAX_ATTEMPTS="${BEDROCK_RETRY_MAX_ATTEMPTS:-15}"
export BEDROCK_READ_TIMEOUT_SEC="${BEDROCK_READ_TIMEOUT_SEC:-600}"
export BEDROCK_CONNECT_TIMEOUT_SEC="${BEDROCK_CONNECT_TIMEOUT_SEC:-30}"
RUN_ID="${RUN_ID:-$(date -u +%Y%m%d_%H%M%S)_pid$$}"
OUTPUT_DIR="${OUTPUT_DIR:-${REPO_ROOT}/logs/mcp_final_eval_${RUN_ID}}"
OUTPUT="${OUTPUT:-${OUTPUT_DIR}/final_eval.json}"

mkdir -p "${OUTPUT_DIR}"

echo "=== MCP Final-Eval (No-Evolution Baseline) ==="
echo "Run ID:    ${RUN_ID}"
echo "Config:    ${CONFIG}"
echo "Workspace: ${WORKSPACE}"
echo "Trials:    ${TRIALS}"
echo "Workers:   ${WORKERS}"
echo "Output:    ${OUTPUT}"
echo ""

cmd=(
  python "${REPO_ROOT}/examples/mcp_examples/run_final_eval.py"
  --config "${CONFIG}"
  --workspace "${WORKSPACE}"
  --trials "${TRIALS}"
  --workers "${WORKERS}"
  --output "${OUTPUT}"
)

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
echo "=== MCP final-eval completed ==="
echo "  Exit code: ${exit_code}"
echo "  Output:    ${OUTPUT}"
echo "  Log:       ${LOG}"
exit "${exit_code}"
