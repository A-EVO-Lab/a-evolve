# EVO-HARNESS: Context-to-Harness Skill Compilation for Self-Evolving Agents

Official implementation of **“EVO-HARNESS: Context-to-Harness Skill Compilation for Self-Evolving Agents.”**

EVO-HARNESS studies online harness learning: a frozen solver processes a task stream while an external skill harness is updated from execution context and grounded feedback. The release contains the five benchmark pipelines used in the paper and their exact latest experiment configurations.

## Results

Main results with Claude Opus 4.6 as the solver (success rate, %):

| Method | CL-Bench | Terminal-Bench 2 | SWE-bench Lite | τ-bench | WebArena-Infinity |
|---|---:|---:|---:|---:|---:|
| No Evolve | 29.54 | 62.92 | 63.67 | 72.73 | 72.50 |
| XSkill | 31.44 | 66.29 | 64.67 | 73.94 | 73.75 |
| **EVO-HARNESS** | **34.02** | **73.03** | **67.00** | **76.97** | **76.25** |

## Method

For each task batch, EVO-HARNESS:

1. Selects relevant general and task-type skills from the current harness.
2. Injects the selected guidance into the frozen solver.
3. Solves and evaluates tasks with benchmark-grounded feedback.
4. Reflects on failures to propose reusable candidate skills.
5. Curates candidates through accept, merge, revise, and skip operations.
6. Writes the updated Markdown skill harness for the next batch.

The model parameters remain frozen. Learning occurs entirely through inspectable `SKILL.md` files.

## Repository Structure

```text
.
├── evo_harness/                  # Five paper benchmark pipelines
│   ├── cl_bench.py
│   ├── swe_bench.py
│   ├── tau_bench.py
│   ├── terminal_bench.py
│   └── webarena_infinity.py
├── agent_evolve/                 # Shared runtime support used by the pipelines
│   ├── agents/{swe,terminal}/
│   ├── benchmarks/cl_bench.py
│   ├── contract/workspace.py
│   ├── engine/observer.py
│   ├── protocol/base_agent.py
│   └── types.py
├── scripts/                      # One launcher per paper benchmark
│   ├── run_cl_evolve.sh
│   ├── run_swe_evolve.sh
│   ├── run_tau_bench_evolve.sh
│   ├── run_terminal_evolve.sh
│   └── run_webarena_evolve.sh
├── seed_skills/                  # SWE, Terminal, and WebArena seed skills
├── tools/check_release.py
└── pyproject.toml
```

Generated outputs, historical experiment variants, unrelated benchmark adapters, and machine artifacts are excluded.

## Installation

Python 3.11+ is required. Docker is required for SWE-bench Lite and Terminal-Bench 2.

```bash
git clone -b release/evo-harness https://github.com/A-EVO-Lab/a-evolve.git
cd a-evolve

conda create -n mem python=3.11 -y
conda activate mem
pip install -e ".[all,dev]"

cp .env.example .env
```

Configure AWS credentials for Bedrock in `us-west-2` or adjust `--region`.

Install the two external benchmark repositories separately:

```bash
git clone https://github.com/sierra-research/tau-bench.git /path/to/tau-bench
pip install -e /path/to/tau-bench

git clone https://github.com/web-arena-x/webarena-infinity.git /path/to/webarena-infinity
playwright install chromium
```

## Benchmarks

| Benchmark | Tasks | Domain | Evaluation |
|---|---:|---|---|
| CL-Bench | 1,899 | Adaptive reasoning | Rubric judge |
| Terminal-Bench 2 | 89 | CLI and scripting | Docker verifier |
| SWE-bench Lite | 300 | Software engineering | Unit tests |
| τ-bench | 165 | Tool use | State verifier |
| WebArena-Infinity | 80 | Web navigation | State verifier |

Model shortcuts used by the launchers:

| Shortcut | Model |
|---|---|
| `1` | Claude Opus 4.6 |
| `2` | Claude Sonnet 4.5 |
| `3` | Claude Opus 4.5 |

## Reproducing the Paper Runs

Each shell script contains only the latest active configuration from the original experiment script. Run from any directory:

```bash
bash scripts/run_cl_evolve.sh
bash scripts/run_swe_evolve.sh
bash scripts/run_terminal_evolve.sh
bash scripts/run_tau_bench_evolve.sh
bash scripts/run_webarena_evolve.sh
```

The scripts use the original Conda environment name `mem`. Override it without editing the commands:

```bash
CONDA_ENV=my-env bash scripts/run_swe_evolve.sh
```

CL-Bench and WebArena retain the original dataset defaults while supporting portable overrides:

```bash
CL_BENCH_DIR=/path/to/CL-bench bash scripts/run_cl_evolve.sh
WEBARENA_DIR=/path/to/webarena-infinity bash scripts/run_webarena_evolve.sh
```

The five Python entry points are also installed as console commands:

```text
evo-harness-cl
evo-harness-swe
evo-harness-tau
evo-harness-terminal
evo-harness-webarena
```

Pass `--help` to inspect all benchmark-specific options.

## Output Layout

```text
output-dir/
├── workspace/
│   └── skills/
│       ├── general/
│       └── topic/
├── logs/
├── results/
├── summary.json
└── all_results.jsonl
```

Exact files differ slightly by benchmark, but every pipeline preserves the evolved workspace and per-task evaluation records.

## Validation

Run the dependency-free release checks with:

```bash
make test
```

The check validates Python syntax, seed JSON/JSONL files, packaging metadata, the five entry points, and exactly five shell launchers.

The benchmark algorithms, prompts, model settings, task ordering, and evaluation logic are preserved from the original `evo_skill/evo-harness-code` snapshot. Only unreachable historical helpers and components outside the five-paper-benchmark release scope were removed. Hosted model revisions and external benchmark environments may still introduce run-to-run variation.

## Citation

```bibtex
@article{wei2026evoharness,
  title={EVO-HARNESS: Context-to-Harness Skill Compilation for Self-Evolving Agents},
  author={Wei, Tianxin and Shi, Zhan and Lin, Minhua and He, Bing and Liu, Zewen and Sang, Yisi and Bei, Yuanchen and Ning, Xuying and Zou, Jiaru and Li, Ting-Wei and Lin, Xiao and Zhao, Yanjun and Wang, Chi and Dumoulin, Benoit and Wang, Dakuo and He, Jingrui and Lu, Hanqing},
  year={2026}
}
```

## License

MIT. See [LICENSE](LICENSE).
