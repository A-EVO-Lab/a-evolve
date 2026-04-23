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

CYCLES="${CYCLES:-3}"
BATCH_SIZE="${BATCH_SIZE:-30}"
LIMIT="${LIMIT:-100}"
SOLVER_MODEL="${SOLVER_MODEL:-us.anthropic.claude-opus-4-5-20251101-v1:0}"
EVOLVER_MODEL="${EVOLVER_MODEL:-}"
REGION="${REGION:-us-west-2}"
MAX_TOKENS="${MAX_TOKENS:-64000}"
EVAL_MODEL_ID="${EVAL_MODEL_ID:-gemini/gemini-2.5-pro}"
DATASET="${DATASET:-ScaleAI/MCP-Atlas}"
SEED_WORKSPACE="${SEED_WORKSPACE:-${REPO_ROOT}/seed_workspaces/mcp}"
RUN_ID="${RUN_ID:-$(date -u +%Y%m%d_%H%M%S)_pid$$}"
OUTPUT_DIR="${OUTPUT_DIR:-${REPO_ROOT}/logs/unified_mcp_${RUN_ID}}"

mkdir -p "$(dirname "${OUTPUT_DIR}")"

echo "=== MCP-Atlas Unified Run ==="
echo "Run ID:        ${RUN_ID}"
echo "Output dir:    ${OUTPUT_DIR}"
echo "Cycles:        ${CYCLES}"
echo "Batch size:    ${BATCH_SIZE}"
echo "Limit:         ${LIMIT}"
echo "Dataset:       ${DATASET}"
echo "Solver model:  ${SOLVER_MODEL}"
echo "Evolver model: ${EVOLVER_MODEL:-<same as solver>}"
echo "Eval model:    ${EVAL_MODEL_ID}"
echo "Region:        ${REGION}"
echo ""

cmd=(
  python "${REPO_ROOT}/examples/mcp_examples/run_adaptive_evolve_all_unified.py"
  --cycles "${CYCLES}"
  --batch-size "${BATCH_SIZE}"
  --limit "${LIMIT}"
  --solver-model "${SOLVER_MODEL}"
  --region "${REGION}"
  --max-tokens "${MAX_TOKENS}"
  --eval-model-id "${EVAL_MODEL_ID}"
  --dataset "${DATASET}"
  --seed-workspace "${SEED_WORKSPACE}"
  --output-dir "${OUTPUT_DIR}"
  -v
)
[[ -n "${EVOLVER_MODEL}" ]] && cmd+=(--evolver-model "${EVOLVER_MODEL}")

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
