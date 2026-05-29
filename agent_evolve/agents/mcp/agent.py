"""MCP tool-calling agent -- uses strands-agents at runtime.

Connects to the MCP-Atlas agent-environment HTTP service to discover
and invoke tools.  The service runs inside a Docker container on port
1984 and proxies calls to the underlying MCP servers.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from strands import Agent
from strands.models import BedrockModel

from ...protocol.base_agent import BaseAgent
from ...types import Task, Trajectory
from .docker_env import McpAtlasContainer, pull_image
from .key_registry import KeyRegistry, classify_error, redact_secrets
from .conversation_manager import PinnedFirstMessageManager
from .mcp_client import McpClientWrapper
from .tools import create_tool_wrappers

logger = logging.getLogger(__name__)

os.environ.setdefault("BYPASS_TOOL_CONSENT", "true")


def _lazy_skill_mode() -> bool:
    """Lazy = list-all + read_skill tool (mirrors SweAgent).

    Default ON. Set MCP_SKILL_LAZY=0 to fall back to the old keyword-overlap
    top-3 inline behaviour.
    """
    return os.environ.get("MCP_SKILL_LAZY", "1") != "0"


class McpAgent(BaseAgent):
    """Reference agent for MCP tool-calling tasks.

    Reads system prompt, skills, and memories from the workspace via
    BaseAgent, then connects to the MCP-Atlas HTTP service, discovers
    tools, wraps them as strands @tool functions, and builds a
    strands.Agent to solve the task.
    """

    def __init__(
        self,
        workspace_dir: str | Path,
        model_id: str = "us.anthropic.claude-sonnet-4-20250514-v1:0",
        region: str = "us-west-2",
        max_tokens: int = 16384,
        docker_image: str | None = None,
        key_registry: KeyRegistry | None = None,
        shared_client: McpClientWrapper | None = None,
    ):
        super().__init__(workspace_dir)
        self.model_id = model_id
        self.region = region
        self.max_tokens = max_tokens
        self.docker_image = docker_image
        self.key_registry = key_registry
        self.shared_client = shared_client

    def _build_system_prompt(self, task_prompt: str | None = None) -> str:
        """Assemble the full system prompt from workspace files.

        If task_prompt is provided, only inject skills whose description
        is relevant to the task (keyword matching). This reduces prompt
        size and noise by not injecting irrelevant domain skills.

        Args:
            task_prompt: Optional task input text for skill filtering
        """
        parts = [self.system_prompt]

        if self.skills:
            if _lazy_skill_mode():
                # Lazy: list every skill as `- name: description`; the LLM
                # pulls bodies on demand via the read_skill tool registered
                # in _build_strands_agent. Mirrors SweAgent.
                parts.append("\n\n## Available Skills\n")
                parts.append(
                    "You have skills learned from previous tasks. Scan the list below "
                    "before you start; call `read_skill(skill_name)` to load the full "
                    "procedure of any that match your situation. The descriptions here "
                    "are one-line hints — you must call read_skill before applying.\n"
                )
                for skill in self.skills:
                    parts.append(f"- **{skill.name}**: {skill.description}")
            else:
                # Eager (legacy): keyword-overlap top-3 inline.
                if task_prompt:
                    selected = self._select_relevant_skills(task_prompt, max_skills=3)
                else:
                    selected = self.skills  # Fallback: inject all

                if selected:  # Only add section if we have skills to inject
                    parts.append("\n\n## Available Skills\n")
                    parts.append(
                        "You have specialized skills. Review them when facing relevant challenges.\n"
                    )
                    for skill in selected:
                        parts.append(f"- **{skill.name}**: {skill.description}")
                        content = self.get_skill_content(skill.name)
                        if content:
                            body = content.split("---", 2)[-1].strip() if "---" in content else content
                            parts.append(f"\n{body}\n")

        if self.memories:
            parts.append("\n\n## Relevant Memories\n")
            for m in self.memories[-10:]:
                parts.append(f"- {m.get('content', '')}")

        return "\n".join(parts)

    def _select_relevant_skills(self, task_prompt: str, max_skills: int = 3) -> list:
        """Select skills most relevant to the task based on keyword overlap.

        Only injects skills with relevance score >= 2 (at least 2 keyword matches).
        Tasks with no matching skills get the clean vanilla prompt.

        Approach from terminal agent evolution (bing_dev_mar10):
        - v10: Fixed -7 task regression by only injecting skills with score > 0
        - v22: "More skills ≠ better" — filtering prevents dilution

        Args:
            task_prompt: The task input text
            max_skills: Maximum number of skills to inject (default: 3)

        Returns:
            List of selected skills, sorted by relevance score
        """
        task_lower = task_prompt.lower()

        scored = []
        for skill in self.skills:
            # Extract keywords from skill name and description
            keywords = skill.name.replace("-", " ").split()
            keywords += skill.description.lower().split()

            # Count keyword matches (only words longer than 3 chars to avoid noise)
            score = sum(1 for kw in keywords if kw in task_lower and len(kw) > 3)
            scored.append((score, skill))

        # Sort by relevance score (highest first)
        scored.sort(key=lambda x: x[0], reverse=True)

        # Only inject skills with score >= 2 (at least 2 keyword matches)
        # This threshold prevents random/irrelevant skill injection
        selected = []
        for score, skill in scored[:max_skills]:
            if score >= 2:
                selected.append(skill)

        if selected:
            logger.debug(
                "Selected %d/%d skills: %s",
                len(selected),
                len(self.skills),
                [f"{s.name}(score={sc})" for sc, s in scored[:max_skills] if sc >= 2]
            )
        else:
            logger.debug("No relevant skills (using vanilla prompt)")

        return selected

    def _build_strands_agent(self, tools: list, task_prompt: str | None = None) -> Agent:
        """Create a strands Agent with BedrockModel and the given tools.

        Args:
            tools: List of tool wrappers
            task_prompt: Optional task input for skill selection
        """
        from ...llm._bedrock_config import bedrock_boto_config

        model = BedrockModel(
            model_id=self.model_id,
            region_name=self.region,
            max_tokens=self.max_tokens,
            boto_client_config=bedrock_boto_config(),
        )
        system_prompt = self._build_system_prompt(task_prompt=task_prompt)

        # In lazy mode, register a read_skill tool so the LLM can pull bodies
        # by name (system prompt only carries the - name: description list).
        if _lazy_skill_mode() and self.skills:
            from strands import tool

            skill_data: dict[str, str] = {}
            for skill in self.skills:
                content = self.get_skill_content(skill.name)
                if content:
                    body = content.split("---", 2)[-1].strip() if "---" in content else content
                    skill_data[skill.name] = body

            # Tolerant lookup (case + whitespace).
            norm_lookup = {n.strip().lower(): n for n in skill_data}

            @tool
            def read_skill(skill_name: str) -> str:
                """Read the full procedure for a skill by name.

                Call this BEFORE applying a skill — the system prompt only
                lists short descriptions; you must call read_skill to see
                the actual procedure body.

                Args:
                    skill_name: Name of the skill to read (case-insensitive,
                        whitespace tolerant).
                """
                key = (skill_name or "").strip().lower()
                actual = norm_lookup.get(key)
                if actual and actual in skill_data:
                    return skill_data[actual]
                available = ", ".join(skill_data.keys())
                return f"Skill '{skill_name}' not found. Available: {available}"

            tools = list(tools) + [read_skill]

        return Agent(
            model=model,
            system_prompt=system_prompt,
            tools=tools,
            conversation_manager=PinnedFirstMessageManager(),
        )

    def solve(self, task: Task, shared_client: McpClientWrapper | None = None) -> Trajectory:
        """Solve an MCP tool-calling task via the HTTP service.

        If *shared_client* is provided the agent reuses it (and the
        caller is responsible for the container lifecycle).  Falls back
        to self.shared_client if set.  Otherwise the agent starts its
        own container per task (original behaviour).
        """
        effective_image: str | None = task.metadata.get(
            "docker_image", self.docker_image
        )
        raw_tools = task.metadata.get("enabled_tools", [])
        enabled_tools: list[str] = [
            t.get("name") if isinstance(t, dict) else str(t)
            for t in raw_tools
            if (isinstance(t, dict) and t.get("name")) or (not isinstance(t, dict))
        ]

        # Get env vars for task's MCP servers from key registry
        env_vars: dict[str, str] = {}
        if self.key_registry:
            server_names = task.metadata.get("mcp_server_names", [])
            env_vars = self.key_registry.get_keys_for_servers(server_names)

        effective_client = shared_client or self.shared_client
        container: McpAtlasContainer | None = None
        owns_client = effective_client is None
        client = effective_client or McpClientWrapper()

        try:
            # Start Docker container only if no shared client and image provided
            if owns_client and effective_image:
                if not pull_image(effective_image):
                    return self._postprocess_trajectory(
                        Trajectory(
                            task_id=task.id, output="",
                            steps=[{"error": f"Failed to pull image: {effective_image}"}],
                        ),
                        env_vars,
                    )
                container = McpAtlasContainer(effective_image, env_vars=env_vars)
                container.start()
                client = McpClientWrapper(base_url=container.base_url)

            # Discover tools
            all_tools = client.list_tools()
            if not all_tools:
                return self._postprocess_trajectory(
                    Trajectory(
                        task_id=task.id, output="",
                        steps=[{"error": "No tools returned from service"}],
                    ),
                    env_vars,
                )

            # Filter to only the tools enabled for this task
            if enabled_tools:
                enabled_set = set(enabled_tools)
                filtered = [t for t in all_tools if t.get("name") in enabled_set]
            else:
                filtered = all_tools

            if not filtered:
                return self._postprocess_trajectory(
                    Trajectory(
                        task_id=task.id, output="",
                        steps=[{"error": f"No matching tools for task (enabled: {len(enabled_tools)}, available: {len(all_tools)})"}],
                    ),
                    env_vars,
                )

            # Build strands agent with tool wrappers and task-specific skill selection
            tools = create_tool_wrappers(filtered, client)
            agent = self._build_strands_agent(tools, task_prompt=task.input)

            logger.info("Solving %s with %d tools", task.id, len(filtered))
            response = agent(task.input)

            # Extract results
            output = str(response)
            usage: dict[str, Any] = {}
            try:
                u = response.metrics.accumulated_usage
                usage = {
                    "input_tokens": u.get("inputTokens", 0),
                    "output_tokens": u.get("outputTokens", 0),
                    "total_tokens": u.get("totalTokens", 0),
                }
            except Exception:
                pass

            # Capture full conversation trajectory from strands agent
            steps: list[dict[str, Any]] = []
            try:
                for msg in agent.messages:
                    step: dict[str, Any] = {"role": msg.get("role")}
                    for block in msg.get("content", []):
                        if "toolUse" in block:
                            tu = block["toolUse"]
                            step.setdefault("tool_calls", []).append({
                                "tool": tu.get("name"),
                                "toolUseId": tu.get("toolUseId"),
                                "input": tu.get("input"),
                            })
                        elif "toolResult" in block:
                            tr = block["toolResult"]
                            # Truncate large tool results to keep file sizes reasonable
                            result_content = tr.get("content", [])
                            truncated = []
                            for item in (result_content if isinstance(result_content, list) else [result_content]):
                                if isinstance(item, dict) and "text" in item:
                                    text = item["text"]
                                    truncated.append({"text": text[:5000] + ("... [truncated]" if len(text) > 5000 else "")})
                                else:
                                    truncated.append(item)
                            step.setdefault("tool_results", []).append({
                                "toolUseId": tr.get("toolUseId"),
                                "status": tr.get("status"),
                                "content": truncated,
                            })
                        elif "text" in block:
                            step["text"] = block["text"][:5000] + ("... [truncated]" if len(block["text"]) > 5000 else "")
                    steps.append(step)
            except Exception:
                logger.debug("Failed to extract full conversation history", exc_info=True)

            # Always append usage summary as the final step
            steps.append({"llm_output": output[:2000], "usage": usage})

            self.remember(
                f"Solved {task.id}: output={'non-empty' if output.strip() else 'empty'}",
                category="episodic",
                task_id=task.id,
            )

            return self._postprocess_trajectory(
                Trajectory(task_id=task.id, output=output, steps=steps),
                env_vars,
            )

        except Exception as e:
            logger.error("solve() failed for %s: %s", task.id, e)
            return self._postprocess_trajectory(
                Trajectory(
                    task_id=task.id, output="", steps=[{"error": str(e)}]
                ),
                env_vars,
            )
        finally:
            if owns_client:
                client.close()
            if container is not None:
                try:
                    container.stop()
                except Exception:
                    pass

    def _postprocess_trajectory(
        self,
        trajectory: Trajectory,
        env_vars: dict[str, str],
    ) -> Trajectory:
        """Classify errors and redact secrets in trajectory steps."""
        secret_values = {v for v in env_vars.values() if v}

        for step in trajectory.steps:
            error_text = step.get("error")
            if isinstance(error_text, str):
                step["failure_reason"] = classify_error(error_text)
                step["error"] = redact_secrets(error_text, secret_values)

        return trajectory
