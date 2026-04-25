#!/usr/bin/env bash
# Run Terminal-Bench 2.0 evolution via UnifiedEngine (Phase 1).
#
# Unified counterpart to run_evolution.sh. Engine-level parity with
# AdaptiveSkillEngine — see docs/algorithms/unified-equivalence-audit.md.
#
# Usage:
#   bash examples/tb_examples/run_evolve_unified.sh <RUN_NAME>
#   CYCLES=5 BATCH_SIZE=2 bash ... <RUN_NAME>
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

RUN_NAME="${1:?Usage: $0 <RUN_NAME> [--cycles N] [--batch-size N] [--limit N]}"
shift || true

CYCLES="${CYCLES:-}"
PASSES="${PASSES:-}"
CYCLE_PER_BATCH="${CYCLE_PER_BATCH:-}"
BATCH_SIZE="${BATCH_SIZE:-5}"
LIMIT="${LIMIT:-20}"
PARALLEL="${PARALLEL:-6}"
PARALLEL_BACKEND="${PARALLEL_BACKEND:-thread}"
MODEL_ID="${MODEL_ID:-us.anthropic.claude-opus-4-6-v1}"
SOLVER="${SOLVER:-react}"
EVOLVER_MODEL_ID="${EVOLVER_MODEL_ID:-}"
export BEDROCK_RETRY_MAX_ATTEMPTS="${BEDROCK_RETRY_MAX_ATTEMPTS:-15}"
export BEDROCK_READ_TIMEOUT_SEC="${BEDROCK_READ_TIMEOUT_SEC:-600}"
export BEDROCK_CONNECT_TIMEOUT_SEC="${BEDROCK_CONNECT_TIMEOUT_SEC:-30}"
REGION="${REGION:-us-west-2}"
MAX_TOKENS="${MAX_TOKENS:-16384}"
CHALLENGES_DIR="${CHALLENGES_DIR:-}"
SEED_WORKSPACE="${SEED_WORKSPACE:-${REPO_ROOT}/seed_workspaces/terminal}"
LOG_DIR="${LOG_DIR:-${REPO_ROOT}/logs/unified_tb_${RUN_NAME}}"
# Skill-budget cap (mirrors legacy run_evolution.sh --max-skills). Empty
# means "let the python runner use its own default", set to a positive
# integer to cap how many skills the evolver may keep in the workspace.
MAX_SKILLS="${MAX_SKILLS:-6}"

# Forward any extra flags to the python script.
while [[ $# -gt 0 ]]; do
    case "$1" in
        --cycles)          CYCLES="$2";          shift 2 ;;
        --passes)          PASSES="$2";          shift 2 ;;
        --cycle-per-batch) CYCLE_PER_BATCH="$2"; shift 2 ;;
        --batch-size)      BATCH_SIZE="$2";      shift 2 ;;
        --limit)           LIMIT="$2";           shift 2 ;;
        --max-skills)      MAX_SKILLS="$2";      shift 2 ;;
        --model-id)        MODEL_ID="$2";        shift 2 ;;
        --solver)          SOLVER="$2";          shift 2 ;;
        --region)          REGION="$2";          shift 2 ;;
        --challenges-dir)  CHALLENGES_DIR="$2";  shift 2 ;;
        *)                 echo "Unknown flag: $1"; exit 1 ;;
    esac
done

mkdir -p "${LOG_DIR}"

echo "=== Terminal-Bench Unified Run: ${RUN_NAME} ==="
echo "Log dir:       ${LOG_DIR}"
echo "Cycles:        ${CYCLES:-full sweep}"
echo "Batch size:    ${BATCH_SIZE}"
echo "Limit:         ${LIMIT}"
echo "Parallel:      ${PARALLEL}"
echo "Parallel backend: ${PARALLEL_BACKEND}"
echo "Solver:        ${SOLVER}"
echo "Model:         ${MODEL_ID}"
echo "Evolver model: ${EVOLVER_MODEL_ID:-<same as solver>}"
echo "Region:        ${REGION}"
echo "Max skills:    ${MAX_SKILLS}"
echo "Evolve flags:  trajectory-only, skills-only, protect-skills"
[[ -n "${CHALLENGES_DIR}" ]] && echo "Challenges:    ${CHALLENGES_DIR}"
echo ""

# Choose python runner: respect an already-active venv; otherwise fall
# back to `uv run python` (matches legacy run_evolution.sh / run_baseline.sh).
if [[ -n "${VIRTUAL_ENV:-}" ]]; then
    PY_CMD=(python)
else
    PY_CMD=(env UV_CACHE_DIR=/tmp/uv_cache uv run python)
fi

cmd=(
  "${PY_CMD[@]}"
  "${REPO_ROOT}/examples/tb_examples/batch_evolve_terminal_unified.py"
  "${RUN_NAME}"
  --batch-size "${BATCH_SIZE}"
  --parallel "${PARALLEL}"
  --parallel-backend "${PARALLEL_BACKEND}"
  --limit "${LIMIT}"
  --max-skills "${MAX_SKILLS}"
  --model-id "${MODEL_ID}"
  --solver "${SOLVER}"
  --region "${REGION}"
  --max-tokens "${MAX_TOKENS}"
  --seed-workspace "${SEED_WORKSPACE}"
  --log-dir "${LOG_DIR}"
  -v
)
[[ -n "${CYCLES}" ]] && cmd+=(--cycles "${CYCLES}")
[[ -n "${EVOLVER_MODEL_ID}" ]] && cmd+=(--evolver-model-id "${EVOLVER_MODEL_ID}")
[[ -n "${CHALLENGES_DIR}" ]] && cmd+=(--challenges-dir "${CHALLENGES_DIR}")
# Unified pass / cycle knobs (when set, overrides --cycles via formula).
[[ -n "${PASSES}" ]]          && cmd+=(--passes "${PASSES}")
[[ -n "${CYCLE_PER_BATCH}" ]] && cmd+=(--cycle-per-batch "${CYCLE_PER_BATCH}")

LOG="${LOG_DIR}/evolve.log"
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
echo "=== Terminal-Bench unified run completed ==="
echo "  Exit code:  ${exit_code}"
echo "  Results:    ${LOG_DIR}/results.jsonl"
echo "  Metrics:    ${LOG_DIR}/results.metrics.json"
echo "  Log:        ${LOG}"
exit "${exit_code}"
