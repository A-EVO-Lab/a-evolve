#!/usr/bin/env bash
# Run SWE-bench Verified evolution via UnifiedEngine (Phase 1).
#
# Unified counterpart to evolve_sequential.py. Engine-level parity with
# GuidedSynthesisEngine — see docs/algorithms/unified-equivalence-audit.md.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

CYCLES="${CYCLES:-}"
PASSES="${PASSES:-}"
CYCLE_PER_BATCH="${CYCLE_PER_BATCH:-}"
BATCH_SIZE="${BATCH_SIZE:-5}"
LIMIT="${LIMIT:-50}"
PARALLEL="${PARALLEL:-5}"
PARALLEL_BACKEND="${PARALLEL_BACKEND:-process}"
FEEDBACK="${FEEDBACK:-none}"
SOLVER_PROPOSES="${SOLVER_PROPOSES:-true}"
VERIFICATION_FOCUS="${VERIFICATION_FOCUS:-true}"
EFFICIENCY_PROMPT="${EFFICIENCY_PROMPT:-true}"
MODEL_ID="${MODEL_ID:-us.anthropic.claude-opus-4-6-v1}"
EVOLVER_MODEL_ID="${EVOLVER_MODEL_ID:-}"
# Bedrock client tuning — read by agent_evolve/llm/_bedrock_config.py.
export BEDROCK_RETRY_MAX_ATTEMPTS="${BEDROCK_RETRY_MAX_ATTEMPTS:-15}"
export BEDROCK_READ_TIMEOUT_SEC="${BEDROCK_READ_TIMEOUT_SEC:-600}"
export BEDROCK_CONNECT_TIMEOUT_SEC="${BEDROCK_CONNECT_TIMEOUT_SEC:-30}"
REGION="${REGION:-us-west-2}"
MAX_TOKENS="${MAX_TOKENS:-16384}"
MAX_STEPS="${MAX_STEPS:-140}"
WINDOW_SIZE="${WINDOW_SIZE:-40}"
DATASET="${DATASET:-MariusHobbhahn/swe-bench-verified-mini}"
SEED_WORKSPACE="${SEED_WORKSPACE:-${REPO_ROOT}/seed_workspaces/swe}"
RUN_ID="${RUN_ID:-$(date -u +%Y%m%d_%H%M%S)_pid$$}"
OUTPUT_DIR="${OUTPUT_DIR:-${REPO_ROOT}/logs/unified_swe_${RUN_ID}}"

mkdir -p "$(dirname "${OUTPUT_DIR}")"

echo "=== SWE Unified Run ==="
echo "Run ID:        ${RUN_ID}"
echo "Output dir:    ${OUTPUT_DIR}"
echo "Cycles:        ${CYCLES:-full sweep}"
echo "Batch size:    ${BATCH_SIZE}"
echo "Limit:         ${LIMIT}"
echo "Parallel:      ${PARALLEL}"
echo "Parallel backend: ${PARALLEL_BACKEND}"
echo "Feedback:      ${FEEDBACK}"
echo "Solver proposes: ${SOLVER_PROPOSES}"
echo "Dataset:       ${DATASET}"
echo "Model:         ${MODEL_ID}"
echo "Evolver model: ${EVOLVER_MODEL_ID:-<same as solver>}"
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
  "${REPO_ROOT}/examples/swe_examples/evolve_sequential_unified.py"
  --batch-size "${BATCH_SIZE}"
  --parallel "${PARALLEL}"
  --parallel-backend "${PARALLEL_BACKEND}"
  --feedback "${FEEDBACK}"
  --limit "${LIMIT}"
  --model-id "${MODEL_ID}"
  --region "${REGION}"
  --max-tokens "${MAX_TOKENS}"
  --max-steps "${MAX_STEPS}"
  --window-size "${WINDOW_SIZE}"
  --dataset "${DATASET}"
  --seed-workspace "${SEED_WORKSPACE}"
  --output-dir "${OUTPUT_DIR}"
  -v
)
[[ -n "${CYCLES}" ]] && cmd+=(--cycles "${CYCLES}")
[[ -n "${EVOLVER_MODEL_ID}" ]] && cmd+=(--evolver-model-id "${EVOLVER_MODEL_ID}")
[[ "${SOLVER_PROPOSES}" == "true" ]] && cmd+=(--solver-proposes)
[[ "${VERIFICATION_FOCUS}" == "true" ]] && cmd+=(--verification-focus)
[[ "${EFFICIENCY_PROMPT}" == "true" ]] && cmd+=(--efficiency-prompt)
# Unified pass / cycle knobs (when set, the script computes max_cycles
# from passes × ⌈limit/batch⌉ × cycle_per_batch and overrides --cycles).
[[ -n "${PASSES}" ]]          && cmd+=(--passes "${PASSES}")
[[ -n "${CYCLE_PER_BATCH}" ]] && cmd+=(--cycle-per-batch "${CYCLE_PER_BATCH}")

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
