"""Tools for the Terminal-Bench 2.0 agent -- strands @tool definitions."""

from __future__ import annotations

import logging
import subprocess
import threading
import time

from strands import tool

logger = logging.getLogger(__name__)

# Global container name (not thread-local because strands may run tools on asyncio threads)
_global_container_name: str | None = None
_local = threading.local()

TOOL_TIMEOUT = 60  # seconds per tool call

# Task completion flag
_task_completed = threading.local()

# Tool call counter for logging
_tool_call_counter = 0
_counter_lock = threading.Lock()


def _next_call_id() -> int:
    global _tool_call_counter
    with _counter_lock:
        _tool_call_counter += 1
        return _tool_call_counter


def reset_tool_counter() -> None:
    global _tool_call_counter
    _tool_call_counter = 0


def set_container_name(name: str) -> None:
    """Set the active container name for tool execution."""
    global _global_container_name
    _local.container_name = name
    _global_container_name = name


def _get_container_name() -> str:
    name = getattr(_local, "container_name", None)
    if name is None:
        name = _global_container_name
    if name is None:
        raise RuntimeError("Container name not set. Call set_container_name() first.")
    return name


def reset_submit_flag() -> None:
    _task_completed.done = False


def is_task_completed() -> bool:
    return getattr(_task_completed, "done", False)


@tool
def bash(command: str) -> str:
    """Execute a bash command inside the Docker container.

    Use this to explore the environment, install packages, write code,
    run programs, and complete the assigned task.
    Each call is independent -- no state is preserved between calls.
    Chain commands with && if you need sequential execution.

    Args:
        command: The bash command to execute.
    """
    call_id = _next_call_id()
    cmd_preview = command[:200] + ("..." if len(command) > 200 else "")
    logger.info("[tool #%d] bash: $ %s", call_id, cmd_preview)
    t0 = time.time()
    try:
        container = _get_container_name()
        cmd = ["docker", "exec", container, "bash", "-c", command]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=TOOL_TIMEOUT)
        output = (result.stdout or "") + (result.stderr or "")
        if not output.strip():
            output = "(no output)"
        if len(output) > 15000:
            output = output[:7000] + "\n\n... [truncated] ...\n\n" + output[-7000:]
        elapsed = time.time() - t0
        out_preview = output[:300].replace("\n", "\\n")
        logger.info("[tool #%d] bash done (%.1fs, %d chars): %s", call_id, elapsed, len(output), out_preview)
        return output
    except subprocess.TimeoutExpired:
        logger.warning("[tool #%d] bash TIMEOUT after %ds", call_id, TOOL_TIMEOUT)
        return f"ERROR: Command timed out after {TOOL_TIMEOUT} seconds."
    except Exception as e:
        logger.error("[tool #%d] bash ERROR: %s", call_id, e)
        return f"ERROR: {e}"


@tool
def python(code: str) -> str:
    """Execute Python code inside the Docker container.

    Use this to run Python scripts, do data analysis, numerical computation,
    file processing, or any task that benefits from Python over shell commands.

    Args:
        code: The Python code to execute.
    """
    call_id = _next_call_id()
    code_preview = code[:200] + ("..." if len(code) > 200 else "")
    logger.info("[tool #%d] python: >>> %s", call_id, code_preview.replace("\n", "\\n"))
    t0 = time.time()
    try:
        container = _get_container_name()
        cmd = ["docker", "exec", container, "python3", "-c", code]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=TOOL_TIMEOUT)
        output = (result.stdout or "") + (result.stderr or "")
        if not output.strip():
            output = "(no output)"
        if len(output) > 15000:
            output = output[:7000] + "\n\n... [truncated] ...\n\n" + output[-7000:]
        elapsed = time.time() - t0
        out_preview = output[:300].replace("\n", "\\n")
        logger.info("[tool #%d] python done (%.1fs, %d chars): %s", call_id, elapsed, len(output), out_preview)
        return output
    except subprocess.TimeoutExpired:
        logger.warning("[tool #%d] python TIMEOUT after %ds", call_id, TOOL_TIMEOUT)
        return f"ERROR: Command timed out after {TOOL_TIMEOUT} seconds."
    except Exception as e:
        logger.error("[tool #%d] python ERROR: %s", call_id, e)
        return f"ERROR: {e}"


@tool
def submit(answer: str) -> str:
    """Submit your answer and signal that the task is complete.

    Call this tool with "DONE" when you have finished the task.
    This will end the current task execution.

    Args:
        answer: Your submission. Use "DONE" to indicate task completion.
    """
    call_id = _next_call_id()
    logger.info("[tool #%d] submit: %s", call_id, answer)
    _task_completed.done = True
    return f"Task submitted successfully: {answer}. Execution will now stop."
