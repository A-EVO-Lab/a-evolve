"""Round-0 smoke tests for the unified action-space scaffolding.

Covers AC-1 (FeedbackCapability declarations, frozen immutability, default
fallback) and the registration/lookup invariants from AC-3 that do not yet
require any real atoms to be implemented.

Later rounds add per-atom tests and the full differential tests in AC-4/AC-8.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
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


def test_mcp_atlas_capability_declares_per_claim_and_no_proposal():
    """Heavy-dep guard: MCP-Atlas adapter pulls ``strands`` at module load."""
    pytest.importorskip("strands")
    from agent_evolve.benchmarks.mcp_atlas.mcp_atlas import McpAtlasBenchmark

    prop = McpAtlasBenchmark.__dict__["feedback_capability"]

    class _Stub:
        pass

    cap = prop.fget(_Stub())
    assert cap.has_per_claim is True
    assert cap.solver_may_propose is False
    assert cap.judge_available is True


def test_swe_capability_declares_per_test_and_solver_proposes():
    """Heavy-dep guard: SWE-bench adapter pulls ``swebench`` at module load."""
    pytest.importorskip("swebench")
    from agent_evolve.benchmarks.swe_verified_mini.benchmark import (
        SweVerifiedMiniBenchmark,
    )

    prop = SweVerifiedMiniBenchmark.__dict__["feedback_capability"]

    class _Stub:
        pass

    cap = prop.fget(_Stub())
    assert cap.has_per_test is True
    assert cap.solver_may_propose is True


def test_skillbench_capability_declares_partial_score():
    try:
        from agent_evolve.benchmarks.skillbench.skill_bench import SkillBenchBenchmark
    except ModuleNotFoundError as e:
        pytest.skip(f"SkillBench heavy deps unavailable: {e}")

    prop = SkillBenchBenchmark.__dict__["feedback_capability"]

    class _Stub:
        pass

    cap = prop.fget(_Stub())
    assert cap.has_partial_score is True
    assert cap.solver_may_propose is False


def test_terminal_capability_declares_solver_proposes_via_drafts():
    """Terminal2Benchmark.feedback_capability.

    Terminal2 has a pre-existing relative import bug (``from ..types`` in
    ``agent_evolve/benchmarks/tb2/terminal2.py`` refers to a non-existent
    ``agent_evolve.benchmarks.types`` module). Skipping via ``importorskip``
    until the pre-existing issue is resolved outside this Phase 1 work.
    """
    try:
        from agent_evolve.benchmarks.tb2.terminal2 import Terminal2Benchmark
    except (ModuleNotFoundError, ImportError) as e:
        pytest.skip(f"Terminal2 adapter not importable: {e}")

    prop = Terminal2Benchmark.__dict__["feedback_capability"]

    class _Stub:
        pass

    cap = prop.fget(_Stub())
    assert cap.solver_may_propose is True
