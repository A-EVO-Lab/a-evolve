"""HTTP client for the MCP-Atlas agent-environment service.

Replaces the MCP SDK stdio/SSE client with direct HTTP calls to the
agent-environment REST API running on port 1984 inside the Docker
container.

Endpoints used:
  POST /list-tools  -> list all available tool schemas
  POST /call-tool   -> invoke a tool by name with arguments
"""

from __future__ import annotations

import logging
from typing import Any

import requests

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 300  # seconds per HTTP request (increased for slow tools like PubMed)


class McpClientWrapper:
    """HTTP client for the MCP-Atlas agent-environment service."""

    def __init__(self, base_url: str = "http://localhost:1984") -> None:
        self.base_url = base_url.rstrip("/")
        self._session = requests.Session()

    def list_tools(self) -> list[dict[str, Any]]:
        """Retrieve all tool schemas from the service.

        Returns list of dicts with ``name``, ``description``, and
        ``inputSchema`` keys.
        """
        r = self._session.post(
            f"{self.base_url}/list-tools", timeout=DEFAULT_TIMEOUT
        )
        r.raise_for_status()
        return r.json()

    def call_tool_sync(self, name: str, arguments: dict[str, Any]) -> str:
        """Call a tool and return the result as a string."""
        try:
            r = self._session.post(
                f"{self.base_url}/call-tool",
                json={"tool_name": name, "tool_args": arguments},
                timeout=DEFAULT_TIMEOUT,
            )
            data = r.json()

            # Error responses come as {"detail": "..."}
            if r.status_code != 200:
                detail = data.get("detail", str(data)) if isinstance(data, dict) else str(data)
                return f"Error: {detail}"

            # Success responses are a list of content blocks:
            # [{"type": "text", "text": "..."}, ...]
            if isinstance(data, list):
                parts = [
                    block.get("text", str(block))
                    for block in data
                    if isinstance(block, dict)
                ]
                return "\n".join(parts) if parts else str(data)
            if isinstance(data, dict):
                return data.get("result", data.get("content", str(data)))
            return str(data)
        except Exception as exc:
            error_msg = f"Error calling tool '{name}': {exc}"
            logger.warning(error_msg)
            return error_msg

    def close(self) -> None:
        """Close the HTTP session."""
        self._session.close()
        logger.info("MCP HTTP client closed")
