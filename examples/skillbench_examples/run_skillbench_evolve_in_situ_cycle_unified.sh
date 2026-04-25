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
CYCLES="${CYCLES:-3}"
PASSES="${PASSES:-}"
BATCH_SIZE="${BATCH_SIZE:-2}"
LIMIT="${LIMIT:-}"
# Skill selection: '0' or 'all' = inject every skill, N>0 = top-N by
# keyword match. Mirrors the legacy `run_skillbench_evolve_in_situ_cycle.sh`
# env knob so EvolverBench (or manual callers) can cap how many skills
# the solver agent sees per task.
SKILL_SELECT_LIMIT="${SKILL_SELECT_LIMIT:-}"
MODE="${MODE:-native}"
USE_SKILLS="${USE_SKILLS:-true}"
MODEL_ID="${MODEL_ID:-us.anthropic.claude-opus-4-5-20251101-v1:0}"
EVOLVER_MODEL_ID="${EVOLVER_MODEL_ID:-}"
REGION="${REGION:-us-west-2}"
MAX_TOKENS="${MAX_TOKENS:-64000}"
export BEDROCK_RETRY_MAX_ATTEMPTS="${BEDROCK_RETRY_MAX_ATTEMPTS:-15}"
export BEDROCK_READ_TIMEOUT_SEC="${BEDROCK_READ_TIMEOUT_SEC:-600}"
export BEDROCK_CONNECT_TIMEOUT_SEC="${BEDROCK_CONNECT_TIMEOUT_SEC:-30}"

SEED_WORKSPACE="${SEED_WORKSPACE:-${REPO_ROOT}/seed_workspaces/skillbench}"
RUN_ID="${RUN_ID:-$(date -u +%Y%m%d_%H%M%S)_pid$$}"
RUN_DIR="${RUN_DIR:-${REPO_ROOT}/logs/unified_skillbench_${RUN_ID}}"

mkdir -p "$(dirname "${RUN_DIR}")"

echo "=== SkillBench Unified Run ==="
echo "Run ID:        ${RUN_ID}"
echo "Run dir:       ${RUN_DIR}"
echo "Cycles:        ${CYCLES}"
echo "Batch size:    ${BATCH_SIZE}"
echo "Mode:          ${MODE}"
echo "Use skills:    ${USE_SKILLS}"
[[ -n "${LIMIT}" ]] && echo "Limit:         ${LIMIT}"
echo "Model:         ${MODEL_ID}"
echo "Evolver model: ${EVOLVER_MODEL_ID:-<same as solver>}"
echo "Region:        ${REGION}"
echo ""

cmd=(
  python "${REPO_ROOT}/examples/skillbench_examples/skillbench_evolve_in_situ_cycle_unified.py"
  --max-cycles "${CYCLES}"
  --batch-size "${BATCH_SIZE}"
  --mode "${MODE}"
  --use-skills "${USE_SKILLS}"
  --model-id "${MODEL_ID}"
  --region "${REGION}"
  --max-tokens "${MAX_TOKENS}"
  --seed-workspace "${SEED_WORKSPACE}"
  --run-dir "${RUN_DIR}"
  --output "${RUN_DIR}/results.jsonl"
  -v
)
[[ -n "${LIMIT}" ]] && cmd+=(--limit "${LIMIT}")
[[ -n "${EVOLVER_MODEL_ID}" ]] && cmd+=(--evolver-model-id "${EVOLVER_MODEL_ID}")
[[ -n "${PASSES}" ]] && cmd+=(--passes "${PASSES}")
[[ -n "${SKILL_SELECT_LIMIT}" ]] && cmd+=(--skill-select-limit "${SKILL_SELECT_LIMIT}")

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
