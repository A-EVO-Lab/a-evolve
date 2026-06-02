#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Phase 1 (Exp1): Solver-evolvability sweep, single seed (42)
#
# Default matrix: 8 solvers × 4 evolvers (none + 3 working) × 3 benchmarks × 1 seed = 96 cells.
# `none` (no-evolution baseline route) is in the default EVOLVERS list so a
# single invocation covers both baseline and evolve cells.
# Evolvers = {opus46, sonnet46, qwen235b} only. Non-none evolvers route through
# the harness-disentangling unified train/test split wrappers. 'none' uses dedicated
# no-evolution wrappers and is opt-in via --evolver none.
#
# Per pivot 2026-04-21: solver is the axis under study; evolver pool is fixed
# to the three working models. These adapters do not honour --seed for
# task-order reshuffle, so the default is a single seed=42 run.
#
# Skip logic: each cell's done-marker is read from its BENCHMARK_REPORT.md
# sidecar (authored by run_exp1.py) via scripts/lib/read_sidecar.sh. When a
# cell has not yet been launched (no sidecar), the script falls back to a
# per-route default marker so re-launches are still safe.
#
# Usage:
#   bash scripts/phase1_single_seed.sh                   # default 96 cells
#   bash scripts/phase1_single_seed.sh --solver kimi
#   bash scripts/phase1_single_seed.sh --evolver opus46
#   bash scripts/phase1_single_seed.sh --evolver none    # opt-in baseline sweep
#   bash scripts/phase1_single_seed.sh --benchmark swe
#   MAX_PARALLEL=4 bash scripts/phase1_single_seed.sh
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail

EXPERIMENT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROJECT_ROOT="${AEVOLVE_REPO_DIR:-$(cd "$EXPERIMENT_ROOT/../.." && pwd)}"
VENV="${VENV:-$PROJECT_ROOT/.venv/bin/activate}"
RESULTS_DIR="${RESULTS_DIR:-$EXPERIMENT_ROOT/results/exp1_v3}"
LOG_DIR="$RESULTS_DIR/logs"

MAX_PARALLEL="${MAX_PARALLEL:-3}"
SEED=42

# Region-routing knobs (forwarded to run_exp1.py). Default 'single' preserves
# current behaviour (us-west-2). Set REGION_STRATEGY=hash for the deterministic
# multi-region picker.
REGION_STRATEGY="${REGION_STRATEGY:-single}"
REGION="${REGION:-us-west-2}"
FORCE_RELAUNCH_LEGACY=false

# 3 working evolvers only — 'none' opt-in. DEC-7: single default seed.
SOLVERS=(sonnet46 opus46 haiku45 gptoss120b qwen235b qwen32b minimax kimi)
EVOLVERS=(none opus46 sonnet46 qwen235b)
BENCHMARKS=(swe mcp sb)

while [[ $# -gt 0 ]]; do
    case "$1" in
        --solver)       SOLVERS=("$2"); shift 2 ;;
        --evolver)      EVOLVERS=("$2"); shift 2 ;;
        --benchmark)    BENCHMARKS=("$2"); shift 2 ;;
        --max-parallel) MAX_PARALLEL="$2"; shift 2 ;;
        --region-strategy) REGION_STRATEGY="$2"; shift 2 ;;
        --region)       REGION="$2"; shift 2 ;;
        --force-relaunch-legacy) FORCE_RELAUNCH_LEGACY=true; shift ;;
        *) echo "Unknown: $1"; exit 1 ;;
    esac
done

ENV_FILE="$PROJECT_ROOT/.env"
[[ -f "$ENV_FILE" ]] && { set -a; source "$ENV_FILE"; set +a; }

# ── Preflight ───────────────────────────────────────────────────────────────
if [[ -z "${BEDROCK_API_KEY:-}" ]] && ! aws sts get-caller-identity &>/dev/null; then
    echo "ERROR: No Bedrock auth. Set BEDROCK_API_KEY in .env or IAM creds." >&2; exit 1
fi
docker info &>/dev/null || { echo "ERROR: Docker not accessible"; exit 1; }
[[ -d "$PROJECT_ROOT" ]] || { echo "ERROR: repository root not found: $PROJECT_ROOT" >&2; exit 1; }
[[ -f "$VENV" ]] || { echo "ERROR: venv missing: $VENV" >&2; exit 1; }
mkdir -p "$LOG_DIR"

# ── Route-aware done-marker lookup (sidecar-first) ─────────────────────────
# Primary source: each cell's BENCHMARK_REPORT.md authored by run_exp1.py.
# Fallback (used ONLY before the first launch, when no sidecar exists yet)
# is a per-route default. The fallback exists so the skip check is safe even
# on a completely fresh RESULTS_DIR.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib/read_sidecar.sh
source "$SCRIPT_DIR/lib/read_sidecar.sh"

done_marker_fallback() {
    local evolver="$1" bm="$2"
    if [[ "$evolver" == "none" ]]; then
        case "$bm" in
            swe) echo "results.json" ;;
            mcp) echo "RUN_COMPLETE.json" ;;
            sb)  echo "summary.txt" ;;
        esac
    else
        echo "results.metrics.json"
    fi
}

done_marker_for_cell() {
    local cell="$1" evolver="$2" bm="$3"
    local marker
    # MCP baseline: bypass any stale sidecar (older cells recorded
    # done_marker=summary.csv) and force the new completion sentinel so
    # half-streamed cells do not get incorrectly skipped.
    if [[ "$evolver" == "none" && "$bm" == "mcp" ]]; then
        done_marker_fallback "$evolver" "$bm"
        return
    fi
    marker="$(sidecar_done_marker "$cell")"
    if [[ -z "$marker" ]]; then
        marker="$(done_marker_fallback "$evolver" "$bm")"
    fi
    echo "$marker"
}

# ── Job control ─────────────────────────────────────────────────────────────
declare -a PIDS=() JOBS=()
RUNNING=0; TOTAL=0; SKIPPED=0; CONFLICTS=0; FAILED=0

wait_for_slot() {
    while (( RUNNING >= MAX_PARALLEL )); do
        local np=() nj=()
        for i in "${!PIDS[@]}"; do
            if kill -0 "${PIDS[$i]}" 2>/dev/null; then
                np+=("${PIDS[$i]}"); nj+=("${JOBS[$i]}")
            else
                if wait "${PIDS[$i]}" 2>/dev/null; then
                    echo "[DONE] ${JOBS[$i]}"
                else
                    echo "[FAILED] ${JOBS[$i]}"
                    FAILED=$((FAILED + 1))
                fi
                RUNNING=$((RUNNING - 1))
            fi
        done
        PIDS=("${np[@]+"${np[@]}"}")
        JOBS=("${nj[@]+"${nj[@]}"}")
        sleep 3
    done
}

launch() {
    local solver="$1" evolver="$2" bm="$3"
    local run_id="${solver}_x_${evolver}_${bm}_s${SEED}"
    local out_dir="$RESULTS_DIR/${run_id}"
    local log_file="$LOG_DIR/${run_id}.log"

    # Resolve what region this cell would land on under the current
    # strategy. The Python resolver is the single source of truth; bash
    # never duplicates routing logic.
    local resolve_json want_region
    resolve_json="$(
        source "$VENV" 2>/dev/null
        cd "$EXPERIMENT_ROOT"
        python run_exp1.py \
            --solver "$solver" --evolver "$evolver" --benchmark "$bm" \
            --seed "$SEED" \
            --region-strategy "$REGION_STRATEGY" --region "$REGION" \
            --resolve-only --json 2>/dev/null
    )"
    if [[ -z "$resolve_json" ]]; then
        echo "[ERROR] $run_id  resolver returned empty (likely an unavailable model/region pair)"
        FAILED=$((FAILED + 1))
        return 1
    fi
    want_region="$(printf %s "$resolve_json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["region"])')"

    # Read recorded routing metadata (empty for legacy / fresh cells).
    local have_strategy have_region marker
    have_strategy="$(sidecar_region_strategy "$out_dir")"
    have_region="$(sidecar_region "$out_dir")"
    # Legacy fallback: cells produced before the metadata existed are
    # treated as (single, us-west-2) so they are still skip-compatible
    # with the current default.
    have_strategy="${have_strategy:-single}"
    have_region="${have_region:-us-west-2}"
    marker="$(done_marker_for_cell "$out_dir" "$evolver" "$bm")"

    if [[ -f "$out_dir/$marker" ]]; then
        # Done-marker exists. Skip only if (strategy, region) match exactly.
        if [[ "$have_strategy" == "$REGION_STRATEGY" && "$have_region" == "$want_region" ]]; then
            echo "[SKIP] $run_id  (found $marker, region=$have_region)"
            SKIPPED=$((SKIPPED + 1))
            return 0
        fi
        echo "[CONFLICT] $out_dir  recorded=($have_strategy,$have_region) want=($REGION_STRATEGY,$want_region)"
        if $FORCE_RELAUNCH_LEGACY; then
            local ts="$(date +%s)"
            mv "$out_dir" "${out_dir}.legacy.${ts}"
            echo "  [force-relaunch-legacy] renamed → ${out_dir}.legacy.${ts}"
        else
            echo "  hint: re-run with --force-relaunch-legacy to rename and re-launch"
            CONFLICTS=$((CONFLICTS + 1))
            return 0  # conflict counted; loop continues, caller checks CONFLICTS at end
        fi
    fi

    wait_for_slot
    echo "[START] $run_id  (strategy=$REGION_STRATEGY region=$want_region)"
    (
        source "$VENV"
        export AEVOLVE_REPO_DIR="$PROJECT_ROOT"
        export BYPASS_TOOL_CONSENT=true
        cd "$EXPERIMENT_ROOT"
        python run_exp1.py \
            --solver "$solver" \
            --evolver "$evolver" \
            --benchmark "$bm" \
            --seed "$SEED" \
            --region-strategy "$REGION_STRATEGY" \
            --region "$REGION" \
            --output-root "$RESULTS_DIR"
    ) &>"$log_file" &
    PIDS+=($!); JOBS+=("$run_id")
    RUNNING=$((RUNNING + 1)); TOTAL=$((TOTAL + 1))
}

# ── Launch ──────────────────────────────────────────────────────────────────
START=$(date +%s)
echo "============================================================"
echo "Phase 1 (Exp1): Solver-Evolvability Sweep — seed=$SEED"
echo "  Solvers:    ${SOLVERS[*]}"
echo "  Evolvers:   ${EVOLVERS[*]}"
echo "  Benchmarks: ${BENCHMARKS[*]}"
echo "  Parallel:   $MAX_PARALLEL"
echo "  Repo:       $PROJECT_ROOT"
echo "  Output:     $RESULTS_DIR"
echo "  Region:     $REGION_STRATEGY (explicit=$REGION)"
$FORCE_RELAUNCH_LEGACY && echo "  Force-relaunch legacy: yes"
echo "============================================================"

for bm in "${BENCHMARKS[@]}"; do
    for solver in "${SOLVERS[@]}"; do
        for evolver in "${EVOLVERS[@]}"; do
            launch "$solver" "$evolver" "$bm"
        done
    done
done

for i in "${!PIDS[@]}"; do
    if wait "${PIDS[$i]}" 2>/dev/null; then
        echo "[DONE] ${JOBS[$i]}"
    else
        echo "[FAILED] ${JOBS[$i]}"
        FAILED=$((FAILED + 1))
    fi
done
ELAPSED=$(( $(date +%s) - START ))

# ── Summary ─────────────────────────────────────────────────────────────────
echo ""
echo "============================================================"
echo "Phase 1 done ($TOTAL launched, $SKIPPED skipped, $FAILED failed, $CONFLICTS conflicts, ${ELAPSED}s)"
echo "============================================================"
echo "Run scripts/check_status.sh for the per-benchmark solver × evolver matrix."

# Non-zero exit if any cell hit a routing conflict that wasn't resolved
# (--force-relaunch-legacy not set). Unattended phase-script invocations
# rely on this so they don't appear successful while leaving conflicts
# unresolved.
if (( CONFLICTS > 0 )); then
    echo "ERROR: $CONFLICTS cell(s) had a region/strategy conflict and were not relaunched." >&2
    echo "       Re-run with --force-relaunch-legacy to rename and relaunch them." >&2
    exit 3
fi

if (( FAILED > 0 )); then
    echo "ERROR: $FAILED cell(s) failed. Check $LOG_DIR for per-cell logs." >&2
    exit 4
fi
