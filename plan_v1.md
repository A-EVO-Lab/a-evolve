# UnifiedEngine + Rule-Based Controller (Phase 1)

## Goal Description

**Conceptual framing.** The four existing A-Evolve evolution engines all run the same high-level loop: `observe → update → verify`. Their real differences are confined to three axes: (1) what feedback/evidence is available, (2) what update operators are appropriate, (3) what artifact scope may be edited. Phase 1 delivers a **true unification** along all three axes: a shared **atomic action space** — `Reader` classes for evidence, `Operator` classes for update, `Verifier` classes for verify — plus a **rule-based controller** that emits per-benchmark recipes composing these atoms. `UnifiedEngine.step()` executes whatever recipe the controller emits directly. No runtime delegation to opaque legacy engine classes.

Each benchmark's existing algorithm becomes a specific recipe, not a monolith. MCP-Atlas's "adaptive evolve" is a recipe of `[ClaimReader, PatternDetector, ClaimTypeAnalyzer] + [FixHallucinations, AutoSeedSkills, LLMBashEvolve, SanityCheck]` with `NoVerify` (matching the legacy `EvolutionLoop.step()` path; `StagnationRollback` is registered but only used by the standalone `evolve()` API, out of Phase 1 scope). SWE's "guided synth" is a recipe of `[ProposalReader] + [WriteEpisodicMemory, SkillCurator]`. Terminal-Bench and SkillBench are minimal recipes around `LLMBashEvolve`. The controller picks readers/operators/verifiers by rule from a shared registry; the engine executes them in order.

**Atomic interface.** Every atom has a small uniform contract (all positional args with `state` and `context` present from day 1 to avoid Phase 2 signature churn):
- `Reader.read(observations, workspace, history, config, context, state) -> dict`  (populates its slot in `EvidenceContext`; `context` is read-only access to upstream readers' output; `state` is cross-cycle per-atom state)
- `Operator.apply(workspace, context, scope, state) -> MutationReport`     (mutates the workspace under `scope` restrictions; reads `context`; mutates `state`)
- `Verifier.check(workspace, context, reports, trial, history, state) -> Verdict` (accept/rollback; may mutate `state` to track rolling metrics like `_best_pass_rate`)

**Operators are independent reimplementations. No thin wrappers over legacy code.** Each atom's implementation lives entirely under `agent_evolve/algorithms/unified/` and contains no `import` from any legacy engine module (`algorithms/adaptive_evolve/`, `algorithms/adaptive_skill/`, `algorithms/guided_synth/`, `algorithms/skillforge/`). Atoms MAY reference legacy code for specification — copy its logic, match its behavior bit-for-bit, even reproduce identical comments — but they MUST NOT call into it at runtime. This guarantees the two frameworks (legacy engines + unified) are **physically decoupled**: a future change to `adaptive_evolve/engine.py` has zero effect on any unified atom, and vice versa. Differential tests (AC-8) enforce behavior equivalence across the physical duplication.

**Scope target.** Phase 1 runs one unified codebase across SWE-bench, MCP-Atlas, Terminal-Bench 2.0, and SkillsBench. Each benchmark's observable behavior (workspace diff + persisted metadata + verifier outputs) through the real `EvolutionLoop` must remain equivalent to the corresponding legacy engine's `step()` running directly on the same input. Legacy engine classes (`AdaptiveEvolveEngine`, `GuidedSynthesisEngine`, `AdaptiveSkillEngine`, `AEvolveEngine`) remain **unchanged** and continue to operate exactly as they do today. Users who import them keep their current behavior. The unified framework is a **separate, independent implementation** that shares no runtime code with legacy — the two frameworks are physically decoupled.

**Explicitly out of scope for Phase 1.** LLM-agent controller (Phase 2), grind/same-task-retry `EvolutionLoop` variant (Phase 2+), pre-solve hooks, task-specific skill generation operators, refactoring or deprecating the legacy engine classes.

**Controller logic.** `RuleBasedController.plan(regime, capability, config) -> Plan` is a small deterministic decision table mapping evidence regime to a concrete recipe. The controller emits **only** recipes composed from the shared registry. There is no `legacy_engine` escape hatch — a benchmark with no matching rule receives a conservative default recipe (`[PassFailReader], [LLMBashEvolve], NoVerify`) with a logged warning.

## Acceptance Criteria

- **AC-1**: `FeedbackCapability` is a frozen dataclass declaring each benchmark's evidence profile. The 4 in-repo benchmarks override `feedback_capability` on their `BenchmarkAdapter` subclass. The field set is: `has_pass_fail`, `has_per_claim`, `has_per_test`, `has_partial_score`, `solver_may_propose`, `judge_available`. No `default_legacy_engine` field — routing is derived purely from capability + regime + config.
  - Positive Tests:
    - `McpAtlasBenchmark().feedback_capability.has_per_claim == True`
    - `SweVerifiedMiniBenchmark().feedback_capability.solver_may_propose == True`
    - `Terminal2Benchmark().feedback_capability.solver_may_propose == True`
    - `SkillBenchBenchmark().feedback_capability.has_partial_score == True`
    - A custom `BenchmarkAdapter` without override returns a conservative default
    - `FeedbackCapability` instances are immutable (`FrozenInstanceError` on mutation)
  - Negative Tests:
    - A declared capability that sets `has_per_claim=True` while the benchmark's `evaluate()` does not populate `feedback.raw.per_claim` is caught by a static consistency test (optional; minimum scope may skip)

- **AC-2**: `RegimeTag` is produced by a pure function `detect_regime(capability, observations, workspace, config) -> RegimeTag` that reads only from the provided args. Runtime detection respects the existing `config.trajectory_only` flag AND infers feedback masking from observation shape (pattern from `guided_synth/engine.py:466`: `is_masked = score==0.0 AND not feedback.detail AND not feedback.success`). `pass_rate` is `None` when pass/fail signal is masked unless judge proxy is available.
  - Positive Tests:
    - MCP-Atlas observations with `per_claim` present → `regime.has_per_claim=True`
    - Same observations with `config.trajectory_only=True` → `regime.has_per_claim=False`, `regime.pass_rate=None`
    - SWE observations with `trajectory._skill_proposal` set → `regime.has_solver_proposal=True`
    - `detect_regime` is deterministic (equal inputs → equal output, fuzzed)
  - Negative Tests:
    - Capability claiming `solver_may_propose=True` without any `_skill_proposal` in observations does NOT set `regime.has_solver_proposal=True`
    - `detect_regime` reads `benchmark.__class__.__name__` or other identity signals — enforced by mock-based backdoor-access test

- **AC-3**: Three module-level registries (`READERS`, `OPERATORS`, `VERIFIERS`) map string names to classes/instances. Each atom satisfies its corresponding `Reader` / `Operator` / `Verifier` protocol with the exact signatures declared in the Goal Description.
  - Positive Tests:
    - `READERS['ClaimReader']` exists and implements `.read(observations, workspace, history, config, context, state)` returning a dict
    - `OPERATORS['LLMBashEvolve']` exists and implements `.apply(workspace, context, scope, state)` returning a `MutationReport`
    - `VERIFIERS['StagnationRollback']` exists and implements `.check(workspace, context, reports, trial, history, state)` returning a `Verdict`
    - The Phase 1 minimum set is registered: readers `{PassFailReader, ClaimReader, ProposalReader, DraftReader, TrajectoryCompressor, PatternDetector, ClaimTypeAnalyzer, ScoreCurveReader}`; operators `{FixHallucinations, AutoSeedSkills, LLMBashEvolve, WriteEpisodicMemory, SkillCurator, SanityCheck}` (note: `PruneMemory` is not a separate Phase 1 operator — see AC-4); verifiers `{NoVerify, StagnationRollback}` (both registered; `StagnationRollback` is usable by future recipes but not part of the loop-path Phase 1 recipes)
    - Looking up an unknown name raises `KeyError` with a clear message
    - Re-registering an existing name raises `ValueError` (overwrite forbidden)
  - Negative Tests:
    - An atom advertised in a recipe but missing from the registry produces a silent no-op (should raise)
    - An atom class that does not match its protocol signature is registered without error (should raise at registration time via `typing.runtime_checkable` check)

- **AC-4**: `RuleBasedController.plan(regime, capability, config) -> Plan` is a deterministic pure function that emits a `Plan` containing only string names resolvable in the three registries. Each Plan records `readers`, `operators`, `verifier`, `artifact_scope`, and `reason_trace`. No `legacy_engine` field. The Phase 1 recipes are designed to match each benchmark's canonical `EvolutionLoop`-driven step path (which is `engine.step()`, NOT the standalone `engine.evolve()` API).
  - Positive Tests:
    - MCP-Atlas capability + `regime.has_per_claim=True` → recipe `{readers: [PassFailReader, ClaimReader, PatternDetector, ClaimTypeAnalyzer, ScoreCurveReader], operators: [FixHallucinations, AutoSeedSkills, LLMBashEvolve, SanityCheck], verifier: NoVerify, artifact_scope: {prompts: rw, skills: rw, memory: append}}`. Note: `FixHallucinations` operator internally handles memory pruning (matching the legacy `_apply_auto_corrections` behavior at `adaptive_evolve/engine.py:414` which calls `_prune_memory` as a nested step). `StagnationRollback` is a no-op in `EvolutionLoop.step()` path because legacy `AdaptiveEvolveEngine.step()` has no stagnation check (only `evolve()` standalone does) — Phase 1 matches the loop path.
    - SWE capability + `regime.has_solver_proposal=True` → recipe `{readers: [PassFailReader, ProposalReader], operators: [WriteEpisodicMemory, SkillCurator], verifier: NoVerify, artifact_scope: {skills: rw, memory: append}}`
    - Terminal-Bench capability + `regime.has_drafts=True` → recipe `{readers: [PassFailReader, DraftReader, TrajectoryCompressor], operators: [LLMBashEvolve], verifier: NoVerify, artifact_scope: {skills: rw, prompts: rw}}`
    - SkillBench capability + no drafts/proposals → recipe `{readers: [PassFailReader, TrajectoryCompressor], operators: [LLMBashEvolve], verifier: NoVerify, artifact_scope: {skills: rw}}`
    - Masked MCP-Atlas (config.trajectory_only=True) → recipe degrades to `{readers: [TrajectoryCompressor, LLMJudgeReader], operators: [LLMBashEvolve], verifier: NoVerify, artifact_scope: {skills: rw}}` (claim-based readers drop out; auto-seed/pattern-detector drop out because they need per_claim)
    - `reason_trace` is non-empty and includes the matched rule name
  - Negative Tests:
    - Plan contains a reader/operator/verifier name NOT present in the registry
    - Plan contains `legacy_engine` field (should not exist in Phase 1)
    - Controller raises on valid capability (should always produce some recipe, never raise)
    - MCP-Atlas recipe includes a `PruneMemory` atom (should be internal to `FixHallucinations` to match legacy ordering; a separate `PruneMemory` atom would change observable memory-write timing)

- **AC-5**: `UnifiedEngine.step()` executes the emitted recipe directly: runs each reader in order to populate `EvidenceContext`; runs each operator in order under `artifact_scope` restrictions; runs the verifier; rolls back if verdict requires. There is no delegation to legacy engine `step()` methods.
  - Positive Tests:
    - Execution order is readers → operators → verifier, observable by a spy intercepting each atom's method call
    - Each reader's return dict lands in `EvidenceContext.entries` keyed by the reader's class name
    - Each operator receives the full `EvidenceContext` and `artifact_scope`; operators that attempt to write outside their allowed scope raise `ScopeViolationError`
    - A failing operator (e.g., raises) does not prevent later operators from running if `continue_on_error=True` is configured; otherwise the exception propagates
  - Negative Tests:
    - Any module under `agent_evolve/algorithms/unified/` imports anything from `agent_evolve/algorithms/adaptive_evolve/`, `agent_evolve/algorithms/adaptive_skill/`, `agent_evolve/algorithms/guided_synth/`, or `agent_evolve/algorithms/skillforge/` (static check: `grep -r "from agent_evolve.algorithms.\(adaptive_evolve\|adaptive_skill\|guided_synth\|skillforge\)" agent_evolve/algorithms/unified/` must return 0 matches). This enforces physical decoupling required by the DEC-2 resolution.
    - `EvidenceContext` is populated out of order (e.g., operator runs before readers)
    - `artifact_scope` is ignored (test writes to a disallowed path succeed without error)

- **AC-6**: Stateful atoms hold their state themselves; `UnifiedEngine` maintains a per-slot state dict and passes each atom its own slot on every `step()`. Phase 1 uses `dict[str, Any]` keyed by atom name, because no Phase 1 recipe contains the same atom twice. Phase 2 will migrate to `dict[tuple[str, int], Any]` keyed by `(atom_name, ordinal_in_recipe)` to support recipes that use the same operator multiple times with different parameters. The migration path is non-breaking: the Phase 1 dict becomes a special case where `ordinal=0` is implicit.
  - Positive Tests:
    - After 5 cycles with `StagnationRollback` verifier wired into a recipe, the verifier's `state["_best_pass_rate"]` accumulates matching direct-usage of the legacy `_check_stagnation_gate`
    - After 3 cycles with `WriteEpisodicMemory` operator, `episodic.jsonl` contains 3N entries (N = tasks per batch) with monotonically increasing `cycle` values stored in `state["_cycle_count"]`
    - `FixHallucinations` operator's `state["_accumulated_state"]["name_corrections"]` accumulates hallucination mappings across cycles, matching `adaptive_evolve/engine.py:204` behavior
  - Negative Tests:
    - State slots are recreated every cycle, resetting counters
    - Two `UnifiedEngine` instances share one state dict (cross-contamination)
    - An atom reaches into another atom's state slot directly (atoms must access only their own `state` arg)

- **AC-7**: Each `step()` call persists its routing decision and execution trace into `StepResult.metadata`: `unified_regime`, `unified_plan` (with `reason_trace`), `unified_reports` (list of `MutationReport` dicts), and `unified_verdict`. `Observer.collect()` persists these to the batch JSONL.
  - Positive Tests:
    - `unified_plan.operators` lists exactly the operators that ran (same order, same names)
    - `unified_reports[i].operator_name == unified_plan.operators[i]`
    - `unified_verdict.accept` is False when verifier requested rollback
    - `jq '.unified_plan.operators' batch_0001.jsonl` returns the list of operator names
  - Negative Tests:
    - `unified_reports` is empty even though operators ran
    - Operator order in `unified_reports` differs from plan order
    - `unified_verdict` is missing

- **AC-8**: Per-benchmark **recipe equivalence**: for each of the 4 benchmarks, the recipe emitted by `RuleBasedController` for the default regime, executed by `UnifiedEngine`, produces the same observable outcome as running the corresponding legacy engine's `step()` directly. "Observable outcome" = `StepResult.mutated` value, git diff between `pre-evo-N` and `evo-N` tags, and `Observer.collect()`'s persisted batch entry (excluding the new `unified_*` fields). For LLM-driven operators, equivalence is measured under a mocked `LLMProvider` returning deterministic outputs; for deterministic operators, byte-equivalence is required.

  **Canonicalization requirement for prompt-affecting reader outputs.** Any reader whose output is consumed by an LLM operator to build a prompt MUST produce canonicalized output: stable list/dict orderings, `sort_keys=True` when JSON-serialized, fixed float formatting (e.g., `f"{score:.4f}"`). This applies at minimum to `TrajectoryCompressor`, `ClaimReader`, `ClaimTypeAnalyzer`, `PatternDetector`, `ScoreCurveReader`, `DraftReader`, and `ProposalReader`. Without canonicalization, prompt strings differ between cycles and mocked-LLM equivalence tests become flaky.

  - Positive Tests:
    - Captured fixture replay: for a frozen `(workspace, observations, history, trial-spy)` tuple from each benchmark, both `UnifiedEngine.step()` and direct legacy `engine.step()` produce matching diffs
    - Full-loop replay: `EvolutionLoop(..., engine=UnifiedEngine(config, benchmark), ...).run(cycles=1)` produces the same `history.jsonl`, same `batch_XXXX.jsonl` content (modulo `unified_*` fields), same git tags as the legacy run
    - Determinism: running `detect_regime` + `controller.plan` + reader execution twice on identical inputs produces byte-identical prompt strings when fed to `LLMBashEvolve` (canonicalization holds)
  - Negative Tests:
    - A recipe's operator ordering differs from the legacy engine's internal phase ordering and the test fails to catch the drift
    - Mocked-LLM deterministic test passes despite a real operator regression because the mock happens to short-circuit the regressed path
    - Reader output serialization uses Python's default dict ordering (insertion order) rather than sorted keys → prompt strings drift across Python versions or insertion paths

- **AC-9**: Routing is **recipe-stable across a trial by construction** (no special caching needed). Given unchanged `capability` and `config`, `detect_regime` may produce slightly different `RegimeTag` across cycles (e.g., `has_drafts` fluctuating) but the controller's rule table is designed so the emitted recipe is invariant under those fluctuations. If recipe instability is nevertheless observed (diff between cycle N and N+1 recipes), `UnifiedEngine` emits a warning with both recipes printed.
  - Positive Tests:
    - 10-cycle Terminal-Bench trial with varying draft availability: emitted recipes are byte-equal across all 10 cycles
    - 10-cycle MCP-Atlas trial with varying pass rate and pattern detection: emitted recipes are byte-equal (pass_rate informs operators' internal behavior, not recipe structure)
  - Negative Tests:
    - Recipe structure oscillates across cycles (readers list differs)
    - Recipe drift is silent (no warning logged)

- **AC-10**: `config.trajectory_only` is honored by `detect_regime`: when True, all feedback-derived regime flags become False AND `has_solver_proposal` becomes False (solver reflection may itself be feedback-shaped). Observation-shape inference additionally masks feedback-derived flags on a per-observation basis; `has_solver_proposal` is NOT affected by observation-shape inference. Under masking, `regime.pass_rate=None` unless `LLMJudgeReader` ran and produced a proxy; in that case `pass_rate` is the judge proxy.
  - Positive Tests:
    - `config.trajectory_only=True` on any benchmark: all feedback-derived regime flags False; controller emits a trajectory-only recipe (no ClaimReader, no PatternDetector, but includes TrajectoryCompressor + optionally LLMJudgeReader)
    - Externally-masked MCP-Atlas observations: `has_per_claim=False` from observation-shape; `has_solver_proposal` unchanged if the solver still attached proposals
  - Negative Tests:
    - `config.trajectory_only=True` does not mask `has_per_claim`
    - Observation-shape inference incorrectly masks `has_solver_proposal`

- **AC-11** (*upper bound*): `AgentWorkspace` gains a `task_skills_dir` property and 4 I/O methods (`read_task_skill`, `write_task_skill`, `list_task_skills`, `delete_task_skill`), following same snapshot semantics as `skills_dir`. No Phase 1 operator writes there; the primitive unblocks a future `GenerateTaskSkill` operator in Phase 2.
  - Positive Tests:
    - Round-trip: write then read returns the same body
    - List returns the written task id
    - Delete removes only that task's subtree; `skills_dir` unaffected
    - Git snapshot includes `task_skills/` under `pre-evo-N`/`evo-N` tags
  - Negative Tests:
    - `delete_task_skill` affects `skills/`
    - `list_task_skills` returns entries from `skills/`

## Path Boundaries

### Upper Bound (Maximum Acceptable Scope)

The implementation delivers:
- Three protocols (`Reader`, `Operator`, `Verifier`) + three registries (`READERS`, `OPERATORS`, `VERIFIERS`) in `agent_evolve/algorithms/unified/`
- All minimum atoms fully implemented as **independent reimplementations** (each atom's logic lives entirely under `unified/`; no import from any legacy engine module)
- `FeedbackCapability`, `RegimeTag`, `Plan`, `EvidenceContext`, `MutationReport`, `Verdict` as frozen/immutable dataclasses
- `feedback_capability` property on base + 4 concrete benchmarks
- `task_skills_dir` + 4 I/O methods on `AgentWorkspace` (AC-11)
- `UnifiedEngine` that holds per-atom state, executes recipes, persists full trace to `StepResult.metadata`
- Full differential test suite: fixture-level + full-loop per benchmark (AC-8)
- Documentation: `docs/algorithms/unified.md` describing the action-space, the atom catalog, per-benchmark recipes, and the decoupling contract with legacy engines
- Static import-ban test (AC-5) enforced in CI

### Lower Bound (Minimum Acceptable Scope)

The implementation delivers:
- The three protocols + three registries
- Minimum atom set registered (per AC-3), each implemented **independently under `unified/`**: no atom imports from `adaptive_evolve`, `adaptive_skill`, `guided_synth`, or `skillforge` modules. Atoms may reproduce legacy logic bit-for-bit, even with identical inline comments, but the code physically lives in the unified tree.
- `UnifiedEngine` executes recipes directly; no runtime import of any legacy engine class or module
- `FeedbackCapability` on all 4 benchmarks
- `RuleBasedController` emits the 4 default recipes + at least 1 masking-induced degraded recipe
- Per-benchmark recipe equivalence test (AC-8) on at least MCP-Atlas (highest-state-risk) with a mocked LLM; other benchmarks can have fixture tests as stretch
- Static import-ban test (AC-5)

**Legacy engine classes are NOT modified** — they continue to exist and work exactly as today. The unified framework is a separate, parallel implementation. No internal refactor of `AdaptiveEvolveEngine` / `GuidedSynthesisEngine` / `AdaptiveSkillEngine` / `AEvolveEngine`.

**Explicitly NOT in minimum**: AC-11 (`task_skills_dir`), documentation stub.

### Allowed Choices

- **Can use**: `typing.Protocol` for atom interfaces; `dataclass(frozen=True)` for value types; module-level registries (`dict[str, type]` or instances); `functools.cache` where appropriate
- **Can use**: the existing `config.trajectory_only` field on `EvolveConfig` (defined at `agent_evolve/config.py:38`)
- **Can use**: observation-shape inference for feedback masking (same pattern as `guided_synth/engine.py:466`)
- **Can use**: reading legacy engine source code as a specification — copying logic, matching edge cases, even reproducing identical comments — as long as the implementation physically lives under `unified/` and does not import legacy
- **Can use**: adding top-of-file comments in each operator module citing the legacy source file + line range that the operator mirrors (helps reviewers verify equivalence)
- **Cannot use**: any `import` from `agent_evolve/algorithms/adaptive_evolve/`, `agent_evolve/algorithms/adaptive_skill/`, `agent_evolve/algorithms/guided_synth/`, or `agent_evolve/algorithms/skillforge/` inside the `agent_evolve/algorithms/unified/` tree. Enforced by the static grep test in AC-5.
- **Cannot use**: modifying legacy engine source files (`adaptive_evolve/engine.py`, etc.) — they are frozen for Phase 1. Refactoring private methods to module-level, changing signatures, or altering behavior in legacy files is forbidden.
- **Cannot use**: a `legacy_engine` field on `Plan` (escape hatch forbidden)
- **Cannot use**: changing `EvolutionEngine.step()` base signature (would break `meta_harness` / GEPA compatibility)
- **Cannot use**: modifying `EvolutionLoop.run()` logic (out of scope; `SkillBenchEvolutionLoop` already demonstrates the subclassing pattern for loop variants)
- **Cannot use**: adding new `EvolveConfig` fields for feedback masking (`trajectory_only` already exists; observation-shape inference covers the rest)
- **Cannot use**: changing the example script `skillbench_evolve_in_situ_cycle.py` or any code under `examples/`

## Feasibility Hints and Suggestions

### Conceptual Approach

```
agent_evolve/algorithms/unified/
├── __init__.py              # re-exports UnifiedEngine, RuleBasedController, types
├── types.py                 # FeedbackCapability, RegimeTag, Plan, EvidenceContext,
│                            # MutationReport, Verdict, ArtifactMode
├── interfaces.py            # Reader / Operator / Verifier protocols
├── registry.py              # READERS / OPERATORS / VERIFIERS dicts + register()
├── regimes.py               # detect_regime(capability, observations, workspace, config)
├── controller.py            # RuleBasedController.plan(regime, capability, config)
├── engine.py                # UnifiedEngine(EvolutionEngine) — recipe executor
├── readers/                 # one module per reader
│   ├── pass_fail.py
│   ├── claim.py             # reference: adaptive_evolve.analyzer.ClaimAnalyzer
│   ├── proposal.py          # reference: trajectory._skill_proposal contract
│   ├── draft.py             # reference: workspace.list_drafts()
│   ├── trajectory.py        # reference: adaptive_skill.prompts._compress_trajectory
│   ├── judge.py             # reference: adaptive_skill.prompts.judge_trajectories
│   ├── patterns.py          # reference: adaptive_evolve.analyzer.FailurePatternDetector
│   ├── claim_types.py       # reference: adaptive_evolve.analyzer.ClaimAnalyzer
│   └── score_curve.py
└── operators/               # one module per operator
    ├── fix_hallucinations.py  # reference: adaptive_evolve.McpAutoCorrector + _prune_memory
    ├── auto_seed_skills.py    # reference: adaptive_evolve.engine._auto_seed_skills
    ├── llm_bash_evolve.py     # reference: adaptive_skill.engine._run_llm
    ├── write_episodic_memory.py # reference: guided_synth.engine._write_minimal_memory
    ├── skill_curator.py       # reference: guided_synth.engine._execute_curation
    ├── sanity_check.py        # reference: adaptive_evolve.engine._workspace_sanity_check
    └── prune_skills.py        # reference: guided_synth.engine._prune_similar

agent_evolve/algorithms/unified/verifiers/
├── no_verify.py
├── stagnation_rollback.py  # reference: adaptive_evolve.engine._check_stagnation_gate (registered but not in Phase 1 loop recipes)
└── holdout_trial.py         # reference: skillforge/gating.py.GatingStrategy.validate (upper bound only)

# NOTE: "reference" means each unified module is an INDEPENDENT reimplementation.
# No `import` from legacy engine modules — CI enforces this via task25 grep check.
```

**Engine core (conceptual sketch, not prescriptive):**

```python
class UnifiedEngine(EvolutionEngine):
    def __init__(self, config, benchmark):
        self.config = config
        self.capability = benchmark.feedback_capability  # frozen
        self.controller = RuleBasedController()
        # Per-atom state slots, keyed by atom name in Phase 1.
        # Phase 2 will migrate to (name, ordinal) tuples to support
        # recipes that use the same atom twice.
        self._reader_state: dict[str, dict] = {}
        self._operator_state: dict[str, dict] = {}
        self._verifier_state: dict[str, dict] = {}
        self._last_plan: Plan | None = None

    def step(self, workspace, observations, history, trial):
        regime = detect_regime(self.capability, observations, workspace, self.config)
        plan = self.controller.plan(regime, self.capability, self.config)

        # Warn if recipe drifted from the previous cycle.
        if self._last_plan is not None and self._last_plan != plan:
            logger.warning("Recipe drift: prev=%s new=%s", self._last_plan, plan)
        self._last_plan = plan

        context = EvidenceContext()
        for name in plan.readers:
            reader_state = self._reader_state.setdefault(name, {})
            context.entries[name] = READERS[name].read(
                observations, workspace, history, self.config, context, reader_state)

        reports: list[MutationReport] = []
        for name in plan.operators:
            op_state = self._operator_state.setdefault(name, {})
            reports.append(OPERATORS[name].apply(
                workspace, context, plan.artifact_scope, op_state))

        verifier_state = self._verifier_state.setdefault(plan.verifier, {})
        verdict = VERIFIERS[plan.verifier].check(
            workspace, context, reports, trial, history, verifier_state)

        if verdict.rollback:
            self._rollback_workspace()

        return StepResult(
            mutated=any(r.count > 0 for r in reports),
            summary=f"recipe={plan.operators}, verdict={verdict.reason}",
            metadata={
                "unified_regime": asdict(regime),
                "unified_plan": asdict(plan),
                "unified_reports": [asdict(r) for r in reports],
                "unified_verdict": asdict(verdict),
            },
        )
```

**Controller rule table (conceptual):**

```python
def plan(regime, capability, config):
    # 1. Per-claim feedback → adaptive_evolve-style rich recipe
    #    (matches EvolutionLoop step path: FixHallucinations internally handles
    #     memory pruning; no StagnationRollback in loop path — that's only
    #     in the standalone evolve() API)
    if regime.has_per_claim:
        return Plan(
            readers=['PassFailReader', 'ClaimReader', 'PatternDetector',
                     'ClaimTypeAnalyzer', 'ScoreCurveReader'],
            operators=['FixHallucinations', 'AutoSeedSkills', 'LLMBashEvolve',
                       'SanityCheck'],
            verifier='NoVerify',
            artifact_scope={'prompts': 'rw', 'skills': 'rw', 'memory': 'append'},
            reason_trace=['matched: per_claim regime'],
        )

    # 2. Solver proposals → guided_synth-style curator recipe
    if regime.has_solver_proposal and capability.solver_may_propose:
        return Plan(
            readers=['PassFailReader', 'ProposalReader'],
            operators=['WriteEpisodicMemory', 'SkillCurator'],
            verifier='NoVerify',
            artifact_scope={'skills': 'rw', 'memory': 'append'},
            reason_trace=['matched: solver_proposal regime'],
        )

    # 3. Drafts → adaptive_skill-style llm_bash recipe with draft reader
    if regime.has_drafts:
        return Plan(
            readers=['PassFailReader', 'DraftReader', 'TrajectoryCompressor'],
            operators=['LLMBashEvolve'],
            verifier='NoVerify',
            artifact_scope={'skills': 'rw', 'prompts': 'rw'},
            reason_trace=['matched: drafts regime'],
        )

    # 4. Trajectory-only (masked feedback, no drafts) → judge-backed recipe
    if config.trajectory_only or not regime.has_binary_verifier:
        return Plan(
            readers=['TrajectoryCompressor', 'LLMJudgeReader'],
            operators=['LLMBashEvolve'],
            verifier='NoVerify',
            artifact_scope={'skills': 'rw'},
            reason_trace=['matched: trajectory_only regime'],
        )

    # 5. Default: minimal llm_bash recipe (SkillBench fits here)
    return Plan(
        readers=['PassFailReader', 'TrajectoryCompressor'],
        operators=['LLMBashEvolve'],
        verifier='NoVerify',
        artifact_scope={'skills': 'rw'},
        reason_trace=['default: minimal llm_bash recipe'],
    )
```

### Relevant References

- `agent_evolve/engine/base.py:20` — `EvolutionEngine` abstract base
- `agent_evolve/engine/loop.py:44` — `EvolutionLoop` driver (no changes needed)
- `agent_evolve/algorithms/adaptive_evolve/engine.py:167-265` — step() body showing the 8-phase pipeline (source of MCP-Atlas recipe ordering)
- `agent_evolve/algorithms/adaptive_evolve/analyzer.py:396-481` — FailurePatternDetector (source for `PatternDetector` reader)
- `agent_evolve/algorithms/adaptive_evolve/analyzer.py:191-255` — ClaimAnalyzer (source for `ClaimReader` and `ClaimTypeAnalyzer` readers)
- `agent_evolve/algorithms/adaptive_evolve/engine.py:417-462` — `_auto_seed_skills` (source for `AutoSeedSkills` operator)
- `agent_evolve/algorithms/adaptive_evolve/engine.py:525-566` — `_check_stagnation_gate` (source for `StagnationRollback` verifier)
- `agent_evolve/algorithms/adaptive_evolve/engine.py:615-717` — `_workspace_sanity_check` (source for `SanityCheck` operator)
- `agent_evolve/algorithms/guided_synth/engine.py:240-276` — `_write_minimal_memory` (source for `WriteEpisodicMemory`)
- `agent_evolve/algorithms/guided_synth/engine.py:319-431` — `_curate_proposals` + `_execute_curation` (source for `SkillCurator`)
- `agent_evolve/algorithms/adaptive_skill/engine.py:153-185` — `_run_llm` (source for `LLMBashEvolve`)
- `agent_evolve/algorithms/adaptive_skill/prompts.py:129-223` — `_compress_trajectory` (source for `TrajectoryCompressor`)
- `agent_evolve/algorithms/adaptive_skill/prompts.py:247-298` — `judge_trajectories` (source for `LLMJudgeReader`)
- `agent_evolve/algorithms/skillforge/gating.py` — `GatingStrategy` (source for `HoldoutTrial` verifier)
- `agent_evolve/benchmarks/base.py` — `BenchmarkAdapter` ABC (target of `feedback_capability` property)
- `agent_evolve/benchmarks/mcp_atlas/mcp_atlas.py:209-226` — per-claim feedback structure (for `ClaimReader`)
- `agent_evolve/agents/swe/agent.py:279-379` — `_skill_proposal` contract (for `ProposalReader`)
- `agent_evolve/agents/terminal/react_solver.py:256` — draft emission (for `DraftReader`)
- `agent_evolve/config.py:38` — existing `trajectory_only` field
- `agent_evolve/engine/observer.py:29` — `Observer.collect()` (verify persistence of new unified_* fields)

## Dependencies and Sequence

### Milestones

1. **Milestone A — Interface and registry foundation**
   - Step A.1: Define protocols (`Reader`, `Operator`, `Verifier`) in `unified/interfaces.py`
   - Step A.2: Define value types (`FeedbackCapability`, `RegimeTag`, `Plan`, `EvidenceContext`, `MutationReport`, `Verdict`) in `unified/types.py`
   - Step A.3: Implement registry module (`unified/registry.py`) with typed `dict` containers and `register()` decorator
   - Step A.4: Unit tests for protocol conformance (AC-1 partial, AC-3 partial)

2. **Milestone B — Reader extraction** (can parallelize sub-steps)
   - Step B.1: Implement `PassFailReader`, `DraftReader`, `ScoreCurveReader` (trivial)
   - Step B.2: Implement `ProposalReader` (reads `trajectory._skill_proposal`)
   - Step B.3: Independently reimplement `TrajectoryCompressor` under `unified/readers/` (reference: `adaptive_skill.prompts._compress_trajectory`); no import from legacy
   - Step B.4: Independently reimplement `LLMJudgeReader` under `unified/readers/` (reference: `adaptive_skill.prompts.judge_trajectories`); no import from legacy
   - Step B.5: Independently reimplement `ClaimReader`, `ClaimTypeAnalyzer`, `PatternDetector` under `unified/readers/` (reference: `adaptive_evolve.analyzer.*`); no import from legacy
   - Step B.6: Unit tests per reader (AC-2, AC-3)

3. **Milestone C — Operator reimplementation** (legacy source as read-only spec, no modifications to legacy)
   - Step C.1: **For each target legacy method, produce a behavior specification.** Read the legacy source; enumerate every `self.foo` the method touches (config, llm, `_accumulated_state`, `_cumulative_*`, `_cycle_count`, `_best_pass_rate`, etc.); note the method's inputs, outputs, side effects, state dependencies, and exact ordering of mutations. Write this spec as a top-of-file docstring in the forthcoming `unified/operators/<name>.py` module, citing the legacy source file + line range. This is pure analysis — no code change in any file.
   - Step C.2: **Independently reimplement each operator under `unified/operators/`.** The implementation may reproduce legacy logic line-for-line (copy-pasting legacy source as a starting point is fine) but the file physically lives in the unified tree and must not import from legacy modules. State dependencies identified in C.1 become entries in the operator's `state` dict (e.g., `FixHallucinationsOperator.state["name_corrections"]`). Cross-atom shared data flows through `EvidenceContext`.
   - Step C.3: Each operator implements `apply(workspace, context, scope, state)` and returns a `MutationReport`.
   - Step C.4: Implement scope-violation check: operator must declare what it writes (via a class-level `WRITES: set[str]` attribute); `UnifiedEngine` enforces by comparing against `plan.artifact_scope`.
   - Step C.5: Unit tests per operator including state accumulation over multiple cycles (AC-3, AC-6).
   - Step C.6: Static import-ban check in CI: `grep -r "from agent_evolve.algorithms.\(adaptive_evolve\|adaptive_skill\|guided_synth\|skillforge\)" agent_evolve/algorithms/unified/` must return 0 lines (AC-5).

4. **Milestone D — Verifier reimplementation**
   - Step D.1: Implement `NoVerify` (trivial)
   - Step D.2: Independently reimplement `StagnationRollback` under `unified/verifiers/` (reference: `adaptive_evolve.engine._check_stagnation_gate` + rollback via project `VersionControl`); no import from legacy
   - Step D.3: Independently reimplement `HoldoutTrial` under `unified/verifiers/` (reference: `skillforge/gating.py`); no import from legacy [upper-bound only]
   - Step D.4: Unit tests per verifier

5. **Milestone E — Regime detection + controller**
   - Step E.1: Implement `detect_regime()` with `trajectory_only` + observation-shape inference
   - Step E.2: Implement `RuleBasedController.plan()` with 5 rule branches + default (per the sketch)
   - Step E.3: Unit tests for all 4 benchmark default recipes + masking-induced downgrade recipes

6. **Milestone F — UnifiedEngine**
   - Step F.1: Implement `UnifiedEngine.__init__` with per-atom state slots, frozen capability
   - Step F.2: Implement `UnifiedEngine.step()` executing readers → operators → verifier, populating metadata
   - Step F.3: Verify `Observer.collect()` persists the unified fields; patch if needed
   - Step F.4: Smoke test (4 benchmarks × 1 cycle) confirming correct recipe execution

7. **Milestone G — Behavior equivalence validation**
   - Step G.1: Capture frozen fixtures from each benchmark's legacy run (`workspace_snapshot`, `observations`, `history` stub, `trial-spy`)
   - Step G.2: Differential fixture test: `UnifiedEngine.step()` recipe vs direct legacy `engine.step()` on each fixture (mocked LLM where applicable) → assert equivalent workspace diff, metadata, trial-call log
   - Step G.3: Full-loop differential test: `EvolutionLoop.run(cycles=1)` with `UnifiedEngine` vs legacy engine for each benchmark
   - Step G.4: Multi-cycle stability test: 10-cycle MCP-Atlas trial verifies `_best_pass_rate` state accumulation matches legacy

8. **Milestone H — task_skills_dir primitive (upper bound, independent)**
   - Step H.1: Add `task_skills_dir` property + 4 I/O methods to `AgentWorkspace`
   - Step H.2: Round-trip and snapshot test

(Legacy engine refactor is explicitly OUT of Phase 1 scope per DEC-1. Legacy classes are frozen.)

Dependencies: A blocks B/C/D/E. B/C/D can proceed in parallel after A. E depends on A. F depends on A, B, C, D, E. G depends on F. H is independent.

## Task Breakdown

| Task ID | Description | Target AC | Tag | Depends On |
|---------|-------------|-----------|-----|------------|
| task1 | Read existing `BenchmarkAdapter`, `EvolveConfig`, and legacy engines to confirm extraction targets and reimplementation scope; produce the atom list the plan will reproduce | AC-1, AC-3 | analyze | - |
| task2 | Implement `unified/types.py` (dataclasses for FeedbackCapability, RegimeTag, Plan, EvidenceContext, MutationReport, Verdict) | AC-1, AC-4 | coding | task1 |
| task3 | Implement `unified/interfaces.py` with Reader/Operator/Verifier protocols | AC-3 | coding | task2 |
| task4 | Implement `unified/registry.py` with READERS/OPERATORS/VERIFIERS dicts and `register()` decorator | AC-3 | coding | task3 |
| task5 | Add `feedback_capability` property to `BenchmarkAdapter` base + 4 benchmark overrides | AC-1 | coding | task2 |
| task6a | For each target legacy private method, produce a behavior spec (dependency inventory + input/output/side-effect description + ordering notes + line citations). Documented as top-of-file docstring in the forthcoming operator module. Pure analysis task — touches no code. | AC-3, AC-5 | analyze | task1 |
| task6b | Independently reimplement each operator under `unified/operators/`, using the C.1 spec as reference. Copy-paste legacy code as a starting point is allowed; MUST NOT import from any legacy engine module. Physical code lives under `unified/`. Legacy source files are NOT modified. | AC-3, AC-5 | coding | task6a |
| task7 | Implement readers: `PassFailReader`, `DraftReader`, `ProposalReader`, `ScoreCurveReader` (thin/trivial) | AC-3 | coding | task4 |
| task8 | Independently reimplement readers under `unified/readers/`: `TrajectoryCompressor` (reproduces `adaptive_skill.prompts._compress_trajectory`), `LLMJudgeReader` (reproduces `adaptive_skill.prompts.judge_trajectories`). No import from `adaptive_skill`. | AC-3 | coding | task4, task6b |
| task9 | Independently reimplement readers under `unified/readers/`: `ClaimReader`, `ClaimTypeAnalyzer`, `PatternDetector` (reproduce `adaptive_evolve.analyzer.*` classes). No import from `adaptive_evolve`. | AC-3 | coding | task4, task6b |
| task10 | Independently reimplement operators under `unified/operators/`: `FixHallucinations` (reproduces legacy `_apply_auto_corrections` incl. nested prune-memory behavior), `AutoSeedSkills`, `SanityCheck`. `state` slot on each holds per-atom cross-cycle state (e.g., FixHallucinations state carries `name_corrections` dict seeded from `BaseAnalysis` reader output). No import from `adaptive_evolve` module. | AC-3, AC-5, AC-6 | coding | task4, task6b |
| task11 | Independently reimplement operator `LLMBashEvolve` under `unified/operators/` (reproduces legacy `adaptive_skill._run_llm` behavior). Ensure EvidenceContext→prompt serialization uses canonicalized JSON (sort_keys=True, fixed float format) per AC-8. No import from `adaptive_skill` module. | AC-3, AC-5, AC-8 | coding | task4, task6b |
| task12 | Independently reimplement operators under `unified/operators/`: `WriteEpisodicMemory` (state holds `_cycle_count`), `SkillCurator`, `PruneSkills`. No import from `guided_synth` module. | AC-3, AC-5, AC-6 | coding | task4, task6b |
| task13 | Independently reimplement verifiers under `unified/verifiers/`: `NoVerify`, `StagnationRollback` (reproduces legacy `_check_stagnation_gate`; state holds `_best_pass_rate`, `_cycles_without_improvement`, `_best_evo_tag`; uses project `VersionControl` via `history`/`trial` only). Note: `StagnationRollback` is registered for future use but NOT part of Phase 1 loop-path recipes. | AC-3, AC-6 | coding | task4, task6b |
| task14 | Implement `detect_regime()` with trajectory_only + observation-shape inference | AC-2, AC-10 | coding | task2 |
| task15 | Implement `RuleBasedController.plan()` decision table with 5 rule branches + default | AC-4 | coding | task14 |
| task16 | Unit tests for types, registry, all readers, all operators, all verifiers | AC-1, AC-3, AC-5, AC-6 | coding | task7-task13 |
| task17 | Unit tests for detect_regime + RuleBasedController emitting all 5 recipe shapes | AC-2, AC-4, AC-10 | coding | task14, task15 |
| task18 | Implement `UnifiedEngine` with per-atom state slots and recipe executor | AC-5, AC-6, AC-7, AC-9 | coding | task4, task15 |
| task19 | Verify `Observer.collect()` persists `unified_regime`/`unified_plan`/`unified_reports`/`unified_verdict`; patch if needed | AC-7 | coding | task18 |
| task20 | Smoke test: 4 benchmarks × 1 cycle via UnifiedEngine; assert recipe execution + metadata | AC-7 | coding | task18, task19 |
| task21 | Capture frozen fixtures (workspace/observations/history/trial-spy) for each of 4 benchmarks | AC-8 | coding | task18 |
| task22 | Differential fixture test: UnifiedEngine vs legacy engine.step() on each fixture with mocked LLM | AC-8 | coding | task21 |
| task23 | Full-loop differential test: EvolutionLoop.run(cycles=1) with UnifiedEngine vs legacy for each benchmark | AC-8 | coding | task18 |
| task24 | Multi-cycle stability test: 10-cycle MCP-Atlas confirming StagnationRollback state accumulation | AC-6, AC-9 | coding | task18 |
| task25 | Static import-ban check in CI: `grep -r "from agent_evolve.algorithms.\(adaptive_evolve\|adaptive_skill\|guided_synth\|skillforge\)" agent_evolve/algorithms/unified/` returns 0 lines. Fail the build on any match. Enforces the DEC-2 physical decoupling. | AC-5 | coding | task11, task18 |
| task26 | (Upper bound, AC-11) Add `task_skills_dir` + 4 I/O methods to `AgentWorkspace` + snapshot test | AC-11 | coding | - |
| task27 | Codex final review against all AC — special attention to whether the independent reimpl matches legacy behavior on each differential fixture | AC-1..11 | analyze | task22, task23, task24, task25 |
| task28 | (Optional) `docs/algorithms/unified.md` stub explaining action space, atom catalog, per-benchmark recipes, and the DEC-1/DEC-2 decoupling contract | - | coding | task27 |

## Claude-Codex Deliberation

### Agreements

- Phase 1 must deliver real unification, not engine-level routing that merely dispatches to opaque legacy step() methods.
- The observe → update → verify backbone is the right abstraction; atomic Readers / Operators / Verifiers are the concrete representation.
- Unified atoms are **independent reimplementations** that live physically under `unified/` and do not import from any legacy engine module. Legacy source is permitted as a read-only specification. DEC-1/DEC-2 resolve toward maximum decoupling.
- Per-atom state (`StagnationRollback._best_pass_rate`, `WriteEpisodicMemory._cycle_count`) lives on the atom, managed via explicit state dicts passed by `UnifiedEngine`; no shared mutable engine-level state.
- Behavior equivalence is measured through the real `EvolutionLoop` (fixture-level + full-loop differential) on mocked LLM for LLM-driven paths.
- There is no `legacy_engine` escape hatch in Phase 1. The controller emits recipes composed from registered atoms only.

### Resolved Disagreements

- **Engine-level routing vs action-level execution** (the main pivot): resolved in favor of action-level execution. Engine-level routing (delegating `step()` to legacy engine objects) was rejected because it does not satisfy the "unified action space" requirement — each legacy engine remains an opaque monolith. Action-level execution decomposes each legacy engine's step() into atomic Readers/Operators/Verifiers that a controller composes per benchmark. (Subsequent DEC-2 resolution further tightened this to full independent reimplementation; no thin wrappers.)
- **Observation schema normalization** (Codex v1 concern): resolved by having each `Reader` take the uniform `Observation` type and return typed dict entries in `EvidenceContext`. No benchmark-specific dict keys leak into controller logic.
- **adaptive_skill vs skillforge duplication**: the near-duplicate legacy classes are left **unchanged** (DEC-1: legacy frozen). Their shared logic is reproduced once in the `LLMBashEvolveOperator` under `unified/operators/`. Users importing either legacy class continue to get their original behavior; users using `UnifiedEngine` route through the unified atom.
- **SkillBench dual-skill + grind loop scope**: grind loop remains out of scope for Phase 1 (it's a loop-level concern implemented in `examples/skillbench_examples/skillbench_evolve_in_situ_cycle.py` which bypasses `EvolutionLoop`). `task_skills_dir` workspace primitive is AC-11, upper-bound only. The example script's `from agent_evolve.algorithms.skillforge import AEvolveEngine` import continues to resolve to the unchanged legacy class — this is the DEC-1 frozen-legacy guarantee.
- **MCP-Atlas downgrade under masked feedback**: when per-claim is unavailable (externally masked or `config.trajectory_only=True`), the recipe degrades to the trajectory-only recipe (`TrajectoryCompressor + LLMJudgeReader + LLMBashEvolve`), not a cross-routed `adaptive_skill` engine. This is a recipe downgrade, not an engine swap, and is therefore state-safe.
- **Per-trial stickiness**: replaced by recipe stability by construction. The rule table is designed so emitted recipes are invariant within a trial absent capability/config changes. If drift is observed, a warning is logged; no sticky caching layer is needed because atom state is keyed by atom name, not by recipe identity, so atoms retain their state even if the recipe composition changes.
- **`task_skills_dir` scope**: AC-11, upper bound only; may ship as a standalone follow-up.
- **BYOA fallback** (formerly a pending decision): out of Phase 1 scope. The conservative default on `BenchmarkAdapter.feedback_capability` plus the controller's default-recipe fallback keep any hypothetical non-updated adapter working; no BYOA-specific UX work.
- **Feedback-masking config home** (formerly a pending decision): no new `EvolveConfig` field. Use existing `config.trajectory_only` + observation-shape inference.
- **LLM-path test strictness**: mocked `LLMProvider` with deterministic outputs; byte-equivalent workspace diff under the mock. Real-LLM equivalence is not attempted (flaky, expensive).
- **DEC-1 (formerly pending): Phase 1 internal refactor of legacy engine step() bodies**: **resolved — NO**. Legacy engine classes (`AdaptiveEvolveEngine`, `GuidedSynthesisEngine`, `AdaptiveSkillEngine`, `AEvolveEngine`) are frozen for Phase 1. Their `step()` bodies are NOT modified; they continue to operate as today. The unified framework is a parallel, independent implementation. The two frameworks are physically decoupled by construction — a change in one cannot affect the other.
- **DEC-2 (formerly pending): thin wrappers vs full reimplementation**: **resolved — full independent reimplementation, zero runtime coupling**. Unified operators are implemented under `agent_evolve/algorithms/unified/` without importing from any legacy engine module. Legacy source code is a permitted specification (atoms may reproduce legacy logic bit-for-bit, copy-paste is fine), but the physical code lives under `unified/`. A static import-ban test (AC-5 negative test, task25) fails the build on any `import` from `adaptive_evolve`/`adaptive_skill`/`guided_synth`/`skillforge` inside `unified/`. This satisfies the DEC-1 decoupling requirement at the code-dependency level.

### Convergence Status

- Final Status: `converged` after the architectural pivot. Codex pivot-review round produced 5 REQUIRED_CHANGES, all addressed in v5: (1) protocol signatures explicitly include `context` and `state` args on every atom; (2) state slot keying documented with Phase 2 migration path to `(name, ordinal)` tuples; (3) MCP-Atlas recipe corrected — `PruneMemory` stays internal to `FixHallucinations` (matching legacy `_apply_auto_corrections` at `adaptive_evolve/engine.py:414`), verifier is `NoVerify` to match `EvolutionLoop.step()` path (stagnation gate only fires in standalone `evolve()` which is out of Phase 1 scope); (4) canonicalization requirement added to AC-8 for all prompt-affecting reader outputs; (5) dependency-driven extraction mandated — task6 split into task6a (dependency inventory) + task6b (refactor) to prevent mechanical moves of methods that depend on hidden engine state like `_accumulated_state` or `_cumulative_pass`.

## Pending User Decisions

_None. DEC-1 and DEC-2 have been resolved by the user (see Resolved Disagreements). Plan is ready for implementation._

## Implementation Notes

### Code Style Requirements

- Implementation code and comments must NOT contain plan-specific terminology such as "AC-", "Milestone", "Step", "Phase", or similar workflow markers.
- These terms are for plan documentation only, not for the resulting codebase.
- Use descriptive, domain-appropriate naming in code instead (e.g., "regime detection", "recipe", "evidence context", "operator registry", "mutation report", "atomic action space").

--- Original Design Draft Start ---

最后double check一下我们已有的讨论，和准备要做的implementation，看看还会不会有什么遗漏，注意我们phase 1 尽量通过rule-based controller 来决定。
--- Original Design Draft End ---
