#!/usr/bin/env bash
# Run SWE-bench Verified in-situ evolution via UnifiedEngine.
#
# Wrapper around examples/swe_examples/evolve_sequential_unified.py. This keeps
# the v32g SWE hyperparameters while swapping GuidedSynthesisEngine for
# UnifiedEngine over one in-situ task stream.
#
# Defaults follow the v32g full recipe (matches run_swe_evolve_split.sh):
#   --solver-proposes --verification-focus --efficiency-prompt
#   --feedback none --max-steps 140 --window-size 70
#   --batch-size 20 --parallel 20
#   dataset = princeton-nlp/SWE-bench_Verified
#   total LIMIT = 500
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

CYCLES="${CYCLES:-}"
PASSES="${PASSES:-}"
CYCLE_PER_BATCH="${CYCLE_PER_BATCH:-}"
LIMIT="${LIMIT:-500}"
BATCH_SIZE="${BATCH_SIZE:-20}"
PARALLEL="${PARALLEL:-5}"
PARALLEL_BACKEND="${PARALLEL_BACKEND:-process}"
FEEDBACK="${FEEDBACK:-none}"
SOLVER_PROPOSES="${SOLVER_PROPOSES:-false}"
# Option A: route SWE recipe through LLMBashEvolve (controller picks the
# evolver_driven regime). Mutually exclusive with SOLVER_PROPOSES — runner
# will warn and ignore SOLVER_PROPOSES if both are set.
EVOLVER_DRIVEN="${EVOLVER_DRIVEN:-false}"
VERIFICATION_FOCUS="${VERIFICATION_FOCUS:-false}"
EFFICIENCY_PROMPT="${EFFICIENCY_PROMPT:-false}"
VERIFY_FIX_PROMPT="${VERIFY_FIX_PROMPT:-false}"
PIN_FIRST_MESSAGE="${PIN_FIRST_MESSAGE:-false}"
MODEL_ID="${MODEL_ID:-us.anthropic.claude-opus-4-6-v1}"
EVOLVER_MODEL_ID="${EVOLVER_MODEL_ID:-}"
export BEDROCK_RETRY_MAX_ATTEMPTS="${BEDROCK_RETRY_MAX_ATTEMPTS:-15}"
export BEDROCK_READ_TIMEOUT_SEC="${BEDROCK_READ_TIMEOUT_SEC:-600}"
export BEDROCK_CONNECT_TIMEOUT_SEC="${BEDROCK_CONNECT_TIMEOUT_SEC:-30}"
REGION="${REGION:-us-west-2}"
MAX_TOKENS="${MAX_TOKENS:-16384}"
MAX_STEPS="${MAX_STEPS:-140}"
WINDOW_SIZE="${WINDOW_SIZE:-70}"
DATASET="${DATASET:-princeton-nlp/SWE-bench_Verified}"
SEED_WORKSPACE="${SEED_WORKSPACE:-${REPO_ROOT}/seed_workspaces/swe}"
RUN_ID="${RUN_ID:-$(date -u +%Y%m%d_%H%M%S)_pid$$}"
OUTPUT_DIR="${OUTPUT_DIR:-${REPO_ROOT}/logs/unified_swe_in_situ_${RUN_ID}}"

mkdir -p "$(dirname "${OUTPUT_DIR}")"

echo "=== SWE In-Situ Evolution (Unified) ==="
echo "Run ID:        ${RUN_ID}"
echo "Output dir:    ${OUTPUT_DIR}"
echo "Cycles:        ${CYCLES:-full sweep}"
echo "Tasks:         ${LIMIT:-all tasks}"
echo "Batch size:    ${BATCH_SIZE}"
echo "Parallel:      ${PARALLEL}"
echo "Parallel backend: ${PARALLEL_BACKEND}"
echo "Feedback:      ${FEEDBACK}"
echo "Solver proposes: ${SOLVER_PROPOSES}"
echo "Evolver driven:  ${EVOLVER_DRIVEN}  (Option A: LLMBashEvolve operator)"
echo "Verification focus: ${VERIFICATION_FOCUS}"
echo "Efficiency prompt:  ${EFFICIENCY_PROMPT}"
echo "Verify-fix prompt:  ${VERIFY_FIX_PROMPT}"
echo "Pin first msg:      ${PIN_FIRST_MESSAGE}"
echo "Window size:        ${WINDOW_SIZE}"
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
  --limit "${LIMIT}"
  --feedback "${FEEDBACK}"
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
[[ -n "${CYCLES}" ]]           && cmd+=(--cycles "${CYCLES}")
[[ -n "${PASSES}" ]]           && cmd+=(--passes "${PASSES}")
[[ -n "${CYCLE_PER_BATCH}" ]]  && cmd+=(--cycle-per-batch "${CYCLE_PER_BATCH}")
[[ -n "${EVOLVER_MODEL_ID}" ]] && cmd+=(--evolver-model-id "${EVOLVER_MODEL_ID}")
[[ "${SOLVER_PROPOSES}" == "true" ]] && cmd+=(--solver-proposes)
[[ "${EVOLVER_DRIVEN}" == "true" ]]  && cmd+=(--evolver-driven)
[[ "${VERIFICATION_FOCUS}" == "true" ]] && cmd+=(--verification-focus)
[[ "${EFFICIENCY_PROMPT}" == "true" ]] && cmd+=(--efficiency-prompt)
[[ "${VERIFY_FIX_PROMPT}" == "false" ]] && cmd+=(--no-verify-fix-prompt)
[[ "${PIN_FIRST_MESSAGE}" == "false" ]] && cmd+=(--no-pin-first-message)

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
echo "=== SWE unified in-situ run completed ==="
echo "  Exit code:  ${exit_code}"
echo "  Metrics:    ${OUTPUT_DIR}/results.metrics.json"
echo "  Results:    ${OUTPUT_DIR}/results.jsonl"
echo "  Log:        ${LOG}"
exit "${exit_code}"
