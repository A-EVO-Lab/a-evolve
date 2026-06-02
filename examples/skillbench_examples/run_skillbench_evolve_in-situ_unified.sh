#!/usr/bin/env bash
# Run SkillBench evolution via UnifiedEngine (Phase 1).
#
# This is the unified counterpart to
# run_skillbench_evolve_in_situ_cycle.sh. The legacy script uses
# AEvolveEngine.evolve() and the 1639-line orchestration wrapper. This
# one uses EvolutionLoop + UnifiedEngine — engine-level parity only
# (general-skill evolution). See
# docs/algorithms/unified-equivalence-audit.md for the scope difference.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# Override via env vars.
# Unified pass / cycle knobs:
#   CYCLES    → --max-cycles  (= cycle_per_batch in the unified model)
#   PASSES    → --passes      (outer dataset sweeps; currently no-op
#                              with a warning when >1; see python runner
#                              for context)
CYCLES="${CYCLES:-${MAX_CYCLES:-2}}"
PASSES="${PASSES:-}"
BATCH_SIZE="${BATCH_SIZE:-1}"
MAX_WORKERS="${MAX_WORKERS:-${PARALLEL:-1}}"
LIMIT="${LIMIT:-}"
# Skill selection: '0' or 'all' = inject every skill, N>0 = top-N by
# keyword match. Mirrors the legacy `run_skillbench_evolve_in_situ_cycle.sh`
# env knob so EvolverBench (or manual callers) can cap how many skills
# the solver agent sees per task.
SKILL_SELECT_LIMIT="${SKILL_SELECT_LIMIT:-0}"
MODE="${MODE:-native}"
USE_SKILLS="${USE_SKILLS:-false}"
SPLIT_SEED="${SPLIT_SEED:-42}"
NATIVE_PROFILE="${NATIVE_PROFILE:-terminus2}"
SCORE_MODE="${SCORE_MODE:-dual}"
RETRY_MAX="${RETRY_MAX:-6}"
RETRY_MIN_WAIT_SEC="${RETRY_MIN_WAIT_SEC:-1.0}"
RETRY_MAX_WAIT_SEC="${RETRY_MAX_WAIT_SEC:-150.0}"
CATEGORY="${CATEGORY:-}"
DIFFICULTY="${DIFFICULTY:-}"
FEEDBACK_LEVEL="${FEEDBACK_LEVEL:-tests}"
TASK_SKILL_MODE="${TASK_SKILL_MODE:-pre_generate_and_retry}"
NO_DIRECT_ANSWERS="${NO_DIRECT_ANSWERS:-true}"
EVOLVE_SKILLS="${EVOLVE_SKILLS:-true}"
EVOLVE_MEMORY="${EVOLVE_MEMORY:-false}"
EVOLVE_PROMPTS="${EVOLVE_PROMPTS:-false}"
EVOLVE_TOOLS="${EVOLVE_TOOLS:-false}"
DISTILL="${DISTILL:-false}"
SUCCESS_MODE="${SUCCESS_MODE:-gated_promotion}"
PROMOTION_THRESHOLD="${PROMOTION_THRESHOLD:-1}"
MODEL_ID="${MODEL_ID:-us.anthropic.claude-opus-4-6-v1}"
EVOLVER_MODEL_ID="${EVOLVER_MODEL_ID:-}"
REGION="${REGION:-us-west-2}"
MAX_TOKENS="${MAX_TOKENS:-16384}"
export BEDROCK_RETRY_MAX_ATTEMPTS="${BEDROCK_RETRY_MAX_ATTEMPTS:-15}"
export BEDROCK_READ_TIMEOUT_SEC="${BEDROCK_READ_TIMEOUT_SEC:-600}"
export BEDROCK_CONNECT_TIMEOUT_SEC="${BEDROCK_CONNECT_TIMEOUT_SEC:-30}"

SEED_WORKSPACE="${SEED_WORKSPACE:-${REPO_ROOT}/seed_workspaces/skillbench}"
TASKS_DIR_WITH_SKILLS="${TASKS_DIR_WITH_SKILLS:-}"
TASKS_DIR_WITHOUT_SKILLS="${TASKS_DIR_WITHOUT_SKILLS:-}"
HARBOR_REPO="${HARBOR_REPO:-}"
HARBOR_AGENT_IMPORT_PATH="${HARBOR_AGENT_IMPORT_PATH:-libs.terminus_agent.agents.terminus_2.harbor_terminus_2_skills:HarborTerminus2WithSkills}"
HARBOR_MODEL_NAME="${HARBOR_MODEL_NAME:-}"
HARBOR_JOBS_DIR="${HARBOR_JOBS_DIR:-/tmp/aevolve-skillbench-harbor-jobs}"
HARBOR_TIMEOUT_SEC="${HARBOR_TIMEOUT_SEC:-1800}"
HARBOR_UV_CMD="${HARBOR_UV_CMD:-uv run harbor run}"
RUN_ID="${RUN_ID:-$(date -u +%Y%m%d_%H%M%S)_pid$$}"
MODE_LC="$(echo "${MODE}" | tr '[:upper:]' '[:lower:]')"
USE_SKILLS_LC="$(echo "${USE_SKILLS}" | tr '[:upper:]' '[:lower:]')"
RUN_DIR="${RUN_DIR:-${REPO_ROOT}/logs/unified_grind_run_${MODE_LC}_skills-${USE_SKILLS_LC}_${RUN_ID}}"

mkdir -p "$(dirname "${RUN_DIR}")"

echo "=== SkillBench Unified Run ==="
echo "Run ID:        ${RUN_ID}"
echo "Run dir:       ${RUN_DIR}"
echo "Cycles:        ${CYCLES}"
echo "Batch size:    ${BATCH_SIZE}"
echo "Parallel:      ${MAX_WORKERS} (thread)"
echo "Mode:          ${MODE}"
echo "Use skills:    ${USE_SKILLS}"
echo "Feedback:      ${FEEDBACK_LEVEL}"
echo "Task skills:   ${TASK_SKILL_MODE}"
echo "Success mode:  ${SUCCESS_MODE}"
[[ -n "${LIMIT}" ]] && echo "Limit:         ${LIMIT}"
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
  "${REPO_ROOT}/examples/skillbench_examples/skillbench_evolve_in_situ_cycle_unified.py"
  --max-cycles "${CYCLES}"
  --batch-size "${BATCH_SIZE}"
  --max-workers "${MAX_WORKERS}"
  --mode "${MODE}"
  --use-skills "${USE_SKILLS}"
  --split-seed "${SPLIT_SEED}"
  --native-profile "${NATIVE_PROFILE}"
  --score-mode "${SCORE_MODE}"
  --model-id "${MODEL_ID}"
  --region "${REGION}"
  --max-tokens "${MAX_TOKENS}"
  --retry-max "${RETRY_MAX}"
  --retry-min-wait-sec "${RETRY_MIN_WAIT_SEC}"
  --retry-max-wait-sec "${RETRY_MAX_WAIT_SEC}"
  --feedback-level "${FEEDBACK_LEVEL}"
  --task-skill-mode "${TASK_SKILL_MODE}"
  --no-direct-answers "${NO_DIRECT_ANSWERS}"
  --evolve-skills "${EVOLVE_SKILLS}"
  --evolve-memory "${EVOLVE_MEMORY}"
  --evolve-prompts "${EVOLVE_PROMPTS}"
  --evolve-tools "${EVOLVE_TOOLS}"
  --distill "${DISTILL}"
  --success-mode "${SUCCESS_MODE}"
  --promotion-threshold "${PROMOTION_THRESHOLD}"
  --skill-select-limit "${SKILL_SELECT_LIMIT}"
  --seed-workspace "${SEED_WORKSPACE}"
  --run-dir "${RUN_DIR}"
  --output "${RUN_DIR}/results.jsonl"
  --harbor-agent-import-path "${HARBOR_AGENT_IMPORT_PATH}"
  --harbor-jobs-dir "${HARBOR_JOBS_DIR}"
  --harbor-timeout-sec "${HARBOR_TIMEOUT_SEC}"
  --harbor-uv-cmd "${HARBOR_UV_CMD}"
  -v
)
[[ -n "${LIMIT}" ]] && cmd+=(--limit "${LIMIT}")
[[ -n "${EVOLVER_MODEL_ID}" ]] && cmd+=(--evolver-model-id "${EVOLVER_MODEL_ID}")
[[ -n "${PASSES}" ]] && cmd+=(--passes "${PASSES}")
[[ -n "${TASKS_DIR_WITH_SKILLS}" ]] && cmd+=(--tasks-dir-with-skills "${TASKS_DIR_WITH_SKILLS}")
[[ -n "${TASKS_DIR_WITHOUT_SKILLS}" ]] && cmd+=(--tasks-dir-without-skills "${TASKS_DIR_WITHOUT_SKILLS}")
[[ -n "${HARBOR_REPO}" ]] && cmd+=(--harbor-repo "${HARBOR_REPO}")
[[ -n "${HARBOR_MODEL_NAME}" ]] && cmd+=(--harbor-model-name "${HARBOR_MODEL_NAME}")
[[ -n "${CATEGORY}" ]] && cmd+=(--category "${CATEGORY}")
[[ -n "${DIFFICULTY}" ]] && cmd+=(--difficulty "${DIFFICULTY}")

LOG="${RUN_DIR}/evolve.log"
echo "Running: ${cmd[*]}"
mkdir -p "${RUN_DIR}"
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
echo "=== Unified run completed ==="
echo "  Exit code:  ${exit_code}"
echo "  Results:    ${RUN_DIR}/results.jsonl"
echo "  Metrics:    ${RUN_DIR}/results.metrics.json"
echo "  Log:        ${LOG}"
exit "${exit_code}"
