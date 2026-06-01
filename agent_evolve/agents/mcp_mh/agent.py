"""McpMHAgent -- MCP tool-calling agent with Meta-Harness hooks.

Inherits all solving logic from McpAgent.  Adds three hook points
that harness.py can override:

  build_system_prompt(base_prompt: str, skills: list[SkillMeta], task_prompt: str | None) -> str
      Override how the system prompt is assembled.

  build_user_prompt(task_id: str, task_input: str) -> str
      Override how the user prompt is built.  Return None to use default.

  pre_solve(task_metadata: dict) -> dict
      Run before the agent loop.  Can modify task metadata (e.g. change
      enabled_tools, inject extra context).  Return a dict that will be
      merged into task.metadata.

If harness.py does not exist or a hook is not defined, the default
McpAgent behavior is used.
"""

from __future__ import annotations

import concurrent.futures
import logging
from pathlib import Path
from typing import Any

from strands import Agent
from strands.models import BedrockModel

from ..mcp.agent import McpAgent
from ..mcp.docker_env import McpAtlasContainer, pull_image
from ..mcp.key_registry import KeyRegistry, classify_error, redact_secrets
from ..mcp.conversation_manager import PinnedFirstMessageManager
from ..mcp.mcp_client import McpClientWrapper
from ..mcp.tools import create_tool_wrappers
from ...types import Task, Trajectory

logger = logging.getLogger(__name__)


class McpMHAgent(McpAgent):
    """MCP tool-calling agent with dynamic harness.py hook support."""

    def _build_strands_agent(self, tools: list, task_prompt: str | None = None) -> Agent:
        model = BedrockModel(
            model_id=self.model_id,
            region_name=self.region,
            max_tokens=self.max_tokens,
        )
        system_prompt = self._build_system_prompt(task_prompt=task_prompt)
        return Agent(
            model=model,
            system_prompt=system_prompt,
            tools=tools,
            conversation_manager=PinnedFirstMessageManager(),
            callback_handler=None,  # suppress agent stdout noise in logs
        )

    def _build_system_prompt(self, task_prompt: str | None = None) -> str:
        hook = self.harness_hook("build_system_prompt")
        if hook:
            try:
                return hook(self.system_prompt, self.skills, task_prompt)
            except Exception as e:
                logger.warning("harness build_system_prompt failed: %s", e)
        return super()._build_system_prompt(task_prompt=task_prompt)

    def solve(self, task: Task, shared_client: McpClientWrapper | None = None) -> Trajectory:
        """Solve with optional harness hooks.

        Hooks:
          - pre_solve(task_metadata) -> dict: merged into task.metadata
          - build_system_prompt(base, skills, task_prompt) -> str
          - build_user_prompt(task_id, task_input) -> str | None
        """
        # Run pre_solve hook if available
        pre_solve_hook = self.harness_hook("pre_solve")
        if pre_solve_hook:
            try:
                extra_meta = pre_solve_hook(dict(task.metadata))
                if isinstance(extra_meta, dict):
                    merged = dict(task.metadata)
                    merged.update(extra_meta)
                    task = Task(id=task.id, input=task.input, metadata=merged)
                    logger.info("pre_solve hook updated metadata for %s", task.id)
            except Exception as e:
                logger.warning("harness pre_solve failed: %s", e)

        # Check for user prompt hook
        user_prompt_hook = self.harness_hook("build_user_prompt")
        custom_prompt = None
        if user_prompt_hook:
            try:
                custom_prompt = user_prompt_hook(task.id, task.input)
            except Exception as e:
                logger.warning("harness build_user_prompt failed: %s", e)

        # Run the solve flow (mostly mirroring McpAgent.solve but with hooks)
        effective_image: str | None = task.metadata.get(
            "docker_image", self.docker_image
        )
        raw_tools = task.metadata.get("enabled_tools", [])
        enabled_tools: list[str] = [
            t.get("name") if isinstance(t, dict) else str(t)
            for t in raw_tools
            if (isinstance(t, dict) and t.get("name")) or (not isinstance(t, dict))
        ]

        env_vars: dict[str, str] = {}
        if self.key_registry:
            server_names = task.metadata.get("mcp_server_names", [])
            env_vars = self.key_registry.get_keys_for_servers(server_names)

        effective_client = shared_client or self.shared_client
        container: McpAtlasContainer | None = None
        owns_client = effective_client is None
        client = effective_client or McpClientWrapper()

        try:
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

            all_tools = client.list_tools()
            if not all_tools:
                return self._postprocess_trajectory(
                    Trajectory(
                        task_id=task.id, output="",
                        steps=[{"error": "No tools returned from service"}],
                    ),
                    env_vars,
                )

            if enabled_tools:
                enabled_set = set(enabled_tools)
                filtered = [t for t in all_tools if t.get("name") in enabled_set]
            else:
                filtered = all_tools

            if not filtered:
                return self._postprocess_trajectory(
                    Trajectory(
                        task_id=task.id, output="",
                        steps=[{"error": f"No matching tools (enabled: {len(enabled_tools)}, available: {len(all_tools)})"}],
                    ),
                    env_vars,
                )

            tools = create_tool_wrappers(filtered, client)

            # Build agent — _build_system_prompt is hooked above
            agent = self._build_strands_agent(tools, task_prompt=task.input)

            # Use custom prompt if hook provided one
            prompt = custom_prompt if custom_prompt else task.input

            logger.info(
                "Solving %s with %d tools (harness=%s)",
                task.id, len(filtered),
                "loaded" if self.harness else "none",
            )

            import time as _time
            t0 = _time.time()
            timeout_sec = task.metadata.get("agent_timeout_sec", 600)
            response = self._run_with_timeout(agent, prompt, timeout_sec)
            solve_elapsed = _time.time() - t0
            logger.info("Agent finished in %.1fs", solve_elapsed)

            output = str(response) if response else ""
            usage: dict[str, Any] = {}
            if response:
                try:
                    u = response.metrics.accumulated_usage
                    usage = {
                        "input_tokens": u.get("inputTokens", 0),
                        "output_tokens": u.get("outputTokens", 0),
                        "total_tokens": u.get("totalTokens", 0),
                    }
                except Exception:
                    pass

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
                logger.debug("Failed to extract conversation history", exc_info=True)

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

    @staticmethod
    def _run_with_timeout(agent: Agent, prompt: str, timeout_sec: int, max_retries: int = 3):
        """Run the agent with a wall-clock timeout and retry on transient errors."""
        def _run():
            return agent(prompt)

        for attempt in range(1, max_retries + 1):
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_run)
                try:
                    return future.result(timeout=timeout_sec)
                except concurrent.futures.TimeoutError:
                    logger.warning("Agent timed out after %ds", timeout_sec)
                    return None
                except Exception as e:
                    err_str = str(e)[:200]
                    is_transient = any(k in err_str for k in (
                        "timed out", "Read timed out", "ThrottlingException",
                        "ServiceUnavailableException", "TooManyRequestsException",
                        "ConnectionError", "ConnectionReset",
                    ))
                    if is_transient and attempt < max_retries:
                        wait = 2 ** attempt
                        logger.warning(
                            "Transient error (attempt %d/%d), retrying in %ds: %s",
                            attempt, max_retries, wait, err_str,
                        )
                        import time as _time
                        _time.sleep(wait)
                        continue
                    logger.error("Agent exception: %s", err_str)
                    return None
        return None
