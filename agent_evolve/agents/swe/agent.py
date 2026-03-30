"""SWE-bench coding agent -- uses strands-agents at runtime.

The framework layer (BaseAgent) loads prompts/skills/memory from the file
system contract.  This concrete agent then assembles those pieces into a
real ``strands.Agent`` and calls it, exactly like CodeDojo's original
``solve_instance``.  This keeps framework-level code strands-free while
ensuring *this* agent behaves identically to the CodeDojo version.
"""

from __future__ import annotations

import importlib
import logging
import os
from pathlib import Path
from typing import Any

from strands import Agent
from strands.models import BedrockModel

from ...protocol.base_agent import BaseAgent
from ...types import Task, Trajectory
from .env import SWEBenchContainer, pull_image

logger = logging.getLogger(__name__)

os.environ.setdefault("BYPASS_TOOL_CONSENT", "true")


class SweAgent(BaseAgent):
    """Reference agent for SWE-bench coding tasks.

    Reads system prompt, skills, and memories from the workspace via BaseAgent,
    then builds a strands ``Agent`` with those assets at solve-time -- the same
    pattern CodeDojo uses.
    """

    def __init__(
        self,
        workspace_dir: str | Path,
        model_id: str = "us.anthropic.claude-opus-4-6-v1",
        region: str = "us-west-2",
        max_tokens: int = 16384,
        max_steps: int = 0,
        window_size: int = 40,
        verification_focus: bool = False,
        efficiency_prompt: bool = False,
        model_base_url: str | None = None,
        model_api_key: str | None = None,
    ):
        super().__init__(workspace_dir)
        self.model_id = model_id
        self.region = region
        self.max_tokens = max_tokens
        self.max_steps = max_steps  # 0 = unlimited
        self.window_size = window_size
        self.efficiency_prompt = efficiency_prompt
        self.verification_focus = verification_focus
        self.model_base_url = model_base_url
        self.model_api_key = model_api_key

    def _load_tools_from_workspace(self) -> tuple[list, list]:
        """Load tool functions from the workspace tools/registry.yaml.

        Supports two loading modes per entry:

        1. ``module`` — import from an installed Python package.
           e.g. ``{name: bash, module: agent_evolve.agents.swe.tools}``

        2. ``file`` — load from a ``.py`` file inside the workspace ``tools/``
           directory.  The evolver can drop new tool files here without
           touching framework source code.
           e.g. ``{name: smart_edit, file: smart_edit.py}``

        Every entry must have ``name`` plus either ``module`` or ``file``.

        Returns:
            A tuple of (tool_functions, tool_modules).  tool_modules is the
            deduplicated list of loaded modules so the agent can call
            ``reset()`` on each before a task.
        """
        registry = self.workspace.read_tool_registry()
        tools: list = []
        modules: dict[str, Any] = {}  # deduplicate by id
        for entry in registry:
            name = entry.get("name")
            module_path = entry.get("module")
            file_path = entry.get("file")

            if not name or (not module_path and not file_path):
                logger.warning("Skipping tool entry with missing name/module/file: %s", entry)
                continue

            try:
                if module_path:
                    mod = importlib.import_module(module_path)
                else:
                    # Load from workspace tools/ directory
                    spec = importlib.util.spec_from_file_location(
                        f"workspace_tool_{name}",
                        self.workspace.tools_dir / file_path,
                    )
                    if spec is None or spec.loader is None:
                        raise ImportError(f"Cannot load spec for {file_path}")
                    mod = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(mod)

                tool_fn = getattr(mod, name)
                tools.append(tool_fn)
                modules[id(mod)] = mod
            except (ImportError, AttributeError, FileNotFoundError) as exc:
                logger.error("Failed to load tool %s: %s", name, exc)
        return tools, list(modules.values())

    @staticmethod
    def _reset_tool_modules(modules: list, **kwargs) -> None:
        """Call ``reset(**kwargs)`` on each tool module that exposes it."""
        for mod in modules:
            reset_fn = getattr(mod, "reset", None)
            if callable(reset_fn):
                try:
                    reset_fn(**kwargs)
                except Exception as exc:
                    logger.warning("reset() failed on %s: %s", getattr(mod, "__name__", mod), exc)

    @staticmethod
    def _get_submitted_patch(modules: list) -> str | None:
        """Extract submitted patch from whichever tool module provides it."""
        for mod in modules:
            fn = getattr(mod, "get_submitted_patch", None)
            if callable(fn):
                return fn()
        return None

    def _build_strands_agent(self) -> tuple[Agent, list]:
        """Create a strands Agent wired with the workspace's current state.

        Returns:
            A tuple of (Agent, tool_modules) so the caller can reset modules.
        """
        if self.model_base_url:
            from strands.models.litellm import LiteLLMModel
            model = LiteLLMModel(
                model_id=f"openai/{self.model_id}",
                params={
                    "api_base": self.model_base_url,
                    "api_key": self.model_api_key or "local",
                    "max_tokens": self.max_tokens,
                },
            )
        else:
            model = BedrockModel(
                model_id=self.model_id,
                region_name=self.region,
                max_tokens=self.max_tokens,
            )

        system_prompt = self._build_system_prompt()
        tools, modules = self._load_tools_from_workspace()

        # Only register read_skill tool if skills actually exist
        if self.skills:
            skill_data = {}
            for skill in self.skills:
                content = self.get_skill_content(skill.name)
                if content:
                    body = content.split("---", 2)[-1].strip() if "---" in content else content
                    skill_data[skill.name] = body

            from strands import tool

            @tool
            def read_skill(skill_name: str) -> str:
                """Read the full procedure for a skill. Call this when a skill's description matches your current task.

                Args:
                    skill_name: Name of the skill to read
                """
                if skill_name in skill_data:
                    return skill_data[skill_name]
                available = ", ".join(skill_data.keys())
                return f"Skill '{skill_name}' not found. Available: {available}"

            tools.append(read_skill)

        from .conversation_manager import PinnedFirstMessageManager

        agent = Agent(
            model=model,
            system_prompt=system_prompt,
            tools=tools,
            conversation_manager=PinnedFirstMessageManager(
                window_size=self.window_size,
                should_truncate_results=True,
                per_turn=True,
            ),
        )
        return agent, modules

    def solve(self, task: Task) -> Trajectory:
        """Solve a SWE-bench instance.

        Expects task.metadata to contain:
          - docker_image: str (SWE-bench Docker image name)
          - instance_id: str (optional, defaults to task.id)
        """
        docker_image = task.metadata.get("docker_image", "")
        instance_id = task.metadata.get("instance_id", task.id)
        problem_statement = task.input

        if not docker_image:
            raise ValueError(
                f"Task {task.id} missing 'docker_image' in metadata. "
                "SweAgent requires a SWE-bench Docker image."
            )

        pull_image(docker_image)
        container = SWEBenchContainer(docker_image)
        tool_call_trace: list[dict] = []

        with container:
            agent, tool_modules = self._build_strands_agent()
            self._reset_tool_modules(tool_modules, container_name=container.container_name)

            # Register hook to capture tool-call-level trace
            self._register_trace_hook(agent, tool_call_trace)

            # Register turn limiter if max_steps is set
            if self.max_steps > 0:
                from strands.hooks.events import BeforeToolCallEvent
                _step_count = [0]
                _submitted = [False]

                def _turn_limiter(event: BeforeToolCallEvent):
                    if _submitted[0]:
                        event.cancel_tool = "Already submitted."
                        return
                    tool_name = event.tool_use.get("name", "")
                    if tool_name == "submit":
                        _submitted[0] = True
                        return
                    _step_count[0] += 1
                    if _step_count[0] > self.max_steps:
                        event.cancel_tool = f"Step limit reached ({self.max_steps}). Call submit now."

                agent.hooks.add_callback(BeforeToolCallEvent, _turn_limiter)
                logger.info("Turn limiter: max_steps=%d", self.max_steps)

            user_prompt = self._build_user_prompt(instance_id, problem_statement)

            logger.info("Solving %s with image %s", instance_id, docker_image)
            response = agent(user_prompt)

            usage = {}
            per_turn_usage = []
            try:
                u = response.metrics.accumulated_usage
                usage = {
                    "input_tokens": u.get("inputTokens", 0),
                    "output_tokens": u.get("outputTokens", 0),
                    "total_tokens": u.get("totalTokens", 0),
                    "cache_read_input_tokens": u.get("cacheReadInputTokens", 0),
                    "cache_write_input_tokens": u.get("cacheWriteInputTokens", 0),
                }
                # Capture per-turn usage for context window analysis
                invocation = response.metrics.agent_invocations[-1] if response.metrics.agent_invocations else None
                if invocation:
                    for cycle in invocation.cycles:
                        cu = cycle.usage if hasattr(cycle, "usage") else {}
                        per_turn_usage.append({
                            "input_tokens": cu.get("inputTokens", 0),
                            "output_tokens": cu.get("outputTokens", 0),
                        })
            except Exception:
                pass

            patch = self._get_submitted_patch(tool_modules) or container.get_diff()

            # Build steps: tool-call trace + summary with usage
            steps = list(tool_call_trace)
            steps.append({
                "llm_output": str(response)[:2000],
                "usage": usage,
                "per_turn_usage": per_turn_usage,
                "num_turns": len(per_turn_usage),
                "max_input_tokens_per_turn": max((t.get("input_tokens", 0) for t in per_turn_usage), default=0),
            })

            # Capture the full conversation (after sliding window)
            conversation = list(agent.messages)

            if not patch.strip():
                logger.warning("No changes detected for %s", instance_id)

            # Skill evolution: analyze used skills + propose enhancement or new skill
            skill_proposal = ""
            try:
                # Build skill context for the proposal prompt
                skill_context = ""
                if self.skills:
                    skill_list = "\n".join(f"- {s.name}: {s.description}" for s in self.skills)
                    skill_context = (
                        f"You had these skills available (you may have read some via read_skill):\n"
                        f"{skill_list}\n\n"
                    )

                if self.verification_focus:
                    proposal_response = agent(
                        f"{skill_context}"
                        "Reflect on your VERIFICATION process — how you tested and validated your fix.\n\n"
                        "Think about:\n"
                        "- How did you find the right test file? Was it easy or did you guess?\n"
                        "- Did you run tests before AND after your edit?\n"
                        "- Did your repro script catch the real issue, or was it too simple?\n"
                        "- Were there edge cases in the issue that you didn't test?\n"
                        "- Did you verify your fix doesn't break adjacent functionality?\n\n"
                        "Propose a VERIFICATION skill that helps future solvers test more thoroughly.\n"
                        "Focus ONLY on how to verify/test — not on how to find or write the fix.\n\n"
                        "CRITICAL: The skill must be GENERALIZABLE — applicable to many different tasks, "
                        "not just this one. Abstract away the specific framework/module details.\n"
                        "  GOOD names: verify_falsy_edge_cases, verify_before_after_edit, verify_inheritance_chain, "
                        "verify_roundtrip_integrity, verify_mutable_state_isolation\n"
                        "  BAD names: verify_admin_kwargs_passthrough, verify_django_enum_str, verify_sphinx_toctree\n"
                        "  GOOD content: general methodology (e.g., 'test all falsy-but-valid values: 0, False, \"\", [], {}')\n"
                        "  BAD content: specific code paths (e.g., 'check django/contrib/admin/options.py line 200')\n\n"
                        "OPTION A — ENHANCE an existing verification skill:\n"
                        "CONFIDENCE: HIGH/MEDIUM/LOW\n"
                        "ACTION: ENHANCE\n"
                        "TARGET: existing_skill_name\n"
                        "ANALYSIS: what was missing in the verification approach\n"
                        "TYPE: skill\n"
                        "NAME: same_skill_name\n"
                        "DESCRIPTION: TRIGGER when / DO NOT TRIGGER when (one sentence)\n"
                        "CONTENT:\n"
                        "(verification methodology, under 500 words)\n\n"
                        "OPTION B — NEW verification skill:\n"
                        "CONFIDENCE: HIGH/MEDIUM/LOW\n"
                        "ACTION: NEW\n"
                        "TYPE: skill\n"
                        "NAME: verify_<general_pattern>\n"
                        "DESCRIPTION: TRIGGER when / DO NOT TRIGGER when (one sentence)\n"
                        "CONTENT:\n"
                        "(verification methodology, under 500 words)\n\n"
                        "OPTION C — No proposal:\n"
                        "CONFIDENCE: HIGH/MEDIUM/LOW\n"
                        "ACTION: NONE"
                    )
                else:
                    proposal_response = agent(
                        f"{skill_context}"
                        "Help FUTURE solvers by proposing or enhancing a skill.\n\n"
                    "IMPORTANT RULES:\n"
                    "- NAME must be GENERIC and reusable.\n"
                    "  GOOD: fix_shallow_copy_mutation, align_parallel_code_paths\n"
                    "  BAD:  django_mti_pk_fix, sphinx_autodoc_classmethod\n"
                    "- DESCRIPTION must include TRIGGER and DO NOT TRIGGER conditions so the solver knows when to load it:\n"
                    "  GOOD: 'TRIGGER when: copied objects share mutable state, or __deepcopy__ is defined. "
                    "DO NOT TRIGGER when: simple value assignment or immutable types.'\n"
                    "  BAD:  'Fix for deep copy issues' (too vague, no trigger conditions)\n"
                    "- Prefer OPTION A (enhance existing) over OPTION B (new).\n\n"
                    "OPTION A — ENHANCE an existing skill:\n"
                    "CONFIDENCE: HIGH/MEDIUM/LOW\n"
                    "ACTION: ENHANCE\n"
                    "TARGET: existing_skill_name\n"
                    "ANALYSIS: one sentence on what to improve\n"
                    "TYPE: skill\n"
                    "NAME: same_skill_name\n"
                    "DESCRIPTION: one sentence, max 15 words\n"
                    "CONTENT:\n"
                    "(enhanced methodology, under 500 words)\n\n"
                    "OPTION B — NEW skill (only if nothing existing covers this):\n"
                    "CONFIDENCE: HIGH/MEDIUM/LOW\n"
                    "ACTION: NEW\n"
                    "TYPE: skill\n"
                    "NAME: pattern_name\n"
                    "DESCRIPTION: one sentence, max 15 words\n"
                    "CONTENT:\n"
                    "(methodology, under 500 words)\n\n"
                    "OPTION C — No proposal:\n"
                    "CONFIDENCE: HIGH/MEDIUM/LOW\n"
                    "ACTION: NONE"
                )
                skill_proposal = str(proposal_response).strip()[:2500]
                if "ACTION: NONE" not in skill_proposal.upper():
                    action = "ENHANCE" if "ACTION: ENHANCE" in skill_proposal.upper() else "NEW"
                    logger.info("Skill proposal (%s) for %s: %s", action, instance_id, skill_proposal[:100])
                else:
                    skill_proposal = skill_proposal if "CONFIDENCE:" in skill_proposal else ""
            except Exception as e:
                logger.warning("Skill proposal failed for %s: %s", instance_id, e)

        traj = Trajectory(task_id=task.id, output=patch, steps=steps)
        traj._conversation = conversation
        traj._skill_proposal = skill_proposal
        return traj
    @staticmethod
    def _register_trace_hook(agent: Agent, trace: list[dict]) -> None:
        """Register a strands hook that captures every tool call into trace.

        Each entry records the tool name, input args, status, and a truncated
        snippet of the result — giving the evolver fine-grained visibility
        into what the agent actually did.
        """
        from strands.hooks import AfterToolCallEvent

        def _on_tool_call(event: AfterToolCallEvent) -> None:
            tool_name = event.tool_use.get("name", "unknown")
            tool_input = event.tool_use.get("input", {})
            status = event.result.get("status", "unknown") if event.result else "error"

            # Extract file path from common tool input patterns
            file_path = ""
            if isinstance(tool_input, dict):
                file_path = tool_input.get("file", tool_input.get("path", ""))
                # For bash tool, try to extract file from command
                if not file_path and tool_name == "bash":
                    cmd = tool_input.get("command", "")
                    if isinstance(cmd, str):
                        # Detect common file-reading commands
                        for prefix in ("cat ", "head ", "tail ", "less ", "view "):
                            if prefix in cmd:
                                file_path = cmd.split(prefix, 1)[-1].strip().split()[0] if prefix in cmd else ""
                                break

            # Classify the action type
            action = tool_name
            if tool_name == "bash" and isinstance(tool_input, dict):
                cmd = tool_input.get("command", "")
                if isinstance(cmd, str):
                    # Strip common cd prefix: "cd /testbed && actual_cmd"
                    cmd_lower = cmd.strip().lower()
                    if "&&" in cmd_lower:
                        cmd_lower = cmd_lower.split("&&", 1)[-1].strip()
                    if any(cmd_lower.startswith(p) for p in ("cat ", "head ", "tail ", "less ", "view ")):
                        action = "read_file"
                    elif any(cmd_lower.startswith(p) for p in ("grep ", "find ", "ag ", "rg ")):
                        action = "search"
                    elif any(cmd_lower.startswith(p) for p in ("sed -i", "patch ", "tee ")) or any(k in cmd_lower for k in ("cat >", "cat >>", "echo >", "echo >>")):
                        action = "edit_file"
                    elif any(cmd_lower.startswith(p) for p in ("python -m pytest", "pytest ")):
                        action = "run_test"
                    elif any(cmd_lower.startswith(p) for p in ("python ", "python3 ")):
                        # python -c could be edit (writing files) or test
                        if any(k in cmd_lower for k in ("open(", "write(", ".write(", "with open")):
                            action = "edit_file"
                        else:
                            action = "run_test"
                    elif any(cmd_lower.startswith(p) for p in ("ls ", "pwd", "tree ")):
                        action = "navigate"
                    elif cmd_lower.startswith(("sed -n", "sed '")):
                        action = "read_file"
                    else:
                        action = "navigate"

            # Truncate result content for storage
            result_snippet = ""
            if event.result:
                content = event.result.get("content", [])
                if content and isinstance(content, list):
                    for item in content[:1]:
                        if isinstance(item, dict):
                            result_snippet = str(item.get("text", ""))[:500]
                        else:
                            result_snippet = str(item)[:500]

            trace.append({
                "tool": tool_name,
                "action": action,
                "input_summary": str(tool_input)[:300] if tool_input else "",
                "file": file_path,
                "status": status,
                "result_snippet": result_snippet,
            })

        agent.hooks.add_callback(AfterToolCallEvent, _on_tool_call)

    def _build_system_prompt(self) -> str:
        """Assemble the full system prompt from workspace files.

        Includes the base prompt, any evolved prompt fragments, and skills.
        Memories are injected into the user prompt via ``_build_user_prompt``
        so they appear once, in the task-specific context.
        """
        parts = [self.system_prompt]

        # Verify-fix loop instruction
        parts.append(
            "\n\n## Verify Your Fix\n"
            "Before editing, find the existing test file for the module you're changing. Use "
            "`grep -r 'def test_' tests/ --include='*.py' -l` or check `tests/test_<module>.py`. "
            "Run these tests BEFORE your edit to see the baseline, then AFTER to confirm your fix "
            "passes and doesn't break other tests.\n\n"
            "Also write a small reproduction script from the issue description and run it. "
            "If it still fails after your edit, read the traceback — there may be another "
            "file that needs changing. Repeat until both the repro and existing tests pass."
        )

        if self.efficiency_prompt:
            parts.append(
                "\n\n## Efficiency Rules (CRITICAL)\n"
                "You have a LIMITED tool call budget. Work hypothesis-first, not exploration-first.\n\n"
                "**Phase 1 — Locate (max 10 tool calls):**\n"
                "- Read the issue. Identify the key class/function/error mentioned.\n"
                "- Use `grep -rn` to find the relevant source file(s). Limit to 3 candidate files.\n"
                "- Read only the relevant sections of those files (use `sed -n 'START,ENDp'`).\n\n"
                "**Phase 2 — Hypothesize (0 tool calls):**\n"
                "- State your hypothesis: what's wrong and what change will fix it.\n"
                "- If you can't form a hypothesis after Phase 1, re-read the issue — don't explore more files.\n\n"
                "**Phase 3 — Fix & Verify (max 15 tool calls):**\n"
                "- Make your edit. Run the relevant test(s). Check the result.\n"
                "- If tests fail, read the traceback and adjust your edit — don't go back to exploring.\n"
                "- Submit when tests pass.\n\n"
                "**ANTI-PATTERNS — Do NOT:**\n"
                "- Read more than 5 source files before making your first edit\n"
                "- Run `find` or `ls -R` on the entire repository\n"
                "- Read entire files when you only need a specific function\n"
                "- Explore 'just to understand the codebase' — you understand enough after Phase 1\n"
                "- Start over with a different approach more than once"
            )

        # Include evolved prompt fragments
        fragments = self.workspace.list_fragments()
        if fragments:
            for frag_name in fragments:
                content = self.workspace.read_fragment(frag_name)
                if content and content.strip():
                    # Fragments are already injected into system.md via evolve,
                    # but if they weren't (e.g. loaded from fragments/ dir),
                    # include them here as a fallback.
                    marker = f"<!-- evolve:{frag_name.removesuffix('.md')} -->"
                    if marker not in self.system_prompt:
                        parts.append(f"\n\n## {frag_name.removesuffix('.md').replace('_', ' ').title()}")
                        parts.append(content)

        parts.append("\n\n## Skills\n")
        if self.skills:
            parts.append(
                "You have skills learned from previous tasks. Scanning these before you start "
                "is strongly recommended — call `read_skill(skill_name)` to load any that "
                "match your situation. They contain proven strategies that can save you time.\n"
            )
            for skill in self.skills:
                parts.append(f"- **{skill.name}**: {skill.description}")
        else:
            parts.append(
                "No skills available yet. Skills are reusable strategies learned from solving tasks. "
                "When skills become available, they will be listed here with descriptions. "
                "Use `read_skill(skill_name)` to load a skill's full procedure.\n"
            )

        return "\n".join(parts)

    def _build_user_prompt(self, instance_id: str, problem_statement: str) -> str:
        # Build memory context from previous attempts
        memory_section = ""
        if self.memories:
            relevant = [m for m in self.memories if m.get("task_id") == instance_id]
            if relevant:
                memory_section = "\n\n## Previous Attempts on This Task\n"
                memory_section += (
                    "You have tried this task before. Learn from these failures:\n\n"
                )
                for mem in relevant[-5:]:  # last 5 attempts
                    memory_section += f"- {mem.get('approach_summary', '')}\n"

                    # Show specific failing tests so the agent knows what to target
                    f2p_failing = mem.get("fail_to_pass_failing", [])
                    p2p_broken = mem.get("pass_to_pass_broken", [])
                    if f2p_failing:
                        memory_section += (
                            f"  Target tests still failing: {', '.join(f2p_failing[:5])}\n"
                        )
                    if p2p_broken:
                        memory_section += (
                            f"  Tests you broke (regression): {', '.join(p2p_broken[:5])}\n"
                        )
                    # Show patch snippet if available (full feedback level)
                    patch_snippet = mem.get("patch_snippet", "")
                    if patch_snippet:
                        memory_section += f"  Your previous patch:\n```\n{patch_snippet[:500]}\n```\n"

                memory_section += (
                    "\nDo NOT repeat the same approach. "
                    "If the same tests keep failing, try a fundamentally different fix. "
                    "If you caused regressions, make a more targeted change.\n"
                )

        return f"""\
    ## Task
    Resolve the following GitHub issue by modifying the code in /testbed.

    ## Instance ID
    {instance_id}

    ## Problem Statement
    {problem_statement}
    {memory_section}
    ## Instructions
    1. Explore the repository structure at /testbed
    2. Understand the issue
    3. Find the relevant source files
    4. Implement a fix
    5. Test your fix if possible
    6. Once you are done, use your submit tool
    """
