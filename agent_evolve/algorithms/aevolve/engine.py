"""AEvolveEngine -- the core A-Evolve algorithm.

Uses an LLM with bash tool access to analyze observation logs and mutate
the agent workspace (prompts, skills, memory).  This is the first and
default EvolutionEngine implementation.
"""

from __future__ import annotations

import logging
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


class AEvolveEngine(EvolutionEngine):
    """LLM-driven workspace mutation engine.

    Analyses observation logs, reviews draft skills, and uses an LLM with
    bash tool access to mutate workspace files (prompts, skills, memory).

    This is the default engine used when none is specified.
    """

    def __init__(self, config: EvolveConfig, llm: LLMProvider | None = None):
        self.config = config
        self._llm = llm

    @property
    def llm(self) -> LLMProvider:
        if self._llm is None:
            self._llm = create_default_llm(self.config)
        return self._llm

    # ── EvolutionEngine interface ────────────────────────────

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

        skills_before = set(s.name for s in workspace.list_skills())
        prompt_before = workspace.read_prompt()
        memory_before = workspace.read_all_memories(limit=9999)
        tools_before = workspace.read_tool_registry()
        infra_before = set(
            f.name for f in (workspace.root / "infra").iterdir()
        ) if (workspace.root / "infra").exists() else set()
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
            evolve_infra=self.config.evolve_infra,
            include_patches=self.config.evolver_include_patches,
            trajectory_only=self.config.trajectory_only,
        )
        disabled = [name for name, on in [
            ("prompts", self.config.evolve_prompts),
            ("skills",  self.config.evolve_skills),
            ("memory",  self.config.evolve_memory),
            ("tools",   self.config.evolve_tools),
            ("infra",   self.config.evolve_infra),
        ] if not on]
        with workspace.protect(disabled):
            response = self._run_llm(prompt, workspace.root)

        skills_after = set(s.name for s in workspace.list_skills())
        new_skills = len(skills_after - skills_before)

        workspace.clear_drafts()

        prompt_changed = workspace.read_prompt() != prompt_before
        memory_changed = len(workspace.read_all_memories(limit=9999)) != len(memory_before)
        skills_changed = skills_after != skills_before
        tools_changed = workspace.read_tool_registry() != tools_before
        infra_after = set(
            f.name for f in (workspace.root / "infra").iterdir()
        ) if (workspace.root / "infra").exists() else set()
        infra_changed = infra_after != infra_before
        mutated = (prompt_changed or memory_changed or skills_changed
                   or tools_changed or infra_changed)

        changes = []
        if prompt_changed:
            changes.append("prompt")
        if skills_changed:
            changes.append(f"{new_skills} new skills")
        if memory_changed:
            changes.append("memory")
        if tools_changed:
            changes.append("tools")
        summary = ", ".join(changes) if changes else "no mutation"

        return StepResult(
            mutated=mutated,
            summary=f"A-Evolve: {summary}",
            metadata={
                "evo_number": cycle_num,
                "tasks_analyzed": len(recent_logs),
                "drafts_reviewed": len(drafts),
                "skills_before": len(skills_before),
                "skills_after": len(skills_after),
                "new_skills": new_skills,
                "prompt_changed": prompt_changed,
                "memory_changed": memory_changed,
                "tools_changed": tools_changed,
                "usage": response.get("usage", {}),
            },
        )

    # ── Standalone convenience API ───────────────────────────

    def evolve(
        self,
        workspace: AgentWorkspace,
        observation_logs: list[dict[str, Any]] | None = None,
        evo_number: int = 0,
    ) -> dict[str, Any]:
        """Run one evolution pass outside the loop (for scripts/examples).

        This is a simpler entry point that doesn't require history/trial.
        Automatically commits changes to git with an evo-N tag.
        """
        if observation_logs is None:
            observation_logs = []
        vc = VersionControl(workspace.root)
        vc.init()

        skills_before = set(s.name for s in workspace.list_skills())
        prompt_before = workspace.read_prompt()
        memory_before = workspace.read_all_memories(limit=9999)
        tools_before = workspace.read_tool_registry()
        infra_before = set(
            f.name for f in (workspace.root / "infra").iterdir()
        ) if (workspace.root / "infra").exists() else set()
        drafts = workspace.list_drafts()

        # Pre-evolve snapshot
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
            evolve_infra=self.config.evolve_infra,
            include_patches=self.config.evolver_include_patches,
            trajectory_only=self.config.trajectory_only,
        )
        disabled = [name for name, on in [
            ("prompts", self.config.evolve_prompts),
            ("skills",  self.config.evolve_skills),
            ("memory",  self.config.evolve_memory),
            ("tools",   self.config.evolve_tools),
            ("infra",   self.config.evolve_infra),
        ] if not on]
        with workspace.protect(disabled):
            response = self._run_llm(prompt, workspace.root)

        skills_after = set(s.name for s in workspace.list_skills())
        prompt_after = workspace.read_prompt()
        memory_after = workspace.read_all_memories(limit=9999)
        tools_after = workspace.read_tool_registry()
        new_skills = len(skills_after - skills_before)

        workspace.clear_drafts()

        # Detect mutations across all layers (including infra)
        prompt_changed = prompt_after != prompt_before
        memory_changed = len(memory_after) != len(memory_before)
        skills_changed = skills_after != skills_before
        tools_changed = tools_after != tools_before
        infra_after = set(
            f.name for f in (workspace.root / "infra").iterdir()
        ) if (workspace.root / "infra").exists() else set()
        infra_changed = infra_after != infra_before
        mutated = (prompt_changed or memory_changed or skills_changed
                   or tools_changed or infra_changed)

        # Post-evolve commit
        changes = []
        if prompt_changed:
            changes.append("prompt")
        if skills_changed:
            changes.append(f"{new_skills} new skills")
        if memory_changed:
            changes.append("memory")
        if tools_changed:
            changes.append("tools")
        summary = ", ".join(changes) if changes else "no mutation"

        vc.commit(
            message=f"evo-{evo_number}: {summary}",
            tag=f"evo-{evo_number}",
        )

        return {
            "evo_number": evo_number,
            "mutated": mutated,
            "tasks_analyzed": len(observation_logs),
            "drafts_reviewed": len(drafts),
            "skills_before": len(skills_before),
            "skills_after": len(skills_after),
            "new_skills": new_skills,
            "prompt_changed": prompt_changed,
            "memory_changed": memory_changed,
            "tools_changed": tools_changed,
            "usage": response.get("usage", {}),
            "conversation": response.get("conversation", []),
        }

    # ── LLM execution ────────────────────────────────────────

    def _run_llm(
        self,
        prompt: str,
        workspace_root: Path,
        evolver_workspace: Path | None = None,
        system_prompt: str | None = None,
    ) -> dict[str, Any]:
        """Run the evolver LLM with bash access to the workspace(s).

        When evolver_workspace is provided (--navigation mode), the sandbox
        mounts both /solver_workspace and /evolver_workspace. The evolver
        can read/write both.
        """
        network = self.config.extra.get("evolver_sandbox_network", "none")
        # Mount the safe trajectories subtree (conversation + patch only).
        # Ground-truth-bearing observations/*.jsonl is deliberately not
        # mounted: the evolver must infer from behaviour alone.
        if evolver_workspace:
            trajectories_dir = Path(evolver_workspace) / "evolution" / "trajectories"
        else:
            trajectories_dir = Path(workspace_root) / "evolution" / "trajectories"
        trajectories_dir.mkdir(parents=True, exist_ok=True)
        sandbox = make_workspace_bash(
            workspace_root,
            evolver_workspace,
            network=network,
            trajectories_dir=trajectories_dir,
        )
        sys_prompt = system_prompt or self.config.extra.get("evolver_system_prompt") or DEFAULT_EVOLVER_SYSTEM_PROMPT

        # Build tool list: always include bash, optionally add human interaction
        tools = [BASH_TOOL_SPEC]
        executors: dict[str, Any] = {"workspace_bash": lambda command: sandbox.exec(command)}
        if self.config.extra.get("human_in_the_loop"):
            from .tools import HUMAN_TOOL_SPEC
            from ...engine.human_interface import create_interface

            interface = create_interface(self.config.extra)
            tools.append(HUMAN_TOOL_SPEC)
            executors["request_human_action"] = (
                lambda message, action_type="information": interface.handle(message, action_type)
            )

        with sandbox:
            try:
                from ...llm.bedrock import BedrockProvider

                if isinstance(self.llm, BedrockProvider):
                    response = self.llm.converse_loop(
                        system_prompt=sys_prompt,
                        user_message=prompt,
                        tools=tools,
                        tool_executor=executors,
                        max_tokens=self.config.evolver_max_tokens,
                        temperature=self.config.evolver_temperature,
                        verbose=bool(self.config.extra.get("verbose")),
                    )
                    return {
                        "content": response.content,
                        "usage": response.usage,
                        "conversation": response.raw.get("conversation", []),
                    }
            except ImportError:
                pass

            fallback_sys = self.config.extra.get("evolver_system_prompt") or DEFAULT_EVOLVER_SYSTEM_PROMPT
            messages = [
                LLMMessage(role="system", content=fallback_sys),
                LLMMessage(role="user", content=prompt),
            ]
            response = self.llm.complete(
                messages, max_tokens=self.config.evolver_max_tokens,
                temperature=self.config.evolver_temperature,
            )
            return {
                "content": response.content,
                "usage": response.usage,
            }
