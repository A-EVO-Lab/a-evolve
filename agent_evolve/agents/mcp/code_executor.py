"""Code execution tool for the MCP solver agent.

Provides an `execute_code` tool that lets the agent write Python code
which calls MCP tools programmatically via `call_tool(name, args)`.

This reduces context window usage for multi-step tasks by keeping
intermediate results in the execution environment instead of flowing
them through the LLM context.

The sandbox runs each `exec()` in a child process (`multiprocessing`
spawn context) and enforces a wall-clock `EXEC_TIMEOUT`. The child
is its own process group leader; on timeout the parent SIGKILLs the
process group so any threads/grandchildren spawned by the model also
die. `call_tool` inside the sandbox proxies to the parent over a
multiprocessing Pipe; the real `McpClientWrapper` stays in the parent
process.

stdlib `re` inside the sandbox is replaced by a thin shim backed by
the third-party `regex` library, which supports a per-call `timeout=`
argument. This provides defense-in-depth against catastrophic
backtracking even before the wall-clock kill fires.
"""

from __future__ import annotations

import io
import json
import multiprocessing
import os
import re as _stdlib_re
import signal
import time
import traceback
from typing import Any

from multiprocessing.connection import Connection

from strands.tools.tools import PythonAgentTool, ToolSpec

from .mcp_client import McpClientWrapper

# Max output chars returned to the LLM
MAX_OUTPUT_CHARS = 8000
# Max wall-clock per execute_code invocation (per Codex review: 300s)
EXEC_TIMEOUT = 300
# Max wall-clock per individual regex op in the sandbox (defense-in-depth)
REGEX_TIMEOUT = 30
# Max child process startup wait (spawn overhead is ~100ms; allow margin)
CHILD_STARTUP_TIMEOUT = 10
# Max wait for child to die after kill
CHILD_REAP_TIMEOUT = 5

# Set of regex APIs that accept `timeout=` keyword in the `regex` library.
_REGEX_TIMEOUT_METHODS = frozenset(
    {"findall", "search", "match", "fullmatch", "finditer", "split", "sub", "subn"}
)


class _PatternShim:
    """Wraps a compiled `regex` pattern, injecting `timeout=` into match calls."""

    __slots__ = ("_p", "_timeout")

    def __init__(self, pattern: Any, default_timeout: float) -> None:
        self._p = pattern
        self._timeout = default_timeout

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._p, name)
        if callable(attr) and name in _REGEX_TIMEOUT_METHODS:
            def wrapped(*args: Any, **kw: Any) -> Any:
                kw.setdefault("timeout", self._timeout)
                return attr(*args, **kw)
            return wrapped
        return attr


class _ReShim:
    """Drop-in shim for `re`, backed by the `regex` library with default timeout.

    Inside the sandbox, model code's `import re` / `re.findall(...)` is
    transparently routed through `regex.findall(..., timeout=REGEX_TIMEOUT)`
    so catastrophic-backtracking regexes raise `TimeoutError` instead of
    pinning a CPU core for hours.
    """

    def __init__(self, regex_mod: Any, default_timeout: float) -> None:
        self._mod = regex_mod
        self._timeout = default_timeout

    def compile(self, pattern: Any, flags: int = 0) -> _PatternShim:
        return _PatternShim(self._mod.compile(pattern, flags), self._timeout)

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._mod, name)
        if callable(attr) and name in _REGEX_TIMEOUT_METHODS:
            def wrapped(*args: Any, **kw: Any) -> Any:
                kw.setdefault("timeout", self._timeout)
                return attr(*args, **kw)
            return wrapped
        return attr


def create_code_executor_tool(
    client: McpClientWrapper,
    tool_schemas: list[dict[str, Any]],
) -> PythonAgentTool:
    """Create an execute_code tool that can call MCP tools from Python.

    The agent writes Python code using `call_tool(name, args)` to invoke
    any available MCP tool. Results stay in the execution environment;
    only `print()` output is returned to the LLM.

    Args:
        client: MCP HTTP client for tool invocation.
        tool_schemas: Available tool schemas (for the description).

    Returns:
        A PythonAgentTool wrapping the code executor.
    """
    # Build a concise tool list for the description
    tool_names = [s["name"] for s in tool_schemas]
    tool_list_str = ", ".join(tool_names[:30])
    if len(tool_names) > 30:
        tool_list_str += f", ... ({len(tool_names)} total)"

    tool_spec: ToolSpec = {
        "name": "execute_code",
        "description": (
            "Execute Python code that can call MCP tools via call_tool(name, args). "
            "Use this for tasks requiring loops, search/iteration, filtering large "
            "results, chaining 3+ tool calls, or retries. "
            "call_tool(name, args) returns a string result. "
            "Use print() to output your final answer. "
            "Available modules: json, re, math, datetime. "
            f"Available tools: {tool_list_str}"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": (
                        "Python code to execute. Use call_tool(name, args_dict) "
                        "to invoke MCP tools. Use print() for output."
                    ),
                },
            },
            "required": ["code"],
        },
    }

    def handler(tool_use: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        tool_use_id = tool_use.get("toolUseId", "unknown")
        code = tool_use.get("input", {}).get("code", "")

        if not code.strip():
            return {
                "toolUseId": tool_use_id,
                "status": "error",
                "content": [{"text": "Error: empty code"}],
            }

        output = _execute_sandboxed(code, client)

        # Truncate if too long
        if len(output) > MAX_OUTPUT_CHARS:
            output = output[:MAX_OUTPUT_CHARS] + "\n... [truncated]"

        return {
            "toolUseId": tool_use_id,
            "status": "success",
            "content": [{"text": output}],
        }

    return PythonAgentTool("execute_code", tool_spec, handler)


def _child_exec(code: str, child_conn: Connection, regex_timeout: float) -> None:
    """Run model code in a subprocess. Proxies `call_tool` over `child_conn`.

    Protocol over the pipe (child → parent):
      ("call_tool", name, args_dict) — request tool invocation
      ("done", stdout_str, stderr_str) — execution complete

    Parent responses (parent → child):
      ("call_tool_result", result_str)
      ("call_tool_error", error_msg_str)

    Becomes its own process group leader via `os.setpgrp()` so parent
    can `killpg` to take down any threads/grandchildren the model spawns.
    """
    # Defense: make the child its own process-group leader. If a malicious
    # or buggy `code` spawns threads or subprocesses, a single killpg from
    # the parent kills them all atomically (no orphan threads outlive us).
    try:
        os.setpgrp()
    except OSError:
        pass

    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()
    call_count = 0
    max_calls = 200

    def call_tool(name: str, args: dict | None = None) -> str:
        nonlocal call_count
        call_count += 1
        if call_count > max_calls:
            raise RuntimeError(f"Tool call limit exceeded ({max_calls})")
        try:
            child_conn.send(("call_tool", name, args or {}))
            msg = child_conn.recv()
            kind = msg[0]
            if kind == "call_tool_result":
                return msg[1]
            if kind == "call_tool_error":
                return msg[1]
            return f"Error: unexpected IPC message kind: {kind!r}"
        except (EOFError, BrokenPipeError) as e:
            # Parent went away (e.g., timed out and killed us). Best effort:
            # raise so the sandbox try/except catches it. The send/recv
            # below in "done" will also fail, which is fine — we're being
            # reaped.
            raise RuntimeError(f"IPC pipe closed: {e}") from e

    # Build the regex shim. `regex` is a runtime dep declared in
    # pyproject.toml under [project.optional-dependencies] mcp / all.
    import regex as _regex_lib  # local import keeps module-level cheap on `spawn`
    re_shim = _ReShim(_regex_lib, regex_timeout)

    sandbox: dict[str, Any] = {
        "__builtins__": _safe_builtins(re_shim=re_shim),
        "call_tool": call_tool,
        "json": json,
        "re": re_shim,
        "math": __import__("math"),
        "datetime": __import__("datetime"),
        "print": lambda *args, **kw: stdout_buf.write(
            " ".join(str(a) for a in args) + kw.get("end", "\n")
        ),
    }

    try:
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            exec(compile(code, "<agent-code>", "exec"), sandbox)
    except Exception:
        tb = traceback.format_exc()
        # Only include the last few lines of traceback
        tb_lines = tb.strip().split("\n")
        short_tb = "\n".join(tb_lines[-3:])
        stderr_buf.write(f"Error: {short_tb}\n")

    # Best effort: send final stdout/stderr. If parent already gave up
    # (timeout killed us), this will raise BrokenPipeError — but we're
    # dying anyway, so swallow.
    try:
        child_conn.send(("done", stdout_buf.getvalue(), stderr_buf.getvalue()))
        child_conn.close()
    except (BrokenPipeError, OSError):
        pass


def _execute_sandboxed(
    code: str,
    client: McpClientWrapper,
    exec_timeout: float = EXEC_TIMEOUT,
    regex_timeout: float = REGEX_TIMEOUT,
) -> str:
    """Run `code` in a subprocess with wall-clock timeout and call_tool proxy.

    Returns the captured stdout (plus any error trace) or a timeout
    message. Never raises — the LLM gets a clean string regardless of
    how the child died.

    `exec_timeout` and `regex_timeout` are exposed for testing; production
    code paths should rely on the module-level constants.
    """
    ctx = multiprocessing.get_context("spawn")
    parent_conn, child_conn = ctx.Pipe(duplex=True)

    proc = ctx.Process(target=_child_exec, args=(code, child_conn, regex_timeout))
    proc.start()
    # Close parent's copy of child_conn so EOF propagates when child closes.
    child_conn.close()

    deadline = time.monotonic() + exec_timeout
    stdout = ""
    stderr = ""
    timed_out = False
    died = False

    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break

            # Poll with a bounded wait so we re-check timeout periodically.
            poll_wait = min(remaining, 1.0)
            if not parent_conn.poll(timeout=poll_wait):
                # No message yet. Did the child die silently?
                if not proc.is_alive():
                    died = True
                    break
                continue

            try:
                msg = parent_conn.recv()
            except EOFError:
                died = True
                break

            kind = msg[0]
            if kind == "call_tool":
                _, name, args = msg
                # Make the (possibly slow) HTTP call. mcp_client's
                # DEFAULT_TIMEOUT=300 caps the worst-case duration so a
                # hung server cannot block past that even before we check
                # the wall-clock deadline.
                try:
                    result = client.call_tool_sync(name, args)
                    response: tuple = ("call_tool_result", result)
                except Exception as e:
                    response = ("call_tool_error", f"Error calling {name}: {e}")

                # Re-check the deadline AFTER the round-trip. If we've blown
                # the budget, don't bother replying — let the kill path fire
                # on the next loop iteration. The child is waiting on
                # `recv()` and will die cleanly when SIGKILL hits its pgrp.
                if time.monotonic() >= deadline:
                    timed_out = True
                    break

                # Send may fail if child died while we were doing the call
                # (e.g., OOM-killed by the kernel). Treat as died.
                try:
                    parent_conn.send(response)
                except (BrokenPipeError, OSError):
                    died = True
                    break
            elif kind == "done":
                _, stdout, stderr = msg
                break
            else:
                # Unknown message — bail out, treat as malformed.
                stderr = f"Internal error: unexpected IPC message kind {kind!r}"
                break
    finally:
        if timed_out:
            _kill_child_pgroup(proc)
        proc.join(timeout=CHILD_REAP_TIMEOUT)
        if proc.is_alive():
            # Still alive after join? Force SIGKILL the pgrp again.
            _kill_child_pgroup(proc)
            proc.join(timeout=CHILD_REAP_TIMEOUT)
        try:
            parent_conn.close()
        except OSError:
            pass

    if timed_out:
        return (
            f"Error: code execution exceeded {exec_timeout}s wall-clock limit "
            f"and was terminated. Possible causes: catastrophic regex "
            f"backtracking, infinite loop, or runaway computation. Try a "
            f"simpler approach with explicit bounds."
        )
    if died:
        exitcode = proc.exitcode if proc.exitcode is not None else "unknown"
        return (
            f"Error: code execution subprocess died unexpectedly "
            f"(exitcode={exitcode}). Possible causes: OOM, signal, or "
            f"native crash."
        )

    output = stdout
    if stderr:
        output = output + "\n" + stderr if output else stderr
    return output.strip() if output else "(no output)"


def _kill_child_pgroup(proc: multiprocessing.Process) -> None:
    """SIGKILL the child's process group. Best-effort; ignores races."""
    pid = proc.pid
    if pid is None:
        return
    try:
        pgid = os.getpgid(pid)
    except ProcessLookupError:
        return
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        # Process already gone, or pgid mismatch — fall back to single-proc kill.
        try:
            proc.kill()
        except (ProcessLookupError, PermissionError):
            pass


def _safe_builtins(re_shim: _ReShim | None = None) -> dict[str, Any]:
    """Return a restricted set of Python builtins with safe __import__.

    If `re_shim` is provided, `import re` inside the sandbox returns the
    regex-library-backed shim with default timeout, so model code's
    `re.findall(...)` etc. get cancellation semantics.
    """
    import builtins

    allowed = [
        "True", "False", "None",
        "abs", "all", "any", "bool", "chr", "dict", "dir",
        "enumerate", "filter", "float", "format", "frozenset",
        "getattr", "hasattr", "hash", "hex", "id", "int",
        "isinstance", "issubclass", "iter", "len", "list",
        "map", "max", "min", "next", "oct", "ord", "pow",
        "print", "range", "repr", "reversed", "round", "set",
        "slice", "sorted", "str", "sum", "tuple", "type", "zip",
        "ValueError", "TypeError", "KeyError", "IndexError",
        "RuntimeError", "StopIteration", "Exception",
        "AttributeError", "NotImplementedError",
    ]
    safe = {name: getattr(builtins, name) for name in allowed if hasattr(builtins, name)}

    # Allow import of whitelisted modules so `import json` etc. works
    _ALLOWED_MODULES = {"json", "re", "math", "datetime", "collections", "itertools", "functools"}

    def _safe_import(name, *args, **kwargs):
        if name == "re" and re_shim is not None:
            return re_shim
        if name in _ALLOWED_MODULES:
            return __import__(name)
        raise ImportError(f"Module '{name}' is not allowed. Available: {', '.join(sorted(_ALLOWED_MODULES))}")

    safe["__import__"] = _safe_import
    return safe
