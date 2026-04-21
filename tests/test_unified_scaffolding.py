"""Round-0 smoke tests for the unified action-space scaffolding.

Covers AC-1 (FeedbackCapability declarations, frozen immutability, default
fallback) and the registration/lookup invariants from AC-3 that do not yet
require any real atoms to be implemented.

Later rounds add per-atom tests and the full differential tests in AC-4/AC-8.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any

import pytest

from agent_evolve.algorithms.unified import (
    EvidenceContext,
    FeedbackCapability,
    MutationReport,
    Plan,
    RegimeTag,
    Verdict,
)
from agent_evolve.algorithms.unified.interfaces import (
    Operator,
    Reader,
    ScopeViolationError,
    Verifier,
)
from agent_evolve.algorithms.unified.registry import (
    OPERATORS,
    READERS,
    VERIFIERS,
    get_operator,
    get_reader,
    get_verifier,
    register_operator,
    register_reader,
    register_verifier,
)
from agent_evolve.benchmarks.base import BenchmarkAdapter
from agent_evolve.types import Feedback


# ── FeedbackCapability ────────────────────────────────────────


def test_feedback_capability_default_is_conservative():
    cap = FeedbackCapability()
    assert cap.has_pass_fail is True
    assert cap.has_partial_score is False
    assert cap.has_per_claim is False
    assert cap.has_per_test is False
    assert cap.solver_may_propose is False
    assert cap.judge_available is True


def test_feedback_capability_is_frozen():
    cap = FeedbackCapability()
    with pytest.raises(FrozenInstanceError):
        cap.has_pass_fail = False  # type: ignore[misc]


def test_plan_is_frozen():
    plan = Plan(
        readers=("PassFailReader",),
        operators=("LLMBashEvolve",),
        verifier="NoVerify",
        artifact_scope={"skills": "rw"},
    )
    with pytest.raises(FrozenInstanceError):
        plan.verifier = "StagnationRollback"  # type: ignore[misc]


def test_regime_tag_is_frozen():
    regime = RegimeTag()
    with pytest.raises(FrozenInstanceError):
        regime.has_per_claim = True  # type: ignore[misc]


# ── BenchmarkAdapter default ──────────────────────────────────


class _DummyBenchmark(BenchmarkAdapter):
    def get_tasks(self, split="train", limit=10):
        return []

    def evaluate(self, task, trajectory):
        return Feedback(success=False, score=0.0, detail="")


def test_base_benchmark_adapter_has_conservative_default_capability():
    """AC-1: A custom BenchmarkAdapter without override returns a conservative default."""
    cap = _DummyBenchmark().feedback_capability
    assert cap == FeedbackCapability()


# ── Registry behaviour ────────────────────────────────────────


class _MinimalReader:
    def read(self, observations, workspace, history, config, context, state):
        return {"hello": "world"}


class _MinimalOperator:
    def apply(self, workspace, context, scope, state):
        return MutationReport(operator_name="minimal", count=0)


class _MinimalVerifier:
    def check(self, workspace, context, reports, trial, history, state):
        return Verdict(accept=True)


@pytest.fixture(autouse=True)
def _isolate_registries():
    """Snapshot/restore process-global registries per test."""
    r_snap = dict(READERS)
    o_snap = dict(OPERATORS)
    v_snap = dict(VERIFIERS)
    try:
        yield
    finally:
        READERS.clear()
        READERS.update(r_snap)
        OPERATORS.clear()
        OPERATORS.update(o_snap)
        VERIFIERS.clear()
        VERIFIERS.update(v_snap)


def test_register_reader_puts_instance_in_dict():
    register_reader("TestReader")(_MinimalReader)
    assert "TestReader" in READERS
    assert isinstance(READERS["TestReader"], _MinimalReader)


def test_register_operator_and_verifier_put_instance_in_dict():
    register_operator("TestOperator")(_MinimalOperator)
    register_verifier("TestVerifier")(_MinimalVerifier)
    assert isinstance(OPERATORS["TestOperator"], _MinimalOperator)
    assert isinstance(VERIFIERS["TestVerifier"], _MinimalVerifier)


def test_register_raises_on_duplicate_name():
    register_reader("DupReader")(_MinimalReader)
    with pytest.raises(ValueError, match="already registered"):
        register_reader("DupReader")(_MinimalReader)


def test_register_rejects_non_protocol_class():
    class _BadReader:
        def something_unrelated(self):
            return None

    with pytest.raises(TypeError, match="does not satisfy"):
        register_reader("BadReader")(_BadReader)


def test_get_reader_raises_with_available_list():
    register_reader("AvailA")(_MinimalReader)
    register_reader("AvailB")(_MinimalReader)
    with pytest.raises(KeyError) as exc:
        get_reader("Missing")
    msg = str(exc.value)
    assert "Missing" in msg
    assert "AvailA" in msg
    assert "AvailB" in msg


def test_get_operator_and_verifier_raise_on_unknown():
    with pytest.raises(KeyError, match="No operator"):
        get_operator("Nope")
    with pytest.raises(KeyError, match="No verifier"):
        get_verifier("Nope")


# ── Protocol runtime check ────────────────────────────────────


def test_minimal_atoms_are_protocol_conformant():
    """AC-3: each minimal stub satisfies its runtime-checkable protocol."""
    assert isinstance(_MinimalReader(), Reader)
    assert isinstance(_MinimalOperator(), Operator)
    assert isinstance(_MinimalVerifier(), Verifier)


# ── Evidence context & mutation report ────────────────────────


def test_evidence_context_starts_empty_and_is_mutable():
    ctx = EvidenceContext()
    assert ctx.entries == {}
    ctx.entries["foo"] = 1
    assert ctx.entries["foo"] == 1


def test_mutation_report_tracks_details():
    report = MutationReport(operator_name="op", count=3, details={"a": 1})
    assert report.operator_name == "op"
    assert report.count == 3
    assert report.details == {"a": 1}


def test_verdict_defaults_accept_with_no_rollback():
    v = Verdict()
    assert v.accept is True
    assert v.rollback is False


def test_scope_violation_error_is_runtime_error_subclass():
    assert issubclass(ScopeViolationError, RuntimeError)


# ── Benchmark capability declarations (lightweight check) ─────


# ── Test-local stubs for heavy-dep modules (hermetic AC-1 runtime tests) ──
#
# AC-1 positive tests require *runtime* instantiation of the benchmark
# adapter classes and direct attribute access on
# ``benchmark.feedback_capability`` (plan text:
# ``McpAtlasBenchmark().feedback_capability.has_per_claim == True``).
#
# The adapter modules eagerly import heavy third-party deps
# (``strands`` / ``strands.models`` via ``agents.mcp.__init__`` for
# MCP-Atlas; ``swebench.harness.*`` for SWE-bench). To keep the tests
# hermetic — no real installs required — we pre-populate ``sys.modules``
# with minimal stub modules that expose exactly the symbols the adapter
# modules import. No other behaviour is stubbed: the capability
# declaration itself runs real production code. ``monkeypatch.setitem``
# ensures the stubs are torn down after each test.


def _fake_pkg(name: str) -> Any:
    """Fake Python package (has ``__path__`` so dotted imports work)."""
    from types import ModuleType
    m = ModuleType(name)
    m.__path__ = []  # type: ignore[attr-defined]
    return m


def _fake_mod(name: str, **attrs: Any) -> Any:
    """Fake Python module with the given attributes attached."""
    from types import ModuleType
    m = ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    return m


def _install_mcp_atlas_stubs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Seed sys.modules so ``agent_evolve.benchmarks.mcp_atlas.mcp_atlas``
    imports without pulling ``strands`` / ``strands.models`` / etc.

    The adapter's ``from ...agents.mcp.key_registry import KeyRegistry``
    triggers ``agents.mcp.__init__`` which would pull the ``strands``
    chain. Pre-populating ``sys.modules['agent_evolve.agents.mcp']`` with
    a stub package skips the real ``__init__``, then the two submodules
    mcp_atlas.py actually imports from (``key_registry``, ``task_filter``)
    are stubbed with the exact symbol names referenced at import time.
    """
    import sys
    monkeypatch.setitem(
        sys.modules, "agent_evolve.agents.mcp", _fake_pkg("agent_evolve.agents.mcp")
    )
    monkeypatch.setitem(
        sys.modules,
        "agent_evolve.agents.mcp.key_registry",
        _fake_mod(
            "agent_evolve.agents.mcp.key_registry",
            KeyRegistry=type("KeyRegistry", (), {}),
            classify_error=lambda *a, **kw: None,
            redact_secrets=lambda x: x,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "agent_evolve.agents.mcp.task_filter",
        _fake_mod(
            "agent_evolve.agents.mcp.task_filter",
            filter_tasks_by_keys=lambda tasks, keys: tasks,
        ),
    )


def _install_swe_stubs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Seed sys.modules so ``swebench.harness.*`` imports resolve to stubs."""
    import sys
    monkeypatch.setitem(sys.modules, "swebench", _fake_pkg("swebench"))
    monkeypatch.setitem(
        sys.modules, "swebench.harness", _fake_pkg("swebench.harness")
    )
    monkeypatch.setitem(
        sys.modules,
        "swebench.harness.test_spec",
        _fake_pkg("swebench.harness.test_spec"),
    )
    monkeypatch.setitem(
        sys.modules,
        "swebench.harness.constants",
        _fake_mod(
            "swebench.harness.constants",
            APPLY_PATCH_FAIL="apply_patch_fail",
            RESET_FAILED="reset_failed",
            TESTS_ERROR="tests_error",
            TESTS_TIMEOUT="tests_timeout",
            SWEbenchInstance=type("SWEbenchInstance", (), {}),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "swebench.harness.grading",
        _fake_mod("swebench.harness.grading", MAP_REPO_TO_PARSER={}),
    )
    monkeypatch.setitem(
        sys.modules,
        "swebench.harness.test_spec.test_spec",
        _fake_mod(
            "swebench.harness.test_spec.test_spec",
            TestSpec=type("TestSpec", (), {}),
            make_test_spec=lambda *a, **kw: None,
        ),
    )


def _fresh_import(module_name: str, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Import (or re-import) a module ignoring any cached sys.modules entry.

    If a previous test partially imported the module against a different
    set of stubs, clear the cache so this test sees the current stubs.
    """
    import sys
    import importlib

    # Drop any cached entry and any cached partial children.
    for cached in [m for m in list(sys.modules) if m == module_name or m.startswith(module_name + ".")]:
        monkeypatch.delitem(sys.modules, cached, raising=False)
    return importlib.import_module(module_name)


def test_mcp_atlas_capability_runtime(monkeypatch):
    """AC-1 positive: runtime constructor + attribute access on MCP-Atlas.

    Matches the plan text verbatim, including the parenthesised
    constructor call:
      ``McpAtlasBenchmark().feedback_capability.has_per_claim == True``

    The adapter's heavy-dep chain (strands/strands.models) is stubbed
    via sys.modules for the duration of the test so the import resolves
    without installing those deps. ``McpAtlasBenchmark()`` itself runs
    the real ``__init__`` body (attribute assignment + env-var check +
    logging) — we verify the constructor path does not mutate or wire
    the capability object away from its declaration, which was the
    exact gap Codex Round 9 review flagged.
    """
    _install_mcp_atlas_stubs(monkeypatch)
    mod = _fresh_import(
        "agent_evolve.benchmarks.mcp_atlas.mcp_atlas", monkeypatch
    )
    McpAtlasBenchmark = mod.McpAtlasBenchmark

    # Real constructor — runs __init__ body. Defaults only; AC-1 says
    # nothing about specific constructor arguments.
    benchmark = McpAtlasBenchmark()
    cap = benchmark.feedback_capability  # real property access

    assert cap.has_per_claim is True  # plan_v1.md AC-1 positive test
    assert cap.solver_may_propose is False  # not overridden → default False
    assert cap.judge_available is True
    assert cap.has_pass_fail is True
    # Frozen dataclass — confirm the runtime object is immutable too.
    with pytest.raises(FrozenInstanceError):
        cap.has_per_claim = False  # type: ignore[misc]


def test_swe_capability_runtime(monkeypatch):
    """AC-1 positive: runtime constructor + attribute access on SWE-bench.

    Matches the plan text verbatim, including the parenthesised
    constructor call:
      ``SweVerifiedMiniBenchmark().feedback_capability.solver_may_propose == True``
    """
    _install_swe_stubs(monkeypatch)
    mod = _fresh_import(
        "agent_evolve.benchmarks.swe_verified_mini.benchmark", monkeypatch
    )
    SweVerifiedMiniBenchmark = mod.SweVerifiedMiniBenchmark

    # Real constructor — runs __init__ body. Defaults only.
    benchmark = SweVerifiedMiniBenchmark()
    cap = benchmark.feedback_capability

    assert cap.has_per_test is True
    assert cap.solver_may_propose is True  # plan_v1.md AC-1 positive test
    assert cap.has_pass_fail is True
    assert cap.judge_available is True
    with pytest.raises(FrozenInstanceError):
        cap.solver_may_propose = False  # type: ignore[misc]


def test_mcp_atlas_constructor_does_not_mutate_capability(monkeypatch):
    """AC-1: calling ``McpAtlasBenchmark`` with a distinct, non-default
    argument vector does not mutate the declared capability.

    Codex Round 9 review flagged the concern that ``__init__`` might
    perform post-processing that changes the capability. This test is
    a *single* counter-example: it constructs with one non-default
    argument vector and confirms the capability property still returns
    the declared fields. It does NOT claim full branch coverage of
    ``__init__`` — one vector cannot prove that, as Codex Round 10
    review correctly pointed out. What it does prove: if any branch
    reachable through this specific vector mutated the capability, the
    test would fail.
    """
    _install_mcp_atlas_stubs(monkeypatch)
    mod = _fresh_import(
        "agent_evolve.benchmarks.mcp_atlas.mcp_atlas", monkeypatch
    )
    benchmark = mod.McpAtlasBenchmark(
        dataset_name="custom/dataset",
        shuffle=False,
        holdout_ratio=0.1,
        eval_model_id="claude-3-5-sonnet-20241022",
        eval_region="us-east-1",
        use_litellm=False,
        concurrency=2,
    )
    cap = benchmark.feedback_capability
    assert cap.has_per_claim is True
    assert cap.solver_may_propose is False
    assert cap.judge_available is True


def test_swe_constructor_does_not_mutate_capability(monkeypatch):
    """AC-1 mirror of the MCP-Atlas constructor-variance test.

    Single counter-example: one non-default argument vector is checked.
    Does not claim full branch coverage of ``__init__``.
    """
    _install_swe_stubs(monkeypatch)
    mod = _fresh_import(
        "agent_evolve.benchmarks.swe_verified_mini.benchmark", monkeypatch
    )
    benchmark = mod.SweVerifiedMiniBenchmark(
        dataset_name="custom/swe",
        repo_filter="django/django",
        shuffle=False,
        holdout_ratio=0.5,
        eval_timeout=600,
    )
    cap = benchmark.feedback_capability
    assert cap.has_per_test is True
    assert cap.solver_may_propose is True
    assert cap.has_pass_fail is True


# ── Supplemental structural guard (kept from R8) ──────────────────


def _extract_feedback_capability_kwargs(source_path: str) -> dict[str, Any]:
    """AST-walk the kwargs passed to ``FeedbackCapability(...)`` inside a
    benchmark adapter's ``feedback_capability`` property.

    Kept as a supplemental guard beside the runtime tests above: if a
    future refactor accidentally deletes the property body, the runtime
    tests would fail, but so would this one — each catches the other's
    blind spots. Per Codex Round 8 finding #1, the AST check is NOT the
    primary AC-1 discharge; the runtime test above is.
    """
    import ast

    with open(source_path) as f:
        tree = ast.parse(f.read())

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != "feedback_capability":
            continue
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Return) or not isinstance(sub.value, ast.Call):
                continue
            call = sub.value
            func_name = (
                call.func.id if isinstance(call.func, ast.Name)
                else getattr(call.func, "attr", None)
            )
            if func_name != "FeedbackCapability":
                continue
            return {
                kw.arg: ast.literal_eval(kw.value)
                for kw in call.keywords
                if kw.arg is not None
            }
    raise AssertionError(
        f"Could not find `return FeedbackCapability(...)` in "
        f"`feedback_capability` property of {source_path}"
    )


def _adapter_source_path(rel_path: str) -> str:
    import os
    import agent_evolve

    root = os.path.dirname(os.path.abspath(agent_evolve.__file__))
    full = os.path.join(root, rel_path)
    assert os.path.isfile(full), f"adapter source not found: {full}"
    return full


def test_mcp_atlas_capability_source_shape_supplemental():
    """Structural guard (supplemental to :func:`test_mcp_atlas_capability_runtime`)."""
    src = _adapter_source_path("benchmarks/mcp_atlas/mcp_atlas.py")
    kwargs = _extract_feedback_capability_kwargs(src)
    assert kwargs.get("has_per_claim") is True
    assert kwargs.get("solver_may_propose", False) is False


def test_swe_capability_source_shape_supplemental():
    """Structural guard (supplemental to :func:`test_swe_capability_runtime`)."""
    src = _adapter_source_path("benchmarks/swe_verified_mini/benchmark.py")
    kwargs = _extract_feedback_capability_kwargs(src)
    assert kwargs.get("has_per_test") is True
    assert kwargs.get("solver_may_propose") is True


def _clear_skillbench_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear all SkillBench env vars so the test is not ambient-state-coupled.

    Mirrors the autouse fixture in ``tests/test_skillbench_setup.py``.
    Codex Round 11 review flagged that a bare ``SkillBenchBenchmark()``
    call can (a) pick up ambient SKILLBENCH_* env vars, or (b) fall
    through to ``ensure_skillbench_repo()`` bootstrap if none are set.
    Clearing the vars first makes the test's input explicit.
    """
    from agent_evolve.agents.skillbench.repo import (
        SKILLBENCH_HARBOR_REPO_ENV,
        SKILLBENCH_REF_ENV,
        SKILLBENCH_REPO_ENV,
        SKILLBENCH_TASKS_ENV,
        SKILLBENCH_TASKS_NO_SKILLS_ENV,
    )
    for key in (
        SKILLBENCH_REPO_ENV,
        SKILLBENCH_REF_ENV,
        SKILLBENCH_TASKS_ENV,
        SKILLBENCH_TASKS_NO_SKILLS_ENV,
        SKILLBENCH_HARBOR_REPO_ENV,
    ):
        monkeypatch.delenv(key, raising=False)


def _make_skillbench_repo_tree(root: Path) -> Path:
    """Build a minimal on-disk SkillBench repo so ``SkillBenchBenchmark()``
    construction has a real tasks/tasks-no-skills/harbor layout to
    resolve against. Mirrors ``_make_repo_tree`` +
    ``_write_task`` in ``tests/test_skillbench_setup.py``; the constructor
    only reads the layout, it doesn't actually load task content.
    """
    (root / "tasks").mkdir(parents=True, exist_ok=True)
    (root / "tasks-no-skills").mkdir(parents=True, exist_ok=True)
    for split_dir, name in (
        (root / "tasks", "task-with-skills"),
        (root / "tasks-no-skills", "task-without-skills"),
    ):
        task_dir = split_dir / name
        (task_dir / "environment").mkdir(parents=True, exist_ok=True)
        (task_dir / "tests").mkdir(parents=True, exist_ok=True)
        (task_dir / "instruction.md").write_text("Solve.\n")
        (task_dir / "environment" / "Dockerfile").write_text("FROM python:3.11\n")
        (task_dir / "tests" / "test.sh").write_text("#!/usr/bin/env bash\nexit 0\n")
        (task_dir / "task.toml").write_text(
            '[metadata]\nid = "%s"\ncategory = "x"\ndifficulty = "easy"\n' % name
        )
    (root / "libs" / "terminus_agent").mkdir(parents=True, exist_ok=True)
    (root / "libs" / "terminus_agent" / "README.md").write_text("x\n")
    (root / "pyproject.toml").write_text('[project]\nname = "skillsbench"\n')
    (root / "uv.lock").write_text("version = 1\n")
    (root / ".python-version").write_text("3.12\n")
    return root


def test_skillbench_capability_runtime(tmp_path, monkeypatch):
    """AC-1 positive: runtime constructor + attribute access on SkillBench.

    Matches the plan text verbatim, including the parenthesised
    constructor call:
      ``SkillBenchBenchmark().feedback_capability.has_partial_score == True``

    Addresses Codex Round 11 finding: the R11 version of this test
    was environment-coupled because
    ``SkillBenchBenchmark.__init__`` calls ``resolve_skillbench_paths()``
    which reads ambient SKILLBENCH_* env vars and, absent any, falls
    back to ``ensure_skillbench_repo()`` bootstrap. R12 fixes this by
    (a) clearing all SKILLBENCH_* env vars, (b) building a temp
    SkillBench repo tree under ``tmp_path``, (c) setting
    ``SKILLBENCH_REPO_ENV`` to point at it before construction.
    The constructor path is now fully controlled by the test.
    """
    from agent_evolve.agents.skillbench.repo import SKILLBENCH_REPO_ENV
    from agent_evolve.benchmarks.skillbench.skill_bench import SkillBenchBenchmark

    _clear_skillbench_env(monkeypatch)
    repo = _make_skillbench_repo_tree(tmp_path / "skillsbench")
    monkeypatch.setenv(SKILLBENCH_REPO_ENV, str(repo))

    benchmark = SkillBenchBenchmark()  # real constructor against the temp repo
    cap = benchmark.feedback_capability  # real property access

    assert cap.has_partial_score is True  # plan_v1.md AC-1 positive test
    assert cap.solver_may_propose is False
    assert cap.has_pass_fail is True
    assert cap.judge_available is True
    with pytest.raises(FrozenInstanceError):
        cap.has_partial_score = False  # type: ignore[misc]


def test_skillbench_constructor_variance_does_not_mutate_capability(tmp_path, monkeypatch):
    """AC-1: calling ``SkillBenchBenchmark`` with a distinct, non-default
    argument vector against a temp repo does not mutate the declared
    capability.

    Single counter-example: one non-default argument vector is checked.
    Does not claim full branch coverage of ``__init__``. Uses the same
    temp-repo + env-var isolation as
    ``test_skillbench_capability_runtime`` so ``__init__`` has a
    deterministic path to resolve against.
    """
    from agent_evolve.agents.skillbench.repo import SKILLBENCH_REPO_ENV
    from agent_evolve.benchmarks.skillbench.skill_bench import SkillBenchBenchmark

    _clear_skillbench_env(monkeypatch)
    repo = _make_skillbench_repo_tree(tmp_path / "skillsbench")
    monkeypatch.setenv(SKILLBENCH_REPO_ENV, str(repo))

    benchmark = SkillBenchBenchmark(
        shuffle=False,
        holdout_ratio=0.33,
        use_skills=False,
        split_seed=7,
    )
    cap = benchmark.feedback_capability
    assert cap.has_partial_score is True
    assert cap.solver_may_propose is False


def test_terminal_capability_runtime():
    """AC-1 positive: runtime constructor + attribute access on Terminal2.

    Matches the plan text verbatim, including the parenthesised
    constructor call:
      ``Terminal2Benchmark().feedback_capability.solver_may_propose == True``

    Terminal2Benchmark imports only from stdlib + the project's own
    ``types``/``base`` modules; no heavy optional deps — so no sys.modules
    stubbing is needed. ``__init__`` runs to completion with default
    arguments.
    """
    from agent_evolve.benchmarks.tb2.terminal2 import Terminal2Benchmark

    benchmark = Terminal2Benchmark()  # real constructor — runs __init__ body
    cap = benchmark.feedback_capability

    assert cap.solver_may_propose is True  # plan_v1.md AC-1 positive test
    assert cap.has_pass_fail is True
    assert cap.judge_available is True
    with pytest.raises(FrozenInstanceError):
        cap.solver_may_propose = False  # type: ignore[misc]


def test_terminal_constructor_variance_does_not_mutate_capability():
    """AC-1: constructing ``Terminal2Benchmark`` with non-default args
    does not mutate the declared capability.

    Same scope as the SkillBench constructor-variance test: one vector,
    not a proof of full branch coverage.
    """
    from agent_evolve.benchmarks.tb2.terminal2 import Terminal2Benchmark

    benchmark = Terminal2Benchmark(
        challenges_dir="/tmp/nonexistent-challenges",
        task_filter="specific-task",
        category_filter="sysadmin",
        difficulty_filter="hard",
        shuffle=False,
        holdout_ratio=0.5,
    )
    cap = benchmark.feedback_capability
    assert cap.solver_may_propose is True
    assert cap.has_pass_fail is True
