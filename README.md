# EVO-HARNESS: Context-to-Harness Skill Compilation for Self-Evolving Agents

Official implementation of **“EVO-HARNESS: Context-to-Harness Skill Compilation for Self-Evolving Agents.”**

EVO-HARNESS studies online harness learning: a frozen solver processes a stream of tasks while an external skill harness is incrementally updated from execution context and grounded feedback. The implementation supports the five benchmarks used in the paper: CL-Bench, Terminal-Bench 2, SWE-bench Lite, τ-bench, and WebArena-Infinity.

## Paper Results

Main results with Claude Opus 4.6 as the solver (success rate, %):

| Method | CL-Bench | Terminal-Bench 2 | SWE-bench Lite | τ-bench | WebArena-Infinity |
|---|---:|---:|---:|---:|---:|
| No Evolve | 29.54 | 62.92 | 63.67 | 72.73 | 72.50 |
| XSkill | 31.44 | 66.29 | 64.67 | 73.94 | 73.75 |
| **EVO-HARNESS** | **34.02** | **73.03** | **67.00** | **76.97** | **76.25** |

## Method

For every task batch, EVO-HARNESS:

1. Selects relevant general and task-type skills from the current harness.
2. Injects the selected skills into the frozen solver’s context.
3. Solves and evaluates each task with benchmark-grounded feedback.
4. Reflects on failed executions to propose reusable candidate skills.
5. Uses an evolver to accept, merge, revise, or skip candidates.
6. Writes the updated Markdown skill harness for the next batch.

The solver and model parameters remain frozen. Learning occurs through inspectable `SKILL.md` files in the external workspace.

## Project Structure

```text
.
├── agent_evolve/                  # Core workspace, agent, LLM, and benchmark utilities
│   ├── agents/{swe,terminal}/     # ReAct solvers and Docker environments
│   ├── algorithms/aevolve/        # A-EVOLVE framework components
│   ├── benchmarks/                # Benchmark adapters and evaluation utilities
│   ├── contract/                  # File-system harness contract
│   ├── engine/                    # Observation and evolution infrastructure
│   └── llm/                       # Anthropic, Bedrock, and OpenAI providers
├── examples/
│   ├── evolve_cl_bench.py
│   ├── evolve_swe.py
│   ├── evolve_tau_bench.py
│   ├── evolve_terminal.py
│   └── evolve_webarena_infinity.py
├── scripts/                       # Main runs, model ablations, and transfer experiments
├── seed_workspaces/               # Initial benchmark-specific harnesses
├── .env.example
├── pyproject.toml
└── Makefile
```

Generated benchmark outputs are intentionally excluded from the release. Every run writes its own results and evolved workspace under the selected `--output-dir`.

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

Configure AWS credentials using your normal AWS profile or environment variables. The paper runs primarily use AWS Bedrock in `us-west-2`.

Some benchmark packages must be installed separately:

```bash
# τ-bench
git clone https://github.com/sierra-research/tau-bench.git /path/to/tau-bench
pip install -e /path/to/tau-bench

# WebArena-Infinity
git clone https://github.com/web-arena-x/webarena-infinity.git /path/to/webarena-infinity

# Browser runtime
playwright install chromium
```

## Datasets and Evaluation

| Benchmark | Paper tasks | Domain | Evaluation signal |
|---|---:|---|---|
| CL-Bench | 1,899 | Adaptive reasoning | Rubric judge |
| Terminal-Bench 2 | 89 | CLI and scripting | Docker verifier |
| SWE-bench Lite | 300 | Software engineering | Unit tests |
| τ-bench | 165 | Tool use | State verifier |
| WebArena-Infinity | 80 | Web navigation | State verifier |

- **CL-Bench:** provide `CL-bench-grouped.jsonl` and `CL-bench.jsonl` through `--grouped-path` and `--raw-path`.
- **Terminal-Bench 2:** challenge definitions are downloaded on first use or supplied with `--challenges-dir`; benchmark Docker images are pulled on demand.
- **SWE-bench Lite:** `princeton-nlp/SWE-bench_Lite` is downloaded from Hugging Face; SWE-bench Docker images are pulled on demand.
- **τ-bench:** install the official repository as shown above. Airline and retail data are provided by that package.
- **WebArena-Infinity:** point `--webarena-dir` to an official checkout and ensure its application services can be launched.

## Running EVO-HARNESS

All commands below are run from the repository root. Model shortcuts used by the entry points are:

| Shortcut | Model |
|---|---|
| `1` | Claude Opus 4.6 |
| `2` | Claude Sonnet 4.5 |
| `3` | Claude Opus 4.5 |

### SWE-bench Lite

```bash
python examples/evolve_swe.py \
  --solver-model 1 --curator-model 1 --selector-model 2 \
  --batch-size 16 --workers 16 \
  --max-skills-per-topic 5 --max-general-skills 5 \
  --shuffle --shuffle-seed 42 --no-seed-skills \
  --eval-timeout 300 --feedback-level standard \
  --output-dir outputs/swe_evo_harness
```

### Terminal-Bench 2

```bash
python examples/evolve_terminal.py \
  --solver-model 1 --curator-model 1 --selector-model 2 \
  --batch-size 10 --workers 10 \
  --shuffle --shuffle-seed 42 --no-seed-skills \
  --max-general-skills 5 --max-skills-per-topic 0 \
  --feedback-level minimal \
  --output-dir outputs/terminal_evo_harness
```

### τ-bench

```bash
python examples/evolve_tau_bench.py \
  --env both --task-split test \
  --solver-model 1 --user-model 2 \
  --curator-model 1 --selector-model 2 \
  --batch-size 10 --workers 10 \
  --max-skills-per-topic 0 --max-general-skills 5 \
  --feedback-level standard --shuffle --shuffle-seed 42 \
  --output-dir outputs/tau_bench_evo_harness
```

### CL-Bench

```bash
python examples/evolve_cl_bench.py \
  --grouped-path /path/to/CL-bench-grouped.jsonl \
  --raw-path /path/to/CL-bench.jsonl \
  --max-samples 500 --max-evolve-turns 1 --no-retest \
  --solver-model 1 --curator-model 1 --selector-model 2 \
  --max-skills-per-context 5 --max-general-skills 5 \
  --feedback-level 2 --batch-size 16 --batch-workers 16 \
  --output-dir outputs/cl_bench_evo_harness
```

`--max-samples` limits grouped contexts, not the flattened task count; the paper’s 500-context configuration contains the full 1,899-task stream.

### WebArena-Infinity

```bash
python examples/evolve_webarena_infinity.py \
  --webarena-dir /path/to/webarena-infinity \
  --web-app superhuman-general --difficulty hard \
  --solver-model 1 --curator-model 1 --selector-model 2 \
  --batch-size 8 --workers 8 --max-steps 50 --timeout 2400 \
  --max-skills-per-topic 5 --max-general-skills 5 \
  --no-seed-skills --shuffle --shuffle-seed 42 \
  --feedback-level standard --evolve-all \
  --output-dir outputs/webarena_evo_harness
```

### Baselines and Ablations

Use `--no-evolve --no-seed-skills` for a no-harness baseline. The original launch configurations used for model, feedback, curator, and transfer studies are preserved in `scripts/`:

```bash
bash scripts/run_all.sh
bash scripts/run_swe_cross_transfer.sh
bash scripts/run_swe_curator_ablation.sh
bash scripts/run_all_45.sh
bash scripts/run_all_47.sh
bash scripts/run_all_kimi25.sh
bash scripts/run_all_oss.sh
```

Several scripts retain the original `/fsx/tianxin/...` dataset defaults to preserve the exact experiment commands. Edit only the external dataset paths for a different machine; benchmark logic and experiment hyperparameters do not need to change.

## Output Structure

```text
output-dir/
├── workspace/
│   └── skills/
│       ├── general/              # Cross-task guidance
│       └── topic/                # Task-type or domain guidance
├── logs/                         # Solver/evolver conversations and diagnostics
├── results/                      # Per-task results
├── summary.json                  # Aggregate metrics where supported
└── all_results.jsonl             # Cumulative records where supported
```

## Reproducibility

The migrated Python entry points, core package, and seed workspaces are preserved byte-for-byte from the original `evo_skill/evo-harness-code` snapshot. The cleanup removes generated outputs and OS metadata only; it does not change benchmark, solver, evolver, prompt, selection, or evaluation logic.

Use the same model identifiers, provider revisions, datasets, task ordering, random seed (`42`), Docker images, and command-line arguments to reproduce the original setup. Hosted LLM APIs and externally maintained benchmark environments can still introduce run-to-run variation even when the local code is identical.

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
