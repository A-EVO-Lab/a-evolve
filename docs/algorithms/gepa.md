# GEPA (`gepa`)

Population-based prompt evolution using [GEPA](https://gepa-ai.github.io/gepa/) (Genetic-Pareto optimization). Unlike other A-Evolve engines that mutate one candidate per cycle, GEPAEngine runs GEPA's full optimization pipeline — maintaining a Pareto front of diverse candidates, reflecting on failures, and accepting only improvements — inside a single `step()` call.

## Overview

GEPA is an LLM-based optimization framework that evolves `dict[str, str]` candidates through reflective prompt evolution. Its core loop — Select, Run, Reflect, Mutate, Accept — is orchestrated by `optimize_anything()`, GEPA's primary public API.

A-Evolve's GEPAEngine bridges the two systems: it serializes workspace layers (system prompt, fragments, skills, memory) into GEPA's candidate format, wraps A-Evolve's TrialRunner as GEPA's evaluator, and writes the best candidate back to the workspace when optimization completes.

## How It Works

```
A-Evolve Loop (1 cycle)
  │
  ├── SOLVE/OBSERVE → skipped (manages_own_evaluation=True)
  ├── PRE-EVOLVE SNAPSHOT
  │
  ├── step() ──────────────────────────────────────────────┐
  │                                                        │
  │   ┌──────────────────────────────────────────────┐     │
  │   │  GEPA optimize_anything()                    │     │
  │   │                                              │     │
  │   │  ┌────────┐    ┌──────┐    ┌─────────┐      │     │
  │   │  │ Select │───▶│ Run  │───▶│ Reflect │      │     │
  │   │  │candidate│   │eval  │    │  (LLM)  │      │     │
  │   │  └────────┘    └──────┘    └────┬────┘      │     │
  │   │       ▲                         │           │     │
  │   │       │    ┌────────┐    ┌──────▼──────┐    │     │
  │   │       └────│ Accept │◀───│   Mutate    │    │     │
  │   │            │/Reject │    │  candidate  │    │     │
  │   │            └────────┘    └─────────────┘    │     │
  │   │                                              │     │
  │   │  Pareto front tracks non-dominated candidates│     │
  │   └──────────────────────────────────────────────┘     │
  │                                                        │
  │   → writes best candidate to workspace                 │
  │   → returns StepResult(stop=True)                      │
  │                                                        │
  ├── POST-EVOLVE SNAPSHOT                                 │
  ├── RELOAD agent                                         │
  └── STOP (loop exits)                                    │
```

One A-Evolve cycle = GEPA's entire optimization run. The loop provides workspace snapshots, git versioning, and agent reload.

### GEPA's Core Loop

Each iteration inside `optimize_anything()`:

1. **Select** — Pick a candidate from the Pareto front for mutation
2. **Run** — Evaluate the candidate on a minibatch of tasks using the evaluator function
3. **Reflect** — An LLM analyzes scores + ASI (Actionable Side Information) to understand what went wrong
4. **Mutate** — The reflection LLM proposes changes to one component of the candidate (round-robin across system_prompt, skills, memory, etc.)
5. **Accept/Reject** — The mutated candidate is evaluated; only accepted if it improves the Pareto front

The Pareto front maintains population diversity — multiple non-dominated candidates that trade off across metrics. Merge/crossover can combine strengths from different front members.

For a deeper dive into GEPA's algorithm, see the [GEPA documentation](https://gepa-ai.github.io/gepa/) and [quickstart guide](https://gepa-ai.github.io/gepa/guides/quickstart/).

### Candidate Serialization

A-Evolve workspace layers are serialized into GEPA's `dict[str, str]` format:

| Candidate Key | Workspace Source | Serialization |
|---|---|---|
| `system_prompt` | `prompts/system.md` | Direct 1:1 mapping |
| `prompt_fragments` | `prompts/fragments/*.md` | `=== FRAGMENT: name ===` delimited |
| `skills` | `skills/*/SKILL.md` | `=== SKILL: name ===` delimited |
| `memory` | `memory/*.jsonl` | Raw JSONL with `_category` metadata |

Which keys are included depends on `EvolveConfig` gates (`evolve_prompts`, `evolve_skills`, `evolve_memory`). GEPA's reflection LLM evolves each component independently via round-robin — "what's wrong with the skills?" is a better question than "what's wrong with skill file #7?".

### Evaluation Bridge

The evaluator connects GEPA's optimization loop to A-Evolve's TrialRunner:

```python
def evaluator(candidate, task) -> (score, side_info):
    restore_candidate(workspace, candidate, config)  # write candidate files to disk
    agent.reload_from_fs()                           # agent picks up new state
    obs = trial.run_single(task)                     # run the agent on the task
    return obs.feedback.score, build_side_info(obs)  # return score + diagnostics
```

The `side_info` dict provides structured ASI (Actionable Side Information) for GEPA's reflection LLM — task input, agent trace (compressed), feedback status/detail, and raw benchmark diagnostics. Trajectory compression uses head+tail+errors extraction (no LLM call, bounded to 3000 chars).

### Parallel Evaluation

For `parallel_workers > 1`, a worker pool creates N workspace clones with independent agent/trial instances. A thread-safe lock manages worker checkout. GEPA's built-in `ThreadPoolExecutor` dispatches evaluator calls across workers. Cloned workspaces are cleaned up when `optimize_anything` returns.

## Usage

### Basic

```python
import agent_evolve as ae
from agent_evolve.algorithms.gepa import GEPAEngine

config = ae.EvolveConfig(
    batch_size=10,
    max_cycles=20,
    evolve_prompts=True,
    evolve_skills=True,
)

evolver = ae.Evolver(
    agent="mcp",
    benchmark="mcp-atlas",
    engine=GEPAEngine(config, objective="Improve multi-step tool orchestration"),
)
results = evolver.run()
```

### With Full GEPA Config

```python
from gepa.optimize_anything import GEPAConfig, EngineConfig, ReflectionConfig, MergeConfig

gepa_config = GEPAConfig(
    engine=EngineConfig(
        max_metric_calls=200,
        minibatch_size=5,
        parallel=True,
        max_workers=4,
    ),
    reflection=ReflectionConfig(
        reflection_lm="anthropic/claude-sonnet-4-20250514",
    ),
    merge=MergeConfig(
        enabled=True,
    ),
)

engine = GEPAEngine(
    config=ae.EvolveConfig(evolve_prompts=True, evolve_skills=True),
    gepa_config=gepa_config,
    objective="Improve the agent's ability to resolve real-world GitHub issues",
    background="The agent patches open-source repos to fix bug reports.",
    parallel_workers=4,
)
```

For all available `GEPAConfig` options, see the [GEPA API reference](https://gepa-ai.github.io/gepa/api/).

## Key Parameters

### GEPAEngine Constructor

| Parameter | Type | Default | Description |
|---|---|---|---|
| `config` | `EvolveConfig` | required | A-Evolve config — controls which workspace layers are evolved |
| `gepa_config` | `GEPAConfig \| None` | `None` | Full GEPA configuration. If `None`, built from `config` defaults |
| `objective` | `str \| None` | `None` | Natural-language optimization objective for the reflection LLM |
| `background` | `str \| None` | `None` | Domain knowledge context for the reflection LLM |
| `parallel_workers` | `int` | `1` | Number of parallel evaluation workers (1 = serial) |

### Default GEPAConfig (when `gepa_config=None`)

| Setting | Default Value | Source |
|---|---|---|
| `max_metric_calls` | `batch_size * max_cycles` | Total evaluation budget |
| `reflection_lm` | `config.evolver_model` | LLM used for reflection and mutation |

### Key EngineConfig Settings

| Setting | Description |
|---|---|
| `max_metric_calls` | Total evaluator calls before stopping |
| `minibatch_size` | Tasks per evaluation round |
| `run_dir` | Directory for GEPA state persistence and resume |
| `parallel` / `max_workers` | Enable parallel evaluation with N workers |

### Key ReflectionConfig Settings

| Setting | Description |
|---|---|
| `reflection_lm` | Model used for analyzing failures and proposing mutations |

For the full configuration reference, see the [GEPA API docs](https://gepa-ai.github.io/gepa/api/) and [use-case guides](https://gepa-ai.github.io/gepa/guides/use-cases/).

## What GEPA Brings to A-Evolve

Capabilities not available in other A-Evolve engines:

| Capability | Description |
|---|---|
| **Population diversity** | Pareto front maintains multiple candidate workspace states simultaneously |
| **Accept/reject gating** | Mutations only accepted if they improve scores on the minibatch |
| **Structured reflection** | Reflection LLM sees formatted ASI (traces, scores, feedback) per component |
| **Merge/crossover** | Combines strengths from two Pareto-front candidates |
| **Per-component evolution** | System prompt, skills, memory evolved independently via round-robin |
| **Evaluation caching** | Disk-persistent cache avoids redundant (candidate, task) evaluations |
| **Multi-objective Pareto** | Track score + other objectives on a true Pareto frontier |
| **Resume** | `run_dir` enables resuming optimization across process restarts |

## Design Decisions

### Why run-to-completion in one step()?

GEPA's public API is `optimize_anything()`, which orchestrates the full pipeline internally — candidate selection, Pareto tracking, merge scheduling, evaluation caching, and convergence detection. Breaking it into per-cycle calls would require reimplementing that orchestration or hacking around its run-to-completion design. One `step()` call with `stop=True` is the cleanest integration.

### Why manages_own_evaluation?

GEPAEngine needs to evaluate candidates inside its optimization loop, not through A-Evolve's batch SOLVE/OBSERVE phase. The `manages_own_evaluation` property tells the loop to skip its evaluation and let the engine handle it via TrialRunner directly.

### Why dict[str, str] candidates?

GEPA operates on `dict[str, str]` — each key is one evolvable component. This maps naturally to A-Evolve's workspace layers. GEPA's round-robin evolves one key at a time, which matches how a reflection LLM should think about workspace evolution.

## Implementation

The algorithm is implemented in [`agent_evolve/algorithms/gepa/`](../../agent_evolve/algorithms/gepa/) as four files:

| File | Contents |
|---|---|
| `engine.py` | `GEPAEngine` — main engine implementing `EvolutionEngine`, calls `optimize_anything()` |
| `serialization.py` | `build_candidate()`, `restore_candidate()` — workspace-to-candidate round-trip |
| `evaluator.py` | `make_evaluator()`, `make_parallel_evaluator()`, `build_side_info()`, `compress_trajectory()` |
| `__init__.py` | Exports `GEPAEngine` with graceful `ImportError` handling when `gepa` is not installed |

### Dependencies

GEPA is an optional dependency. Install with:

```bash
pip install a-evolve[gepa]
```

All imports from the `gepa` package are isolated to `engine.py`. The `__init__.py` sets `GEPAEngine = None` if `gepa` is not installed, so `import agent_evolve.algorithms.gepa` never fails.
