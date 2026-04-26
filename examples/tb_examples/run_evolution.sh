#!/usr/bin/env bash
set -euo pipefail

# Evolution experiment on Terminal-Bench 2.0
#
# Phase 1: Evolve on N tasks (all 5 seed skills, evolve after each batch)
# Phase 2: Evaluate remaining tasks with evolved workspace (no further evolution)
#          Resume support skips tasks already solved in Phase 1.
# Report:  Combined Phase 1 + Phase 2 results.
#
# Usage:
#   bash examples/tb_examples/run_evolution.sh <RUN_NAME>
#   bash examples/tb_examples/run_evolution.sh Mar25_v1 --workers 8
#   bash examples/tb_examples/run_evolution.sh Mar25_test --evolve-limit 1 --eval-limit 1 --batch-size 1
#   nohup bash examples/tb_examples/run_evolution.sh Mar25_v1 &

RUN_NAME="${1:?Usage: $0 <RUN_NAME> [--workers N] [--evolve-limit N] [--eval-limit N] [--batch-size N] [--max-skills N] [--exclude task1,task2]}"
shift

LOG_DIR="logs/evolve_${RUN_NAME}"
WORK_DIR="evolution_workdir/evolve_${RUN_NAME}"
WORKERS=6
BATCH_SIZE=5
EVOLVE_LIMIT=20
EVAL_LIMIT=""
MAX_SKILLS=6
EXCLUDE=""
EVOLVER="adaptive_skill"
MODEL_ID="${MODEL_ID:-us.anthropic.claude-opus-4-6-v1}"
EVOLVER_MODEL_ID="${EVOLVER_MODEL_ID:-}"
REGION="${REGION:-us-west-2}"
MAX_TOKENS="${MAX_TOKENS:-16384}"
export BEDROCK_RETRY_MAX_ATTEMPTS="${BEDROCK_RETRY_MAX_ATTEMPTS:-15}"
export BEDROCK_READ_TIMEOUT_SEC="${BEDROCK_READ_TIMEOUT_SEC:-600}"
export BEDROCK_CONNECT_TIMEOUT_SEC="${BEDROCK_CONNECT_TIMEOUT_SEC:-30}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --workers)       WORKERS="$2";       shift 2 ;;
        --batch-size)    BATCH_SIZE="$2";     shift 2 ;;
        --evolve-limit)  EVOLVE_LIMIT="$2";   shift 2 ;;
        --eval-limit)    EVAL_LIMIT="$2";     shift 2 ;;
        --max-skills)    MAX_SKILLS="$2";     shift 2 ;;
        --exclude)       EXCLUDE="$2";        shift 2 ;;
        --evolver)       EVOLVER="$2";        shift 2 ;;
        --model-id)      MODEL_ID="$2";       shift 2 ;;
        --evolver-model-id) EVOLVER_MODEL_ID="$2"; shift 2 ;;
        --region)        REGION="$2";         shift 2 ;;
        --max-tokens)    MAX_TOKENS="$2";     shift 2 ;;
        *) echo "Unknown flag: $1"; exit 1 ;;
    esac
done

# Phase 2 limit = evolve_limit + eval_limit (resume skips Phase 1 tasks)
PHASE2_LIMIT_FLAG=""
if [[ -n "$EVAL_LIMIT" ]]; then
    PHASE2_TOTAL=$(( EVOLVE_LIMIT + EVAL_LIMIT ))
    PHASE2_LIMIT_FLAG="--limit $PHASE2_TOTAL"
fi

EXCLUDE_FLAG=""
if [[ -n "$EXCLUDE" ]]; then
    EXCLUDE_FLAG="--exclude $EXCLUDE"
fi

COMMON="--solver react $EXCLUDE_FLAG --workers $WORKERS --model-id $MODEL_ID --region $REGION --max-tokens $MAX_TOKENS"
EVOLVER_MODEL_FLAG=""
if [[ -n "$EVOLVER_MODEL_ID" ]]; then
    EVOLVER_MODEL_FLAG="--evolver-model-id $EVOLVER_MODEL_ID"
fi
EVOLVE_FLAGS="--trajectory-only --skills-only --protect-skills --max-skills $MAX_SKILLS --evolver $EVOLVER"
OUTPUT="--log-dir $LOG_DIR --output $LOG_DIR/results.jsonl --errors $LOG_DIR/errors.jsonl"

mkdir -p "$LOG_DIR"

echo "============================================================"
echo "  Evolution Experiment: ${RUN_NAME}"
echo "  Workspace:     ${WORK_DIR}"
echo "  Logs:          ${LOG_DIR}"
echo "  Phase 1:       evolve on ${EVOLVE_LIMIT} tasks (batch-size ${BATCH_SIZE})"
echo "  Phase 2:       evaluate ${EVAL_LIMIT:-all remaining} tasks (no evolution)"
echo "  Workers:       ${WORKERS}"
echo "  Max skills:    ${MAX_SKILLS}"
echo "  Exclude:       ${EXCLUDE:-none}"
echo "  Evolver:       ${EVOLVER}"
echo "  Model:         ${MODEL_ID}"
echo "  Evolver model: ${EVOLVER_MODEL_ID:-<same as model>}"
echo "  Region:        ${REGION}"
echo "  Max tokens:    ${MAX_TOKENS}"
echo "  Evolve flags:  trajectory-only, skills-only, protect-skills"
echo "============================================================"
echo ""

# Phase 1: Evolve
echo ">>> Phase 1: Evolve on ${EVOLVE_LIMIT} tasks (batch-size ${BATCH_SIZE})"
set +e
UV_CACHE_DIR=/tmp/uv_cache uv run python examples/tb_examples/batch_evolve_terminal.py \
  $COMMON \
  $EVOLVER_MODEL_FLAG \
  --limit "$EVOLVE_LIMIT" \
  --batch-size "$BATCH_SIZE" \
  $EVOLVE_FLAGS \
  --seed-workspace seed_workspaces/terminal \
  --work-dir "$WORK_DIR" \
  $OUTPUT \
  2>&1 | tee "$LOG_DIR/batch.log"
PHASE1_EXIT=${PIPESTATUS[0]}
set -e

if [[ $PHASE1_EXIT -ne 0 ]]; then
    echo "WARNING: Phase 1 exited with code $PHASE1_EXIT — continuing to Phase 2"
fi

echo ""

# Phase 2: Evaluate remaining tasks with evolved workspace
echo ">>> Phase 2: Evaluate tasks with evolved workspace (--no-evolve)"
UV_CACHE_DIR=/tmp/uv_cache uv run python examples/tb_examples/batch_evolve_terminal.py \
  $COMMON \
  $EVOLVER_MODEL_FLAG \
  --no-evolve \
  --work-dir "$WORK_DIR" \
  $PHASE2_LIMIT_FLAG \
  $OUTPUT \
  2>&1 | tee -a "$LOG_DIR/batch.log"

echo ""
echo "============================================================"
echo "  Experiment complete: ${RUN_NAME}"
echo "  Results: ${LOG_DIR}/results.jsonl"
echo "  Workspace: ${WORK_DIR}"
echo "============================================================"
