# PolyBench example — Adaptive Auto-Harness

End-to-end runnable example of the navigation + adaptation methodology on
**PolyBench** (Polymarket prediction-market streams). PolyBench is pure
reasoning — no Docker required.

Everything here is self-contained: the runner, launcher, configs, seed harness,
and evolver prompts. It drives the library code in
`agent_evolve/{benchmarks,agents}/polybench/`.

## Run

```bash
# From the repo root. Configure credentials + models (the launcher reads env vars):
cp .env.template .env        # then edit: SOLVER_MODEL, EVOLVER_MODEL, AWS_*
set -a; source .env; set +a

# Get the dataset (SQLite snapshot of Polymarket markets):
python examples/polybench/download_data.py --benchmark polybench   # -> data/polymarket_analysis.db

# Smoke test: no-evolution baseline on 5 markets
bash examples/polybench/poly_hypothesis.sh --limit 5 H0
```

The launcher `cd`s into this directory and adds the repo root to `PYTHONPATH`,
so `import agent_evolve` resolves and `configs/`, `seed/`, and the runner are
found by local paths. Pass a custom DB as the first arg:
`bash examples/polybench/poly_hypothesis.sh /path/to/markets.db --limit 5 H0`.

## Hypothesis cells

```bash
bash examples/polybench/poly_hypothesis.sh H0            # baseline: no evolution
bash examples/polybench/poly_hypothesis.sh H1            # full evolution
bash examples/polybench/poly_hypothesis.sh H4_multi      # multi-agent structured evolution
bash examples/polybench/poly_hypothesis.sh H4_multi_nav  # + tree routing
```

Add `--adaptation {whole_store|tree_routing|retrieval|agentic_filter}` to pick
the solve-time adaptation operator (default: `tree_routing` when routing is
enabled, else `whole_store`). Results land in `results/polybench_<cell>/`,
logs in `logs/` (both under this directory, git-ignored).

## Layout

```
examples/polybench/
├── solve_all_with_evolution.py   # the run loop (solve → evolve → route)
├── poly_hypothesis.sh            # launcher (H0–H4 cells)
├── download_data.py             # dataset fetch helper
├── configs/                     # baseline / full_evo / navigation / structured_* yaml
├── seed/                        # initial harness H_0 (prompt + skills + memory + tools)
├── evolver_prompt*.md           # single-agent evolver prompts
└── evolver_prompts*/            # multi-agent role prompts (analyst/research/builder/verifier)
```
