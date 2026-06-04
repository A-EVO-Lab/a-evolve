#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Exp1 progress dashboard — solver × evolver matrix per benchmark at seed=42.
#
# Route-aware score reading is driven BY THE SIDECAR (AC-4). Each cell's
# `BENCHMARK_REPORT.md` (authored by run_exp1.py) names the done-marker and
# score-kind; `scripts/lib/read_sidecar.sh::cell_score` dispatches on
# score_kind to the matching parser. No score-contract duplication lives
# here — all mapping comes from the sidecar.
#
# For cells that were launched before sidecars existed, a per-route default
# kicks in.
#
# Usage:
#   bash scripts/check_status.sh                     # all 3 benchmarks, seed 42
#                                                      (4-evolver matrix incl. none)
#   bash scripts/check_status.sh swe                 # one benchmark
#   bash scripts/check_status.sh swe 42              # one benchmark, one seed
#   bash scripts/check_status.sh --no-baseline       # hide the 'none' column
#   bash scripts/check_status.sh --include-baseline  # no-op (kept for back-compat)
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
RESULTS_DIR="${RESULTS_DIR:-$REPO_ROOT/results/exp1_v3}"
# shellcheck source=lib/read_sidecar.sh
source "$SCRIPT_DIR/lib/read_sidecar.sh"

SOLVERS=(sonnet46 opus46 haiku45 gptoss120b qwen235b qwen32b minimax kimi)
# Mirrors the phase1 launchers' default EVOLVERS (incl. `none` baseline route).
EVOLVERS=(none opus46 sonnet46 qwen235b)
BENCHMARKS=(swe mcp sb)
SEEDS=(42)

positional=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        # Kept for backward compat — `none` is now in the default EVOLVERS,
        # so this flag is a no-op. Pass --no-baseline to drop it instead.
        --include-baseline) shift ;;
        --no-baseline)
            # Drop "none" from EVOLVERS (it is the first element by convention).
            filtered=()
            for _e in "${EVOLVERS[@]}"; do [[ "$_e" != "none" ]] && filtered+=("$_e"); done
            EVOLVERS=("${filtered[@]}")
            shift ;;
        *) positional+=("$1"); shift ;;
    esac
done
[[ ${#positional[@]} -ge 1 ]] && BENCHMARKS=("${positional[0]}")
[[ ${#positional[@]} -ge 2 ]] && SEEDS=("${positional[1]}")

TOTAL=0
DONE=0
for bm in "${BENCHMARKS[@]}"; do
    for seed in "${SEEDS[@]}"; do
        echo "=== $bm — seed=$seed ==="
        printf "  %-12s " ""
        for e in "${EVOLVERS[@]}"; do printf "%-12s" "$e"; done
        echo ""
        for s in "${SOLVERS[@]}"; do
            printf "  %-12s " "$s"
            for e in "${EVOLVERS[@]}"; do
                TOTAL=$((TOTAL + 1))
                cell="$RESULTS_DIR/${s}_x_${e}_${bm}_s${seed}"
                score="$(cell_score_with_fallback "$cell" "$e" "$bm")"
                if [[ -n "$score" ]]; then
                    DONE=$((DONE + 1))
                    printf "%-12s" "$score"
                else
                    printf "%-12s" "-"
                fi
            done
            echo ""
        done
        echo ""
    done
done
echo "$DONE / $TOTAL cells complete"
