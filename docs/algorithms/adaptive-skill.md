# Adaptive Skill — In-Situation Evolution Without Labels

`adaptive_skill` evolves an agent's skill library by analyzing trajectories from a batch of tasks — without ever seeing ground-truth labels, test results, or pass/fail signals during evolution.

## Core Idea

Most evolution algorithms rely on a labeled evaluation signal: the agent solves tasks, a judge tells it which answers were right, and the evolver learns from that feedback. Adaptive Skill removes the label dependency entirely. The evolver only sees **what the agent did** (commands, errors, loops, outputs), never **whether it succeeded**. An LLM judge estimates success from behavior, and the evolver uses those proxy signals to decide which skills to create.

This makes Adaptive Skill suitable for domains where:

- Ground-truth evaluation is expensive, slow, or unavailable during evolution.
- The agent operates in open-ended environments (terminal, CLI, system administration).
- You want to evolve continuously on live traffic without waiting for labeled feedback.

## Algorithm Overview

```mermaid
flowchart TD
    A["Batch of Tasks"] --> B["Agent Solves Tasks<br/>(black-box execution)"]
    B --> C["Collect Trajectories<br/>(tool calls, outputs, errors)"]
    C --> D{"trajectory_only<br/>mode?"}
    D -- "Yes" --> E["Trajectory Signal Extraction<br/>(errors, loops, timeouts, commands)"]
    D -- "No" --> F["Standard Feedback<br/>(pass/fail, score)"]
    E --> G["LLM Judge Scoring<br/>(0-10 per trajectory)"]
    G --> H["Build Evolution Prompt<br/>(signals + verdicts + current skills)"]
    F --> H
    H --> I["Evolver LLM<br/>(meta-learning agent with bash access)"]
    I --> J{"Skill Budget<br/>Exceeded?"}
    J -- "No" --> K["Create New Skills<br/>from failure patterns"]
    J -- "Yes" --> L["Refine Existing Skills<br/>with new patterns"]
    K --> M["Gating Validation<br/>(holdout tasks)"]
    L --> M
    M --> N{"Accepted?"}
    N -- "Yes" --> O["Commit Mutation<br/>(git tag evo-N)"]
    N -- "No" --> P["Rollback via git"]
    O --> Q["Agent Reloads<br/>Workspace"]
    P --> Q
    Q --> A
```

## Detailed Flow

### Phase 1 — Solve

The agent processes a batch of tasks in a black-box manner. Each task produces a **trajectory**: the full sequence of tool calls, commands, outputs, and errors the agent encountered.

### Phase 2 — Observe (Trajectory-Only)

Instead of collecting labeled feedback, the algorithm extracts **behavioral signals** from each trajectory:

```mermaid
flowchart LR
    T["Raw Trajectory"] --> S1["Signal Extraction"]
    S1 --> S2["n_turns, n_tool_calls"]
    S1 --> S3["n_errors, n_timeouts"]
    S1 --> S4["tools_used frequency"]
    S1 --> S5["repeated_commands (loops)"]
    S1 --> S6["submitted? submit_value"]
    S1 --> S7["error_snippets"]

    T --> C1["Trajectory Compression"]
    C1 --> C2["First 3 commands (approach)"]
    C1 --> C3["All errors + context"]
    C1 --> C4["Detected loops (cmd x3+)"]
    C1 --> C5["Last 3 commands (resolution)"]

    T --> J1["LLM Judge"]
    J1 --> J2["score: 0-10"]
    J1 --> J3["category: build, debug, ..."]
    J1 --> J4["outcome: one sentence"]
    J1 --> J5["failure_reason: specific cause"]
```

The **LLM Judge** acts as a proxy evaluator. It reads the compressed trajectory and estimates:

| Field | Description |
| :--- | :--- |
| `score` (0-10) | 0 = complete failure, 5 = partial progress, 10 = likely solved |
| `category` | Task type (build, debug, data-science, security, system-admin, ...) |
| `outcome` | One-sentence description of what happened |
| `failure_reason` | Specific thing that went wrong (if score < 7) |

### Phase 3 — Evolve (Skill Mutation Under Budget)

The evolver LLM receives all signals and verdicts, plus the current skill library, and decides what to mutate. This is where the **skill budget** controls growth.

```mermaid
flowchart TD
    subgraph Input
        V["Judge Verdicts<br/>(score, category, failure_reason)"]
        CS["Current Skills<br/>(name, description, content)"]
        B["Skill Budget<br/>(max_skills, e.g. 5)"]
    end

    V --> SORT["Sort by judge score<br/>(lowest first)"]
    SORT --> FILTER["Filter: score < 7<br/>(FAILED or PARTIAL)"]
    FILTER --> GROUP["Group failures by<br/>category + failure_reason"]

    GROUP --> CHECK{"Pattern has<br/>2+ failed tasks?"}
    CHECK -- "No" --> SKIP["Skip<br/>(not a pattern)"]
    CHECK -- "Yes" --> BUDGET{"current_skills<br/>< max_skills?"}

    BUDGET -- "Yes (budget remaining)" --> NEW["Create New Skill<br/>targeting this failure category"]
    BUDGET -- "No (budget reached)" --> REFINE["Refine Existing Skill<br/>add new patterns from failures"]

    NEW --> QUALITY["Quality Gate:<br/>- kebab-case name<br/>- clear WHEN description<br/>- domain knowledge only<br/>- max 2000 chars<br/>- verification steps"]
    REFINE --> QUALITY
    QUALITY --> WRITE["Write skill via bash tool<br/>to skills/SKILL.md"]
```

#### Skill Budget

The skill budget (`max_skills`, default 5) prevents unbounded skill library growth:

| State | Behavior |
| :--- | :--- |
| `current < max_skills` | Evolver may create new skills for uncovered failure categories |
| `current >= max_skills` | **No new skills allowed.** Evolver must refine existing skills instead |

The budget forces the evolver to produce **general, high-coverage skills** rather than one-off fixes. As the library fills up, new failure patterns must be folded into existing skills that cover the closest category.

#### What the Evolver Writes

Each skill is a `SKILL.md` file with YAML frontmatter:

```yaml
---
name: build-legacy-c-projects
description: >
  When building legacy C/C++ projects that fail with missing GUI/X11
  dependencies or outdated Makefiles.
---

## Steps
1. Check for optional GUI dependencies (X11, SDL, ncurses) and disable them
   via configure flags or Makefile edits.
2. ...

## Verification
- `make` completes with exit code 0
- Binary exists in expected output path
```

Skills are loaded **on demand** by the agent via `read_skill(name)` — the agent sees skill names and descriptions in its system prompt and decides which to read for a given task.

### Phase 4 — Gate (Optional)

After the evolver mutates the workspace, the **gating strategy** validates the mutation:

```mermaid
flowchart LR
    MUT["Mutated Workspace"] --> HOLDOUT["Run Agent on<br/>Holdout Tasks"]
    HOLDOUT --> SCORE["Compute avg score"]
    SCORE --> THRESH{"avg >= threshold?"}
    THRESH -- "Yes" --> ACCEPT["Accept mutation<br/>git tag evo-N"]
    THRESH -- "No" --> REJECT["Reject mutation<br/>git rollback"]
```

Holdout tasks are sampled from the benchmark (default 20% holdout ratio). If the mutated agent regresses on holdout tasks, the entire mutation is rolled back via git.

### Phase 5 — Reload & Converge (Optional)

The agent reloads its workspace (skills, prompts, memory) and the loop repeats. Convergence is tracked via **EGL (Evolutionary Generality Loss)**:

```
EGL = (new_skills_created / total_tasks_solved) * 1000
```

When EGL stays below a threshold (default 0.05) for a configurable window (default 3 cycles), the evolution is considered converged — the agent has stabilized and is no longer discovering new failure patterns.

## End-to-End Lifecycle

```mermaid
flowchart TD
    START(["Start: Base Agent<br/>(0 skills, generic prompt)"]) --> CYCLE["Evolution Cycle N"]

    subgraph CYCLE["Evolution Cycle"]
        direction TB
        SOLVE["1. Solve batch<br/>(agent runs tasks)"]
        OBS["2. Observe<br/>(extract trajectory signals,<br/>LLM judge scores)"]
        EVOLVE["3. Evolve<br/>(create/refine skills<br/>under budget)"]
        GATE["4. Gate<br/>(holdout validation)"]
        RELOAD["5. Reload workspace"]
        SOLVE --> OBS --> EVOLVE --> GATE --> RELOAD
    end

    RELOAD --> CONV{"EGL converged<br/>or max_cycles?"}
    CONV -- "No" --> CYCLE
    CONV -- "Yes" --> END(["End: Evolved Agent<br/>(N targeted skills)"])
```

## Configuration

Key config fields for Adaptive Skill (set via YAML or `EvolveConfig`):

| Parameter | Default | Description |
| :--- | :--- | :--- |
| `trajectory_only` | `True` | Only show trajectories to evolver (no labels) |
| `max_skills` | `5` | Skill budget — max number of skills allowed |
| `evolve_skills` | `True` | Allow skill creation/modification |
| `evolve_prompts` | `True` | Allow system prompt edits |
| `evolve_memory` | `True` | Allow memory updates |
| `protect_skills` | `False` | If True, existing skills are read-only (only new creation allowed) |
| `solver_proposed` | `False` | If True, the solver agent proposes draft skills for the evolver to generalize |
| `prompt_only` | `False` | If True, only system prompt mutations are allowed (no skills) |
| `batch_size` | `10` | Number of tasks per evolution cycle |
| `holdout_ratio` | `0.2` | Fraction of tasks reserved for gating validation |
| `egl_threshold` | `0.05` | EGL convergence threshold |
| `egl_window` | `3` | Number of consecutive cycles EGL must stay below threshold |

## Key Design Decisions

**Why no labels?** In open-ended terminal tasks, ground-truth evaluation can be expensive (spinning up Docker environments, running test suites). By judging from trajectories alone, the evolver can run continuously without waiting for evaluation infrastructure.

**Why a skill budget?** Without a budget, the evolver tends to create narrow, task-specific skills that don't generalize. The budget forces consolidation — five well-crafted category skills outperform twenty fragmented ones.

**Why an LLM judge?** The judge provides a structured signal (score + category + failure reason) that the evolver can sort, filter, and group. Raw trajectories are noisy; the judge distills them into actionable patterns.

## Source Files

| File | Role |
| :--- | :--- |
| [`engine.py`](../../agent_evolve/algorithms/adaptive_skill/engine.py) | `AdaptiveSkillEngine` — orchestrates the step/evolve loop |
| [`prompts.py`](../../agent_evolve/algorithms/adaptive_skill/prompts.py) | Prompt templates, trajectory compression, LLM judge |
| [`gating.py`](../../agent_evolve/algorithms/adaptive_skill/gating.py) | Holdout validation strategy |
| [`egl.py`](../../agent_evolve/algorithms/adaptive_skill/egl.py) | EGL computation and convergence check |
| [`tools.py`](../../agent_evolve/algorithms/adaptive_skill/tools.py) | Bash tool spec and LLM provider factory |
