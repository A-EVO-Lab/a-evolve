#!/usr/bin/env bash
set -euo pipefail

# Baseline (no evolution, no skills) on Terminal-Bench 2.0
#
# Runs tasks without evolution and without any skills (vanilla prompt only).
#
# Env-var controlled (for EvolverBench dispatcher integration):
#   MODEL_ID         solver model id (default: claude-opus-4-5-...)
#   REGION           AWS region (default: us-west-2)
#   MAX_TOKENS       solver max tokens (default: 16384)
#   LOG_DIR          output dir for results/errors/log (default: logs/baseline_<RUN_NAME>)
#   WORK_DIR         workspace dir (default: /tmp/baseline_<RUN_NAME>)
#   WORKERS          parallel workers (default: 6)
#   LIMIT            max tasks (default: unset = all)
#   EXCLUDE          comma-separated task names to skip (default: unset)
#
# Usage:
#   bash examples/tb_examples/run_baseline.sh <RUN_NAME>
#   MODEL_ID=... LOG_DIR=... bash examples/tb_examples/run_baseline.sh <RUN_NAME>
#   bash examples/tb_examples/run_baseline.sh Mar25_baseline --workers 8
#   bash examples/tb_examples/run_baseline.sh Mar25_test --limit 2

RUN_NAME="${1:?Usage: $0 <RUN_NAME> [--workers N] [--limit N] [--exclude task1,task2]}"
shift

MODEL_ID="${MODEL_ID:-us.anthropic.claude-opus-4-5-20251101-v1:0}"
REGION="${REGION:-us-west-2}"
MAX_TOKENS="${MAX_TOKENS:-16384}"
export BEDROCK_RETRY_MAX_ATTEMPTS="${BEDROCK_RETRY_MAX_ATTEMPTS:-15}"
export BEDROCK_READ_TIMEOUT_SEC="${BEDROCK_READ_TIMEOUT_SEC:-600}"
export BEDROCK_CONNECT_TIMEOUT_SEC="${BEDROCK_CONNECT_TIMEOUT_SEC:-30}"
LOG_DIR="${LOG_DIR:-logs/baseline_${RUN_NAME}}"
WORK_DIR="${WORK_DIR:-/tmp/baseline_${RUN_NAME}}"
WORKERS="${WORKERS:-6}"
LIMIT="${LIMIT:-}"
EXCLUDE="${EXCLUDE:-}"

# CLI overrides (still supported for backward compat).
while [[ $# -gt 0 ]]; do
    case "$1" in
        --workers)  WORKERS="$2"; shift 2 ;;
        --limit)    LIMIT="$2";   shift 2 ;;
        --exclude)  EXCLUDE="$2"; shift 2 ;;
        --model-id) MODEL_ID="$2"; shift 2 ;;
        --region)   REGION="$2"; shift 2 ;;
        --max-tokens) MAX_TOKENS="$2"; shift 2 ;;
        --log-dir)  LOG_DIR="$2"; shift 2 ;;
        --work-dir) WORK_DIR="$2"; shift 2 ;;
        *) echo "Unknown flag: $1"; exit 1 ;;
    esac
done

LIMIT_FLAG=()
if [[ -n "$LIMIT" ]]; then
    LIMIT_FLAG=(--limit "$LIMIT")
fi

EXCLUDE_FLAG=()
if [[ -n "$EXCLUDE" ]]; then
    EXCLUDE_FLAG=(--exclude "$EXCLUDE")
fi

mkdir -p "$LOG_DIR"
mkdir -p "$WORK_DIR"

echo "============================================================"
echo "  Baseline (No Evolution, No Skills): ${RUN_NAME}"
echo "  Model:      ${MODEL_ID}"
echo "  Region:     ${REGION}"
echo "  Max tokens: ${MAX_TOKENS}"
echo "  Workspace:  ${WORK_DIR}"
echo "  Logs:       ${LOG_DIR}"
echo "  Workers:    ${WORKERS}"
echo "  Tasks:      ${LIMIT:-all}"
echo "  Exclude:    ${EXCLUDE:-none}"
echo "============================================================"
echo ""

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# Choose python runner: respect an already-active venv; otherwise fall back
# to `uv run python` (the legacy default).
if [[ -n "${VIRTUAL_ENV:-}" ]]; then
    PY_CMD=(python)
else
    PY_CMD=(env UV_CACHE_DIR=/tmp/uv_cache uv run python)
fi

cmd=(
  "${PY_CMD[@]}"
  "${REPO_ROOT}/examples/tb_examples/batch_evolve_terminal.py"
  --solver react
  --no-evolve
  --no-skills
  --model-id "$MODEL_ID"
  --region "$REGION"
  --max-tokens "$MAX_TOKENS"
  --workers "$WORKERS"
  "${EXCLUDE_FLAG[@]}"
  "${LIMIT_FLAG[@]}"
  --work-dir "$WORK_DIR"
  --log-dir "$LOG_DIR"
  --output "$LOG_DIR/results.jsonl"
  --errors "$LOG_DIR/errors.jsonl"
)

LOG="$LOG_DIR/evolve.log"
echo ">>> Running: ${cmd[*]}"
echo ">>> Log: $LOG"
echo ""

set +e
"${cmd[@]}" 2>&1 | tee "$LOG"
exit_code=${PIPESTATUS[0]}
set -e

echo ""
echo "============================================================"
echo "  Baseline complete: ${RUN_NAME}"
echo "  Exit code: ${exit_code}"
echo "  Results:   ${LOG_DIR}/results.jsonl"
echo "  Log:       ${LOG}"
echo "============================================================"
exit "${exit_code}"
