#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# ------------------------------------------------------------------------------
# Config (override via env vars)
# ------------------------------------------------------------------------------

# Grind settings
MAX_CYCLES="${MAX_CYCLES:-3}"          # max solve→evolve cycles per task
BATCH_SIZE="${BATCH_SIZE:-1}"          # tasks per evolution batch (1 = evolve after each task, like SWE-evolve)
MAX_WORKERS="${MAX_WORKERS:-1}"        # parallel workers for solving (1=serial, >1=parallel solve, serial evolve)

# SkillBench
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
LIMIT="${LIMIT:-}"
FEEDBACK_LEVEL="${FEEDBACK_LEVEL:-tests}"
TASK_SKILL_MODE="${TASK_SKILL_MODE:-pre_generate_and_retry}" # pre_generate_and_retry ｜ retry_only | off
NO_DIRECT_ANSWERS="${NO_DIRECT_ANSWERS:-true}"
EVOLVE_SKILLS="${EVOLVE_SKILLS:-true}"
EVOLVE_MEMORY="${EVOLVE_MEMORY:-false}"
EVOLVE_PROMPTS="${EVOLVE_PROMPTS:-false}"
EVOLVE_TOOLS="${EVOLVE_TOOLS:-false}"
DISTILL="${DISTILL:-false}"
SUCCESS_MODE="${SUCCESS_MODE:-gated_promotion}" # off | draft_only | gated_promotion
PROMOTION_THRESHOLD="${PROMOTION_THRESHOLD:-1}" # number of supporting tasks required for promotion
SKILL_SELECT_LIMIT="${SKILL_SELECT_LIMIT:-0}" # 0 or all = inject all skills; N>0 = keyword-match top N

# Model
MODEL_ID="${MODEL_ID:-us.anthropic.claude-opus-4-5-20251101-v1:0}"
EVOLVER_MODEL_ID="${EVOLVER_MODEL_ID:-}"
REGION="${REGION:-us-west-2}"
MAX_TOKENS="${MAX_TOKENS:-64000}"

# Workspace
# Default to the bundled skillbench workspace.
SEED_WORKSPACE="${SEED_WORKSPACE:-${REPO_ROOT}/seed_workspaces/skillbench}"

# Tasks
TASKS_DIR_WITH_SKILLS="${TASKS_DIR_WITH_SKILLS:-}"
TASKS_DIR_WITHOUT_SKILLS="${TASKS_DIR_WITHOUT_SKILLS:-}"

# Harbor
HARBOR_REPO="${HARBOR_REPO:-}"
HARBOR_AGENT_IMPORT_PATH="${HARBOR_AGENT_IMPORT_PATH:-libs.terminus_agent.agents.terminus_2.harbor_terminus_2_skills:HarborTerminus2WithSkills}"
HARBOR_MODEL_NAME="${HARBOR_MODEL_NAME:-}"
HARBOR_JOBS_DIR="${HARBOR_JOBS_DIR:-/tmp/aevolve-skillbench-harbor-jobs}"
HARBOR_TIMEOUT_SEC="${HARBOR_TIMEOUT_SEC:-1800}"
HARBOR_UV_CMD="${HARBOR_UV_CMD:-uv run harbor run}"

# Output — timestamp-based to prevent overwrite
MODE_LC="$(echo "${MODE}" | tr '[:upper:]' '[:lower:]')"
USE_SKILLS_LC="$(echo "${USE_SKILLS}" | tr '[:upper:]' '[:lower:]')"
RUN_ID="${RUN_ID:-$(date -u +%Y%m%d_%H%M%S)_pid$$}"
RUN_DIR="${RUN_DIR:-${REPO_ROOT}/logs/grind_run_${MODE_LC}_skills-${USE_SKILLS_LC}_${RUN_ID}}"

mkdir -p "${RUN_DIR}"

# ------------------------------------------------------------------------------
# Validation
# ------------------------------------------------------------------------------

if [[ "${MODE_LC}" != "native" && "${MODE_LC}" != "harbor" ]]; then
  echo "Invalid MODE=${MODE}. Expected native|harbor." >&2; exit 1
fi
if [[ "${USE_SKILLS_LC}" != "true" && "${USE_SKILLS_LC}" != "false" ]]; then
  echo "Invalid USE_SKILLS=${USE_SKILLS}. Expected true|false." >&2; exit 1
fi
if [[ ! -d "${SEED_WORKSPACE}" ]]; then
  echo "Seed workspace not found: ${SEED_WORKSPACE}" >&2; exit 1
fi

# ------------------------------------------------------------------------------
# Print config
# ------------------------------------------------------------------------------

echo "=== SkillBench Grind Run ==="
echo "Run ID:            ${RUN_ID}"
echo "Run dir:           ${RUN_DIR}"
echo ""
echo "--- Grind ---"
echo "Max cycles:        ${MAX_CYCLES} per task"
echo "Batch size:        ${BATCH_SIZE}"
[[ -n "${LIMIT}" ]] && echo "Limit:             ${LIMIT} tasks"
echo ""
echo "--- SkillBench ---"
echo "Mode:              ${MODE_LC}"
echo "Use skills:        ${USE_SKILLS_LC}"
echo "Tasks dir (with):  ${TASKS_DIR_WITH_SKILLS:-<auto>}"
echo "Tasks dir (without): ${TASKS_DIR_WITHOUT_SKILLS:-<auto>}"
echo "Native profile:    ${NATIVE_PROFILE}"
echo "Score mode:        ${SCORE_MODE}"
echo "Split seed:        ${SPLIT_SEED}"
echo "Feedback level:    ${FEEDBACK_LEVEL}"
echo "Task skill mode:   ${TASK_SKILL_MODE}"
[[ -n "${CATEGORY}" ]] && echo "Category:          ${CATEGORY}"
[[ -n "${DIFFICULTY}" ]] && echo "Difficulty:        ${DIFFICULTY}"
if [[ "${TASK_SKILL_MODE}" == "pre_generate_and_retry" ]]; then
  echo "Note:              cycle-1 passes may include pre-generated task-specific skills"
fi
echo ""
echo "--- Model ---"
echo "Model ID:          ${MODEL_ID}"
echo "Evolver model:     ${EVOLVER_MODEL_ID:-<same as MODEL_ID>}"
echo "Region:            ${REGION}"
echo "Max tokens:        ${MAX_TOKENS}"
echo "Harbor repo:       ${HARBOR_REPO:-<auto>}"
echo ""

# ------------------------------------------------------------------------------
# Build command
# ------------------------------------------------------------------------------

cmd=(
  python "${REPO_ROOT}/examples/skillbench_examples/skillbench_evolve_in_situ_cycle.py"
  --max-cycles "${MAX_CYCLES}"
  --batch-size "${BATCH_SIZE}"
  --max-workers "${MAX_WORKERS}"
  --mode "${MODE_LC}"
  --use-skills "${USE_SKILLS_LC}"
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

[[ -n "${TASKS_DIR_WITH_SKILLS}" ]] && cmd+=(--tasks-dir-with-skills "${TASKS_DIR_WITH_SKILLS}")
[[ -n "${TASKS_DIR_WITHOUT_SKILLS}" ]] && cmd+=(--tasks-dir-without-skills "${TASKS_DIR_WITHOUT_SKILLS}")
[[ -n "${HARBOR_REPO}" ]] && cmd+=(--harbor-repo "${HARBOR_REPO}")
[[ -n "${EVOLVER_MODEL_ID}" ]] && cmd+=(--evolver-model-id "${EVOLVER_MODEL_ID}")
[[ -n "${CATEGORY}" ]] && cmd+=(--category "${CATEGORY}")
[[ -n "${DIFFICULTY}" ]] && cmd+=(--difficulty "${DIFFICULTY}")
[[ -n "${LIMIT}" ]] && cmd+=(--limit "${LIMIT}")
[[ -n "${HARBOR_MODEL_NAME}" ]] && cmd+=(--harbor-model-name "${HARBOR_MODEL_NAME}")

# ------------------------------------------------------------------------------
# Execute
# ------------------------------------------------------------------------------

EVOLVE_LOG="${RUN_DIR}/evolve.log"
echo "Running: ${cmd[*]}"
echo "Log: ${EVOLVE_LOG}"
echo ""

set +e
if command -v stdbuf >/dev/null 2>&1; then
  stdbuf -oL -eL "${cmd[@]}" 2>&1 | tee "${EVOLVE_LOG}"
else
  "${cmd[@]}" 2>&1 | tee "${EVOLVE_LOG}"
fi
exit_code=${PIPESTATUS[0]}
set -e

echo ""
echo "=== Grind run completed ==="
echo "  Exit code:  ${exit_code}"
echo "  Results:    ${RUN_DIR}/results.jsonl"
echo "  Metrics:    ${RUN_DIR}/results.metrics.json"
echo "  Log:        ${EVOLVE_LOG}"
echo "  Run dir:    ${RUN_DIR}"

exit "${exit_code}"
