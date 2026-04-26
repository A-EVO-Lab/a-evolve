# Adaptive Evolve

A-Evolve's evolution algorithm. It analyzes per-claim feedback, stratifies performance by task type, mines judge justifications for root causes, and uses meta-learning to make surgical, evidence-based mutations — targeting the specific weaknesses that matter most.

---

## How It Works

The agent workspace (prompts, skills, memory) is a directory on disk. The evolution engine reads observation logs from solve cycles, analyzes them across multiple layers, and mutates workspace files to improve the agent. Every accepted mutation is git-tagged for full reproducibility and rollback.

### The Evolution Cycle

Each cycle runs eight phases:

```
  Observation Logs (from solve cycles)
          │
          ▼
  ┌─────────────────────────────────────────────────────────────┐
  │  Phase 1 — Base Analysis                                    │
  │  Aggregate pass/fail rates, tool error counts,              │
  │  hallucinated tool names, strategy issues.                  │
  ├─────────────────────────────────────────────────────────────┤
  │  Phase 2 — Code Execution Analysis                          │
  │  How often did the agent use execute_code?                  │
  │  Pass rate with vs. without code. Missed opportunities.     │
  ├─────────────────────────────────────────────────────────────┤
  │  Phase 3 — Adaptive Analysis                                │
  │  Per-claim breakdown: which requirement types fail?         │
  │  Per-task-type breakdown: which task categories are weak?   │
  │  Judge feedback mining: what do judges repeatedly say?      │
  │  Failure pattern detection: systematic issues.              │
  ├─────────────────────────────────────────────────────────────┤
  │  Phase 4 — Auto-Corrections                                 │
  │  Fix hallucinated tool names in skills/prompts.             │
  │  Prune memory to cap (default: 15 entries).                 │
  ├─────────────────────────────────────────────────────────────┤
  │  Phase 5 — Auto-Seed Skills                                 │
  │  If failure patterns cross thresholds, inject targeted      │
  │  skills immediately (before the LLM runs).                  │
  ├─────────────────────────────────────────────────────────────┤
  │  Phase 6 — LLM-Driven Evolution                             │
  │  Evolver LLM receives the full analysis + evolution         │
  │  history. It reads/writes workspace files via bash tool.    │
  │  Graduated scope: intensity scales with performance.        │
  ├─────────────────────────────────────────────────────────────┤
  │  Phase 7 — Sanity Check                                     │
  │  Deterministic post-mutation fixes (prompt length,          │
  │  skill format, broken references).                          │
  ├─────────────────────────────────────────────────────────────┤
  │  Phase 8 — Meta-Evolution                                   │
  │  Record what changed and its impact. Detect stagnation.     │
  │  Roll back to best-known state if no improvement for        │
  │  N cycles.                                                  │
  └─────────────────────────────────────────────────────────────┘
          │
          ▼
  Mutated Workspace → Agent reloads → Next solve cycle
```

### Illustrative Example

Suppose the agent scores 68% on a batch. The analysis reveals:

```
Claim-type breakdown:
  provide_fact    — 85% pass rate  ✓ strong
  calculate       — 40% pass rate  ✗ weak
  entity_property — 90% pass rate  ✓ strong

Task-type breakdown:
  single_fact       — 90%  ✓
  multi_requirement — 55%  ✗ weak
  search_iteration  — 70%  ~

Failure patterns detected:
  multi_requirement_miss  — 5 tasks (score ~0.5, agent drops 2nd requirement)
  wrong_entity_targeting  — 3 tasks (score 0.0, agent answered about wrong entity)

Judge feedback:
  missing_data — 8 occurrences ("agent did not provide the calculated difference")
  wrong_entity — 3 occurrences ("agent returned data for a different repository")
```

The engine responds with:

1. **Auto-seeds** a `multi-requirement-handler` skill (threshold: ≥3 multi-req misses) and an `entity-verification` skill (threshold: ≥2 wrong-entity failures).
2. **Auto-seeds** a `calculate-handler` skill because the `calculate` claim type is below 50%.
3. **Graduated scope** selects "targeted" intensity (68% < 70%) → both prompt and skills are eligible for LLM mutation.
4. **Evolver LLM** receives the full analysis, sees the evolution history ("last cycle we added X and it helped by +3%"), and makes targeted edits.
5. **Meta-evolution** records the changes. If the next 5 cycles show no improvement, the engine rolls back to the best git-tagged state.

---

## Core Mechanisms

### 1. Per-Claim Analysis

Instead of just pass/fail per task, the analyzer breaks benchmark feedback into individual claims and classifies each one:

| Claim Type | Keywords | Example |
|---|---|---|
| `provide_fact` | provide, what is, get, return | "What is the creation date of repo X?" |
| `calculate` | difference, sum, calculate, how many | "How many years between X and Y?" |
| `compare` | compare, difference between, versus | "Compare stars of repo A vs repo B" |
| `aggregate` | total, all, list all, every | "List all open issues" |
| `identify_entity` | identify, find, which, who | "Which user created this repo?" |
| `entity_property` | status, date, name, owner | "What is the status of issue #42?" |
| `chain` | then, after, using, next | "Get X, then use it to find Y" |
| `conditional` | if, when, where, in case | "If repo is public, get its stars" |

Each claim type gets its own pass rate. The weakest types drive evolution priorities.

### 2. Task-Type Stratification

Tasks are classified by input text patterns so the engine knows which categories need help:

| Task Type | Signals | Complexity |
|---|---|---|
| `single_fact` | "what is", "when was", "who is" | Low |
| `multi_requirement` | " and ", " also ", bullet points | Medium |
| `search_iteration` | "find", "search for", "all", "list" | Medium-High |
| `comparison` | "compare", "difference between", "vs" | Medium |
| `action` | "create", "update", "delete", "send" | Medium |
| `calculation` | "calculate", "compute", "sum", "total" | Low-Medium |

### 3. Judge Feedback Mining

LLM judge justifications are scanned for recurring patterns:

- `missing_data` — "not mention", "missing", "does not include"
- `wrong_entity` — "wrong", "different", "incorrect", "not the"
- `partial_answer` — "partial", "incomplete", "only provides"
- `calculation_error` — "incorrect calculation", "wrong number"
- `wrong_format` — "format", "structure", "not formatted"

High-frequency patterns become explicit evolution targets.

### 4. Failure Pattern Detection

The engine detects four systematic failure patterns:

| Pattern | Signal | Suggested Fix |
|---|---|---|
| `multi_requirement_miss` | Score ~0.5 on tasks with "and"/"also" | Structured requirement extraction protocol |
| `wrong_entity_targeting` | Score 0.0 despite long output | Early entity verification checkpoint |
| `near_miss` | Score ~0.7 (most claims pass, one missed) | Strengthen final verification |
| `missed_code_opportunity` | 15+ tool calls, no code execution, failed | Lower code execution threshold |

### 5. Auto-Seeded Skills

When patterns cross thresholds, skills are injected before the LLM runs:

- **≥3 multi-requirement misses** → seeds `multi-requirement-handler` (requirement extraction + per-requirement tracking protocol)
- **≥2 wrong-entity failures** → seeds `entity-verification` (mandatory identity checkpoint after first tool call)
- **Claim type below 50% pass rate** → seeds `{type}-handler` with type-specific guidance (e.g., `calculate-handler` teaches the agent to show explicit calculations)

### 6. Graduated Evolution Scope

The engine scales mutation intensity to current performance:

| Pass Rate | Intensity | What Changes |
|---|---|---|
| ≥90% + stable (2+ cycles) | None | Skip LLM evolution entirely |
| ≥90% (unstable) | Minimal | Skills only |
| ≥85% | Minimal | Skills only if a specific claim type is below 60% |
| ≥70% | Targeted | Skills + prompt (only if failure patterns require it) |
| <70% | Comprehensive | Prompt + skills + memory |

This prevents over-mutation when the agent is already performing well.

### 7. Meta-Learning

The engine maintains a rolling history of the last 10 evolution cycles, recording what changed and its impact. This history is fed to the evolver LLM so it can:

- Build on changes that improved performance
- Avoid repeating changes that hurt performance
- Understand the trajectory of improvement

### 8. Stagnation Detection & Rollback

If no improvement ≥ `improvement_threshold` (default: 2%) occurs for `stagnation_window` cycles (default: 5), and either:
- The agent has degraded >5% from its best, or
- The best score was below 90%

…the engine rolls back to the best-known git-tagged state and resets the counter.

---

## Module Structure

---

## Code Execution (`execute_code`)

The solver agent has access to an `execute_code` tool — a sandboxed Python environment that can call any MCP tool programmatically via `call_tool(name, args)`. This is central to how the agent handles complex tasks, and the evolution engine actively monitors and optimizes its usage.

### What It Is

`execute_code` accepts a `code` string and runs it in a restricted Python sandbox. Inside the sandbox the agent can:

- Call any MCP tool via `call_tool(name, args)` → returns a string result
- Use `print()` to return output to the LLM context
- Import a whitelisted set of modules: `json`, `re`, `math`, `datetime`, `collections`, `itertools`, `functools`

The sandbox enforces a 200-call safety limit and a 120-second timeout. Output is truncated to 8,000 characters.

### Why It Matters

Without `execute_code`, every tool call flows through the LLM context window — the agent calls a tool, reads the result, reasons about it, calls the next tool, and so on. For tasks that require searching, iterating, or chaining many calls, this is slow and eats context.

With `execute_code`, the agent writes a Python loop that calls tools directly. Intermediate results stay in the execution environment and never enter the LLM context. Only the final `print()` output comes back.

```
Without execute_code (15 round-trips):
  LLM → tool_call → result → LLM → tool_call → result → LLM → ... → answer

With execute_code (1 round-trip):
  LLM → execute_code("""
      for candidate in candidates:
          result = call_tool("search", {"query": candidate})
          if "found" in result:
              print(result)
              break
  """) → answer
```

### When the Agent Should Use It

| Scenario | Use `execute_code`? |
|---|---|
| Task needs 1–2 simple tool calls with known parameters | No — direct calls |
| Task requires searching/iterating over multiple values | Yes |
| Task chains 3+ tool calls where output feeds into the next | Yes |
| Task needs to filter or aggregate large result sets | Yes |
| A tool call fails and the agent wants to retry with variations | Yes |
| Agent needs to reason carefully about each intermediate result | No — direct calls |

### How the Evolution Engine Optimizes It

The adaptive analyzer tracks code execution usage across every batch:

- **`tasks_used_code`** / **`tasks_no_code`** — how many tasks used `execute_code`
- **`code_pass_rate`** vs **`no_code_pass_rate`** — pass rate with and without code execution
- **`missed_opportunities`** — tasks that made 15+ direct tool calls, failed, and never used `execute_code` (strong signal the agent should have written a loop)
- **`effective_patterns`** — tasks where code execution was used and the agent passed

When the engine detects missed opportunities, it:

1. Flags the `missed_code_opportunity` failure pattern
2. Reports it to the evolver LLM with specific task IDs
3. On workspace preparation, patches the system prompt with code execution guidance and seeds a `code-execution-patterns` skill that teaches the agent when and how to use `execute_code`

### Workspace Preparation

When `AdaptiveEvolveEngine.prepare_workspace()` is called on a fresh workspace, it:

1. Patches `prompts/system.md` with a code execution section (if not already present)
2. Seeds a `skills/code-execution-patterns/SKILL.md` with usage patterns (search loops, tool chaining, retry with fallbacks)

This ensures the agent knows about `execute_code` from the first solve cycle, before any evolution has occurred.

---

## Module StructureaptiveEvolveEngine` — main engine implementing the `EvolutionEngine` interface |
| `analyzer.py` | `AdaptiveAnalyzer` — multi-layer analysis (claims, task types, judge feedback, failure patterns) |
| `prompts.py` | Prompt construction for the evolver LLM with claim/task breakdowns and evolution history |
| `__init__.py` | Public API exports |

---

## Key Classes

### `AdaptiveEvolveEngine`

The main engine. Implements both the `EvolutionEngine.step()` interface (for use inside the Evolver loop) and a standalone `evolve()` method.

```python
from agent_evolve.algorithms.adaptive_evolve import AdaptiveEvolveEngine

engine = AdaptiveEvolveEngine(
    config=config,
    llm=llm_provider,                  # optional, auto-created from config
    improvement_threshold=0.02,         # minimum improvement to reset stagnation counter
    stagnation_window=5,                # cycles without improvement before rollback
    memory_cap=15,                      # max memory entries
)
```

### `AdaptiveAnalyzer`

Composes four sub-analyzers into a single `AdaptiveAnalysisResult`:

- `ClaimAnalyzer` — classifies claims by type and tracks per-type pass rates
- `TaskTypeDetector` / `TaskTypePerformanceTracker` — detects and tracks task types
- `JudgeFeedbackMiner` — extracts recurring failure reasons from judge justifications
- `FailurePatternDetector` — identifies systematic failure patterns

```python
from agent_evolve.algorithms.adaptive_evolve import AdaptiveAnalyzer

analyzer = AdaptiveAnalyzer()
result = analyzer.analyze(observations, base_analysis, code_stats)

# result.weakest_claim_types  → [("calculate", 0.40), ("compare", 0.55)]
# result.failure_patterns     → [FailurePattern(pattern_name="multi_requirement_miss", ...)]
# result.evolution_recommendations → ["Create skill for 'calculate' claims", ...]
```

### `AdaptivePromptConfig`

Controls prompt generation and evolver LLM constraints:

```python
from agent_evolve.algorithms.adaptive_evolve import AdaptivePromptConfig

cfg = AdaptivePromptConfig(
    prompt_max_chars=4000,              # system prompt length limit
    skill_max_chars=2000,               # per-skill length limit
    max_skills=15,                      # maximum number of skills
    include_claim_details=True,         # show per-claim breakdowns to evolver
    include_judge_patterns=True,        # show judge feedback patterns
    include_task_type_stats=True,       # show task-type performance
    include_evolution_history=True,     # show what worked/didn't in past cycles
)
```

---

## Usage

### Inside the Evolver loop

```python
import agent_evolve as ae

evolver = ae.Evolver(
    agent="mcp-atlas",
    benchmark="mcp-atlas",
    engine=AdaptiveEvolveEngine(config),
)
results = evolver.run(cycles=10)
```

### Standalone evolution pass

```python
engine = AdaptiveEvolveEngine(config)
result = engine.evolve(workspace, observation_logs, evo_number=3)

print(result["pass_rate"])           # 0.794
print(result["failure_patterns"])    # ["multi_requirement_miss", "near_miss"]
print(result["weakest_claim_types"]) # {"calculate": 0.40, "compare": 0.55}
print(result["rejected"])            # False (True if rolled back due to stagnation)
```

### Scripts to run examples
```python

# Adaptive evolve (with evolution)
uv run python examples/mcp_examples/adaptive_evolve_all.py \
    --solver-model us.anthropic.claude-opus-4-6-v1 \
    --evolver-model us.anthropic.claude-opus-4-6-v1 \
    --judge-model us.anthropic.claude-sonnet-4-20250514-v1:0 \
    --region us-west-2 \
    --env-file .env \
    --docker-image ghcr.io/scaleapi/mcp-atlas:latest \
    --limit 500 \
    --batch-size 30

# Baseline (no evolution)
SOLVER_MODEL=us.anthropic.claude-opus-4-6-v1 \
JUDGE_MODEL=us.anthropic.claude-sonnet-4-20250514-v1:0 \
LIMIT=500 BATCH_SIZE=30 WORKERS=5 \
bash examples/mcp_examples/run_adaptive_evolve_baseline.sh
```
