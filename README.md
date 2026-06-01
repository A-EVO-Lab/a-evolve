# Harness Updating Is Not Harness Benefit: Disentangling Evolution Capabilities in Self-Evolving LLM Agents

[![arXiv](https://img.shields.io/badge/arXiv-2605.30621-b31b1b.svg)](https://arxiv.org/abs/2605.30621)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

Official implementation of **"Harness Updating Is Not Harness Benefit: Disentangling Evolution Capabilities in Self-Evolving LLM Agents."**

This repository contains the harness self-evolution engine, the three benchmark adapters, and the experiment and analysis scripts used in the paper.

If you find this work helpful, please cite our paper:

```bibtex
@article{lin2026harness,
  title={Harness Updating Is Not Harness Benefit: Disentangling Evolution Capabilities in Self-Evolving LLM Agents},
  author={Lin, Minhua and Wu, Juncheng and Wang, Zijun and Shi, Zhan and Sang, Yisi and He, Bing and Liu, Zewen and Wei, Tianxin and Wu, Zongyu and Zhang, Zhiwei and Wang, Dakuo and Zhang, Xiang and Dumoulin, Benoit and Xie, Cihang and Zhou, Yuyin and Wang, Suhang and Lu, Hanqing},
  journal={arXiv preprint arXiv:2605.30621},
  year={2026}
}
```

## Table of Contents

- [Overview](#overview)
- [Project Structure](#project-structure)
- [Installation](#installation)
- [Dataset Preparation](#dataset-preparation)
- [Quick Start](#quick-start)
- [Reproducing the Paper Experiments](#reproducing-the-paper-experiments)
  - [Exp0: Evolver-side Analysis (harness-updating)](#exp0-evolver-side-analysis-harness-updating)
  - [Exp1: Agent-side Analysis (harness-benefit)](#exp1-agent-side-analysis-harness-benefit)
  - [Harness-Following Rate (HFR) Diagnostic](#harness-following-rate-hfr-diagnostic)
- [Models](#models)

## Overview

LLM agents are increasingly deployed as systems built around an editable external **harness** (prompts, skills, memories, and tools) that shapes task execution without changing model parameters. *Harness self-evolution* adapts such an agent by updating its harness from execution evidence. Two roles drive this loop:

- an **evolver** that turns execution evidence into harness updates, and
- an **agent** that solves tasks under the (updated) harness.

We analyze two harness self-evolution capabilities, both distinct from a model's **base capability** (its task-solving performance under the initial harness):

1. **Harness-updating** (exercised as the evolver): the capability to produce useful persistent harness updates from execution evidence.
2. **Harness-benefit** (exercised as the agent): the capability to benefit from updated harnesses during task solving.

Pairing seven LLMs as agents and evolvers across three agentic benchmarks (SWE-bench Verified, MCP-Atlas, SkillsBench), all driven by a single evolution engine (`agent_evolve/algorithms/unified`, `UnifiedEngine`), the analysis reveals two findings:

- **Harness-updating is flat in base capability.** Models from different capability tiers produce harness updates that lead to surprisingly similar gains; even Qwen3.5-9B's updates yield gains comparable to those of Claude Opus 4.6.
- **Harness-benefit is non-monotonic in base capability.** Weak-tier models benefit little from updated harnesses, mid-tier models benefit most, and strong-tier models benefit less than mid-tier. We trace the low gains at the weak tier to two failure modes: weak-tier models may fail to activate relevant harness artifacts, or activate them but fail to follow them faithfully (measured by the Harness-Following Rate).

These findings suggest investing capability budget in the task-solving agent rather than the evolver, and targeting harness invocation and long-horizon instruction following in agent training.

## Project Structure

```
.
├── agent_evolve/                  # Core library
│   ├── algorithms/unified/        # UnifiedEngine: readers, operators, verifiers (the evolution engine)
│   ├── agents/{swe,mcp,skillbench}/   # Task-solving agents for the three benchmarks
│   ├── benchmarks/{swe_verified_mini,mcp_atlas,skillbench}/  # Benchmark adapters
│   ├── engine/                    # Evolution loop, history, observer, trial runner
│   ├── llm/                       # Bedrock / OpenAI-compatible LLM backends
│   └── contract/                  # Harness (workspace) contract: manifest, schema
│
├── examples/
│   ├── swe_examples/              # SWE-bench Verified runners (*_unified.py) + baseline (solve_all.py)
│   ├── mcp_examples/              # MCP-Atlas runner (run_adaptive_evolve_all_unified.py) + baseline
│   ├── skillbench_examples/       # SkillsBench runners (skillbench_evolve_*_unified.py)
│   ├── configs/                   # Per-benchmark evolution configs (swe / mcp / skillbench)
│   └── harness-evolution/         # Paper experiment orchestration
│       ├── run_exp0_unified_insitu.py    # Exp0: evolver-side (harness-updating)
│       ├── run_exp1_unified_insitu.py    # Exp1: agent-side (harness-benefit)
│       ├── run_exp1.py                   # Exp1 train/test split variant
│       ├── _region_picker.py + model_region_availability.json  # model nickname -> Bedrock id / region
│       ├── experiment_plans/             # Research questions and methodology
│       ├── scripts/                      # Single-seed sweep launchers + status dashboard
│       └── hfr_analysis/                 # Harness-Following Rate diagnostic pipeline
│
├── seed_workspaces/{swe,mcp,skillbench-upstream-parity}/  # Initial harnesses (H_0)
├── docs/                          # Unified engine + benchmark setup docs
├── tests/                         # Unit tests (unified engine, scaffolding, isolation)
├── pyproject.toml
├── Makefile
└── .env.example
```

## Installation

Requires Python 3.11+.

```bash
git clone -b release/harness-evolution https://github.com/A-EVO-Lab/a-evolve.git
cd a-evolve

# Create environment
conda create -n aevolve python=3.11 -y
conda activate aevolve

# Editable install with the three paper benchmarks + dev tools
pip install -e ".[swe,mcp,skillbench,dev]"
# or, all extras:
pip install -e ".[all,dev]"
```

Optional-dependency extras: `swe` (SWE-bench Verified), `mcp` (MCP-Atlas), `skillbench` (SkillsBench), `all`, `dev` (pytest/ruff). `make install` runs `pip install -e ".[all,dev]"`.

### Environment Variables

All agents call **AWS Bedrock**. Copy the example file and fill in credentials/keys:

```bash
cp .env.example .env
```

| Variable | Description |
|----------|-------------|
| AWS credentials | `aws configure` or `AWS_*` env vars with `bedrock:InvokeModel` permission |
| `BEDROCK_RETRY_MAX_ATTEMPTS` / `BEDROCK_READ_TIMEOUT_SEC` / `BEDROCK_CONNECT_TIMEOUT_SEC` | Bedrock client tuning (defaults 15 / 600 / 30) |
| `EVAL_USE_LITELLM` | MCP-Atlas judge backend: `true` uses LiteLLM (Gemini 2.5 Pro, official default); `false` falls back to Bedrock |
| `MCP_ENV_FILE` | Path to a `.env` with per-server MCP API keys (MCP-Atlas only) |
| `SKILLBENCH_REPO_DIR` | Local SkillsBench clone (skips auto-bootstrap) |

Model nicknames are resolved to Bedrock model IDs and regions via `examples/harness-evolution/model_region_availability.json` and `_region_picker.py`.

## Dataset Preparation

- **SWE-bench Verified** (`swe`): the HuggingFace dataset `MariusHobbhahn/swe-bench-verified-mini` (or `princeton-nlp/SWE-bench_Verified`) downloads on first use. Each task runs in a SWE-bench Docker image pulled on demand, so a running Docker daemon is required. Seed harness: `seed_workspaces/swe/`.
- **MCP-Atlas** (`mcp`): the HuggingFace dataset `ScaleAI/MCP-Atlas` downloads on first use. Tasks run against MCP servers in a container (`--docker-image`, or `--external-container-url` for a pre-running container) and require per-server API keys in a `.env`. Evaluation uses an LLM judge (Gemini 2.5 Pro via LiteLLM by default). Seed harness: `seed_workspaces/mcp/`. See `docs/mcp-atlas-demo.md`.
- **SkillsBench** (`skillbench`): tasks are auto-cloned from `https://github.com/benchflow-ai/skillsbench` (pinned commit `828bb921...`) into `~/.cache/agent-evolve/skillbench/` on first use; set `SKILLBENCH_REPO_DIR` to use a local clone. Seed harness: `seed_workspaces/skillbench-upstream-parity/`. See `docs/skillbench-setup.md`.

## Quick Start

Each runner copies a seed workspace, runs the agent over a task stream, lets the evolver update the harness between batches, and writes `results.jsonl`, `results.metrics.json`, and the evolved `workspace/` to the output directory. Pass `--help` for the full argument list. The task-solving **agent** model is set with `--model-id` / `--solver-model`; the **evolver** model with `--evolver-model-id` / `--evolver-model` (defaults to the agent model).

**SWE-bench Verified (evolve):**

```bash
python examples/swe_examples/evolve_sequential_unified.py \
  --cycles 3 --limit 50 --batch-size 5 --parallel 5 --feedback minimal \
  --model-id us.anthropic.claude-opus-4-6-v1 --region us-west-2 \
  --dataset MariusHobbhahn/swe-bench-verified-mini \
  --seed-workspace seed_workspaces/swe --output-dir logs/swe_evolve
```

No-evolve baseline (base capability):

```bash
python examples/swe_examples/solve_all.py \
  --dataset MariusHobbhahn/swe-bench-verified-mini \
  --model-id us.anthropic.claude-opus-4-6-v1 \
  --workers 5 --limit 50 --output-dir logs/swe_baseline
```

**MCP-Atlas (evolve / baseline):**

```bash
python examples/mcp_examples/run_adaptive_evolve_all_unified.py \
  --cycles 3 --batch-size 30 --limit 500 \
  --solver-model us.anthropic.claude-opus-4-6-v1 \
  --judge-model us.anthropic.claude-sonnet-4-6 --region us-west-2 \
  --docker-image <mcp-atlas-image> --env-file .env \
  --dataset ScaleAI/MCP-Atlas --seed-workspace seed_workspaces/mcp \
  --output-dir logs/mcp_evolve

python examples/mcp_examples/adaptive_evolve_baseline.py \
  --limit 500 --batch-size 30 --workers 5 \
  --solver-model us.anthropic.claude-opus-4-6-v1 \
  --judge-model us.anthropic.claude-sonnet-4-6 \
  --docker-image <mcp-atlas-image> --env-file .env \
  --seed-workspace seed_workspaces/mcp --output-dir logs/mcp_baseline
```

**SkillsBench (train/test split):**

```bash
python examples/skillbench_examples/skillbench_evolve_split_unified.py \
  --evolve-limit 20 --batch-size 1 --max-cycles 1 \
  --train-parallel 1 --test-parallel 5 \
  --model-id us.anthropic.claude-opus-4-6-v1 --region us-west-2 \
  --feedback-level tests \
  --seed-workspace seed_workspaces/skillbench-upstream-parity \
  --run-dir logs/skillbench_split
```

## Reproducing the Paper Experiments

Experiments are orchestrated under `examples/harness-evolution/`. Each *cell* is an `(agent, evolver, benchmark, seed)` tuple; the orchestrators dispatch one cell per invocation and write a `BENCHMARK_REPORT.md` sidecar used for skip/resume. Point them at this repository:

```bash
export AEVOLVE_V3_DIR=$(pwd)
```

Model nicknames: `opus46`, `sonnet46`, `haiku45`, `qwen235b`, `qwen32b`, `qwen35_9b`, `gptoss120b` (see [Models](#models)). `--evolver none` is the no-evolve baseline.

### Exp0: Evolver-side Analysis (harness-updating)

Fix the agent over the anchor set and vary the evolver, isolating each evolver's harness-updating capability.

```bash
# One cell:
python examples/harness-evolution/run_exp0_unified_insitu.py \
  --solver opus46 --evolver qwen35_9b --benchmark mcp --seed 42 \
  --region-strategy hash --output-root results/exp0_unified_insitu

# Full single-seed sweep:
bash examples/harness-evolution/scripts/phase0_unified_insitu_single_seed.sh
```

### Exp1: Agent-side Analysis (harness-benefit)

Fix the evolver over the anchor set and vary the agent, isolating each agent's harness-benefit.

```bash
# One cell (in-situ route, comparable cell-by-cell with Exp0):
python examples/harness-evolution/run_exp1_unified_insitu.py \
  --solver qwen32b --evolver opus46 --benchmark sb --seed 42 \
  --region-strategy hash --output-root results/exp1_unified_insitu

# Full single-seed sweep + progress dashboard:
bash examples/harness-evolution/scripts/phase1_unified_insitu_single_seed.sh
bash examples/harness-evolution/scripts/check_status.sh
```

`run_exp1.py` provides a train/test split route (evolve on a subset, evaluate on a held-out slice) for unbiased harness-benefit estimation.

### Harness-Following Rate (HFR) Diagnostic

`hfr_analysis/pipeline.py` diagnoses the weak-agent failure modes on SkillsBench: given an evolved skill, how faithfully does the agent follow its procedural instructions? It runs in resumable stages: (1) extract a per-skill rubric with an LLM judge, (2) judge per-cell trajectory adherence, and (3) compute mechanical proxies (retrieval-to-use gap, early termination, answer-before-validation).

```bash
python examples/harness-evolution/hfr_analysis/pipeline.py --max-workers 4 --stages 1,2,4
```

## Models

The study pairs seven LLMs (via AWS Bedrock) as agents and evolvers. The anchor agent set and anchor evolver set are both `{Claude Opus 4.6, Claude Sonnet 4.6, Qwen3-235B}`.

| Nickname | Model | Example Bedrock model ID |
|----------|-------|--------------------------|
| `opus46` | Claude Opus 4.6 | `us.anthropic.claude-opus-4-6-v1` |
| `sonnet46` | Claude Sonnet 4.6 | `us.anthropic.claude-sonnet-4-6-v1` |
| `haiku45` | Claude Haiku 4.5 | `us.anthropic.claude-haiku-4-5-v1` |
| `qwen235b` | Qwen3-235B-A22B | see `model_region_availability.json` |
| `qwen32b` | Qwen3-32B | see `model_region_availability.json` |
| `qwen35_9b` | Qwen3.5-9B | see `model_region_availability.json` |
| `gptoss120b` | GPT-OSS-120B | see `model_region_availability.json` |
