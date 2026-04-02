# SkillForge

The SkillBench evolution algorithm. It follows a **grind** pattern -- solve a task, and if the agent fails, evolve the workspace (skills, memory, prompts) and retry the same task -- repeating until the task passes or a cycle budget is exhausted. Skills created from failures accumulate in a library and transfer to future tasks, building domain knowledge over time.

---

## How It Works

The agent workspace (prompts, skills, memory) is a directory on disk, seeded from the bundled `skillbench` workspace. The grind script processes tasks in batches. For each task, it runs a **solve-fail-evolve-retry** loop: the solver agent works inside a Docker container, and on failure the evolver LLM analyzes the observation and mutates workspace files. Every evolution step is git-tagged for reproducibility.

### The Grind Cycle

Each task goes through up to `max_cycles` iterations:

```
  Task from SkillBench
          |
          v
  +--------------------------------------------------+
  |  Phase 1 -- Solve                                  |
  |  Build Docker image from task Dockerfile.          |
  |  Select skills to inject (see Skill Selection).    |
  |  Inject selected skills + task-specific skills     |
  |  into container skill directories.                 |
  |  Run solver agent (Terminus2 or strands profile).  |
  |  Execute test.sh to verify solution.               |
  +--------------------------------------------------+
          |
     PASS? --yes--> Record result, move to next task
          |
          no
          |
          v
  +--------------------------------------------------+
  |  Phase 2 -- Write Observation                      |
  |  Build sanitized feedback (failure class, test     |
  |  names, reward score, task description).            |
  |  Write evolution/current_observation.md for the    |
  |  evolver to read.                                  |
  +--------------------------------------------------+
          |
          v
  +--------------------------------------------------+
  |  Phase 3 -- Evolve (General Skills)                |
  |  AEvolveEngine.evolve():                           |
  |    - Reads observation logs + current skills       |
  |    - Builds a prompt with permissions, task        |
  |      summaries, draft skills, current skills       |
  |    - Runs evolver LLM with bash tool access        |
  |    - LLM reads/writes workspace files              |
  |  Post-evolution: optionally distill bloated skills |
  |  Git-tag the mutation.                             |
  +--------------------------------------------------+
          |
          v
  +--------------------------------------------------+
  |  Phase 4 -- Evolve (Task-Specific Skills)          |
  |  If task_skill_mode is enabled:                    |
  |    - Generate or update per-task skill in          |
  |      task_skills/<task_id>/SKILL.md                |
  |    - Focused on THIS task's failure mode           |
  +--------------------------------------------------+
          |
          v
  +--------------------------------------------------+
  |  Phase 5 -- Reload & Retry                         |
  |  Agent reloads workspace from disk.                |
  |  Loop back to Phase 1 for the same task.           |
  +--------------------------------------------------+
          |
     max_cycles exhausted? --> Record as failed
```

### Illustrative Example

Suppose the agent faces a task in the `financial-modeling` category that requires building an Excel budget spreadsheet. On the first attempt:

```
Cycle 1: FAIL  score=0.000  (34/37 tests)  failure_class=test_fail
  - Skills loaded: [] (no relevant skills in workspace)
  - Failed tests: test_growth_values, test_format_percent, test_total_formula
```

The evolver receives this feedback and creates a `financial-modeling` skill containing the domain knowledge (specific openpyxl patterns, percentage formatting, growth formula construction). On retry:

```
Cycle 2: FAIL  score=0.919  (34/37 tests)  delta=+0.919
  - Skills loaded: [financial-modeling]
  - Failed tests: test_format_percent, test_total_formula, test_growth_values
```

The evolver updates the skill with more specific guidance about the remaining failures. On the third attempt:

```
Cycle 3: PASS  score=1.000  (37/37 tests)
  - Skills loaded: [financial-modeling]
```

The `financial-modeling` skill now persists in the workspace and will be injected into future tasks in the same category, enabling first-attempt passes on similar problems.

---

## Core Mechanisms

### 1. Dual Skill Library

SkillForge maintains two distinct skill libraries:

| Library | Location | Scope | Lifecycle |
|---|---|---|---|
| **General skills** | `skills/<name>/SKILL.md` | Category-level or cross-category knowledge | Persist indefinitely, accumulate across batches |
| **Task-specific skills** | `task_skills/<task_id>/SKILL.md` | Guidance for one specific task | Created per-task, evolved on failure retries |

**General skills** are the primary output of evolution. They encode domain knowledge, working code patterns, common pitfalls, and verification procedures that transfer across tasks in the same category. The evolver LLM creates and refines these through bash tool access to the workspace directory.

**Task-specific skills** are short-lived guides (max 100 lines) generated for individual tasks. They encode the specific approach for a particular task without leaking test answers. They are injected alongside general skills but do not transfer to other tasks.

### 2. Skill Injection into Docker Containers

Each SkillBench task runs inside an isolated Docker container. Skills from the workspace must be physically copied into the container's filesystem so the solver agent can discover them:

```
Workspace (host)                    Docker Container
skills/                             /root/.agents/skills/
  financial-modeling/SKILL.md  -->    financial-modeling/SKILL.md
  data-formats/SKILL.md        -->    data-formats/SKILL.md
task_skills/                        /root/.claude/skills/
  task-042/SKILL.md            -->    task-042/SKILL.md
```

Skills are injected into multiple legacy skill directories (`/root/.agents/skills/`, `/root/.claude/skills/`, `/root/.codex/skills/`, `/root/.terminus/skills/`) so the solver agent can discover them regardless of which skill path convention it checks.

### 3. Skill Selection (Selective Loading)

As the skill library grows (easily 50+ skills after a full grind run), injecting every skill into every container dilutes the solver's attention and wastes context. SkillForge supports two selection strategies, controlled by `skill_select_limit`:

#### Mode A: Inject All (default, `skill_select_limit=0`)

All workspace skills are copied into the container. The solver agent discovers them via `list_skills`/`load_skill` and decides which to load at runtime. This is simple but can cause prompt dilution when the library is large.

#### Mode B: Keyword-Overlap Selection (`skill_select_limit > 0`)

A lightweight scoring algorithm selects the top N most relevant skills before injection:

```
For each skill in the library:
  score = 0
  # Name keywords: "energy-market" → ["energy", "market"]
  score += count(name_keywords ∩ task_input_words, len > 3)
  # Description keywords from SKILL.md frontmatter
  score += count(desc_keywords ∩ task_input_words, len > 3)
  # Category match bonus
  if skill.category overlaps task.category: score += 5

Select top skill_select_limit skills with score >= 2
```

This filtering prevents prompt dilution when the skill library grows large. As noted in the codebase: *"More skills ≠ better — filtering prevents dilution."*

#### Mode C: Semantic Similarity Selection

An alternative selection strategy using embedding-based semantic similarity between the task description and skill content. Instead of keyword overlap, this would:

1. Embed each skill's SKILL.md (name + description + first section) using a sentence embedding model
2. Embed the task input at solve time
3. Select the top-K skills by cosine similarity

This would better handle cases where skill names don't share keywords with the task (e.g., a `data-formats` skill being relevant to a "parse the CSV and produce an Excel report" task). Not yet implemented in the current codebase.


### 4. Skill Format

Skills follow a structured SKILL.md format with YAML frontmatter:

```markdown
---
name: financial-modeling
description: Domain knowledge for Excel/openpyxl financial modeling tasks
category: financial-modeling
version: 3
---

## Overview
When to use this skill and what it covers.

## Workflow
### Step 1: Environment setup
```python
# working code
```
### Step 2: Data processing
...

## Key Rules
1. ALWAYS use openpyxl for .xlsx files
2. NEVER hardcode column indices

## Verification
- [ ] All output files exist
- [ ] Formulas reference correct cells
```

The evolver LLM creates skills in this format. A distillation pass (optional, controlled by `--distill`) rewrites bloated skills (> 200 lines or > 6000 bytes) back into this structure using a separate LLM call.

### 5. Feedback Analysis

The feedback passed to the evolver is controlled by `--feedback-level`:

| Level | What the Evolver Sees |
|---|---|
| `none` | Category + failure class only (zero leakage) |
| `score` | + reward score + aggregate pass/fail counts |
| `tests` | + stripped test function names (default, SWE-bench equivalent) |
| `masked` | + verifier output with assertion values replaced by `<VALUE>` |
| `full` | + full verifier output including assertion values |

The default `tests` level provides test names with parametrized values stripped (e.g., `test_growth_values[<PARAMS>]`), giving the evolver enough signal to understand what failed without leaking expected answers. Assertion masking (`expected <VALUE>, got <VALUE>`) prevents skills from memorizing specific test outputs.

### 6. Success Distillation

When a task passes on cycle 1 (without needing evolution), the grind script can distill transferable knowledge from the success:

| Mode | Behavior |
|---|---|
| `off` | No action on success |
| `draft_only` | Extract a candidate skill to `skills/_drafts/<support_key>.md` with metadata tracking |
| `gated_promotion` | Draft + promote to `skills/<support_key>/` when `promotion_threshold` tasks support the same skill |

The gated promotion mechanism prevents premature generalization: a draft skill must be independently useful for multiple tasks before it enters the main library.

### 7. Conversation Management

The Terminus2 solver profile uses a summarizing conversation manager that prevents context overflow during long solving sessions. Instead of dropping old messages, it summarizes them into a single message using an LLM call. This preserves information from earlier commands while keeping the conversation within token limits.

### 8. Evolutionary Generality Loss (EGL)

EGL tracks how efficiently the skill library grows relative to tasks solved:

```
EGL = (new_skills_created / total_tasks_solved) * 1000
```

Convergence is detected when EGL stays below a threshold (default: 0.05) for a configurable window of consecutive cycles. Low EGL means the existing skill library is sufficient for new tasks -- the system has learned enough domain knowledge.

### 9. Gating via Holdout Validation

The `GatingStrategy` class validates evolver mutations by running the agent on holdout tasks. If the post-mutation score drops below a threshold, the mutation is rejected. This prevents the evolver from over-fitting to specific failure modes at the expense of general capability.

---

## Module Structure

### Algorithm Core (`agent_evolve/algorithms/skillforge/`)

| File | Contents |
|---|---|
| `engine.py` | `AEvolveEngine` -- main engine implementing the `EvolutionEngine` interface |
| `prompts.py` | `build_evolution_prompt()` -- prompt construction with task summaries, permissions, drafts, and current skills |
| `tools.py` | `BASH_TOOL_SPEC`, `make_workspace_bash()` -- bash tool for LLM workspace access, `create_default_llm()` -- provider factory |
| `egl.py` | `compute_egl()`, `is_converged()` -- Evolutionary Generality Loss tracking |
| `gating.py` | `GatingStrategy` -- holdout validation of mutations |
| `__init__.py` | Public exports: `AEvolveEngine`, `DEFAULT_EVOLVER_SYSTEM_PROMPT`, `BASH_TOOL_SPEC`, `make_workspace_bash` |

### SkillBench Agent (`agent_evolve/agents/skillbench/`)

| File | Contents |
|---|---|
| `agent.py` | `SkillBenchAgent` -- reference agent that assembles prompts from workspace, manages skill injection, drives solving |
| `backends.py` | `NativeSkillBenchBackend`, `HarborSkillBenchBackend` -- execution backends; skill selection, injection, conversation management |
| `evolver.py` | `SkillBenchEvolver` -- programmatic entrypoint that wires agent + benchmark + engine into the evolution loop |
| `loop.py` | `SkillBenchEvolutionLoop` -- evolution loop with dual-comparison support and convergence detection |
| `dataset.py` | Task loading from SkillBench task directories |
| `docker_env.py` | `SkillBenchContainer`, `build_image()` -- Docker container management for task execution |
| `artifacts.py` | `export_skillbench_artifacts()` -- result export in multiple formats |
| `tools.py` | Tool wrappers for the solver agent (bash, file read/write, skill list/load) |

### SkillBench Benchmark (`agent_evolve/benchmarks/skillbench/`)

| File | Contents |
|---|---|
| `skill_bench.py` | `SkillBenchBenchmark` -- benchmark adapter: loads tasks from disk, evaluates by parsing verification results, builds sanitized evolver feedback, masks assertion values |

---

## Key Classes

### `AEvolveEngine`

The core evolution engine. Uses an LLM with bash tool access to analyze observation logs and mutate the agent workspace. Implements both `EvolutionEngine.step()` (for the loop) and a standalone `evolve()` method.

```python
from agent_evolve.algorithms.skillforge import AEvolveEngine
from agent_evolve.config import EvolveConfig

config = EvolveConfig(
    evolver_model="us.anthropic.claude-opus-4-5-20251101-v1:0",
    evolve_skills=True,
    evolve_memory=False,
    evolve_prompts=False,
    extra={"region": "us-west-2"},
)
engine = AEvolveEngine(config)

# Standalone evolution pass
result = engine.evolve(
    workspace=agent.workspace,
    observation_logs=evo_logs,
    evo_number=3,
)

print(result["skills_before"])   # 4
print(result["skills_after"])    # 6
print(result["skills_added"])    # ["financial-modeling", "data-validation"]
print(result["usage"])           # {"input_tokens": 12345, "output_tokens": 2345}
```

The engine's evolver LLM receives:
- A system prompt instructing it to analyze observations and mutate workspace files
- A user prompt containing: permissions, task summaries (last 30), draft skills, current skill names
- A `workspace_bash` tool for reading/writing files in the workspace

### `SkillBenchAgent`

The solver agent. Assembles a system prompt from the workspace (base prompt + skills + memories), builds Docker containers for each task, injects skills, and drives the solving loop through Terminus2 or strands profiles.

```python
from agent_evolve.agents.skillbench import SkillBenchAgent

agent = SkillBenchAgent(
    workspace_dir="./evolution_workdir/skillbench",
    model_id="us.anthropic.claude-opus-4-5-20251101-v1:0",
    region="us-west-2",
    max_tokens=16384,
    execution_mode="native",
    native_profile="terminus2",    # terminus2 | strands | terminus2_legacy
    score_mode="dual",             # reward | binary | dual
    skill_select_limit=0,          # 0=all, N>0=top N by relevance
)
```

### `SkillBenchBenchmark`

The benchmark adapter. Loads SkillBench tasks from a directory on disk, partitions into train/holdout splits, and evaluates by parsing verification results embedded in trajectories. Builds sanitized feedback for the evolver with configurable leakage levels.

```python
from agent_evolve.benchmarks.skill_bench import SkillBenchBenchmark

benchmark = SkillBenchBenchmark(
    tasks_with_skills_dir="/path/to/skillsbench/tasks",
    tasks_without_skills_dir="/path/to/skillsbench/tasks-no-skills",
    use_skills=False,
    holdout_ratio=0.2,
    split_seed=42,
    score_mode="dual",
    native_profile="terminus2",
)
```

### `SkillBenchEvolver`

High-level facade that wires agent, benchmark, and engine together for programmatic evolution runs.

```python
from agent_evolve.agents.skillbench.evolver import SkillBenchEvolver

evolver = SkillBenchEvolver(
    seed_workspace="seed_workspaces/skillbench",
    work_dir="./evolution_workdir/skillbench",
    model_id="us.anthropic.claude-sonnet-4-20250514-v1:0",
    region="us-west-2",
)
result = evolver.run(cycles=10)
print(result.final_score, result.converged)
```

---

## Seed Workspaces

The bundled seed workspace is `seed_workspaces/skillbench`:

The bootstrap workspace. Contains the same system prompt plus four starter skills:

```
skillbench/
  manifest.yaml
  prompts/system.md
  skills/
    data-formats/SKILL.md          # CSV, JSON, Excel handling patterns
    environment-discovery/SKILL.md # Container inspection procedures
    python-packages/SKILL.md       # Common package installation
    skill-usage/SKILL.md           # How to discover and use skills
  memory/memories.jsonl
```

---

## Usage

### Grind Script (recommended)

The primary entry point is the grind script, which implements the full solve-fail-evolve-retry loop:

```bash
# Quick test: 2 tasks, max 3 retries each
python examples/skillbench_examples/skillbench_evolve_in_situ_cycle.py \
  --batch-size 2 --max-cycles 3 --use-skills false --limit 2 -v

# Full run with evolution
python examples/skillbench_examples/skillbench_evolve_in_situ_cycle.py \
  --batch-size 1 --max-cycles 3 --use-skills false \
  --feedback-level tests \
  --task-skill-mode pre_generate_and_retry \
  --evolve-skills true --evolve-memory false --evolve-prompts false \
  --success-mode gated_promotion --promotion-threshold 1 \
  --model-id us.anthropic.claude-opus-4-5-20251101-v1:0 \
  --region us-west-2
```

### Shell Wrapper (production)

The shell wrapper `run_skillbench_evolve_in_situ_cycle.sh` provides environment-variable configuration for production runs:

```bash
# Override settings via env vars
MAX_CYCLES=5 \
BATCH_SIZE=1 \
MODEL_ID=us.anthropic.claude-opus-4-5-20251101-v1:0 \
USE_SKILLS=false \
FEEDBACK_LEVEL=tests \
TASK_SKILL_MODE=pre_generate_and_retry \
SUCCESS_MODE=gated_promotion \
  bash examples/skillbench_examples/run_skillbench_evolve_in_situ_cycle.sh
```

### Programmatic API

```python
from agent_evolve.agents.skillbench.evolver import SkillBenchEvolver

evolver = SkillBenchEvolver(
    seed_workspace="seed_workspaces/skillbench",
    model_id="us.anthropic.claude-opus-4-5-20251101-v1:0",
    execution_mode="native",
    native_profile="terminus2",
)
result = evolver.run(cycles=10)
```

---

## Key Parameters and Configuration

### Grind Settings

| Parameter | Default | Description |
|---|---|---|
| `--max-cycles` | 3 | Max solve-evolve-retry cycles per task |
| `--batch-size` | 1 | Tasks per evolution batch (1 = evolve after each task) |
| `--max-workers` | 1 | Parallel workers for solving (solving parallel, evolution serial) |

### Evolution Scope

| Parameter | Default | Description |
|---|---|---|
| `--evolve-skills` | `true` | Allow evolver to create/modify/delete skills |
| `--evolve-memory` | `false` | Allow evolver to modify memory files |
| `--evolve-prompts` | `false` | Allow evolver to modify system prompt |
| `--evolve-tools` | `false` | Allow evolver to create tools |
| `--distill` | `false` | Post-evolution distillation of bloated skills (> 200 lines) |

### Feedback Control

| Parameter | Default | Description |
|---|---|---|
| `--feedback-level` | `tests` | How much verifier feedback the evolver sees |
| `--no-direct-answers` | `true` | Prevent skills from encoding specific answer values |

### Task-Specific Skills

| Parameter | Default | Description |
|---|---|---|
| `--task-skill-mode` | `pre_generate_and_retry` | `off` = no task skills; `retry_only` = evolve on failure; `pre_generate_and_retry` = pre-generate from task description + evolve on failure |

### Skill Injection

| Parameter | Default | Description |
|---|---|---|
| `--skill-select-limit` | `0` | Max workspace skills to inject per task (0 = all, N > 0 = keyword-match top N) |

### Success Distillation

| Parameter | Default | Description |
|---|---|---|
| `--success-mode` | `gated_promotion` | What to do on first-attempt passes: `off`, `draft_only`, `gated_promotion` |
| `--promotion-threshold` | 1 | Min tasks supporting a draft before promotion to main library |

### Agent / Execution

| Parameter | Default | Description |
|---|---|---|
| `--model-id` | `us.anthropic.claude-opus-4-5-20251101-v1:0` | Solver model |
| `--evolver-model-id` | (same as solver) | Evolver model (can be different) |
| `--native-profile` | `terminus2` | Solver profile: `strands`, `terminus2`, `terminus2_legacy` |
| `--score-mode` | `dual` | Scoring: `reward` (partial), `binary` (0/1), `dual` (max of both) |
| `--seed-workspace` | `seed_workspaces/skillbench` | Starting workspace directory |

---

## How SkillForge Differs from Adaptive Evolve

| Dimension | Adaptive Evolve | SkillForge |
|---|---|---|
| **Evolution trigger** | Batch-level: evolve after a batch of tasks | Task-level: evolve after each failed task |
| **Retry pattern** | Agent solves new tasks each cycle | Agent retries the SAME failed task after evolution |
| **Analysis depth** | Multi-layer: per-claim, per-task-type, judge feedback mining, failure pattern detection | Lightweight: observation logs + task feedback |
| **Auto-seeded skills** | Deterministic rules trigger skill injection before LLM runs | LLM-driven: evolver decides what skills to create |
| **Graduated scope** | Mutation intensity scales with performance level | Permissions-based: scope flags control what the evolver can touch |
| **Meta-learning** | 10-cycle rolling history of what changed and its impact | Git-tagged snapshots, no explicit history tracking in prompts |
| **Stagnation detection** | Automatic rollback after N cycles without improvement | EGL-based convergence detection |
| **Skill library** | Single library (general skills only) | Dual library: general skills + task-specific skills |
| **Execution** | MCP tool server in Docker | Task Docker containers with skill injection |
| **Benchmark** | MCP-Atlas (API-based tasks) | SkillBench (coding/data tasks with test.sh verification) |
