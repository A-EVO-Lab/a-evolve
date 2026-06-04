"""UnifiedEngine — recipe executor with per-atom state slots.

Drops in wherever an ``EvolutionEngine`` is expected. Imports only from
``agent_evolve/algorithms/unified/`` and the engine/contract/types base
packages — NO import from any legacy engine module (``adaptive_evolve``,
``adaptive_skill``, ``guided_synth``, ``skillforge``). This is enforced
statically in ``tests/test_unified_import_ban.py``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agent_evolve.engine.base import EvolutionEngine
from agent_evolve.types import StepResult
from dataclasses import replace

from .controller import RuleBasedController
from .regimes import detect_regime
from .registry import get_operator, get_reader, get_verifier
from .types import EvidenceContext, FeedbackCapability, Plan, RegimeTag

# Triggers reader/operator/verifier registration as a side effect.
from . import operators as _operators  # noqa: F401
from . import readers as _readers  # noqa: F401
from . import verifiers as _verifiers  # noqa: F401

if TYPE_CHECKING:
    from agent_evolve.benchmarks.base import BenchmarkAdapter
    from agent_evolve.config import EvolveConfig
    from agent_evolve.contract.workspace import AgentWorkspace
    from agent_evolve.engine.history import EvolutionHistory
    from agent_evolve.engine.trial import TrialRunner
    from agent_evolve.types import Observation

logger = logging.getLogger(__name__)


def _scope_allows_write(scope: dict[str, str], artifact: str) -> bool:
    return scope.get(artifact) in ("rw", "append")


class UnifiedEngine(EvolutionEngine):
    """Executes a :class:`Plan` emitted by :class:`RuleBasedController`.

    Holds three per-atom state dicts so stateful atoms
    (``StagnationRollback._best_pass_rate``, ``WriteEpisodicMemory._cycle_count``,
    ``FixHallucinations._accumulated_state``) accumulate correctly across
    cycles without sharing state with each other.
    """

    def __init__(
        self,
        config: "EvolveConfig",
        benchmark: "BenchmarkAdapter",
    ) -> None:
        self.config = config
        self.benchmark = benchmark
        # Freeze capability at construction so mid-trial drift is observable.
        self.capability: FeedbackCapability = benchmark.feedback_capability
        self.controller = RuleBasedController()
        self._reader_state: dict[str, dict[str, Any]] = {}
        self._operator_state: dict[str, dict[str, Any]] = {}
        self._verifier_state: dict[str, dict[str, Any]] = {}
        self._last_plan: Plan | None = None

    # ── EvolutionEngine interface ────────────────────────────────

    def step(
        self,
        workspace: "AgentWorkspace",
        observations: list["Observation"],
        history: "EvolutionHistory",
        trial: "TrialRunner",
    ) -> StepResult:
        regime = detect_regime(self.capability, observations, workspace, self.config)
        plan = self.controller.plan(regime, self.capability, self.config)

        if self._last_plan is not None and self._last_plan != plan:
            # AC-9: "If recipe instability is nevertheless observed,
            # UnifiedEngine emits a warning with both recipes printed."
            # Print both full Plan dataclasses (readers + operators +
            # verifier + scope + reason_trace) rather than only the
            # operator tuple, so the drift is fully auditable.
            logger.warning(
                "Recipe drift across cycles; both plans printed below.\n"
                "  prev plan: %s\n"
                "  new plan:  %s",
                asdict(self._last_plan),
                asdict(plan),
            )
        self._last_plan = plan

        context = EvidenceContext()
        # Publish observations for operators that need them (e.g.,
        # WriteEpisodicMemory needs raw trajectories).
        context.entries["__observations__"] = observations

        for name in plan.readers:
            reader = get_reader(name)
            slot = self._reader_state.setdefault(name, {})
            _seed_runtime_state(slot, self.config)
            try:
                out = reader.read(
                    observations, workspace, history, self.config, context, slot
                )
            except Exception as exc:  # noqa: BLE001
                logger.error("Reader %s raised: %s", name, exc)
                out = {"_error": str(exc)[:200]}
            context.entries[name] = out

        # AC-2 + AC-10: "Under masking, regime.pass_rate=None unless
        # LLMJudgeReader ran and produced a proxy; in that case
        # pass_rate is the judge proxy." The first detect_regime call
        # above runs before any reader, so pass_rate starts as None
        # under masking. If LLMJudgeReader is now in the context with a
        # proxy value, upgrade regime.pass_rate to that proxy so the
        # persisted unified_regime metadata and downstream operators
        # see the proxy.
        if regime.pass_rate is None:
            judge_out = context.entries.get("LLMJudgeReader", {}) or {}
            judge_proxy = judge_out.get("pass_rate")
            if isinstance(judge_proxy, (int, float)):
                regime = replace(regime, pass_rate=float(judge_proxy))

        reports = []
        # AC-5: "A failing operator (e.g., raises) does not prevent later
        # operators from running if continue_on_error=True is configured;
        # otherwise the exception propagates." Read the flag from the
        # config object (defaults to False for safety — preserves
        # fail-fast behaviour from earlier rounds).
        continue_on_error = bool(getattr(self.config, "continue_on_error", False))
        from .types import MutationReport
        for name in plan.operators:
            op = get_operator(name)
            slot = self._operator_state.setdefault(name, {})
            _seed_runtime_state(slot, self.config)
            _enforce_scope(op, plan.artifact_scope, name)
            try:
                report = op.apply(workspace, context, plan.artifact_scope, slot)
            except Exception as exc:  # noqa: BLE001
                if continue_on_error:
                    logger.error(
                        "Operator %s raised %s; continue_on_error=True, "
                        "skipping this operator and moving on.",
                        name, exc,
                    )
                    report = MutationReport(
                        operator_name=name,
                        count=0,
                        details={"error": str(exc)[:200]},
                    )
                else:
                    raise
            reports.append(report)

        verifier = get_verifier(plan.verifier)
        v_slot = self._verifier_state.setdefault(plan.verifier, {})
        verdict = verifier.check(
            workspace, context, reports, trial, history, v_slot
        )
        if verdict.rollback:
            logger.warning(
                "Verifier %s requested rollback: %s", plan.verifier, verdict.reason
            )
            # Actually restore the workspace instead of only logging. The
            # plan's AC-5 requires rollback to be applied when a verdict
            # demands it. Legacy engines use the project's ``VersionControl``
            # (via ``history.rollback_workspace``) which checks out the
            # previous state as a new commit, preserving the rejected
            # version in git history.
            try:
                _rollback_workspace(history)
            except Exception as exc:  # noqa: BLE001
                # Rollback failures are logged but not re-raised — the
                # engine must not crash the loop over a git hiccup. The
                # caller can inspect ``verdict.rollback=True`` to see
                # that rollback was attempted.
                logger.error("Workspace rollback failed: %s", exc)

        mutated = any(r.count > 0 for r in reports) and not verdict.rollback

        metadata = {
            "unified_regime": _as_jsonable(asdict(regime)),
            "unified_plan": _as_jsonable(asdict(plan)),
            "unified_reports": [_as_jsonable(asdict(r)) for r in reports],
            "unified_verdict": _as_jsonable(asdict(verdict)),
        }

        # AC-7: Observer.collect() persists unified_* fields to the batch
        # JSONL. Since EvolutionLoop.run() calls observer.collect(observations)
        # BEFORE engine.step(), the batch file exists by the time we get
        # here. We append a trailer record (marked with
        # _record_type=step_metadata) carrying the unified_* keys to the
        # same batch file. Downstream readers distinguish observation
        # rows (no _record_type) from step-metadata rows (_record_type
        # == step_metadata).
        observer = getattr(history, "observer", None) or getattr(history, "_observer", None)
        if observer is not None:
            try:
                _append_step_metadata(observer, metadata)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not append step metadata to batch: %s", exc)

        # Also keep the sidecar file for debugging/jq inspection. Not
        # required by AC-7 but useful and preserved for backwards
        # compatibility with any consumer that already reads it.
        self._persist_step_metadata(workspace, metadata, mutated)

        # Summary: human-readable AND machine-parseable. The numeric tags
        # (``<N> mutations``, ``<K> skills changed``) are stable suffixes
        # that a differential-test summary parser can match regex against,
        # so ``_extract_summary_signals`` in
        # ``tests/test_unified_legacy_differential.py`` can assert the same
        # numbers appear in both legacy and unified summaries.
        total_count = sum(int(r.count) for r in reports)
        # Count skills changes across the whole recipe via the canonical
        # "skills_added" / "skills_removed" keys that operators publish
        # in their MutationReport.details.
        skills_added_n = 0
        skills_removed_n = 0
        for r in reports:
            details = getattr(r, "details", {}) or {}
            skills_added_n += len(details.get("skills_added", []) or [])
            skills_removed_n += len(details.get("skills_removed", []) or [])

        summary = (
            f"UnifiedEngine: recipe={list(plan.operators)}, "
            f"{total_count} mutations, "
            f"{skills_added_n} skills_added, "
            f"{skills_removed_n} skills_removed, "
            f"verdict={verdict.reason}"
        )

        return StepResult(
            mutated=mutated,
            summary=summary,
            metadata=metadata,
            stop=False,
        )

    def _persist_step_metadata(
        self,
        workspace: "AgentWorkspace",
        metadata: dict[str, Any],
        mutated: bool,
    ) -> None:
        """Append a JSONL record of the unified routing decision.

        Writes to ``<workspace>/evolution/unified_steps.jsonl`` so runs can be
        inspected with ``jq``. Failures here are logged but swallowed — the
        engine must never fail a cycle because of diagnostics.
        """
        try:
            evolution_dir = Path(workspace.root) / "evolution"
            evolution_dir.mkdir(parents=True, exist_ok=True)
            record = {
                "timestamp": datetime.now().isoformat(),
                "mutated": mutated,
                **metadata,
            }
            with open(evolution_dir / "unified_steps.jsonl", "a") as f:
                f.write(json.dumps(record, default=str) + "\n")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not persist unified step metadata: %s", exc)


def _enforce_scope(op: Any, scope: dict[str, str], op_name: str) -> None:
    """Validate that the plan grants the operator at least one write target.

    ``op.WRITES`` is the set of artifacts the operator *may* write depending
    on scope and runtime context. Strict "every-WRITES-must-be-granted"
    semantics would be wrong for operators whose write set is conditional
    (e.g., ``LLMBashEvolve`` is gated by per-artifact config flags). Instead
    we require that **at least one** of the declared writes is granted; if
    none are, the operator cannot do anything useful and this is a plan
    configuration error.

    Operators still perform fine-grained per-artifact scope checks inside
    their ``apply()`` bodies for the artifacts they actually decide to write.
    """
    writes = getattr(op, "WRITES", None)
    if not writes:
        return
    from .interfaces import ScopeViolationError

    granted = {
        artifact
        for artifact in writes
        if scope.get(artifact) in ("rw", "append")
    }
    if not granted:
        raise ScopeViolationError(
            f"Operator {op_name!r} declares WRITES={sorted(writes)} but "
            f"plan.artifact_scope={dict(scope)} grants none of them."
        )


def _seed_runtime_state(slot: dict[str, Any], config: Any) -> None:
    """Expose EvolveConfig and ``extra`` knobs to readers/operators.

    Unified atoms are intentionally decoupled from the full config object, but
    legacy-compatible runs still need the same knobs (max_skills,
    protect_skills, verification_focus, region, token budget, etc.) that the
    old engines threaded into their prompt builders.
    """
    extra = getattr(config, "extra", {}) or {}
    if not isinstance(extra, dict):
        extra = {}

    model_id = getattr(config, "evolver_model", None)
    if model_id is not None:
        slot.setdefault("model_id", model_id)
    max_tokens = extra.get("max_tokens", getattr(config, "evolver_max_tokens", None))
    if max_tokens is not None:
        slot.setdefault("max_tokens", max_tokens)
    for key, value in extra.items():
        slot.setdefault(key, value)

    slot.setdefault("evolve_prompts", bool(getattr(config, "evolve_prompts", True)))
    slot.setdefault("evolve_skills", bool(getattr(config, "evolve_skills", True)))
    slot.setdefault("evolve_memory", bool(getattr(config, "evolve_memory", True)))
    slot.setdefault("evolve_tools", bool(getattr(config, "evolve_tools", False)))
    slot.setdefault("trajectory_only", bool(getattr(config, "trajectory_only", False)))


def _as_jsonable(obj: Any) -> Any:
    """Recursively convert dataclass dicts to JSON-friendly values.

    Tuples become lists; dataclass-dict leaves of dataclasses are handled
    by the caller via :func:`dataclasses.asdict`. Objects we don't recognise
    are coerced to strings so ``Observer.collect()`` can ``json.dumps``
    the metadata.
    """
    if isinstance(obj, dict):
        return {str(k): _as_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_as_jsonable(v) for v in obj]
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return str(obj)


def _append_step_metadata(observer: Any, metadata: dict[str, Any]) -> Path:
    """Append step metadata without requiring changes to agent_evolve.Observer."""
    append = getattr(observer, "append_step_metadata", None)
    if callable(append):
        return append(metadata)

    observations_dir = Path(observer.observations_dir)
    batch_id = max(1, int(getattr(observer, "_batch_id", 1)) - 1)
    batch_file = observations_dir / f"batch_{batch_id:04d}.jsonl"
    record = {
        "type": "engine_step_metadata",
        "metadata": metadata,
        "timestamp": datetime.now().isoformat(),
    }
    with open(batch_file, "a") as f:
        f.write(json.dumps(record, default=str) + "\n")
    return batch_file


def _rollback_workspace(history: Any) -> None:
    rollback = getattr(history, "rollback_workspace", None)
    if callable(rollback):
        rollback()
        return
    versioning = getattr(history, "_versioning", None)
    if versioning is None:
        raise AttributeError("history has neither rollback_workspace nor _versioning")
    versioning.rollback()


__all__ = ["UnifiedEngine"]
