"""Task filtering by API key availability."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_evolve.agents.mcp.key_registry import KeyRegistry
    from agent_evolve.types import Task

logger = logging.getLogger(__name__)


def filter_tasks_by_keys(
    tasks: list[Task],
    key_registry: KeyRegistry,
) -> tuple[list[Task], list[Task]]:
    """Partition tasks into (runnable, filtered_out) based on key availability.

    Tasks whose mcp_server_names require no keys or whose keys are all
    available are included. Others are excluded.

    Logs filtered count and missing keys at INFO level.
    Logs WARNING if all tasks are filtered out.
    """
    runnable: list[Task] = []
    filtered_out: list[Task] = []
    all_missing_keys: set[str] = set()

    for task in tasks:
        server_names: list[str] = task.metadata.get("mcp_server_names", [])
        all_available, missing = key_registry.has_keys_for_servers(server_names)

        if all_available:
            runnable.append(task)
        else:
            filtered_out.append(task)
            all_missing_keys.update(missing)

    if filtered_out:
        logger.info(
            "Filtered out %d task(s) due to missing keys: %s",
            len(filtered_out),
            ", ".join(sorted(all_missing_keys)),
        )

    if tasks and not runnable:
        logger.warning(
            "All tasks filtered out. Missing environment variables: %s",
            ", ".join(sorted(all_missing_keys)),
        )

    return runnable, filtered_out
