"""Terminal-Bench 2.0 coding agent -- uses strands-agents at runtime.

The framework layer (BaseAgent) loads prompts/skills/memory from the file
system contract. This concrete agent assembles those pieces into a real
strands Agent and calls it to solve Terminal-Bench challenges inside Docker
containers.
"""

from __future__ import annotations

import concurrent.futures
import logging
import os
from pathlib import Path

from strands import Agent
from strands.models import BedrockModel

from ...protocol.base_agent import BaseAgent
from ...types import Task, Trajectory
from .docker_env import TB2Container, pull_image
from .thinking import reset_thinking, sequentialthinking
from .tools import bash, python, submit, set_container_name, reset_submit_flag, reset_tool_counter

logger = logging.getLogger(__name__)

os.environ.setdefault("BYPASS_TOOL_CONSENT", "true")

SUBMIT_PROMPT = """
When you have completed the task, call the `submit()` tool with "DONE" as argument to report that the task is complete.
"""


class TerminalAgent(BaseAgent):
    """Reference agent for Terminal-Bench 2.0 tasks.

    Reads system prompt, skills, and memories from the workspace via BaseAgent,
    then builds a strands Agent with those assets at solve-time.
    """

    def __init__(
        self,
        workspace_dir: str | Path,
        model_id: str = "us.anthropic.claude-sonnet-4-20250514-v1:0",
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
        tools = [bash, python, submit, sequentialthinking]

        return Agent(
            model=model,
            system_prompt=system_prompt,
            tools=tools,
        )

    def solve(self, task: Task) -> Trajectory:
        """Solve a Terminal-Bench 2.0 task.

        Expects task.metadata to contain:
          - docker_image: str (Docker image name)
          - test_sh_path: str (local path to test.sh)
          - test_py_path: str (optional, local path to test_outputs.py)
        """
        docker_image = task.metadata.get("docker_image", "")
        task_name = task.metadata.get("task_name", task.id)
        test_sh_path = task.metadata.get("test_sh_path", "")
        test_py_path = task.metadata.get("test_py_path")
        timeout_sec = task.metadata.get("agent_timeout_sec", 900)

        if not docker_image:
            raise ValueError(
                f"Task {task.id} missing 'docker_image' in metadata. "
                "TerminalAgent requires a Docker image."
            )

        pull_image(docker_image)
        container = TB2Container(docker_image)
        steps: list[dict] = []

        with container:
            set_container_name(container.container_name)
            reset_submit_flag()
            reset_thinking()
            reset_tool_counter()

            # NOTE: Test files are NOT copied before solving — only during evaluation
            # to prevent the agent from reading test expectations.

            agent = self._build_strands_agent()
            user_prompt = self._build_user_prompt(task_name, task.input)

            logger.info("Solving %s with image %s (timeout=%ds)", task_name, docker_image, timeout_sec)
            logger.info("Container: %s", container.container_name)
            logger.info("System prompt: %d chars, skills: %d, memories: %d",
                        len(self.system_prompt), len(self.skills), len(self.memories))

            import time as _time
            t0 = _time.time()

            # Run agent with wall-clock timeout
            response = self._run_with_timeout(agent, user_prompt, timeout_sec)

            solve_elapsed = _time.time() - t0
            logger.info("Agent finished in %.1fs (response=%s)",
                        solve_elapsed, "OK" if response else "TIMEOUT/ERROR")

            # Extract usage
            usage = {}
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

            # Run evaluation
            passed = False
            eval_output = ""
            if test_sh_path and os.path.exists(test_sh_path):
                # Copy test files NOW (only for evaluation)
                self._copy_test_files(container, test_sh_path, test_py_path)
                logger.info("Running evaluation (test.sh)...")
                eval_t0 = _time.time()
                verifier_timeout = task.metadata.get("verifier_timeout_sec", 900)
                passed, eval_output = container.run_tests_with_retry(
                    test_sh_path, timeout=verifier_timeout, max_retries=3
                )
                eval_elapsed = _time.time() - eval_t0
                logger.info("Evaluation done in %.1fs: %s", eval_elapsed, "PASS" if passed else "FAIL")
            else:
                logger.warning("No test.sh found, skipping evaluation")

            # Capture the full strands conversation for logging
            conversation = []
            try:
                conversation = _extract_conversation(agent.messages)
            except Exception:
                logger.debug("Could not extract conversation from strands agent")

            steps.append({
                "llm_output": str(response)[:2000] if response else "(timeout)",
                "usage": usage,
                "passed": passed,
                "eval_output": eval_output[-2000:] if len(eval_output) > 2000 else eval_output,
                "conversation": conversation,
            })

            self.remember(
                f"Solved {task_name}: passed={passed}, "
                f"tokens={usage.get('input_tokens', 0) + usage.get('output_tokens', 0)}",
                category="episodic",
                task_id=task_name,
            )

        # output is the eval result summary for the evolution pipeline
        output = f"passed={passed}\n{eval_output}"
        return Trajectory(task_id=task.id, output=output, steps=steps)

    def _build_system_prompt(self) -> str:
        """Assemble the full system prompt from workspace files."""
        parts = [self.system_prompt, SUBMIT_PROMPT]

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

    def _build_user_prompt(self, task_name: str, prompt: str) -> str:
        memory_context = ""
        if self.memories:
            memory_context = "\n\n## Relevant Memories from Previous Tasks\n"
            for m in self.memories[-5:]:
                memory_context += f"- {m.get('content', '')}\n"

        return f"{prompt}\n{memory_context}"

    @staticmethod
    def _copy_test_files(
        container: TB2Container,
        test_sh_path: str,
        test_py_path: str | None,
    ) -> None:
        """Copy test files into the container."""
        container.exec("mkdir -p /tests /logs/verifier")
        if test_sh_path and os.path.exists(test_sh_path):
            container.copy_to(test_sh_path, "/tests/test.sh")
        if test_py_path and os.path.exists(test_py_path):
            container.copy_to(test_py_path, "/tests/test_outputs.py")

    @staticmethod
    def _run_with_timeout(agent: Agent, prompt: str, timeout_sec: int):
        """Run the agent with a wall-clock timeout."""
        def _run():
            return agent(prompt)

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_run)
            try:
                return future.result(timeout=timeout_sec)
            except concurrent.futures.TimeoutError:
                logger.warning("Agent timed out after %ds", timeout_sec)
                return None
            except Exception as e:
                logger.error("Agent exception: %s", str(e)[:200])
                return None


def _extract_conversation(messages: list) -> list[dict]:
    """Extract a standardized conversation log from strands agent messages.

    Converts from Bedrock/strands internal format to the standard
    assistant/tool convention:

      - Assistant text-only turns:
          {"role": "assistant", "content": "reasoning text..."}

      - Assistant tool-call turns:
          {"role": "assistant", "content": "reasoning text...",
           "tool_calls": [{"id": "...", "function": "bash",
                           "arguments": {...}, "type": "function"}]}

      - Tool result turns (one per tool call):
          {"role": "tool", "tool_call_id": "...", "function": "bash",
           "content": "output text..."}

      - User turns:
          {"role": "user", "content": "prompt text..."}
    """
    import json as _json

    conv = []
    for msg in messages:
        role = msg.get("role", "unknown")
        raw_content = msg.get("content", [])

        # Classify what's in this message
        text_parts = []
        tool_uses = []
        tool_results = []

        for b in raw_content:
            if isinstance(b, str):
                text_parts.append(b)
            elif isinstance(b, dict):
                if "text" in b:
                    text_parts.append(b["text"])
                elif "toolUse" in b:
                    tool_uses.append(b["toolUse"])
                elif "toolResult" in b:
                    tool_results.append(b["toolResult"])

        # Case 1: assistant message with tool calls
        if role == "assistant" and tool_uses:
            entry: dict = {"role": "assistant"}
            if text_parts:
                entry["content"] = "\n".join(text_parts)
            else:
                entry["content"] = ""
            entry["tool_calls"] = []
            for tu in tool_uses:
                inp = tu.get("input", {})
                inp_str = _json.dumps(inp)
                if len(inp_str) > 2000:
                    inp = {"_truncated": inp_str[:2000] + "..."}
                entry["tool_calls"].append({
                    "id": tu.get("toolUseId", ""),
                    "function": tu.get("name", ""),
                    "arguments": inp,
                    "type": "function",
                })
            conv.append(entry)

        # Case 2: user message containing tool results (Bedrock format)
        #         -> emit as role: "tool" entries
        elif tool_results:
            for tr in tool_results:
                result_parts = []
                for c in tr.get("content", []):
                    if isinstance(c, dict) and "text" in c:
                        txt = c["text"]
                        if len(txt) > 3000:
                            txt = txt[:3000] + "\n...[truncated]"
                        result_parts.append(txt)
                # Try to find the matching tool_use to get the function name
                tool_use_id = tr.get("toolUseId", "")
                func_name = _find_tool_name(conv, tool_use_id)
                conv.append({
                    "role": "tool",
                    "tool_call_id": tool_use_id,
                    "function": func_name,
                    "content": "\n".join(result_parts),
                })

        # Case 3: plain text message (user prompt or assistant text-only)
        elif text_parts:
            conv.append({
                "role": role,
                "content": "\n".join(text_parts),
            })

    return conv


def _find_tool_name(conv: list[dict], tool_use_id: str) -> str:
    """Walk backwards through conversation to find the function name for a tool_call_id."""
    for entry in reversed(conv):
        for tc in entry.get("tool_calls", []):
            if tc.get("id") == tool_use_id:
                return tc.get("function", "")
    return ""
