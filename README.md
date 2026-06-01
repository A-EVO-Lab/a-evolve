# Adaptive Auto-Harness

Code accompanying the paper **"Adaptive Auto-Harness: Sustained
Self-Improvement for Agentic System Deployment on Open-Ended Task
Streams."**

The framework constructs and adapts an agent's *harness* (prompts,
skills, tools, memories, and supporting infrastructure) for
open-ended task streams. It contributes:

- A stateful multi-agent evolver that decomposes evolution into
  Analyst, parallel Researchers, Builder, and Verifier roles with
  cross-cycle memory and a temporal-reveal feedback gate.
- An adaptation operator over a harness tree, selecting a specialized
  branch per task at solve time.
- Two human-steering hooks for cases when stream history lacks the
  signal needed to build the next harness.
- Three benchmark adapters: PolyBench (prediction markets), CTF-Dojo
  (security challenges), and FutureX (event forecasting).

## Table of Contents

- [Project Structure](#project-structure)
- [Installation](#installation)
- [Configuration](#configuration)
- [Dataset Preparation](#dataset-preparation)
- [Running the PolyBench Demo](#running-the-polybench-demo)
  - [1. Smoke test (a few tasks)](#1-smoke-test-a-few-tasks)
  - [2. Baseline vs. evolution](#2-baseline-vs-evolution)
  - [3. Direct invocation](#3-direct-invocation)
  - [Outputs](#outputs)
- [Other Benchmarks](#other-benchmarks)
- [Security and Privacy](#security-and-privacy)
- [License](#license)

## Project Structure

```
.
├── agent_evolve/                       # Core library
│   ├── algorithms/                     # Evolution engine (aevolve) + navigation + adaptation
│   ├── agents/{polybench,ctf_dojo,futurex}/  # Task-solving agents for the three benchmarks
│   ├── benchmarks/{polybench,ctf_dojo,futurex}/  # Benchmark adapters (task loading + scoring)
│   ├── engine/                         # Evolution loop, versioning, human interface, observer
│   ├── protocol/                       # Adaptation operators, retrieval, base agent
│   ├── contract/                       # Harness (workspace) contract: protect, infra_dir
│   ├── llm/                            # Bedrock / OpenAI / Anthropic LLM backends
│   └── tools/, utils/
│
├── experiments/
│   └── polybench/
│       ├── configs/                    # baseline / full_evo / navigation / structured_* configs
│       ├── evolver_prompt*.md          # Evolver system prompts (single + navigation)
│       ├── evolver_prompts*/           # Multi-agent role prompts (analyst/research/builder/verifier)
│       └── seed/                       # Initial harness (H_0): manifest + prompts + skills
│
├── seed_workspaces/                    # Initial harnesses per benchmark
├── scripts/
│   ├── poly_hypothesis.sh              # PolyBench experiment launcher (H0–H4)
│   ├── ctf_dojo_hypothesis.sh
│   └── futurex_hypothesis.sh
├── data/
│   ├── README.md                       # Dataset sources + layout
│   └── download_data.py                # Dataset fetch helper
├── solve_all_with_evolution.py         # Main entry point (all benchmarks)
├── pyproject.toml
├── .env.template
└── INSTALL.md
```

## Installation

Requires **Python 3.11+** and (for PolyBench) access to **AWS Bedrock**.

```bash
git clone -b release/adaptive-auto-harness \
  https://github.com/A-EVO-Lab/a-evolve.git
cd a-evolve

# Create an environment
conda create -n aevolve python=3.11 -y
conda activate aevolve

# Editable install with all providers + benchmark backends
pip install -e ".[all]"
```

`[all]` pulls every supported LLM provider and benchmark backend
(PolyBench needs `boto3` + `strands-agents`, both included). To install
a minimal subset, replace `[all]` with one or more of: `[bedrock]`,
`[openai]`, `[litellm]`, `[swe]`, `[mcp]`, `[skillbench]`, `[gepa]`.
Add `[dev]` for the test tools (`pytest`, `ruff`).

Verify the import:

```bash
python -c "import agent_evolve; print(agent_evolve.__file__)"
```

See [`INSTALL.md`](INSTALL.md) for the full provider matrix and
optional FutureX retrieval keys.

## Configuration

Copy the template and fill in the values for the provider you use:

```bash
cp .env.template .env
# then edit .env
```

For the PolyBench path, the LLM client and the launcher scripts read
**process environment variables** — they do *not* auto-load `.env`.
Export the values into your shell before running (this sources every
key from `.env` at once):

```bash
set -a; source .env; set +a
```

Minimum required for the PolyBench demo:

| Variable                | Purpose                                              |
|-------------------------|------------------------------------------------------|
| `SOLVER_MODEL`          | Bedrock model ID for the task-solving agent          |
| `EVOLVER_MODEL`         | Bedrock model ID for the evolver agents              |
| `AWS_ACCESS_KEY_ID`     | AWS credentials (or use an EC2 instance IAM role)    |
| `AWS_SECRET_ACCESS_KEY` | "                                                    |
| `AWS_REGION`            | Bedrock region (default `us-west-2`)                 |

`SOLVER_MODEL` / `EVOLVER_MODEL` are Bedrock model IDs, e.g.
`us.anthropic.claude-sonnet-4-6` (solver) and
`global.anthropic.claude-opus-4-6-v1` (evolver). The placeholders
`<solver-model-id>` / `<evolver-model-id>` in the configs resolve from
these env vars at runtime.

## Dataset Preparation

PolyBench uses a SQLite snapshot of Polymarket markets. The dataset is
**not** bundled with this repository. Place it at
`data/polymarket_analysis.db`:

```bash
python data/download_data.py --benchmark polybench
```

This prints the source (Polymarket public API) and the target path; see
[`data/README.md`](data/README.md) for how the snapshot is built. The
launcher exits early with a clear message if the DB is missing.

## Running the PolyBench Demo

PolyBench is pure reasoning (no Docker required). All commands run from
the repository root, after `pip install -e ".[all]"`,
`set -a; source .env; set +a`, and placing the DB.

### 1. Smoke test (a few tasks)

Run the no-evolution baseline on just 5 markets to confirm the pipeline,
credentials, and dataset are wired correctly:

```bash
bash scripts/poly_hypothesis.sh --limit 5 H0
```

You should see a line like `Database: data/polymarket_analysis.db (N
resolved markets)` followed by per-task progress; results land under
`results/polybench_baseline/` and a log under `logs/`.

### 2. Baseline vs. evolution

The launcher defines the paper's hypothesis cells. Run one at a time, or
omit the target to run all:

```bash
bash scripts/poly_hypothesis.sh H0          # baseline: no evolution (control)
bash scripts/poly_hypothesis.sh H1          # full evolution: prompts + skills + memory + tools
bash scripts/poly_hypothesis.sh H4_multi    # multi-agent structured evolution (analyze→research→build→verify)
bash scripts/poly_hypothesis.sh H4_multi_nav  # structured evolution + navigation (git branching)
```

Useful flags (see the header of `scripts/poly_hypothesis.sh`):

| Flag                  | Effect                                              |
|-----------------------|-----------------------------------------------------|
| `--limit N`           | Cap the number of markets (great for demos)         |
| `--batch-size N`      | Tasks per evolution cycle (default 100)             |
| `--solver-temp T`     | Solver LLM temperature (default 0)                  |
| `--evolver-temp T`    | Evolver LLM temperature (default 0)                 |
| `--suffix S`          | Suffix for result/log folder names (e.g. `_run2`)   |
| `--model-id <id>`     | Override the solver model for this run              |
| `--evolver-model <id>`| Override the evolver model for this run             |

### 3. Direct invocation

To bypass the launcher and call the entry point yourself:

```bash
python solve_all_with_evolution.py \
  --benchmark polybench \
  --dataset data/polymarket_analysis.db \
  --seed-workspace experiments/polybench/seed \
  --config experiments/polybench/configs/baseline.yaml \
  --model-id "$SOLVER_MODEL" \
  --evolver-model "$EVOLVER_MODEL" \
  --temporal-reveal \
  --no-infra-evo \
  --limit 5 \
  --workers 8 \
  --output-dir results/polybench_demo
```

Swap `--config` for `experiments/polybench/configs/full_evo.yaml` to
enable evolution. Run `python solve_all_with_evolution.py --help` for
the complete flag list.

### Outputs

| Path                          | Contents                                         |
|-------------------------------|--------------------------------------------------|
| `results/polybench_<cell>/`   | Per-task trajectories, scores, evolved harnesses |
| `logs/<cell>_polybench_*.log` | Full run log (the launcher tails the last lines) |

`results/` and `logs/` are git-ignored.

## Other Benchmarks

The same entry point drives CTF-Dojo and FutureX via
`scripts/ctf_dojo_hypothesis.sh` and `scripts/futurex_hypothesis.sh`.
CTF-Dojo additionally requires Docker and the CTF archive; FutureX
auto-downloads its splits from HuggingFace. See [`INSTALL.md`](INSTALL.md)
and [`data/README.md`](data/README.md).

## Security and Privacy

- **Never commit credentials.** `.env` is git-ignored; only
  `.env.template` (with empty values) is tracked. Keep real keys in
  `.env` and out of configs, code, and commits.
- Datasets, `results/`, and `logs/` are git-ignored to avoid leaking
  run artifacts or large data files.
- Before pushing, verify nothing sensitive is staged:

  ```bash
  git status
  git diff --cached            # review every staged change
  git grep -nE 'AKIA|sk-ant-|AWS_SECRET|api_key' -- ':!*.template'
  ```

## License

MIT.
