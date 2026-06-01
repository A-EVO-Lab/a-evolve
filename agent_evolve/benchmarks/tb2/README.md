# Terminal-Bench 2.0 Dataset

This directory contains the Terminal-Bench 2.0 challenge definitions used by AdaptiveAutoHarness experiments.

## Directory Structure

```
tb2/
  terminal2.py             # Benchmark adapter (task loading + evaluation)
  download_challenges.sh   # Manual download script
  README.md                # This file
  challenges/              # 89 challenge directories (downloaded separately)
    adaptive-rejection-sampler/
      eval.yaml            # Task prompt, metadata, difficulty, category
      compose.yaml         # Docker image reference
      tests/test.sh        # Evaluation script
    bn-fit-modify/
    ...
```

## Downloading Challenges

The `challenges/` directory contains 89 task definitions. It is not tracked in git due to its size. There are three ways to obtain it:

### Option 1: Automatic (recommended)

The dataset loader auto-downloads challenges on first use. Just run any experiment:

```bash
bash examples/tb_examples/run_baseline.sh test --limit 1
```

If `challenges/` is empty, it will download from the pinned GitHub commit automatically.

### Option 2: Manual script

```bash
bash agent_evolve/benchmarks/tb2/download_challenges.sh
```

This downloads to `agent_evolve/benchmarks/tb2/challenges/` by default. To download to a custom location:

```bash
bash agent_evolve/benchmarks/tb2/download_challenges.sh /path/to/custom/dir
```

### Option 3: Environment variable

If you already have the challenges elsewhere, point to them:

```bash
export TB2_CHALLENGES_DIR=/path/to/existing/challenges
```

This skips any download and uses the specified directory directly.

## Source

Challenges are downloaded from the inspect_evals repository at a pinned commit for reproducibility:

- Repository: https://github.com/UKGovernmentBEIS/inspect_evals
- Commit: `6e30b2de72e98dd5cc342eb9ba545ae27d2f63d7`
- Path: `src/inspect_evals/terminal_bench_2/challenges`

## Re-downloading

To force a re-download (e.g., to update to a newer version), remove the existing directory:

```bash
rm -rf agent_evolve/benchmarks/tb2/challenges
bash agent_evolve/benchmarks/tb2/download_challenges.sh
```

To pin a different commit, update `TB2_COMMIT` in both `download_challenges.sh` and `agent_evolve/agents/terminal/dataset.py`.
