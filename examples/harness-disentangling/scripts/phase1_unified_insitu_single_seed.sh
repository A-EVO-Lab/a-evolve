#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Phase 1 unified IN-SITU: solver-evolvability sweep through UnifiedEngine
# in-situ runners (every task evolved-on AND scored; no train/test split).
# Single seed (42).
#
# Default matrix: 8 solvers x 4 evolvers (none + 3 working) x 3 benchmarks x 1 seed = 96 cells.
# `none` (no-evolution baseline route) is in the default EVOLVERS list so a
# single invocation covers both baseline and evolve cells.
# Defaults to deterministic multi-region routing (--region-strategy hash) and
# at most 5 concurrent outer cells.
#
# No-evolution baselines are shared with the legacy launcher because they do
# not instantiate an evolution engine.
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail

EXPERIMENT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROJECT_ROOT="${AEVOLVE_REPO_DIR:-$(cd "$EXPERIMENT_ROOT/../.." && pwd)}"
VENV="${VENV:-$PROJECT_ROOT/.venv/bin/activate}"
RESULTS_DIR="${RESULTS_DIR:-$EXPERIMENT_ROOT/results/exp1_unified_insitu}"
LOG_DIR="$RESULTS_DIR/logs"

MAX_PARALLEL="${MAX_PARALLEL:-5}"
SEED=42
REGION_STRATEGY="${REGION_STRATEGY:-hash}"
REGION="${REGION:-us-west-2}"
FORCE_RELAUNCH_UNIFIED=false

SOLVERS=(opus46 sonnet46 haiku45 gptoss120b qwen235b qwen32b minimax kimi)
EVOLVERS=(none opus46 sonnet46 qwen235b)
BENCHMARKS=(swe mcp sb)

while [[ $# -gt 0 ]]; do
    case "$1" in
        --solver)       IFS=',' read -ra SOLVERS    <<< "$2"; shift 2 ;;
        --evolver)      IFS=',' read -ra EVOLVERS   <<< "$2"; shift 2 ;;
        --benchmark)    IFS=',' read -ra BENCHMARKS <<< "$2"; shift 2 ;;
        --max-parallel) MAX_PARALLEL="$2"; shift 2 ;;
        --region-strategy) REGION_STRATEGY="$2"; shift 2 ;;
        --region)       REGION="$2"; shift 2 ;;
        --force-relaunch-unified|--force-relaunch-legacy) FORCE_RELAUNCH_UNIFIED=true; shift ;;
        *) echo "Unknown: $1"; exit 1 ;;
    esac
done

ENV_FILE="$PROJECT_ROOT/.env"
[[ -f "$ENV_FILE" ]] && { set -a; source "$ENV_FILE"; set +a; }

if [[ -z "${BEDROCK_API_KEY:-}" ]] && ! aws sts get-caller-identity &>/dev/null; then
    echo "ERROR: No Bedrock auth. Set BEDROCK_API_KEY in .env or IAM creds." >&2
    exit 1
fi
docker info &>/dev/null || { echo "ERROR: Docker not accessible"; exit 1; }
[[ -d "$PROJECT_ROOT" ]] || { echo "ERROR: repository root not found: $PROJECT_ROOT" >&2; exit 1; }
[[ -f "$VENV" ]] || { echo "ERROR: venv missing: $VENV" >&2; exit 1; }
mkdir -p "$LOG_DIR"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib/read_sidecar.sh
source "$SCRIPT_DIR/lib/read_sidecar.sh"

done_marker_fallback() {
    local evolver="$1" bm="$2"
    if [[ "$evolver" == "none" ]]; then
        case "$bm" in
            swe)          echo "results.json" ;;
            mcp)          echo "RUN_COMPLETE.json" ;;
            sb)           echo "summary.txt" ;;
        esac
    else
        case "$bm" in
            swe)          echo "results.metrics.json" ;;
            mcp)          echo "results.metrics.json" ;;
            sb)           echo "results.metrics.json" ;;
        esac
    fi
}

done_marker_for_cell() {
    local cell="$1" evolver="$2" bm="$3"
    local marker
    # MCP baseline: bypass any stale sidecar (older cells recorded
    # done_marker=summary.csv) and force the new completion sentinel so
    # half-streamed cells do not get incorrectly skipped.
    # MCP evolve under unified routes through UnifiedEngine →
    # results.metrics.json, so this override is baseline-only.
    if [[ "$evolver" == "none" && "$bm" == "mcp" ]]; then
        done_marker_fallback "$evolver" "$bm"
        return
    fi
    marker="$(sidecar_done_marker "$cell")"
    [[ -n "$marker" ]] || marker="$(done_marker_fallback "$evolver" "$bm")"
    echo "$marker"
}

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

    local resolve_json want_region
    resolve_json="$(
        source "$VENV" 2>/dev/null
        cd "$EXPERIMENT_ROOT"
        python run_exp1_unified_insitu.py \
            --solver "$solver" --evolver "$evolver" --benchmark "$bm" \
            --seed "$SEED" \
            --region-strategy "$REGION_STRATEGY" --region "$REGION" \
            --resolve-only --json 2>/dev/null
    )"
    if [[ -z "$resolve_json" ]]; then
        echo "[ERROR] $run_id  resolver returned empty"
        FAILED=$((FAILED + 1))
        return 1
    fi
    want_region="$(printf %s "$resolve_json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["region"])')"

    local have_strategy have_region marker
    have_strategy="$(sidecar_region_strategy "$out_dir")"
    have_region="$(sidecar_region "$out_dir")"
    have_strategy="${have_strategy:-single}"
    have_region="${have_region:-us-west-2}"
    marker="$(done_marker_for_cell "$out_dir" "$evolver" "$bm")"

    if [[ -f "$out_dir/$marker" ]]; then
        if [[ "$have_strategy" == "$REGION_STRATEGY" && "$have_region" == "$want_region" ]]; then
            echo "[SKIP] $run_id  (found $marker, region=$have_region)"
            SKIPPED=$((SKIPPED + 1))
            return 0
        fi
        echo "[CONFLICT] $out_dir  recorded=($have_strategy,$have_region) want=($REGION_STRATEGY,$want_region)"
        if $FORCE_RELAUNCH_UNIFIED; then
            local ts
            ts="$(date +%s)"
            mv "$out_dir" "${out_dir}.unified.${ts}"
            echo "  [force-relaunch-unified] renamed -> ${out_dir}.unified.${ts}"
        else
            echo "  hint: re-run with --force-relaunch-unified to rename and re-launch"
            CONFLICTS=$((CONFLICTS + 1))
            return 0
        fi
    fi

    wait_for_slot
    echo "[START] $run_id  (strategy=$REGION_STRATEGY region=$want_region)"
    (
        source "$VENV"
        export AEVOLVE_REPO_DIR="$PROJECT_ROOT"
        export BYPASS_TOOL_CONSENT=true
        cd "$EXPERIMENT_ROOT"
        python run_exp1_unified_insitu.py \
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

START=$(date +%s)
echo "============================================================"
echo "Phase 1 unified IN-SITU: Solver-Evolvability Sweep - seed=$SEED"
echo "  Solvers:    ${SOLVERS[*]}"
echo "  Evolvers:   ${EVOLVERS[*]}"
echo "  Benchmarks: ${BENCHMARKS[*]}"
echo "  Parallel:   $MAX_PARALLEL"
echo "  Repo:       $PROJECT_ROOT"
echo "  Output:     $RESULTS_DIR"
echo "  Region:     $REGION_STRATEGY (explicit=$REGION)"
$FORCE_RELAUNCH_UNIFIED && echo "  Force-relaunch unified: yes"
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
echo "============================================================"
echo "Launched: $TOTAL   Skipped: $SKIPPED   Conflicts: $CONFLICTS   Failed: $FAILED"
echo "Elapsed:  ${ELAPSED}s"
echo "Output:   $RESULTS_DIR"
echo "============================================================"

if (( CONFLICTS > 0 || FAILED > 0 )); then
    exit 1
fi
