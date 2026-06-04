#!/usr/bin/env bash
# Run SWE-bench Verified solve-all as a no-evolution baseline (full 500).
#
# Invokes examples/swe_examples/solve_all.py directly — no EvolutionEngine,
# no evolver calls, no workspace mutation. Pure "solve every task once
# with the baseline agent" pass.
#
# Defaults follow the README full-baseline recipe:
#   dataset = princeton-nlp/SWE-bench_Verified
#   workers = 16
#   max-turns = 140
#   limit = 500
#
# Env-var configurable; matches the style of run_swe_evolve_unified.sh so
# EvolverBench's dispatcher can route `--evolver none` to this wrapper.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

MODEL_ID="${MODEL_ID:-us.anthropic.claude-opus-4-6-v1}"
REGION="${REGION:-us-west-2}"
MAX_TOKENS="${MAX_TOKENS:-16384}"
export BEDROCK_RETRY_MAX_ATTEMPTS="${BEDROCK_RETRY_MAX_ATTEMPTS:-15}"
export BEDROCK_READ_TIMEOUT_SEC="${BEDROCK_READ_TIMEOUT_SEC:-600}"
export BEDROCK_CONNECT_TIMEOUT_SEC="${BEDROCK_CONNECT_TIMEOUT_SEC:-30}"
MAX_TURNS="${MAX_TURNS:-140}"
WORKERS="${WORKERS:-20}"
DATASET="${DATASET:-princeton-nlp/SWE-bench_Verified}"
LIMIT="${LIMIT:-500}"
RUN_EVAL="${RUN_EVAL:-true}"
# Opt-in conversation-manager overrides. Defaults preserve the historical
# baseline (Strands implicit SlidingWindowConversationManager(window_size=40)).
PIN_FIRST_MESSAGE="${PIN_FIRST_MESSAGE:-false}"
WINDOW_SIZE="${WINDOW_SIZE:-}"
RUN_ID="${RUN_ID:-$(date -u +%Y%m%d_%H%M%S)_pid$$}"
OUTPUT_DIR="${OUTPUT_DIR:-${REPO_ROOT}/logs/swe_solve_all_${RUN_ID}}"

mkdir -p "$(dirname "${OUTPUT_DIR}")"
mkdir -p "${OUTPUT_DIR}"

echo "=== SWE Solve-All (No-Evolution Baseline) ==="
echo "Run ID:     ${RUN_ID}"
echo "Output dir: ${OUTPUT_DIR}"
echo "Dataset:    ${DATASET}"
echo "Model:      ${MODEL_ID}"
echo "Region:     ${REGION}"
echo "Max tokens: ${MAX_TOKENS}"
echo "Max turns:  ${MAX_TURNS}"
echo "Workers:    ${WORKERS}"
echo "Limit:      ${LIMIT}"
echo "Run eval:   ${RUN_EVAL}"
echo "Pin first:  ${PIN_FIRST_MESSAGE}"
echo "Window:     ${WINDOW_SIZE:-<strands default 40>}"
echo ""

cmd=(
  python "${REPO_ROOT}/examples/swe_examples/solve_all.py"
  --dataset "${DATASET}"
  --model-id "${MODEL_ID}"
  --region "${REGION}"
  --max-tokens "${MAX_TOKENS}"
  --max-turns "${MAX_TURNS}"
  --workers "${WORKERS}"
  --limit "${LIMIT}"
  --output-dir "${OUTPUT_DIR}"
)
if [[ "${RUN_EVAL}" == "false" ]]; then
  cmd+=(--no-eval)
fi
if [[ "${PIN_FIRST_MESSAGE}" == "true" ]]; then
  cmd+=(--pin-first-message)
fi
if [[ -n "${WINDOW_SIZE}" ]]; then
  cmd+=(--window-size "${WINDOW_SIZE}")
fi

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
echo "=== SWE solve-all completed ==="
echo "  Exit code: ${exit_code}"
echo "  Results:   ${OUTPUT_DIR}"
echo "  Log:       ${LOG}"
exit "${exit_code}"
