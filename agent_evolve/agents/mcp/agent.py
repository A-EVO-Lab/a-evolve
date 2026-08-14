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
from .mcp_client import McpClientWrapper
from .tools import create_tool_wrappers

logger = logging.getLogger(__name__)

os.environ.setdefault("BYPASS_TOOL_CONSENT", "true")


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
    ):
        super().__init__(workspace_dir)
        self.model_id = model_id
        self.region = region
        self.max_tokens = max_tokens
        self.docker_image = docker_image
        self.key_registry = key_registry

    def _build_system_prompt(self) -> str:
        """Assemble the full system prompt from workspace files."""
        parts = [self.system_prompt]

        if self.skills:
            parts.append("\n\n## Available Skills\n")
            parts.append(
                "You have specialized skills. Review them when facing relevant challenges.\n"
            )
            for skill in self.skills:
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

    def _build_strands_agent(self, tools: list) -> Agent:
        """Create a strands Agent with BedrockModel and the given tools."""
        model = BedrockModel(
            model_id=self.model_id,
            region_name=self.region,
            max_tokens=self.max_tokens,
        )
        system_prompt = self._build_system_prompt()
        return Agent(
            model=model,
            system_prompt=system_prompt,
            tools=tools,
        )

    def solve(self, task: Task, shared_client: McpClientWrapper | None = None) -> Trajectory:
        """Solve an MCP tool-calling task via the HTTP service.

        If *shared_client* is provided the agent reuses it (and the
        caller is responsible for the container lifecycle).  Otherwise
        the agent starts its own container per task (original behaviour).
        """
        effective_image: str | None = task.metadata.get(
            "docker_image", self.docker_image
        )
        enabled_tools: list[str] = task.metadata.get("enabled_tools", [])

        # Get env vars for task's MCP servers from key registry
        env_vars: dict[str, str] = {}
        if self.key_registry:
            server_names = task.metadata.get("mcp_server_names", [])
            env_vars = self.key_registry.get_keys_for_servers(server_names)

        container: McpAtlasContainer | None = None
        owns_client = shared_client is None
        client = shared_client or McpClientWrapper()

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

            # Build strands agent with tool wrappers
            tools = create_tool_wrappers(filtered, client)
            agent = self._build_strands_agent(tools)

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
