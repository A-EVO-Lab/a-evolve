# Nemotron Reasoning — Training Design

## Overview

This workspace evolves LoRA training recipes for `nvidia/Nemotron-3-Nano-30B-A3B`
(hybrid Mamba-attention MoE, 30B params, 3B active) on the Kaggle Nemotron Model
Reasoning Challenge. The evolution loop mutates `training_config.yaml` and
`data_config.yaml` each cycle, trains a LoRA adapter, evaluates on held-out
prompts, and uses the signal to propose the next mutation.

## Architecture

```
┌─────────────────────────────────────────────────────┐
│  a-evolve (host)                                    │
│                                                     │
│  EvolutionLoop                                      │
│   ├─ TrainingEvolutionEngine (manages_own_eval)     │
│   │   ├─ trainer.solve(task)  ──► docker train      │
│   │   ├─ benchmark.evaluate_batch() ──► docker infer│
│   │   ├─ aggregate scores + write memory            │
│   │   └─ LLM + bash ──► mutate YAMLs                │
│   └─ cycle: train → eval → evolve → reload → repeat │
└─────────────────────────────────────────────────────┘
         │                    │
    ┌────▼────┐         ┌──── ▼────┐
    │  Docker │         │  Docker │
    │  train  │         │ infer   │
    │ verb    │         │ verb    │
    └─────────┘         └─────────┘
```

## Model & LoRA

- **Base**: Nemotron-3-Nano-30B-A3B-BF16 (local at `/fsx/models/`)
- **Adapter**: LoRA rank 32, targets `q_proj, k_proj, v_proj, o_proj`
- **Training**: bare-torch loop, single GPU `.to("cuda:0")`,
  `gradient_checkpointing(use_reentrant=False)`, `attn_implementation="eager"`
- **Why not HF Trainer**: `device_map="auto"` + Nemotron-H Mamba layers cause
  SIGSEGV. Bare-torch loop on single GPU is the proven pattern from
  `nemotron-auto-research`.

## Docker Image

**Image**: `801953956576.dkr.ecr.ap-south-1.amazonaws.com/aevolve/nemo-reasoner:2026-04-24-dp`
(also `:latest`; local alias `aevolve/nemo-reasoner:2026-04-24-dp`)

Stack:
- CUDA 12.8, torch 2.10, transformers, peft 0.19.1, accelerate 1.13
- `mamba_ssm` shim (pure-torch fallback; real CUDA kernels segfault with PEFT)

Two verbs:
- `train`: reads workspace YAMLs + CSV, outputs `/out/adapter/`
- `inference`: loads base + adapter, generates on eval prompts, outputs `/out/scores.jsonl`

### Known Issues

- **vLLM 0.19.x segfaults on NemotronH in Docker** at every tested config
  (TP=1/8, with/without LoRA, with or without all compilation passes disabled).
  Crash is reproducible right after `Model loading took ...` on the first
  dummy forward. Likely GitHub #35104 / #40038 (MoE-LoRA) + the V1 engine's
  `shm_broadcast` IPC being unreliable inside containers (#39685). 0.19
  removed V0, so there is no engine fallback.
  **Inference path: transformers + `generate()` only, data-parallel across
  all visible GPUs** (see `docker/inference.py`).
- **Nemotron-H requires mamba_ssm shim**: the real `mamba_ssm` CUDA kernels
  cause SIGSEGV during backward pass with PEFT + gradient checkpointing.
  The shim provides `rmsnorm_fn` as a pure-torch fallback; the model's own
  code falls back to naive Mamba scan when fast kernels are unavailable.

## Data

- **Source**: Kaggle competition CSV (9,500 rows total)
- **Split**: 9,000 train / 500 val (deterministic, seed=42)
- **Location**: `$AEVOLVE_DATA_DIR/nemotron_reasoning/{train,val}.csv`
- **Format**: `id, prompt, answer` columns
- **Tokenization**: chat-template with system prompt, labels masked before
  `\boxed{answer}` span

## Evolvable Surface

The LLM evolver mutates these files each cycle:

| File | What it controls |
|---|---|
| `training_config.yaml` | lr, lora rank/alpha/targets, warmup/total steps, batch, optimizer |
| `data_config.yaml` | data sources, filters, format, synth teacher config |
| `memory/training.jsonl` | per-cycle notes (score, config hash, reasoning) |

### Mutation Constraints (prompt-enforced)
- At most ONE parameter changed per cycle
- LR changes must be ≥ 2× or ≤ 0.5× current value
- No new top-level YAML keys
- `base_ckpt.yaml`, `manifest.yaml`, `train.py`, `eval.yaml` are read-only

## Evaluation

- **Metric**: exact match on `\boxed{...}` answer extraction
- **Split**: `val` (500 rows held out)
- **Pipeline**: transformers + PEFT load base + LoRA adapter on each GPU
  (DP fan-out), batched `generate()` at T=0, extracts last `\boxed{...}`
  group, compares to gold answer. Worker count defaults to
  `nvidia-smi --list-gpus` (capped at `len(prompts)`); override with
  `AEVOLVE_DP_WORKERS`.
- **Score**: fraction correct (0.0–1.0)

## Execution Modes

| Mode | Training | Inference | When to use |
|---|---|---|---|
| `__local__` | host venv subprocess | host venv subprocess | Development on this machine |
| Docker | `docker run ... train` | `docker run ... inference` | Production / other hosts |

For local mode, set `manifest.yaml` → `docker_image: __local__` and
`AEVOLVE_PYTHON=/path/to/venv/bin/python`.

## Running

```bash
# Env vars
export AEVOLVE_DATA_DIR=/fsx/zzsamshi/a-evolve/evolution_workdir/_data
export AEVOLVE_MODELS_DIR=/fsx/models
export AEVOLVE_SHARED_FS_ROOT=/fsx
export AWS_REGION=us-west-2

# 2-cycle smoke test
python examples/nemotron_examples/evolve_nemotron.py \
  --cycles 2 --limit 20 \
  --workspace /fsx/zzsamshi/a-evolve/seed_workspaces/nemo_reason

# Full run
python examples/nemotron_examples/evolve_nemotron.py \
  --cycles 20 --limit 200 \
  --workspace /fsx/zzsamshi/a-evolve/seed_workspaces/nemo_reason
```

## File Layout

```
seed_workspaces/nemo_reason/
├── manifest.yaml              # agent type, docker image, evolvable layers
├── base_ckpt.yaml             # base model URI (read-only)
├── training_config.yaml       # ← evolvable: lr, lora, schedule, batch
├── data_config.yaml           # ← evolvable: data sources, filters
├── eval.yaml                  # inference config (read-only)
├── train.py                   # training driver (read-only, mounted into container)
├── prompts/system.md          # contract placeholder; loop is code-driven
├── memory/                    # per-cycle training notes (evolvable)
├── docker/
│   ├── Dockerfile
│   ├── build.sh
│   ├── common.py              # shared utils (load_yaml, resolve_uri, etc.)
│   ├── entrypoint.py          # verb dispatcher
│   ├── inference.py           # transformers-DP inference driver
│   └── mamba_ssm_shim/        # pure-torch mamba fallback
└── IMAGE_REQUIREMENTS.md      # build spec for the docker image
```
