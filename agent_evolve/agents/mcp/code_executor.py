"""Code execution tool for the MCP solver agent.

Provides an `execute_code` tool that lets the agent write Python code
which calls MCP tools programmatically via `call_tool(name, args)`.

This reduces context window usage for multi-step tasks by keeping
intermediate results in the execution environment instead of flowing
them through the LLM context.
"""

from __future__ import annotations

import io
import json
import re
import traceback
from contextlib import redirect_stdout, redirect_stderr
from typing import Any

from strands.tools.tools import PythonAgentTool, ToolSpec

from .mcp_client import McpClientWrapper

# Max output chars returned to the LLM
MAX_OUTPUT_CHARS = 8000
# Max execution time in seconds
EXEC_TIMEOUT = 120


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


def _execute_sandboxed(code: str, client: McpClientWrapper) -> str:
    """Execute code in a restricted sandbox with MCP tool access."""
    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()

    call_count = 0
    max_calls = 200  # safety limit

    def call_tool(name: str, args: dict | None = None) -> str:
        nonlocal call_count
        call_count += 1
        if call_count > max_calls:
            raise RuntimeError(f"Tool call limit exceeded ({max_calls})")
        try:
            return client.call_tool_sync(name, args or {})
        except Exception as e:
            return f"Error calling {name}: {e}"

    # Build sandbox globals
    sandbox: dict[str, Any] = {
        "__builtins__": _safe_builtins(),
        "call_tool": call_tool,
        "json": json,
        "re": re,
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
    except Exception as e:
        tb = traceback.format_exc()
        # Only include the last few lines of traceback
        tb_lines = tb.strip().split("\n")
        short_tb = "\n".join(tb_lines[-3:])
        stderr_buf.write(f"Error: {short_tb}\n")

    output = stdout_buf.getvalue()
    errors = stderr_buf.getvalue()

    if errors:
        output = output + "\n" + errors if output else errors

    return output.strip() if output else "(no output)"


def _safe_builtins() -> dict[str, Any]:
    """Return a restricted set of Python builtins with safe __import__."""
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
        if name in _ALLOWED_MODULES:
            return __import__(name)
        raise ImportError(f"Module '{name}' is not allowed. Available: {', '.join(sorted(_ALLOWED_MODULES))}")

    safe["__import__"] = _safe_import
    return safe
