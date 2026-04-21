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

Hermetic fixture-based parity tests under `tests/test_unified_differential.py` cover every recipe branch:

| Fixture | Profile | What it pins |
|---|---|---|
| `_mcp_atlas_fixture` | per-claim feedback + multi-requirement patterns | `per_claim` recipe, `FixHallucinations` + `AutoSeedSkills` + `LLMBashEvolve` + `SanityCheck` execution |
| `_swe_fixture` | solver-attached proposal | `solver_proposal` recipe, `WriteEpisodicMemory` + `SkillCurator` execution including episodic memory write and skill curation |
| `_terminal_fixture` | drafts present in workspace | `drafts` recipe, `LLMBashEvolve` with draft reader |
| `_skillbench_fixture` | partial-score, no claims/drafts/proposals | `default` recipe |
| same MCP-Atlas fixture + `trajectory_only=True` | masked MCP-Atlas | degradation to `trajectory_only` recipe |

For each fixture the test asserts:

1. **Regime detection** — every field of `RegimeTag` matches the expected values.
2. **Plan composition** — `Plan.readers`, `Plan.operators`, `Plan.verifier`, `Plan.reason_trace` are byte-equal to the expected tuple.
3. **Engine metadata** — `StepResult.metadata["unified_plan"]` round-trips through `asdict`, `"unified_regime"` reflects detection, `"unified_verdict"` records accept/rollback.
4. **Operator effects** — for deterministic operators, direct filesystem assertions (e.g., the exact episodic.jsonl content after `WriteEpisodicMemory`, the exact `SKILL.md` body after `SkillCurator`). For LLM-driven operators (`LLMBashEvolve`, `SkillCurator`, `LLMJudgeReader`), deterministic `state["mock"]` / `state["mock_curator"]` hooks replace the real provider.
5. **Sidecar persistence** — `evolution/unified_steps.jsonl` is parsed line-by-line and each record is cross-checked against `StepResult.metadata`.
6. **Recipe stability** — repeated `step()` calls with the same capability/config produce byte-equal plans (AC-9).
7. **No `legacy_engine` leakage** — the JSON-serialized metadata for every recipe contains no substring `"legacy_engine"`.

The tests are hermetic. They do not import `strands`, `swebench`, or any HTTP client; they do not touch the network; they stand up `AgentWorkspace` clones against `tmp_path`.

---

## CI gates

| Test file | Ensures |
|---|---|
| `tests/test_unified_scaffolding.py` | types/registry/capability invariants (20 tests) |
| `tests/test_unified_atoms.py` | per-atom behaviour and state (22 tests) |
| `tests/test_unified_controller.py` | regime detection + controller routing (14 tests) |
| `tests/test_unified_engine.py` | end-to-end `step()` + 4-benchmark routing (9 tests) |
| `tests/test_unified_import_ban.py` | static grep-based check: no legacy imports under `unified/` (3 tests) |
| `tests/test_unified_task_skills_isolation.py` | `task_skills_dir` isolation invariants (12 tests) |
| `tests/test_unified_differential.py` | hermetic parity suite per recipe (9 tests) |

At the time of writing, the full unified test suite runs in well under a second (~89 tests, 2 heavy-dep benchmark-capability tests skipped when `strands`/`swebench` are not installed) and the whole project test run stays green.

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
