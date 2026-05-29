#!/usr/bin/env bash
# SWE legacy in-situ evolution wrapper.
#
# Runs examples/swe_examples/evolve_sequential.py: solve tasks in batches with
# GuidedSynthesisEngine evolution between batches over one in-situ task stream.
#
# Defaults follow docs/algorithms/guided-synth.md recommendations:
#   --solver-proposes --verification-focus --efficiency-prompt
#   --feedback none --max-steps 140 --window-size 70
#   --batch-size 20 --parallel 20
#   dataset = princeton-nlp/SWE-bench_Verified
#   total LIMIT = 500
#
# Usage:
#   bash examples/swe_examples/run_swe_evolve_in-situ.sh
#   LIMIT=50 BATCH_SIZE=5 PARALLEL=5 bash examples/swe_examples/run_swe_evolve_in-situ.sh
#   NO_EVOLVE=true bash examples/swe_examples/run_swe_evolve_in-situ.sh
#   nohup bash examples/swe_examples/run_swe_evolve_in-situ.sh &
#
# All knobs are env-var configurable (no CLI parsing here -- mirrors TB's
# run_evolution.sh shape).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# ---------------------------------------------------------------------------
# In-situ batch defaults
# ---------------------------------------------------------------------------
LIMIT="${LIMIT:-500}"
BATCH_SIZE="${BATCH_SIZE:-20}"
PARALLEL="${PARALLEL:-5}"

# ---------------------------------------------------------------------------
# Agent / evolver knobs (guided-synth recommended setting)
# ---------------------------------------------------------------------------
FEEDBACK="${FEEDBACK:-none}"
SOLVER_PROPOSES="${SOLVER_PROPOSES:-true}"
VERIFICATION_FOCUS="${VERIFICATION_FOCUS:-true}"
EFFICIENCY_PROMPT="${EFFICIENCY_PROMPT:-true}"
VERIFY_FIX_PROMPT="${VERIFY_FIX_PROMPT:-true}"
PIN_FIRST_MESSAGE="${PIN_FIRST_MESSAGE:-true}"
# Modified-D toggle: when "true", the per-task skill-proposal LLM call is
# routed to the EVOLVER model (fed solver's full conversation), instead of
# letting the solver agent make a second turn. Requires SOLVER_PROPOSES=true.
EVOLVER_PROPOSES="${EVOLVER_PROPOSES:-true}"
EVOLVER_REGION="${EVOLVER_REGION:-${REGION:-us-west-2}}"
MAX_STEPS="${MAX_STEPS:-140}"
WINDOW_SIZE="${WINDOW_SIZE:-70}"
NO_EVOLVE="${NO_EVOLVE:-false}"

# ---------------------------------------------------------------------------
# Model / region
# ---------------------------------------------------------------------------
MODEL_ID="${MODEL_ID:-us.anthropic.claude-opus-4-6-v1}"
EVOLVER_MODEL_ID="${EVOLVER_MODEL_ID:-}"
REGION="${REGION:-us-west-2}"
MAX_TOKENS="${MAX_TOKENS:-16384}"
export BEDROCK_RETRY_MAX_ATTEMPTS="${BEDROCK_RETRY_MAX_ATTEMPTS:-15}"
export BEDROCK_READ_TIMEOUT_SEC="${BEDROCK_READ_TIMEOUT_SEC:-600}"
export BEDROCK_CONNECT_TIMEOUT_SEC="${BEDROCK_CONNECT_TIMEOUT_SEC:-30}"

# ---------------------------------------------------------------------------
# Dataset / workspace / output
# ---------------------------------------------------------------------------
DATASET="${DATASET:-princeton-nlp/SWE-bench_Verified}"
SEED_WORKSPACE="${SEED_WORKSPACE:-${REPO_ROOT}/seed_workspaces/swe}"
RUN_ID="${RUN_ID:-$(date -u +%Y%m%d_%H%M%S)_pid$$}"
OUTPUT_DIR="${OUTPUT_DIR:-${REPO_ROOT}/logs/swe_in_situ_${RUN_ID}}"

mkdir -p "$(dirname "${OUTPUT_DIR}")"
mkdir -p "${OUTPUT_DIR}"

# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------
echo "============================================================"
echo "  SWE In-Situ Evolution (legacy GuidedSynthesisEngine)"
echo "  Run ID:        ${RUN_ID}"
echo "  Output dir:    ${OUTPUT_DIR}"
echo "  Tasks:         ${LIMIT} tasks"
echo "  Batch size:    ${BATCH_SIZE}"
echo "  Parallel:      ${PARALLEL}"
echo "  Dataset:       ${DATASET}"
echo "  Feedback:      ${FEEDBACK}"
echo "  Solver-proposes:    ${SOLVER_PROPOSES}"
echo "  Verification-focus: ${VERIFICATION_FOCUS}"
echo "  Efficiency-prompt:  ${EFFICIENCY_PROMPT}"
echo "  Verify-fix prompt:  ${VERIFY_FIX_PROMPT}"
echo "  Pin first msg:      ${PIN_FIRST_MESSAGE}"
echo "  Evolver-proposes:   ${EVOLVER_PROPOSES}  (Modified-D: evolver writes proposal)"
echo "  Evolver region:     ${EVOLVER_REGION}"
echo "  No evolve:          ${NO_EVOLVE}"
echo "  Max steps / window: ${MAX_STEPS} / ${WINDOW_SIZE}"
echo "  Model:         ${MODEL_ID}"
echo "  Evolver model: ${EVOLVER_MODEL_ID:-<same as solver>}"
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
  "${REPO_ROOT}/examples/swe_examples/evolve_sequential.py"
  --batch-size "${BATCH_SIZE}"
  --parallel "${PARALLEL}"
  --feedback "${FEEDBACK}"
  --max-steps "${MAX_STEPS}"
  --window-size "${WINDOW_SIZE}"
  --model-id "${MODEL_ID}"
  --region "${REGION}"
  --max-tokens "${MAX_TOKENS}"
  --dataset "${DATASET}"
  --seed-workspace "${SEED_WORKSPACE}"
  --output-dir "${OUTPUT_DIR}"
  --limit "${LIMIT}"
  -v
)
[[ -n "${EVOLVER_MODEL_ID}" ]]         && cmd+=(--evolver-model-id "${EVOLVER_MODEL_ID}")
[[ "${SOLVER_PROPOSES}" == "true" ]]    && cmd+=(--solver-proposes)
[[ "${VERIFICATION_FOCUS}" == "true" ]] && cmd+=(--verification-focus)
[[ "${EFFICIENCY_PROMPT}" == "true" ]]  && cmd+=(--efficiency-prompt)
[[ "${VERIFY_FIX_PROMPT}" == "false" ]] && cmd+=(--no-verify-fix-prompt)
[[ "${PIN_FIRST_MESSAGE}" == "false" ]] && cmd+=(--no-pin-first-message)
[[ "${EVOLVER_PROPOSES}" == "true" ]]   && cmd+=(--evolver-proposes)
[[ -n "${EVOLVER_REGION}" ]]            && cmd+=(--evolver-region "${EVOLVER_REGION}")
[[ "${NO_EVOLVE}" == "true" ]]          && cmd+=(--no-evolve)

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
echo "  SWE in-situ run completed"
echo "  Exit code:  ${exit_code}"
echo "  Results:    ${OUTPUT_DIR}/results.json"
echo "  Log:        ${LOG}"
echo "============================================================"
exit "${exit_code}"
