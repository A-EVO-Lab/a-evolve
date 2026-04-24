#!/usr/bin/env bash
# Run SWE-bench Verified evolution via UnifiedEngine (Phase 1).
#
# Unified counterpart to evolve_sequential.py. Engine-level parity with
# GuidedSynthesisEngine — see docs/algorithms/unified-equivalence-audit.md.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

CYCLES="${CYCLES:-3}"
BATCH_SIZE="${BATCH_SIZE:-5}"
LIMIT="${LIMIT:-50}"
MODEL_ID="${MODEL_ID:-us.anthropic.claude-opus-4-5-20251101-v1:0}"
EVOLVER_MODEL_ID="${EVOLVER_MODEL_ID:-}"
REGION="${REGION:-us-west-2}"
MAX_TOKENS="${MAX_TOKENS:-16384}"
DATASET="${DATASET:-MariusHobbhahn/swe-bench-verified-mini}"
SEED_WORKSPACE="${SEED_WORKSPACE:-${REPO_ROOT}/seed_workspaces/swe}"
RUN_ID="${RUN_ID:-$(date -u +%Y%m%d_%H%M%S)_pid$$}"
OUTPUT_DIR="${OUTPUT_DIR:-${REPO_ROOT}/logs/unified_swe_${RUN_ID}}"

mkdir -p "$(dirname "${OUTPUT_DIR}")"

echo "=== SWE Unified Run ==="
echo "Run ID:        ${RUN_ID}"
echo "Output dir:    ${OUTPUT_DIR}"
echo "Cycles:        ${CYCLES}"
echo "Batch size:    ${BATCH_SIZE}"
echo "Limit:         ${LIMIT}"
echo "Dataset:       ${DATASET}"
echo "Model:         ${MODEL_ID}"
echo "Evolver model: ${EVOLVER_MODEL_ID:-<same as solver>}"
echo "Region:        ${REGION}"
echo ""

cmd=(
  python "${REPO_ROOT}/examples/swe_examples/evolve_sequential_unified.py"
  --cycles "${CYCLES}"
  --batch-size "${BATCH_SIZE}"
  --limit "${LIMIT}"
  --model-id "${MODEL_ID}"
  --region "${REGION}"
  --max-tokens "${MAX_TOKENS}"
  --dataset "${DATASET}"
  --seed-workspace "${SEED_WORKSPACE}"
  --output-dir "${OUTPUT_DIR}"
  -v
)
[[ -n "${EVOLVER_MODEL_ID}" ]] && cmd+=(--evolver-model-id "${EVOLVER_MODEL_ID}")

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
echo "=== SWE unified run completed ==="
echo "  Exit code:  ${exit_code}"
echo "  Results:    ${OUTPUT_DIR}/results.jsonl"
echo "  Metrics:    ${OUTPUT_DIR}/results.metrics.json"
echo "  Log:        ${LOG}"
exit "${exit_code}"
