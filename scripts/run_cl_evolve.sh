#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

# V3: multi-turn evolve (solve → judge → evolve+merge → re-solve, 3 rounds)
# conda run -n mem --no-capture-output python examples/evolve_cl_bench.py \
#     --grouped-path /fsx/tianxin/CL-bench/CL-bench-grouped.jsonl \
#     --raw-path /fsx/tianxin/CL-bench/CL-bench.jsonl \
#     --max-samples 50 \
#     --max-evolve-turns 3 \
#     --batch-size 8 \
#     --batch-workers 8 \
#     --output-dir outputs/cl_bench_evolve_v3

# V4: no-retest (solve → judge → evolve+merge → next batch, no re-solve)
# conda run -n mem --no-capture-output python examples/evolve_cl_bench.py \
#     --grouped-path /fsx/tianxin/CL-bench/CL-bench-grouped.jsonl \
#     --raw-path /fsx/tianxin/CL-bench/CL-bench.jsonl \
#     --max-samples 500 \
#     --max-evolve-turns 1 \
#     --no-retest \
#     --batch-size 16 \
#     --batch-workers 16 \
#     --output-dir outputs/cl_bench_evolve_v4_full

# V5: unified evolve + hierarchical skill tree (no parallel evolve, no merge)
# conda run -n mem --no-capture-output python examples/evolve_cl_bench.py \
#     --grouped-path /fsx/tianxin/CL-bench/CL-bench-grouped.jsonl \
#     --raw-path /fsx/tianxin/CL-bench/CL-bench.jsonl \
#     --max-samples 500 \
#     --max-evolve-turns 1 \
#     --no-retest \
#     --selector-model 3 \
#     --batch-size 16 \
#     --batch-workers 16 \
#     --output-dir outputs/cl_bench_evolve_v5_full

# V6: Opus 4.6 solver + evolver
# conda run -n mem --no-capture-output python examples/evolve_cl_bench.py \
#     --grouped-path /fsx/tianxin/CL-bench/CL-bench-grouped.jsonl \
#     --raw-path /fsx/tianxin/CL-bench/CL-bench.jsonl \
#     --max-samples 500 \
#     --max-evolve-turns 1 \
#     --no-retest \
#     --solver-model 1 \
#     --evolver-model 1 \
#     --selector-model 3 \
#     --batch-size 16 \
#     --batch-workers 16 \
#     --output-dir outputs/cl_bench_evolve_v6_opus46

# V6-opus45: Opus 4.5 solver + evolver
# conda run -n mem --no-capture-output python examples/evolve_cl_bench.py \
#     --grouped-path /fsx/tianxin/CL-bench/CL-bench-grouped.jsonl \
#     --raw-path /fsx/tianxin/CL-bench/CL-bench.jsonl \
#     --max-samples 500 \
#     --max-evolve-turns 1 \
#     --no-retest \
#     --solver-model 3 \
#     --evolver-model 3 \
#     --selector-model 3 \
#     --batch-size 16 \
#     --batch-workers 16 \
#     --output-dir outputs/cl_bench_evolve_v6_opus45

# V7: short tips + context-level batching (Opus 4.6)
# conda run -n mem --no-capture-output python examples/evolve_cl_bench.py \
#     --grouped-path /fsx/tianxin/CL-bench/CL-bench-grouped.jsonl \
#     --raw-path /fsx/tianxin/CL-bench/CL-bench.jsonl \
#     --max-samples 100 \
#     --max-evolve-turns 1 \
#     --no-retest \
#     --solver-model 1 \
#     --evolver-model 1 \
#     --selector-model 3 \
#     --batch-size 16 \
#     --batch-workers 16 \
#     --output-dir outputs/cl_bench_evolve_v7

# V7-opus45: short tips + context-level batching (Opus 4.5)
# conda run -n mem --no-capture-output python examples/evolve_cl_bench.py \
#     --grouped-path /fsx/tianxin/CL-bench/CL-bench-grouped.jsonl \
#     --raw-path /fsx/tianxin/CL-bench/CL-bench.jsonl \
#     --max-samples 100 \
#     --max-evolve-turns 1 \
#     --no-retest \
#     --solver-model 3 \
#     --evolver-model 3 \
#     --selector-model 3 \
#     --batch-size 16 \
#     --batch-workers 8 \
#     --output-dir outputs/cl_bench_evolve_v7_opus45

# V8: interleaved contexts + feedback-driven detailed skills (Opus 4.6)
# conda run -n mem --no-capture-output python examples/evolve_cl_bench.py \
#     --grouped-path /fsx/tianxin/CL-bench/CL-bench-grouped.jsonl \
#     --raw-path /fsx/tianxin/CL-bench/CL-bench.jsonl \
#     --max-samples 100 \
#     --max-evolve-turns 1 \
#     --no-retest \
#     --solver-model 1 \
#     --evolver-model 2 \
#     --selector-model 2 \
#     --batch-size 16 \
#     --batch-workers 16 \
#     --output-dir outputs/cl_bench_evolve_v8

# V9: context-only skills, no general skills (Opus 4.6 solver, Sonnet evolver)
# conda run -n mem --no-capture-output python examples/evolve_cl_bench.py \
#     --grouped-path /fsx/tianxin/CL-bench/CL-bench-grouped.jsonl \
#     --raw-path /fsx/tianxin/CL-bench/CL-bench.jsonl \
#     --max-samples 100 \
#     --max-evolve-turns 1 \
#     --no-retest \
#     --no-general-skills \
#     --solver-model 1 \
#     --evolver-model 2 \
#     --batch-size 16 \
#     --batch-workers 16 \
#     --output-dir outputs/cl_bench_evolve_v9

# V10: GuidedSynthesis — solver proposes skills + curator reviews (Opus 4.6 solver, Sonnet proposal+curator)
# conda run -n mem --no-capture-output python examples/evolve_cl_bench.py \
#     --grouped-path /fsx/tianxin/CL-bench/CL-bench-grouped.jsonl \
#     --raw-path /fsx/tianxin/CL-bench/CL-bench.jsonl \
#     --max-samples 100 \
#     --max-evolve-turns 1 \
#     --no-retest \
#     --no-general-skills \
#     --solver-model 1 \
#     --proposal-model 1 \
#     --curator-model 2 \
#     --max-skills-per-context 5 \
#     --batch-size 16 \
#     --batch-workers 16 \
#     --output-dir outputs/cl_bench_evolve_v10

# V11: in-context proposal + full body injection (default)
# conda run -n mem --no-capture-output python examples/evolve_cl_bench.py \
#     --grouped-path /fsx/tianxin/CL-bench/CL-bench-grouped.jsonl \
#     --raw-path /fsx/tianxin/CL-bench/CL-bench.jsonl \
#     --max-samples 500 \
#     --max-evolve-turns 1 \
#     --no-retest \
#     --no-general-skills \
#     --solver-model 1 \
#     --curator-model 2 \
#     --max-skills-per-context 5 \
#     --batch-size 16 \
#     --batch-workers 16 \
#     --output-dir outputs/cl_bench_evolve_v11_full

# conda run -n mem --no-capture-output python examples/evolve_cl_bench.py \
#     --grouped-path /fsx/tianxin/CL-bench/CL-bench-grouped.jsonl \
#     --raw-path /fsx/tianxin/CL-bench/CL-bench.jsonl \
#     --max-samples 500 \
#     --max-evolve-turns 1 \
#     --no-retest \
#     --no-general-skills \
#     --solver-model 3 \
#     --curator-model 2 \
#     --max-skills-per-context 5 \
#     --batch-size 16 \
#     --batch-workers 16 \
#     --output-dir outputs/cl_bench_evolve_v11_opus45_full

# V11-lazy: in-context proposal + lazy loading (read_skill tool)
# conda run -n mem --no-capture-output python examples/evolve_cl_bench.py \
#     --grouped-path /fsx/tianxin/CL-bench/CL-bench-grouped.jsonl \
#     --raw-path /fsx/tianxin/CL-bench/CL-bench.jsonl \
#     --max-samples 500 \
#     --max-evolve-turns 1 \
#     --no-retest \
#     --no-general-skills \
#     --lazy-loading \
#     --solver-model 3 \
#     --curator-model 2 \
#     --max-skills-per-context 5 \
#     --batch-size 16 \
#     --batch-workers 16 \
#     --output-dir outputs/cl_bench_evolve_v11_opus45_lazy_full

# V12: random sampling + feedback analysis+propose merged + general skills + full body injection
# conda run -n mem --no-capture-output python examples/evolve_cl_bench.py \
#     --grouped-path /fsx/tianxin/CL-bench/CL-bench-grouped.jsonl \
#     --raw-path /fsx/tianxin/CL-bench/CL-bench.jsonl \
#     --max-samples 500 \
#     --max-evolve-turns 1 \
#     --no-retest \
#     --solver-model 3 \
#     --curator-model 2 \
#     --max-skills-per-context 5 \
#     --max-general-skills 0 \
#     --batch-size 16 \
#     --batch-workers 16 \
#     --output-dir outputs/cl_bench_evolve_v12_opus45_full_noglobal

# V13: V11 propose (no analyze) + general skills (name+desc only) + full body context skills
# conda run -n mem --no-capture-output python examples/evolve_cl_bench.py \
#     --grouped-path /fsx/tianxin/CL-bench/CL-bench-grouped.jsonl \
#     --raw-path /fsx/tianxin/CL-bench/CL-bench.jsonl \
#     --max-samples 500 \
#     --max-evolve-turns 1 \
#     --no-retest \
#     --solver-model 1 \
#     --curator-model 1 \
#     --max-skills-per-context 5 \
#     --max-general-skills 5 \
#     --batch-size 16 \
#     --batch-workers 16 \
#     --output-dir outputs/cl_bench_evolve_v13_opus46_full_curator_opus46

# V13 + minimal feedback (score only)
# conda run -n mem --no-capture-output python examples/evolve_cl_bench.py \
#     --grouped-path /fsx/tianxin/CL-bench/CL-bench-grouped.jsonl \
#     --raw-path /fsx/tianxin/CL-bench/CL-bench.jsonl \
#     --max-samples 500 \
#     --max-evolve-turns 1 \
#     --no-retest \
#     --solver-model 1 \
#     --curator-model 1 \
#     --max-skills-per-context 5 \
#     --max-general-skills 5 \
#     --feedback-level 1 \
#     --batch-size 16 \
#     --batch-workers 16 \
#     --output-dir outputs/cl_bench_evolve_v13_opus46_minimal_feedback

# V13 + standard feedback (score + rephrased)
# conda run -n mem --no-capture-output python examples/evolve_cl_bench.py \
#     --grouped-path /fsx/tianxin/CL-bench/CL-bench-grouped.jsonl \
#     --raw-path /fsx/tianxin/CL-bench/CL-bench.jsonl \
#     --max-samples 500 \
#     --max-evolve-turns 1 \
#     --no-retest \
#     --solver-model 1 \
#     --curator-model 1 \
#     --max-skills-per-context 5 \
#     --max-general-skills 5 \
#     --feedback-level 2 \
#     --batch-size 16 \
#     --batch-workers 16 \
#     --output-dir outputs/cl_bench_evolve_v13_opus46_standard_feedback

# V14: with skill gating + standard feedback
conda run -n mem --no-capture-output python examples/evolve_cl_bench.py \
    --grouped-path /fsx/tianxin/CL-bench/CL-bench-grouped.jsonl \
    --raw-path /fsx/tianxin/CL-bench/CL-bench.jsonl \
    --max-samples 500 \
    --max-evolve-turns 1 \
    --no-retest \
    --solver-model 1 \
    --curator-model 1 \
    --max-skills-per-context 5 \
    --max-general-skills 5 \
    --feedback-level 2 \
    --batch-size 16 \
    --batch-workers 16 \
    --output-dir outputs/cl_bench_v14_standard_feedback_all
