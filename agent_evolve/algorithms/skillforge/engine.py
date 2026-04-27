"""AEvolveEngine -- the core A-Evolve algorithm.

Uses an LLM with bash tool access to analyze observation logs and mutate
the agent workspace (prompts, skills, memory). This is the first and
default EvolutionEngine implementation.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

from ...config import EvolveConfig
from ...contract.workspace import AgentWorkspace
from ...engine.base import EvolutionEngine
from ...engine.versioning import VersionControl
from ...llm.base import LLMMessage, LLMProvider
from ...types import Observation, StepResult
from .prompts import DEFAULT_EVOLVER_SYSTEM_PROMPT, build_evolution_prompt
from .tools import BASH_TOOL_SPEC, create_default_llm, make_workspace_bash

logger = logging.getLogger(__name__)


# ── Protected-artifact snapshot/rollback ─────────────────────────────────
#
# When EvolveConfig.evolve_{memory,prompts,tools} is False, the prompt
# instructs the evolver to leave those dirs untouched, but nothing in the
# tool layer enforces it. Real evolvers (e.g. qwen) occasionally write to
# `memory/` anyway — once with non-JSONL content, which crashed
# `agent.reload_from_fs()` and killed the entire cell. These helpers
# snapshot the protected dirs before each LLM call and roll back any
# changes after, with try/finally so the rollback fires even if the LLM
# call itself raises mid-write.
#
# Snapshot is content-only (file bytes); skills/ is intentionally NOT
# snapshotted so legitimate skill mutations survive.

def _snapshot_protected(
    root: Path, names: list[str],
) -> dict[str, dict[str, bytes] | None]:
    snap: dict[str, dict[str, bytes] | None] = {}
    for name in names:
        base = root / name
        if not base.exists():
            snap[name] = None
            continue
        files: dict[str, bytes] = {}
        for p in base.rglob("*"):
            if p.is_file():
                try:
                    files[str(p.relative_to(base))] = p.read_bytes()
                except OSError:
                    pass  # skip unreadable file; restore will recreate from snap
        snap[name] = files
    return snap


def _restore_protected(
    root: Path, snap: dict[str, dict[str, bytes] | None],
) -> None:
    import shutil
    for name, files in snap.items():
        base = root / name
        if base.exists():
            shutil.rmtree(base)
        if files is None:
            continue
        base.mkdir(parents=True, exist_ok=True)
        for rel, data in files.items():
            p = base / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)


def _diff_protected(
    root: Path, snap: dict[str, dict[str, bytes] | None],
) -> dict[str, Any]:
    """Detect file-level diffs AND dir creation/deletion against the snapshot.

    Distinguishes "dir didn't exist before" (snap value None) from "dir was
    empty before" (snap value {}) so a newly-created empty dir is still
    flagged for rollback.
    """
    after = _snapshot_protected(root, list(snap.keys()))
    summary: dict[str, Any] = {}
    for name in snap:
        before_state = snap[name]
        after_state = after[name]
        before = before_state if before_state is not None else {}
        now = after_state if after_state is not None else {}
        added = sorted(set(now) - set(before))
        removed = sorted(set(before) - set(now))
        modified = sorted(k for k in (set(before) & set(now)) if before[k] != now[k])
        dir_created = before_state is None and after_state is not None
        dir_deleted = before_state is not None and after_state is None
        if added or removed or modified or dir_created or dir_deleted:
            entry: dict[str, Any] = {
                "added": added, "removed": removed, "modified": modified,
            }
            if dir_created:
                entry["created"] = True
            if dir_deleted:
                entry["deleted"] = True
            summary[name] = entry
    return summary


def _format_diff(diff: dict[str, Any], cap: int = 8) -> str:
    parts = []
    for name, changes in diff.items():
        bits = []
        if changes.get("created"):
            bits.append("dir-created")
        if changes.get("deleted"):
            bits.append("dir-deleted")
        for kind in ("added", "removed", "modified"):
            files = changes.get(kind, [])
            if not files:
                continue
            shown = files[:cap]
            extra = len(files) - len(shown)
            tail = f" (+{extra} more)" if extra > 0 else ""
            bits.append(f"{kind}={shown}{tail}")
        parts.append(f"{name}: " + "; ".join(bits))
    return " | ".join(parts)


class AEvolveEngine(EvolutionEngine):
    """LLM-driven workspace mutation engine."""

    def __init__(self, config: EvolveConfig, llm: LLMProvider | None = None):
        self.config = config
        self._llm = llm

    @property
    def llm(self) -> LLMProvider:
        if self._llm is None:
            self._llm = create_default_llm(self.config)
        return self._llm

    def step(
        self,
        workspace: AgentWorkspace,
        observations: list[Observation],
        history: Any,
        trial: Any,
    ) -> StepResult:
        """Analyze observations and mutate the workspace via LLM."""
        recent_logs = history.get_observations(last_n_cycles=2)
        cycle_num = history.latest_cycle + 1

        skills_before = [s.name for s in workspace.list_skills()]
        drafts = workspace.list_drafts()

        prompt = build_evolution_prompt(
            workspace,
            recent_logs,
            drafts,
            cycle_num,
            evolve_prompts=self.config.evolve_prompts,
            evolve_skills=self.config.evolve_skills,
            evolve_memory=self.config.evolve_memory,
            evolve_tools=self.config.evolve_tools,
            trajectory_only=self.config.trajectory_only,
            max_skills=self.config.extra.get("max_skills", 5),
            solver_proposed=self.config.extra.get("solver_proposed", False),
            prompt_only=self.config.extra.get("prompt_only", False),
            protect_skills=self.config.extra.get("protect_skills", False),
        )
        response = self._run_llm_with_protection(prompt, workspace.root)

        skills_after = [s.name for s in workspace.list_skills()]
        new_skills = len(set(skills_after) - set(skills_before))

        workspace.clear_drafts()

        mutated = set(skills_after) != set(skills_before) or new_skills > 0

        return StepResult(
            mutated=mutated,
            summary=f"A-Evolve: {new_skills} new skills, {len(drafts)} drafts reviewed",
            metadata={
                "evo_number": cycle_num,
                "tasks_analyzed": len(recent_logs),
                "drafts_reviewed": len(drafts),
                "skills_before": len(skills_before),
                "skills_after": len(skills_after),
                "new_skills": new_skills,
                "usage": response.get("usage", {}),
            },
        )

    def evolve(
        self,
        workspace: AgentWorkspace,
        observation_logs: list[dict[str, Any]],
        evo_number: int = 0,
    ) -> dict[str, Any]:
        """Run one evolution pass outside the loop (for scripts/examples)."""
        import time as _time

        vc = VersionControl(workspace.root)
        vc.init()

        skills_before = [s.name for s in workspace.list_skills()]
        drafts = workspace.list_drafts()

        logger.info(
            "EVOLVER: evo #%d — analyzing %d observations, workspace has %d skills, %d drafts",
            evo_number, len(observation_logs), len(skills_before), len(drafts),
        )

        vc.commit(
            message=f"pre-evo-{evo_number}: snapshot before evolution",
            tag=f"pre-evo-{evo_number}",
        )

        prompt = build_evolution_prompt(
            workspace,
            observation_logs,
            drafts,
            evo_number,
            evolve_prompts=self.config.evolve_prompts,
            evolve_skills=self.config.evolve_skills,
            evolve_memory=self.config.evolve_memory,
            evolve_tools=self.config.evolve_tools,
            trajectory_only=self.config.trajectory_only,
            max_skills=self.config.extra.get("max_skills", 5),
            solver_proposed=self.config.extra.get("solver_proposed", False),
            prompt_only=self.config.extra.get("prompt_only", False),
            protect_skills=self.config.extra.get("protect_skills", False),
        )
        _evo_t0 = _time.time()
        response = self._run_llm_with_protection(prompt, workspace.root)
        _evo_elapsed = _time.time() - _evo_t0

        skills_after = [s.name for s in workspace.list_skills()]
        new_skills = len(set(skills_after) - set(skills_before))
        added = sorted(set(skills_after) - set(skills_before))
        removed = sorted(set(skills_before) - set(skills_after))
        _usage = response.get("usage", {})

        logger.info(
            "EVOLVER: LLM completed in %.0fs, tokens_in=%s tokens_out=%s",
            _evo_elapsed,
            _usage.get("input_tokens", "?"),
            _usage.get("output_tokens", "?"),
        )
        if added:
            logger.info("EVOLVER: +%d skills %s", len(added), added)
        if removed:
            logger.info("EVOLVER: -%d skills %s", len(removed), removed)

        workspace.clear_drafts()

        mutated = set(skills_after) != set(skills_before) or new_skills > 0
        if mutated:
            vc.commit(
                message=f"evo-{evo_number}: {new_skills} new skills",
                tag=f"evo-{evo_number}",
            )
        else:
            vc.commit(
                message=f"evo-{evo_number}: no mutation",
                tag=f"evo-{evo_number}",
            )

        return {
            "evo_number": evo_number,
            "tasks_analyzed": len(observation_logs),
            "drafts_reviewed": len(drafts),
            "skills_before": len(skills_before),
            "skills_after": len(skills_after),
            "new_skills": new_skills,
            "skills_added": added,
            "skills_removed": removed,
            "usage": _usage,
        }

    def _protected_dirs(self) -> list[str]:
        """Dir names under workspace root that the evolver should NOT mutate."""
        names = []
        if not self.config.evolve_memory:
            names.append("memory")
        if not self.config.evolve_prompts:
            names.append("prompts")
        if not self.config.evolve_tools:
            names.append("tools")
        return names

    def _run_llm_with_protection(
        self, prompt: str, workspace_root: Path,
    ) -> dict[str, Any]:
        """Wrap _run_llm with snapshot+rollback for protected dirs.

        Restoration runs in `finally` so partial writes are undone even if
        `_run_llm` raises mid-operation. Skills/ is NEVER protected so
        legitimate skill mutations always survive.
        """
        protected = self._protected_dirs()
        if not protected:
            return self._run_llm(prompt, workspace_root)

        snap = _snapshot_protected(workspace_root, protected)
        try:
            return self._run_llm(prompt, workspace_root)
        finally:
            # `sys.exc_info()[0]` is non-None iff this `finally` is unwinding
            # a primary exception from `_run_llm`. Suppress secondary errors
            # ONLY in that case — if `_run_llm` succeeded, a rollback failure
            # is itself the primary problem and must propagate.
            primary_exc_type = sys.exc_info()[0]
            try:
                diff = _diff_protected(workspace_root, snap)
                if diff:
                    _restore_protected(workspace_root, snap)
                    logger.warning(
                        "EVOLVER: rolled back protected artifacts | %s | "
                        "(evolve_memory=%s evolve_prompts=%s evolve_tools=%s)",
                        _format_diff(diff),
                        self.config.evolve_memory,
                        self.config.evolve_prompts,
                        self.config.evolve_tools,
                    )
            except Exception as exc:  # noqa: BLE001
                if primary_exc_type is not None:
                    # Primary exception is unwinding; preserve it, log the
                    # secondary so it isn't silently lost.
                    logger.warning(
                        "EVOLVER: rollback bookkeeping failed "
                        "(original %s exception preserved): %s",
                        primary_exc_type.__name__, exc,
                    )
                else:
                    # Clean LLM run; rollback failure is the primary problem.
                    raise

    def _run_llm(self, prompt: str, workspace_root: Path) -> dict[str, Any]:
        """Run the evolver LLM with bash access to the workspace."""
        bash_fn = make_workspace_bash(workspace_root)

        try:
            from ...llm.bedrock import BedrockProvider

            if isinstance(self.llm, BedrockProvider):
                response = self.llm.converse_loop(
                    system_prompt=DEFAULT_EVOLVER_SYSTEM_PROMPT,
                    user_message=prompt,
                    tools=[BASH_TOOL_SPEC],
                    tool_executor={"workspace_bash": lambda command: bash_fn(command)},
                    max_tokens=self.config.evolver_max_tokens,
                )
                return {
                    "content": response.content,
                    "usage": response.usage,
                }
        except ImportError:
            pass

        messages = [
            LLMMessage(role="system", content=DEFAULT_EVOLVER_SYSTEM_PROMPT),
            LLMMessage(role="user", content=prompt),
        ]
        response = self.llm.complete(
            messages, max_tokens=self.config.evolver_max_tokens
        )
        return {
            "content": response.content,
            "usage": response.usage,
        }
