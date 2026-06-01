"""Dynamic strands tool wrapper factory for MCP tools.

Creates ``PythonAgentTool`` instances at runtime from MCP tool schemas
so that a ``strands.Agent`` can invoke MCP server tools natively.
"""

from __future__ import annotations

from typing import Any, Callable

from strands.tools.tools import PythonAgentTool, ToolSpec

from .mcp_client import McpClientWrapper


def create_tool_wrappers(
    schemas: list[dict[str, Any]], client: McpClientWrapper
) -> list[PythonAgentTool]:
    """Create a PythonAgentTool for each MCP tool schema.

    Each tool:
    - Exposes the full inputSchema so the LLM knows parameter names/types
    - Extracts arguments from the strands tool_use dict
    - Forwards the call to client.call_tool_sync(name, args)

    Args:
        schemas: List of tool schema dicts from /list-tools.
        client: Connected MCP HTTP client.

    Returns:
        List of PythonAgentTool instances.
    """
    return [_make_tool(schema, client) for schema in schemas]


def _make_tool(
    schema: dict[str, Any], client: McpClientWrapper
) -> PythonAgentTool:
    """Build a single PythonAgentTool for an MCP tool schema."""
    tool_name: str = schema["name"]
    tool_desc: str = schema.get("description") or tool_name
    input_schema: dict = schema.get("inputSchema", {})

    tool_spec: ToolSpec = {
        "name": tool_name,
        "description": tool_desc,
        "inputSchema": input_schema,
    }

    def handler(tool_use: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        tool_use_id = tool_use.get("toolUseId", "unknown")
        actual_args = tool_use.get("input", {})
        try:
            result = client.call_tool_sync(tool_name, actual_args)
        except RecursionError as e:
            result = f"RecursionError calling {tool_name}: maximum recursion depth exceeded. The tool result may be too deeply nested."
        except Exception as e:
            result = f"Error calling {tool_name}: {e}"

        # Ensure result is a string to prevent serialization issues
        result_str = str(result)[:10000]  # Limit to 10KB

        return {
            "toolUseId": tool_use_id,
            "status": "success",
            "content": [{"text": result_str}],
        }

    return PythonAgentTool(tool_name, tool_spec, handler)
