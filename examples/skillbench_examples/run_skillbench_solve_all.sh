#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# ------------------------------------------------------------------------------
# Config (override via env vars)
# ------------------------------------------------------------------------------
MODE="${MODE:-native}"                      # native | harbor
USE_SKILLS="${USE_SKILLS:-true}"            # true | false
MAX_WORKERS="${MAX_WORKERS:-5}"             # parallel workers
SPLIT_SEED="${SPLIT_SEED:-42}"
NATIVE_PROFILE="${NATIVE_PROFILE:-terminus2}"   # strands | terminus2 | terminus2_legacy
SCORE_MODE="${SCORE_MODE:-dual}"                # reward | binary | dual
RETRY_MAX="${RETRY_MAX:-6}"
RETRY_MIN_WAIT_SEC="${RETRY_MIN_WAIT_SEC:-1.0}"
RETRY_MAX_WAIT_SEC="${RETRY_MAX_WAIT_SEC:-150.0}"
MODEL_ID="${MODEL_ID:-us.anthropic.claude-opus-4-5-20251101-v1:0}"
REGION="${REGION:-us-west-2}"
MAX_TOKENS="${MAX_TOKENS:-64000}"
# Default to the bundled skillbench workspace.
SEED_WORKSPACE="${SEED_WORKSPACE:-${REPO_ROOT}/seed_workspaces/skillbench}"

TASKS_DIR_WITH_SKILLS="${TASKS_DIR_WITH_SKILLS:-}"
TASKS_DIR_WITHOUT_SKILLS="${TASKS_DIR_WITHOUT_SKILLS:-}"

# Backward-compatible override: TASKS_DIR wins if set.
TASKS_DIR="${TASKS_DIR:-}"

HARBOR_REPO="${HARBOR_REPO:-}"
HARBOR_CONFIG_TEMPLATE="${HARBOR_CONFIG_TEMPLATE:-}"
HARBOR_AGENT_IMPORT_PATH="${HARBOR_AGENT_IMPORT_PATH:-libs.terminus_agent.agents.terminus_2.harbor_terminus_2_skills:HarborTerminus2WithSkills}"
HARBOR_MODEL_NAME="${HARBOR_MODEL_NAME:-}"  # empty -> follows --model-id in skillbench_solve_one.py
MODE_LC="$(echo "${MODE}" | tr '[:upper:]' '[:lower:]')"
USE_SKILLS_LC="$(echo "${USE_SKILLS}" | tr '[:upper:]' '[:lower:]')"
if [[ "${USE_SKILLS_LC}" == "true" ]]; then
  HARBOR_JOBS_DIR="${HARBOR_JOBS_DIR:-${REPO_ROOT}/outputs/aevolve-skillbench-harbor-jobs-skills}"
else
  HARBOR_JOBS_DIR="${HARBOR_JOBS_DIR:-${REPO_ROOT}/outputs/aevolve-skillbench-harbor-jobs-no-skills}"
fi
HARBOR_TIMEOUT_SEC="${HARBOR_TIMEOUT_SEC:-1800}"
HARBOR_UV_CMD="${HARBOR_UV_CMD:-uv run harbor run}"

RUN_ID="${RUN_ID:-$(date -u +%Y%m%d_%H%M%S)}"
RUN_DIR="${RUN_DIR:-${REPO_ROOT}/logs/skillbench_full_solve_${MODE}_skills-${USE_SKILLS}_${RUN_ID}}"
ARTIFACTS_DIR="${ARTIFACTS_DIR:-${RUN_DIR}/outputs}"
SKILLBENCH_RUN_ID_BASE="${SKILLBENCH_RUN_ID_BASE:-${RUN_ID}-skills-${USE_SKILLS_LC}-pid$$}"

mkdir -p "${RUN_DIR}"
mkdir -p "${ARTIFACTS_DIR}"

RESULTS_JSONL="${RUN_DIR}/results.jsonl"
SUMMARY_TXT="${RUN_DIR}/summary.txt"
TASK_RESULT_DIR="${RUN_DIR}/_task_results"
mkdir -p "${TASK_RESULT_DIR}"

if command -v rg >/dev/null 2>&1; then
  FIND_CMD="rg"
else
  FIND_CMD="grep"
  echo "WARN: 'rg' not found; falling back to grep." >&2
fi

has_text() {
  local needle="$1"
  local file="$2"
  if [[ ! -f "${file}" ]]; then
    return 1
  fi
  if [[ "${FIND_CMD}" == "rg" ]]; then
    rg -q --fixed-strings -- "${needle}" "${file}"
  else
    grep -Fq -- "${needle}" "${file}"
  fi
}

count_text() {
  local needle="$1"
  local file="$2"
  if [[ ! -f "${file}" ]]; then
    echo 0
    return 0
  fi
  if [[ "${FIND_CMD}" == "rg" ]]; then
    rg -c --fixed-strings --no-filename -- "${needle}" "${file}" 2>/dev/null || echo 0
  else
    grep -F -c -- "${needle}" "${file}" 2>/dev/null || echo 0
  fi
}

if [[ "${MODE_LC}" != "native" && "${MODE_LC}" != "harbor" ]]; then
  echo "Invalid MODE=${MODE}. Expected native|harbor." >&2
  exit 1
fi

if [[ "${USE_SKILLS_LC}" != "true" && "${USE_SKILLS_LC}" != "false" ]]; then
  echo "Invalid USE_SKILLS=${USE_SKILLS}. Expected true|false." >&2
  exit 1
fi

if ! [[ "${MAX_WORKERS}" =~ ^[1-9][0-9]*$ ]]; then
  echo "Invalid MAX_WORKERS=${MAX_WORKERS}. Expected a positive integer." >&2
  exit 1
fi

SELECTED_TASKS_DIR="$(TASKS_DIR="${TASKS_DIR}" TASKS_DIR_WITH_SKILLS="${TASKS_DIR_WITH_SKILLS}" TASKS_DIR_WITHOUT_SKILLS="${TASKS_DIR_WITHOUT_SKILLS}" HARBOR_REPO="${HARBOR_REPO}" USE_SKILLS_LC="${USE_SKILLS_LC}" PYTHONPATH="${REPO_ROOT}:${PYTHONPATH:-}" python - <<'PY'
from agent_evolve.agents.skillbench.repo import SkillBenchSetupError, resolve_skillbench_paths
import os
import sys

try:
    paths = resolve_skillbench_paths(
        tasks_dir=os.environ.get("TASKS_DIR") or None,
        tasks_with_skills_dir=os.environ.get("TASKS_DIR_WITH_SKILLS") or None,
        tasks_without_skills_dir=os.environ.get("TASKS_DIR_WITHOUT_SKILLS") or None,
        harbor_repo=os.environ.get("HARBOR_REPO") or None,
    )
except SkillBenchSetupError as exc:
    print(str(exc), file=sys.stderr)
    raise SystemExit(1)
print(paths.selected_tasks_dir(use_skills=os.environ.get("USE_SKILLS_LC", "true") == "true"))
PY
)"

if [[ ! -d "${SELECTED_TASKS_DIR}" ]]; then
  echo "Resolved tasks dir not found: ${SELECTED_TASKS_DIR}" >&2
  exit 1
fi

mapfile -t TASKS < <(find "${SELECTED_TASKS_DIR}" -mindepth 1 -maxdepth 1 -type d -printf "%f\n" | sort)
TOTAL="${#TASKS[@]}"

if [[ "${TOTAL}" -eq 0 ]]; then
  echo "No tasks found under ${SELECTED_TASKS_DIR}" >&2
  exit 1
fi

echo "Run dir: ${RUN_DIR}"
echo "Mode: ${MODE_LC}"
echo "Use skills: ${USE_SKILLS_LC}"
echo "Tasks dir (selected): ${SELECTED_TASKS_DIR}"
echo "Tasks dir (with skills): ${TASKS_DIR_WITH_SKILLS:-<auto>}"
echo "Tasks dir (without skills): ${TASKS_DIR_WITHOUT_SKILLS:-<auto>}"
echo "Split seed: ${SPLIT_SEED}"
echo "Native profile: ${NATIVE_PROFILE}"
echo "Score mode: ${SCORE_MODE}"
echo "Retry: max=${RETRY_MAX}, wait=[${RETRY_MIN_WAIT_SEC}, ${RETRY_MAX_WAIT_SEC}]"
echo "Model: ${MODEL_ID}"
echo "Region: ${REGION}"
echo "Max tokens: ${MAX_TOKENS}"
echo "Max workers: ${MAX_WORKERS}"
echo "Seed workspace: ${SEED_WORKSPACE}"
echo "Artifacts dir: ${ARTIFACTS_DIR}"
echo "SkillBench run id base: ${SKILLBENCH_RUN_ID_BASE}"
if [[ "${MODE_LC}" == "harbor" ]]; then
  echo "Harbor repo: ${HARBOR_REPO:-<auto>}"
  echo "Harbor agent import path: ${HARBOR_AGENT_IMPORT_PATH}"
  if [[ -n "${HARBOR_MODEL_NAME}" ]]; then
    echo "Harbor model name: ${HARBOR_MODEL_NAME}"
  else
    echo "Harbor model name: <follow MODEL_ID>"
  fi
  echo "Harbor jobs dir: ${HARBOR_JOBS_DIR}"
  echo "Harbor timeout sec: ${HARBOR_TIMEOUT_SEC}"
fi
echo "Total tasks: ${TOTAL}"
echo

run_one_task() {
  local task="$1"
  local idx="$2"
  local task_log="${RUN_DIR}/${task}_${MODE_LC}_skills-${USE_SKILLS_LC}.log"
  local task_result="${TASK_RESULT_DIR}/${task}_${MODE_LC}_skills-${USE_SKILLS_LC}.json"
  local start_ts elapsed exit_code status score failure_class reward_float pass_binary native_impl prompt_template_sha256 metrics_line

  start_ts="$(date +%s)"
  cmd=(
    python "${REPO_ROOT}/examples/skillbench_examples/skillbench_solve_one.py"
    --task-id "${task}"
    --mode "${MODE_LC}"
    --use-skills "${USE_SKILLS_LC}"
    --split-seed "${SPLIT_SEED}"
    --native-profile "${NATIVE_PROFILE}"
    --score-mode "${SCORE_MODE}"
    --retry-max "${RETRY_MAX}"
    --retry-min-wait-sec "${RETRY_MIN_WAIT_SEC}"
    --retry-max-wait-sec "${RETRY_MAX_WAIT_SEC}"
    --seed-workspace "${SEED_WORKSPACE}"
    --artifacts-dir "${ARTIFACTS_DIR}"
    --model-id "${MODEL_ID}"
    --region "${REGION}"
    --max-tokens "${MAX_TOKENS}"
  )

  [[ -n "${TASKS_DIR}" ]] && cmd+=(--tasks-dir "${TASKS_DIR}")
  [[ -n "${TASKS_DIR_WITH_SKILLS}" ]] && cmd+=(--tasks-dir-with-skills "${TASKS_DIR_WITH_SKILLS}")
  [[ -n "${TASKS_DIR_WITHOUT_SKILLS}" ]] && cmd+=(--tasks-dir-without-skills "${TASKS_DIR_WITHOUT_SKILLS}")

  if [[ "${MODE_LC}" == "harbor" ]]; then
    cmd+=(
      --harbor-agent-import-path "${HARBOR_AGENT_IMPORT_PATH}"
      --harbor-jobs-dir "${HARBOR_JOBS_DIR}"
      --harbor-timeout-sec "${HARBOR_TIMEOUT_SEC}"
      --harbor-uv-cmd "${HARBOR_UV_CMD}"
    )
    [[ -n "${HARBOR_REPO}" ]] && cmd+=(--harbor-repo "${HARBOR_REPO}")
    if [[ -n "${HARBOR_CONFIG_TEMPLATE}" ]]; then
      cmd+=(--harbor-config-template "${HARBOR_CONFIG_TEMPLATE}")
    fi
    if [[ -n "${HARBOR_MODEL_NAME}" ]]; then
      cmd+=(--harbor-model-name "${HARBOR_MODEL_NAME}")
    fi
  fi

  set +e
  SKILLBENCH_RUN_ID="${SKILLBENCH_RUN_ID_BASE}" "${cmd[@]}" > "${task_log}" 2>&1
  exit_code=$?
  set -e

  elapsed="$(( $(date +%s) - start_ts ))"
  status="error"
  score="0.0"
  failure_class="unknown"
  reward_float="0.0"
  pass_binary="false"
  native_impl="unknown"
  prompt_template_sha256="unknown"
  if has_text "RESULT: PASS" "${task_log}"; then
    status="pass"
    score="1.0"
  elif has_text "RESULT: FAIL" "${task_log}"; then
    status="fail"
    score="0.0"
  fi

  metrics_line="$(grep -F 'Metrics:' "${task_log}" | tail -1 || true)"
  if [[ -n "${metrics_line}" ]]; then
    reward_float="$(echo "${metrics_line}" | sed -n 's/.*reward_float=\([^ ]*\).*/\1/p')"
    pass_binary="$(echo "${metrics_line}" | sed -n 's/.*pass_binary=\([^ ]*\).*/\1/p')"
    failure_class="$(echo "${metrics_line}" | sed -n 's/.*failure_class=\([^ ]*\).*/\1/p')"
    native_impl="$(echo "${metrics_line}" | sed -n 's/.*native_impl=\([^ ]*\).*/\1/p')"
    prompt_template_sha256="$(echo "${metrics_line}" | sed -n 's/.*prompt_template_sha256=\([^ ]*\).*/\1/p')"
    [[ -z "${reward_float}" ]] && reward_float="0.0"
    [[ -z "${pass_binary}" ]] && pass_binary="false"
    [[ -z "${failure_class}" ]] && failure_class="unknown"
    [[ -z "${native_impl}" ]] && native_impl="unknown"
    [[ -z "${prompt_template_sha256}" ]] && prompt_template_sha256="unknown"
  fi

  printf '{"task_id":"%s","mode":"%s","use_skills":%s,"status":"%s","score":%s,"reward_float":%s,"pass_binary":%s,"failure_class":"%s","native_impl":"%s","prompt_template_sha256":"%s","exit_code":%d,"duration_sec":%d,"tasks_dir":"%s","log_file":"%s"}\n' \
    "${task}" "${MODE_LC}" "${USE_SKILLS_LC}" "${status}" "${score}" "${reward_float}" "${pass_binary}" "${failure_class}" "${native_impl}" "${prompt_template_sha256}" "${exit_code}" "${elapsed}" "${SELECTED_TASKS_DIR}" "${task_log}" > "${task_result}"

  echo "[${idx}/${TOTAL}] ${task} -> ${status} (reward=${reward_float}, exit=${exit_code}, ${elapsed}s)"
}

to_run=()
idx=0
for task in "${TASKS[@]}"; do
  idx=$((idx + 1))
  if [[ -f "${RESULTS_JSONL}" ]] && has_text "\"task_id\":\"${task}\",\"mode\":\"${MODE_LC}\",\"use_skills\":${USE_SKILLS_LC}" "${RESULTS_JSONL}"; then
    echo "[${idx}/${TOTAL}] ${task} -> skipped (already recorded)"
    continue
  fi
  to_run+=("${task}")
done

to_run_count="${#to_run[@]}"
echo "Queued tasks: ${to_run_count}"
echo

active_jobs=0
launch_idx=0
for task in "${to_run[@]}"; do
  launch_idx=$((launch_idx + 1))
  run_one_task "${task}" "${launch_idx}" &
  active_jobs=$((active_jobs + 1))
  if (( active_jobs >= MAX_WORKERS )); then
    wait -n
    active_jobs=$((active_jobs - 1))
  fi
done

wait

if [[ -d "${TASK_RESULT_DIR}" ]]; then
  while IFS= read -r -d '' f; do
    cat "${f}" >> "${RESULTS_JSONL}"
  done < <(find "${TASK_RESULT_DIR}" -type f -name '*.json' -print0 | sort -z)
fi

pass_count="$(count_text '"status":"pass"' "${RESULTS_JSONL}")"
fail_count="$(count_text '"status":"fail"' "${RESULTS_JSONL}")"
error_count="$(count_text '"status":"error"' "${RESULTS_JSONL}")"
reward_stats="$(python3 -c "
import json, sys
total_reward = 0.0; n = 0
for line in open('${RESULTS_JSONL}'):
    line = line.strip()
    if not line: continue
    line = line.replace('True','true').replace('False','false').replace('None','null')
    try:
        d = json.loads(line)
        total_reward += float(d.get('reward_float', 0.0))
        n += 1
    except Exception:
        pass
avg = total_reward / n if n else 0.0
print(f'{total_reward:.4f} {avg:.4f} {n}')
" 2>/dev/null || echo "0.0000 0.0000 0")"
reward_sum="$(echo "${reward_stats}" | awk '{print $1}')"
reward_avg="$(echo "${reward_stats}" | awk '{print $2}')"
reward_count="$(echo "${reward_stats}" | awk '{print $3}')"
native_impl_values="$(
  grep -o '\"native_impl\":\"[^\"]*\"' "${RESULTS_JSONL}" 2>/dev/null \
    | sed 's/\"native_impl\":\"//;s/\"$//' \
    | sort -u \
    | paste -sd, - || true
)"
prompt_template_sha256_values="$(
  grep -o '\"prompt_template_sha256\":\"[^\"]*\"' "${RESULTS_JSONL}" 2>/dev/null \
    | sed 's/\"prompt_template_sha256\":\"//;s/\"$//' \
    | sort -u \
    | paste -sd, - || true
)"
[[ -z "${native_impl_values}" ]] && native_impl_values="unknown"
[[ -z "${prompt_template_sha256_values}" ]] && prompt_template_sha256_values="unknown"

{
  echo "tasks_total=${TOTAL}"
  echo "mode=${MODE_LC}"
  echo "use_skills=${USE_SKILLS_LC}"
  echo "tasks_dir_selected=${SELECTED_TASKS_DIR}"
  echo "split_seed=${SPLIT_SEED}"
  echo "native_profile=${NATIVE_PROFILE}"
  echo "score_mode=${SCORE_MODE}"
  echo "retry_max=${RETRY_MAX}"
  echo "retry_min_wait_sec=${RETRY_MIN_WAIT_SEC}"
  echo "retry_max_wait_sec=${RETRY_MAX_WAIT_SEC}"
  echo "model_id=${MODEL_ID}"
  echo "region=${REGION}"
  echo "max_tokens=${MAX_TOKENS}"
  echo "max_workers=${MAX_WORKERS}"
  echo "artifacts_dir=${ARTIFACTS_DIR}"
  echo "skillbench_run_id_base=${SKILLBENCH_RUN_ID_BASE}"
  echo "tasks_queued=${to_run_count}"
  if [[ "${MODE_LC}" == "harbor" ]]; then
    echo "harbor_repo=${HARBOR_REPO}"
    echo "harbor_agent_import_path=${HARBOR_AGENT_IMPORT_PATH}"
    echo "harbor_model_name=${HARBOR_MODEL_NAME:-<follow MODEL_ID>}"
    echo "harbor_jobs_dir=${HARBOR_JOBS_DIR}"
    echo "harbor_timeout_sec=${HARBOR_TIMEOUT_SEC}"
  fi
  echo "pass=${pass_count}"
  echo "fail=${fail_count}"
  echo "error=${error_count}"
  echo "reward_sum=${reward_sum}"
  echo "reward_avg=${reward_avg}"
  echo "native_impl_values=${native_impl_values}"
  echo "prompt_template_sha256_values=${prompt_template_sha256_values}"
  echo "results_jsonl=${RESULTS_JSONL}"
  echo "run_dir=${RUN_DIR}"
} > "${SUMMARY_TXT}"

echo
echo "Full solve run completed."
echo "  pass=${pass_count}  fail=${fail_count}  error=${error_count}  reward_sum=${reward_sum}  reward_avg=${reward_avg}"
echo "Summary: ${SUMMARY_TXT}"
