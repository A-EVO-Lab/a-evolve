# Unified Evolution Engine

`UnifiedEngine` is A-Evolve's Phase 1 attempt at a single evolution algorithm that covers every benchmark. The four original engines (`adaptive_evolve`, `adaptive_skill`, `guided_synth`, `skillforge`) all implement the same high-level loop — `observe → update → verify` — and differ only in (1) the evidence they can consume, (2) the update operators they can invoke, and (3) the artifact scope they are allowed to touch. The unified framework encodes those three axes as a shared atomic action space.

A rule-based controller maps per-benchmark capability + runtime evidence to a *recipe* (an ordered tuple of readers, operators, and a verifier). `UnifiedEngine.step()` executes the recipe directly; there is no runtime delegation to legacy engine classes. Legacy engines remain untouched for backward compatibility (see DEC-1 in `plan_v1.md`), and the unified tree is **physically decoupled** from them (no imports — see DEC-2).

---

## Package layout

```
agent_evolve/algorithms/unified/
├── __init__.py              re-exports UnifiedEngine, RuleBasedController, detect_regime, types
├── types.py                 frozen dataclasses: FeedbackCapability, RegimeTag, Plan, EvidenceContext, MutationReport, Verdict
├── interfaces.py            @runtime_checkable Protocols: Reader, Operator, Verifier + ScopeViolationError
├── registry.py              READERS / OPERATORS / VERIFIERS dicts + register_*/get_* helpers
├── regimes.py               detect_regime(capability, observations, workspace, config)
├── controller.py            RuleBasedController.plan(regime, capability, config) → Plan
├── engine.py                UnifiedEngine(EvolutionEngine) — recipe executor
├── readers/
│   ├── pass_fail.py         PassFailReader
│   ├── draft.py             DraftReader
│   ├── proposal.py          ProposalReader
│   ├── score_curve.py       ScoreCurveReader
│   ├── trajectory.py        TrajectoryCompressor
│   ├── judge.py             LLMJudgeReader
│   ├── claim.py             ClaimReader
│   ├── claim_types.py       ClaimTypeAnalyzer
│   └── patterns.py          PatternDetector
├── operators/
│   ├── fix_hallucinations.py    FixHallucinations (+ nested prune-memory)
│   ├── auto_seed_skills.py      AutoSeedSkills (rule-triggered skill injection)
│   ├── sanity_check.py          SanityCheck (deterministic post-mutation cleanup)
│   ├── llm_bash_evolve.py       LLMBashEvolve (LLM+bash workspace mutation)
│   ├── write_episodic_memory.py WriteEpisodicMemory
│   ├── skill_curator.py         SkillCurator (ACCEPT/REPLACE/MERGE/SKIP)
│   ├── prune_skills.py          PruneSkills
│   └── _seed_skill_templates.py literal skill bodies mirrored from legacy
└── verifiers/
    ├── no_verify.py             NoVerify
    └── stagnation_rollback.py   StagnationRollback (registered, not in Phase 1 recipes)
```

Every module under `unified/` is an independent reimplementation of the behaviour it mirrors. `agent_evolve/algorithms/adaptive_evolve/`, `.../adaptive_skill/`, `.../guided_synth/`, `.../skillforge/` are never imported from anywhere under `unified/`. A CI-enforced grep test (`tests/test_unified_import_ban.py`) fails the build on any violation.

---

## Atomic interfaces

Every atom follows a uniform contract (all positional args — `state` and `context` are present from day 1 to avoid Phase 2 churn).

```python
class Reader(Protocol):
    def read(self, observations, workspace, history, config, context, state) -> dict: ...

class Operator(Protocol):
    def apply(self, workspace, context, scope, state) -> MutationReport: ...

class Verifier(Protocol):
    def check(self, workspace, context, reports, trial, history, state) -> Verdict: ...
```

- `context: EvidenceContext` — a mutable dict populated by readers, consumed (read-only) by operators and the verifier. Downstream readers can see upstream reader output under each reader's registered name.
- `state: dict` — per-atom cross-cycle state. `UnifiedEngine` maintains one dict per atom name and passes each atom its own slot every `step()`. Phase 2 will migrate to `(name, ordinal)` keys to support recipes that use the same atom twice.
- `scope: dict[str, ArtifactMode]` — per-artifact write permission. `"rw"` / `"append"` permit writes; `"ro"` / `"none"` / missing forbid them.

Operators may declare a `WRITES: frozenset[str]` class attribute listing the artifacts they might write. `UnifiedEngine` raises `ScopeViolationError` when a plan grants **none** of an operator's declared writes (operators are responsible for per-artifact fine-grained checks inside `apply()`).

---

## Registries

Three module-level dicts map string names to atom instances:

- `READERS`
- `OPERATORS`
- `VERIFIERS`

Atoms register themselves at import time via `register_reader` / `register_operator` / `register_verifier` decorators. Duplicate registration raises `ValueError`; an instance that fails its protocol `isinstance` check raises `TypeError`. `get_reader` / `get_operator` / `get_verifier` raise `KeyError` with the full list of available names on lookup failure.

Importing `agent_evolve.algorithms.unified` triggers registration of every atom in `readers/`, `operators/`, and `verifiers/`.

---

## Regime detection

`detect_regime(capability, observations, workspace, config) -> RegimeTag` is a **pure function** that reads only from its arguments. The output is a frozen `RegimeTag` with booleans for each available evidence source plus an optional `pass_rate`.

Two masking sources are supported, reproducing the legacy `guided_synth/engine.py:466` pattern without importing from it:

1. **Config masking** — `config.trajectory_only=True` forces every feedback-derived flag to `False` and additionally suppresses `has_solver_proposal` (solver reflection may itself be feedback-shaped).
2. **Observation-shape inference** — a batch whose every observation has `score == 0.0`, empty `detail`, and `success == False` is treated as externally masked. `has_solver_proposal` is **not** affected by this path (a masked feedback is orthogonal to whether the solver still attached a proposal).

Under any masking, `pass_rate` is `None` unless `LLMJudgeReader` runs and produces a proxy.

---

## Controller routing

`RuleBasedController.plan(regime, capability, config) -> Plan` is a deterministic decision table with five mutually exclusive branches:

| Branch | Condition | Recipe | Verifier |
|---|---|---|---|
| per_claim | `regime.has_per_claim` | `[PassFailReader, ClaimReader, ClaimTypeAnalyzer, PatternDetector, ScoreCurveReader]` + `[FixHallucinations, AutoSeedSkills, LLMBashEvolve, SanityCheck]` | `NoVerify` |
| solver_proposal | `regime.has_solver_proposal and capability.solver_may_propose` | `[PassFailReader, ProposalReader]` + `[WriteEpisodicMemory, SkillCurator]` | `NoVerify` |
| drafts | `regime.has_drafts` | `[PassFailReader, DraftReader, TrajectoryCompressor]` + `[LLMBashEvolve]` | `NoVerify` |
| trajectory_only | `config.trajectory_only or not regime.has_binary_verifier` | `[TrajectoryCompressor, LLMJudgeReader]` + `[LLMBashEvolve]` | `NoVerify` |
| default | otherwise | `[PassFailReader, TrajectoryCompressor]` + `[LLMBashEvolve]` | `NoVerify` |

`StagnationRollback` is registered but not used in any Phase 1 recipe — matching the legacy `EvolutionLoop.step()` path, which omits the stagnation gate (that gate only fires in the standalone `AdaptiveEvolveEngine.evolve()` API, which is out of Phase 1 scope).

There is **no** `legacy_engine` field on `Plan`. The controller emits only names resolvable in the three registries.

Per-benchmark routing is summarized here:

| Benchmark | Canonical recipe branch | Notes |
|---|---|---|
| MCP-Atlas | `per_claim` | `feedback_capability.has_per_claim = True` |
| SWE-bench | `solver_proposal` | agent attaches `trajectory._skill_proposal` |
| Terminal-Bench 2.0 | `drafts` | solver writes to `workspace/skills/_drafts/` |
| SkillsBench | `default` | partial-score benchmark, no claims/drafts/proposals |

When masking is applied, any of those benchmarks will downgrade to the `trajectory_only` recipe.

---

## Engine execution

`UnifiedEngine(config, benchmark)` holds:

- `self.capability = benchmark.feedback_capability` (frozen at construction)
- `self.controller = RuleBasedController()`
- `self._reader_state: dict[str, dict]` — per-reader slot
- `self._operator_state: dict[str, dict]` — per-operator slot
- `self._verifier_state: dict[str, dict]` — per-verifier slot
- `self._last_plan: Plan | None` — used to warn on recipe drift

`step()` pseudocode:

```python
def step(self, workspace, observations, history, trial):
    regime = detect_regime(self.capability, observations, workspace, self.config)
    plan = self.controller.plan(regime, self.capability, self.config)

    if self._last_plan is not None and self._last_plan != plan:
        logger.warning("Recipe drift: prev=%s new=%s", ...)
    self._last_plan = plan

    context = EvidenceContext()
    context.entries["__observations__"] = observations

    for name in plan.readers:
        slot = self._reader_state.setdefault(name, {})
        context.entries[name] = READERS[name].read(observations, workspace, history, self.config, context, slot)

    reports = []
    for name in plan.operators:
        slot = self._operator_state.setdefault(name, {})
        _enforce_scope(OPERATORS[name], plan.artifact_scope, name)
        reports.append(OPERATORS[name].apply(workspace, context, plan.artifact_scope, slot))

    v_slot = self._verifier_state.setdefault(plan.verifier, {})
    verdict = VERIFIERS[plan.verifier].check(workspace, context, reports, trial, history, v_slot)

    if verdict.rollback:
        ...

    metadata = {
        "unified_regime": asdict(regime),
        "unified_plan": asdict(plan),
        "unified_reports": [asdict(r) for r in reports],
        "unified_verdict": asdict(verdict),
    }
    self._persist_step_metadata(workspace, metadata, mutated=...)
    return StepResult(mutated=..., summary=..., metadata=metadata)
```

### Why a sidecar file

The default `EvolutionLoop` does not forward `step_result.metadata` to `Observer.collect()`. Rather than modify the shared loop (out of scope per Path Boundaries), `UnifiedEngine` writes its own append-only JSONL log at `<workspace>/evolution/unified_steps.jsonl`. Each line contains a timestamp, the boolean `mutated`, and the four `unified_*` metadata keys. The file is inspectable with `jq`.

### State accumulation

- `WriteEpisodicMemory.state["cycle_count"]` increments per call, mirroring legacy `GuidedSynthesisEngine._cycle_count`.
- `FixHallucinations.state["name_corrections"]` accumulates hallucination mappings across cycles, mirroring legacy `AdaptiveEvolveEngine._accumulated_state["name_corrections"]`.
- `StagnationRollback.state["best_pass_rate"]` tracks the best-observed pass rate, mirroring legacy `_check_stagnation_gate`.

Because state lives in atom-local slots (not shared across atoms), an atom cannot corrupt another atom's state. Shared flow between atoms goes through `EvidenceContext` only.

---

## `task_skills_dir` primitive (AC-11)

`AgentWorkspace` now exposes a sibling to `skills_dir`:

- `workspace.task_skills_dir` — `<root>/task_skills/`
- `workspace.read_task_skill(task_id)` / `write_task_skill` / `list_task_skills()` → `{task_id: SkillMeta}` / `delete_task_skill`

The invariant is **bidirectional isolation** (see `tests/test_unified_task_skills_isolation.py`):

- `list_skills`/`read_skill`/`write_skill`/`delete_skill` never see or touch `task_skills_dir`.
- `list_task_skills`/`read_task_skill`/`write_task_skill`/`delete_task_skill` never see or touch `skills_dir`.

No Phase 1 operator writes to `task_skills_dir`. The primitive is present so a Phase 2 `GenerateTaskSkill` operator can land without breaking any existing invariants. Existing Phase 1 operators (`SanityCheck`, `PruneSkills`, `AutoSeedSkills`) are proven to ignore `task_skills_dir` in the isolation test suite.

---

## Differential test strategy (AC-8)

AC-8 parity is verified at two levels:

### Level 1 — Fixture-level replay

Hermetic fixture-based parity tests under `tests/test_unified_differential.py` and `tests/test_unified_legacy_differential.py` cover every recipe branch.

`tests/test_unified_differential.py` (9 tests) pins unified output against hand-rolled expectations per recipe:

| Fixture | Profile | What it pins |
|---|---|---|
| `_mcp_atlas_fixture` | per-claim feedback + multi-requirement patterns | `per_claim` recipe, `FixHallucinations` + `AutoSeedSkills` + `LLMBashEvolve` + `SanityCheck` execution |
| `_swe_fixture` | solver-attached proposal | `solver_proposal` recipe, `WriteEpisodicMemory` + `SkillCurator` execution including episodic memory write and skill curation |
| `_terminal_fixture` | drafts present in workspace | `drafts` recipe, `LLMBashEvolve` with draft reader |
| `_skillbench_fixture` | partial-score, no claims/drafts/proposals | `default` recipe |
| same MCP-Atlas fixture + `trajectory_only=True` | masked MCP-Atlas | degradation to `trajectory_only` recipe |

`tests/test_unified_legacy_differential.py` (11 tests) goes further: it imports the real legacy engine classes (`AdaptiveEvolveEngine`, `AdaptiveSkillEngine`, `GuidedSynthesisEngine`, `AEvolveEngine`) and co-runs them alongside `UnifiedEngine` on shared fixtures. Both no-op (`NO_PROPOSALS`) and positive-mutation paths are exercised. For LLM-driven engines a Bedrock-compatible mock (`_FakeBedrockProvider`) is injected via `BedrockProvider.__new__(BedrockProvider)` to satisfy `isinstance` checks without loading `boto3`; its `converse_loop` invokes a real bash command (`workspace_bash`) that writes a skill, so both engines produce the same filesystem delta under a real mutation.

`_assert_step_result_parity` covers every `StepResult` field:

1. `mutated` — byte-equal boolean.
2. `stop` — byte-equal boolean (Phase 1 always `False`).
3. `summary` — numeric signals (skills added, total mutations) extracted via engine-specific regex and asserted equal across unified and legacy summaries.
4. `metadata` — a 6-key normalized signal dict (`total_mutations`, `mutating_ops`, `skills_on_disk`, `memory_rows_written`, `verdict_accept`, `verdict_rollback`) is asserted equal between legacy and unified sides, plus a full-dict and exact-key-set drift guard.

### Level 2 — Full-loop replay

`tests/test_unified_fullloop_replay.py` (2 tests) runs the real `EvolutionLoop.run(cycles=1)` twice — once with `UnifiedEngine`, once with a legacy engine (`AEvolveEngine`, the simplest recipe) — using a mock `BaseAgent` + mock `BenchmarkAdapter`. Parity assertions:

- Full-content `history.jsonl` equality (only the `timestamp` field is waived via an explicit `_AUTHORIZED_VOLATILE_FIELDS = frozenset({"timestamp"})` set).
- Full-content `batch_0001.jsonl` equality with the same explicit waiver. `Observer.collect()` is engine-independent (reads only from `Observation`, never from `StepResult.metadata`), so "modulo unified_* fields" reduces to "modulo timestamp" in practice.
- Git tag parity: both the tag SET *and* `git diff pre-evo-1..evo-1` must match. The diff check uses an explicit `_UNIFIED_PATHSPECS_TO_EXCLUDE = (":(exclude)evolution/unified_steps.jsonl",)` list that applies AC-8's "modulo unified_* fields" rule at the file-path level (the sidecar file is literally a `unified_*` artifact by name).

Both fixture-level and full-loop tests are hermetic — no `strands`, `swebench`, `boto3`, or network clients are imported; workspaces are built fresh under `tmp_path`.

---

## CI gates

| Test file | Ensures | Count |
|---|---|---|
| `tests/test_unified_scaffolding.py` | types/registry/capability invariants + AC-1 runtime constructor tests for all 4 benchmarks (hermetic — see below) | 26 |
| `tests/test_unified_atoms.py` | per-atom behaviour, protocol conformance, per-atom state slots, scope enforcement | 22 |
| `tests/test_unified_controller.py` | regime detection + 5 controller recipe branches + Plan exact-field guard + determinism + no-legacy-engine-field | 15 |
| `tests/test_unified_engine.py` | end-to-end `step()` + 4-benchmark routing + AC-7 exact-key-set metadata guard + import audit | 10 |
| `tests/test_unified_import_ban.py` | static grep-based check: no legacy imports under `unified/` | 3 |
| `tests/test_unified_task_skills_isolation.py` | `task_skills_dir` bidirectional-isolation invariants | 12 |
| `tests/test_unified_differential.py` | hermetic parity suite per recipe (fixture-level, expectations vs live UnifiedEngine) | 9 |
| `tests/test_unified_legacy_differential.py` | co-running real legacy engine classes; full `StepResult` parity via 6-key normalized metadata contract + engine-specific summary regex extraction; no-op and positive-mutation paths across all 4 legacy engines | 11 |
| `tests/test_unified_fullloop_replay.py` | real `EvolutionLoop.run(cycles=1)` replay: full-content history.jsonl + batch_0001.jsonl + git-diff parity (modulo `_AUTHORIZED_VOLATILE_FIELDS` and `_UNIFIED_PATHSPECS_TO_EXCLUDE`) | 2 |
| `tests/test_skillbench_setup.py` | SkillBench repo fixture + `SkillBenchBenchmark` load path | 6 |

At the time of writing, the full project test run is **116 passed, 0 skipped, 0 failed** (the two earlier `importorskip`-guarded adapter tests were replaced in R9-R14 with hermetic runtime tests using `sys.modules` stubs — see "AC-1 hermetic runtime tests" below).

### AC-1 hermetic runtime tests

The plan requires every benchmark adapter's `feedback_capability` to be callable via the bare constructor (`McpAtlasBenchmark()`, `SweVerifiedMiniBenchmark()`, `Terminal2Benchmark()`, `SkillBenchBenchmark()`). Some adapter modules pull heavy third-party deps (`strands` for MCP-Atlas; `swebench.harness.*` for SWE-bench) at module load, and some have constructors that read env vars or filesystem state. The `test_unified_scaffolding.py` runtime tests use a consistent pattern:

1. **Stub heavy imports** via `monkeypatch.setitem(sys.modules, ...)` with minimal `_fake_pkg` / `_fake_mod` helpers exposing only the symbols the adapter imports.
2. **Clear env reads** via `monkeypatch.delenv(..., raising=False)` for any env var `__init__` touches (`EVAL_USE_LITELLM` for MCP-Atlas; the 5 `SKILLBENCH_*` vars for SkillBench).
3. **Control filesystem state** — for SkillBench, build a temp repo tree via `_make_skillbench_repo_tree` and set `SKILLBENCH_REPO_ENV` to it. For Terminal2, build a temp challenges tree and `setenv("TB2_CHALLENGES_DIR", ...)` **before** importing so the module-level `DEFAULT_CHALLENGES_DIR` constant binds to the controlled path; use `_fresh_import` to re-execute the module body and re-bind the constant.
4. **Instantiate with the bare `Class()` constructor** (no kwargs) so the `__init__` fallback branches (e.g., `self.challenges_dir = challenges_dir or DEFAULT_CHALLENGES_DIR`) execute against the controlled state.
5. **Assert on the real capability object** — `FrozenInstanceError` on mutation confirms runtime immutability; `benchmark.challenges_dir == str(tmp_challenges)` confirms the bare-constructor fallback landed on the controlled path.

Each adapter also has a supplementary **constructor-variance test** that exercises one non-default argument vector — single counter-example; not a proof of full branch coverage — to guard against `__init__`-time capability mutation.

---

## Non-goals (Phase 1)

The following are deliberately out of scope:

- LLM-agent controller — the controller is strictly rule-based in Phase 1. The action space is identical so Phase 2 can swap in an agent without changing atoms.
- Grind / same-task-retry loops — SkillBench's `examples/skillbench_examples/skillbench_evolve_in_situ_cycle.py` bypasses `EvolutionLoop` and continues to work unchanged.
- Pre-solve hooks / `GenerateTaskSkill` operator — the `task_skills_dir` primitive is in place; the operator itself is Phase 2.
- Refactoring legacy engine step() bodies — the four legacy engine classes are frozen per DEC-1.

---

## Using `UnifiedEngine`

```python
from agent_evolve.algorithms.unified import UnifiedEngine
from agent_evolve.benchmarks.mcp_atlas.mcp_atlas import McpAtlasBenchmark
from agent_evolve.config import EvolveConfig
from agent_evolve.engine.loop import EvolutionLoop
from agent_evolve.agents.mcp.agent import McpAgent

config = EvolveConfig()
benchmark = McpAtlasBenchmark()
agent = McpAgent("./seed_workspaces/mcp")

engine = UnifiedEngine(config, benchmark)
loop = EvolutionLoop(agent, benchmark, engine, config)
result = loop.run(cycles=10)
```

Inspect the per-cycle routing decisions afterwards:

```bash
$ jq '.unified_plan.operators' evolution_workdir/mcp/evolution/unified_steps.jsonl
["FixHallucinations","AutoSeedSkills","LLMBashEvolve","SanityCheck"]
["FixHallucinations","AutoSeedSkills","LLMBashEvolve","SanityCheck"]
...
```
