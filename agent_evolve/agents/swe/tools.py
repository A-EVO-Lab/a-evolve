"""Tools for the SWE agent -- strands @tool definitions.

Kept identical to CodeDojo/swe-agent/swe_agent/tools.py so that the
strands Agent event loop handles them exactly the same way.
"""

from __future__ import annotations

import subprocess

from strands import tool

# Global (not thread-local) because strands executes tools on asyncio threads.
_container_name: str | None = None


def set_container_name(name: str) -> None:
    """Set the active container name for bash execution."""
    global _container_name
    _container_name = name


def get_container_name() -> str:
    if _container_name is None:
        raise RuntimeError("Container name not set. Call set_container_name() first.")
    return _container_name


@tool
def bash(command: str, workdir: str = "/testbed") -> str:
    """Execute a bash command inside the SWE-bench Docker container.

    Use this to explore the codebase, edit files, run tests, and generate patches.
    The repository is located at /testbed.

    Args:
        command: The bash command to execute.
        workdir: Working directory for the command. Defaults to /testbed.
    """
    try:
        result = subprocess.run(
            ["docker", "exec", "-w", workdir, get_container_name(), "bash", "-c", command],
            capture_output=True,
            text=True,
            timeout=300,
        )
        output = (result.stdout or "") + (result.stderr or "")
        if not output.strip():
            output = "(no output)"
        if len(output) > 15000:
            output = output[:7000] + "\n\n... [truncated] ...\n\n" + output[-7000:]
        return output
    except subprocess.TimeoutExpired:
        return "ERROR: Command timed out after 300 seconds."
    except Exception as e:
        return f"ERROR: {e}"

# ── Submit tool ──────────────────────────────────────────────────────────
# Provides a clean termination signal, similar to Inspect AI's submit tool.

_submitted: bool = False
_submit_patch: str | None = None


def reset_submit_state() -> None:
    """Reset submit state between tasks."""
    global _submitted, _submit_patch
    _submitted = False
    _submit_patch = None


def was_submitted() -> bool:
    """Check if the agent explicitly submitted."""
    return _submitted


def get_submitted_patch() -> str | None:
    """Return the patch captured at submit time, if any."""
    return _submit_patch


@tool
def submit(confirmation: str = "done") -> str:
    """Submit your solution and end the task.

    Call this tool when you have finished fixing the issue and verified your
    changes (e.g. by running relevant tests or reviewing git diff).
    After calling submit, do NOT call any more tools.

    Args:
        confirmation: A short summary of what you changed and why.
    """
    global _submitted, _submit_patch
    _submitted = True
    # Capture the current diff at submit time
    try:
        import subprocess
        result = subprocess.run(
            ["docker", "exec", "-w", "/testbed", get_container_name(),
             "bash", "-c", "git diff"],
            capture_output=True, text=True, timeout=30,
        )
        _submit_patch = result.stdout or ""
    except Exception:
        _submit_patch = None
    return (
        f"Solution submitted: {confirmation}\n"
        "You are done. Do NOT call any more tools."
    )

