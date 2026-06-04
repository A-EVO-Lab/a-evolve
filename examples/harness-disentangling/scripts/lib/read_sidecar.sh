#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Sidecar reader for EvolverBench Exp1 cells.
#
# Source this file to gain three functions:
#
#   sidecar_done_marker <cell_dir>   → stdout: relative filename of the
#                                      cell's done-marker, or "" if no
#                                      sidecar exists.
#   sidecar_score_kind  <cell_dir>   → stdout: score_kind identifier, or
#                                      "" if no sidecar exists.
#   cell_score          <cell_dir>   → stdout: computed score (float, one
#                                      line), or "" if the done-marker is
#                                      absent.
#
# The sidecar is `BENCHMARK_REPORT.md`, authored by run_exp1.py::
# _write_benchmark_report(). Keep the score_kind → parser mapping in sync
# with the SCORE_KIND_* constants in run_exp1.py.
# ─────────────────────────────────────────────────────────────────────────────

sidecar_done_marker() {
    local cell="$1"
    local report="$cell/BENCHMARK_REPORT.md"
    [[ -f "$report" ]] || { echo ""; return; }
    awk -F': ' '/^done_marker: / {print $2; exit}' "$report"
}

sidecar_score_kind() {
    local cell="$1"
    local report="$cell/BENCHMARK_REPORT.md"
    [[ -f "$report" ]] || { echo ""; return; }
    awk -F': ' '/^score_kind: / {print $2; exit}' "$report"
}

# Region-routing metadata (added by run_exp1.py from plan_v2 onward).
# Empty string for legacy cells produced before the metadata existed —
# callers should treat that as `(single, us-west-2)` for compatibility.
sidecar_region_strategy() {
    local cell="$1"
    local report="$cell/BENCHMARK_REPORT.md"
    [[ -f "$report" ]] || { echo ""; return; }
    awk -F': ' '/^region_strategy: / {print $2; exit}' "$report"
}

sidecar_region() {
    local cell="$1"
    local report="$cell/BENCHMARK_REPORT.md"
    [[ -f "$report" ]] || { echo ""; return; }
    awk -F': ' '/^region: / {print $2; exit}' "$report"
}

# Score-file path for a given (cell, kind, marker). For most kinds the
# score and the done-marker live in the same file. MCP is the exception:
# RUN_COMPLETE.json is the done-marker (so half-streamed cells aren't
# treated as complete), but the score still comes from summary.csv.
_score_path_for_kind() {
    local cell="$1" kind="$2" marker="$3"
    case "$kind" in
        mcp_baseline_summary_csv) echo "$cell/summary.csv" ;;
        *) echo "$cell/$marker" ;;
    esac
}

# Reads a cell's done-marker and score_kind from its sidecar, then computes
# the cell's score. Prints the float (one line) on stdout, or nothing if
# the marker is absent. Delegates the per-kind parser to _parse_by_kind.
cell_score() {
    local cell="$1"
    local marker kind path
    marker="$(sidecar_done_marker "$cell")"
    kind="$(sidecar_score_kind "$cell")"
    [[ -n "$marker" ]] || return 0
    [[ -f "$cell/$marker" ]] || return 0
    path="$(_score_path_for_kind "$cell" "$kind" "$marker")"
    [[ -f "$path" ]] || return 0
    _parse_by_kind "$kind" "$path"
}

# Fallback path for cells that predate the sidecar OR for check_status.sh
# callers that have (evolver, bm) in hand and can infer the contract even
# without a sidecar. Tries the sidecar first; if absent, infers done_marker
# and score_kind from (evolver, bm) using the same per-route defaults
# phase1_single_seed.sh uses, then parses accordingly.
#
# Usage: cell_score_with_fallback <cell_dir> <evolver_short> <benchmark>
cell_score_with_fallback() {
    local cell="$1" evolver="$2" bm="$3"
    local score
    score="$(cell_score "$cell")"
    if [[ -n "$score" ]]; then
        echo "$score"
        return 0
    fi
    # Sidecar-less fallback: infer the contract.
    local marker kind
    if [[ "$evolver" == "none" ]]; then
        case "$bm" in
            swe) marker="results.json";     kind="swe_baseline_results_json"   ;;
            mcp) marker="summary.csv";      kind="mcp_baseline_summary_csv"    ;;
            sb)  marker="summary.txt";      kind="sb_baseline_summary_txt"     ;;
            *)   return 0 ;;
        esac
    else
        marker="results.metrics.json"
        kind="evolve_metrics_json"
    fi
    local path
    path="$(_score_path_for_kind "$cell" "$kind" "$marker")"
    if [[ ! -f "$path" ]]; then
        # Legacy MCP baseline cells used the MetaHarness final-eval contract.
        # Keep them readable, but new cells use adaptive_evolve_baseline.py
        # and summary.csv.
        if [[ "$evolver" == "none" && "$bm" == "mcp" && -f "$cell/final_eval.json" ]]; then
            _parse_by_kind "mcp_baseline_final_eval_json" "$cell/final_eval.json"
            return 0
        fi
        return 0
    fi
    _parse_by_kind "$kind" "$path"
}

# Parser dispatch: given a score_kind and a path to the done-marker file,
# print the cell's score. Internal helper used by cell_score and
# cell_score_with_fallback so the kind→parser mapping lives in one place.
_parse_by_kind() {
    local kind="$1" path="$2"
    case "$kind" in
        evolve_metrics_json)
            python3 -c "
import json,sys
d = json.load(open(sys.argv[1]))
print(f\"{float(d.get('final_score',0)):.4f}\")
" "$path" 2>/dev/null ;;
        pass_ratio_metrics_json)
            # Some evolve metrics use pass_ratio rather than final_score.
            python3 -c "
import json,sys
d = json.load(open(sys.argv[1]))
print(f\"{float(d.get('pass_ratio', d.get('final_score', 0))):.4f}\")
" "$path" 2>/dev/null ;;
        swe_baseline_results_json)
            # solve_all.py writes a bare JSON list of per-task result dicts.
            python3 -c "
import json,sys
d = json.load(open(sys.argv[1]))
if isinstance(d, dict):
    d = d.get('results', [])
if not isinstance(d, list) or not d:
    print('0.0000')
else:
    print(f\"{sum(1 for x in d if x.get('success'))/len(d):.4f}\")
" "$path" 2>/dev/null ;;
        mcp_baseline_summary_csv)
            # adaptive_evolve_baseline.py writes summary.csv with columns:
            # task_id,result,score,elapsed_s,output_len,detail. Use the mean
            # fractional score to match MCP evolve-route final_score.
            python3 -c "
import csv,sys
rows=list(csv.DictReader(open(sys.argv[1])))
scores=[float(r.get('score') or 0.0) for r in rows]
print(f\"{(sum(scores)/len(scores) if scores else 0.0):.4f}\")
" "$path" 2>/dev/null ;;
        mcp_baseline_final_eval_json)
            # avg_score_mean = mean of per-task fractional scores (matches
            # evolve-route `final_score`, which is the mean of Feedback.score
            # per task). Legacy MetaHarness final-eval baseline fallback.
            python3 -c "
import json,sys
d = json.load(open(sys.argv[1]))
print(f\"{float(d.get('avg_score_mean', d.get('pass_rate_mean', 0))):.4f}\")
" "$path" 2>/dev/null ;;
        tb_baseline_results_jsonl)
            python3 -c "
import json,sys
ls=[json.loads(l) for l in open(sys.argv[1]) if l.strip()]
if not ls:
    print('0.0000')
else:
    print(f\"{sum(1 for r in ls if r.get('passed') or r.get('success'))/len(ls):.4f}\")
" "$path" 2>/dev/null ;;
        sb_baseline_summary_txt)
            # run_skillbench_solve_all.sh writes lowercase tasks_total=<N> and pass=<N>.
            awk -F= '
                /^tasks_total=/ { t=$2 }
                /^pass=/        { p=$2 }
                END { if (t+0>0) printf("%.4f", p/t); else print "0.0000" }
            ' "$path" 2>/dev/null ;;
        *)
            # Fallback: unknown score_kind. Report 0.0 but do not fail.
            echo "0.0000"
            ;;
    esac
}

# Guard against running this file directly — it is a library.
if [[ "${BASH_SOURCE[0]}" == "${0:-}" ]]; then
    echo "read_sidecar.sh is a library; source it, don't run it." >&2
    exit 1
fi
