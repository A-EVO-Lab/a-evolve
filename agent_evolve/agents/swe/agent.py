"""SWE-bench coding agent -- uses strands-agents at runtime.

The framework layer (BaseAgent) loads prompts/skills/memory from the file
system contract.  This concrete agent then assembles those pieces into a
real ``strands.Agent`` and calls it, exactly like CodeDojo's original
``solve_instance``.  This keeps framework-level code strands-free while
ensuring *this* agent behaves identically to the CodeDojo version.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from strands import Agent
from strands.models import BedrockModel

from ...protocol.base_agent import BaseAgent
from ...types import Task, Trajectory
from .docker_env import SWEBenchContainer, pull_image
from .thinking import reset_thinking, sequentialthinking
from .tools import bash, set_container_name, submit, reset_submit_state, was_submitted, get_submitted_patch

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
    ):
        super().__init__(workspace_dir)
        self.model_id = model_id
        self.region = region
        self.max_tokens = max_tokens

    def _build_strands_agent(self) -> Agent:
        """Create a strands Agent wired with the workspace's current state."""
        model = BedrockModel(
            model_id=self.model_id,
            region_name=self.region,
            max_tokens=self.max_tokens,
        )

        system_prompt = self._build_system_prompt()
        tools = [bash, sequentialthinking, submit]

        return Agent(
            model=model,
            system_prompt=system_prompt,
            tools=tools,
        )

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
        steps: list[dict] = []

        with container:
            set_container_name(container.container_name)
            reset_thinking()
            reset_submit_state()

            agent = self._build_strands_agent()
            user_prompt = self._build_user_prompt(instance_id, problem_statement)

            logger.info("Solving %s with image %s", instance_id, docker_image)
            response = agent(user_prompt)

            usage = {}
            try:
                u = response.metrics.accumulated_usage
                usage = {
                    "input_tokens": u.get("inputTokens", 0),
                    "output_tokens": u.get("outputTokens", 0),
                    "total_tokens": u.get("totalTokens", 0),
                    "cache_read_input_tokens": u.get("cacheReadInputTokens", 0),
                    "cache_write_input_tokens": u.get("cacheWriteInputTokens", 0),
                }
            except Exception:
                pass

            patch = get_submitted_patch() or container.get_diff()

            steps.append({"llm_output": str(response)[:2000], "usage": usage})

            if not patch.strip():
                logger.warning("No changes detected for %s", instance_id)

            self.remember(
                f"Solved {instance_id}: patch={'non-empty' if patch.strip() else 'empty'}, "
                f"tokens={usage.get('input_tokens', 0) + usage.get('output_tokens', 0)}",
                category="episodic",
                task_id=instance_id,
            )

        return Trajectory(task_id=task.id, output=patch, steps=steps)

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

    def _build_user_prompt(self, instance_id: str, problem_statement: str) -> str:
        memory_context = ""
        if self.memories:
            memory_context = "\n\n## Relevant Memories from Previous Tasks\n"
            for m in self.memories[-5:]:
                memory_context += f"- {m.get('content', '')}\n"

        return f"""\
## Task
Resolve the following GitHub issue by modifying the code in /testbed.

## Instance ID
{instance_id}

## Problem Statement
{problem_statement}
{memory_context}
## Instructions
1. Explore the repository structure at /testbed
2. Understand the issue
3. Find the relevant source files
4. Implement a fix
5. Test your fix if possible
6. Once you are done, use your submit tool
"""
