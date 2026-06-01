"""Custom conversation manager that preserves the first user message.

The default SlidingWindowConversationManager drops the oldest messages,
which includes the initial user message containing the problem statement.
After 40+ tool calls, the agent forgets what issue it's solving.

This subclass pins the first user message so it's never dropped.
"""

from __future__ import annotations

import copy
import logging
from typing import TYPE_CHECKING, Any

from strands.agent.conversation_manager.sliding_window_conversation_manager import (
    SlidingWindowConversationManager,
)

if TYPE_CHECKING:
    from strands.agent.agent import Agent

logger = logging.getLogger(__name__)


class PinnedFirstMessageManager(SlidingWindowConversationManager):
    """Sliding window that always keeps the first user message.

    The first message contains the problem statement — losing it means
    the agent forgets what it's solving. This manager:
    1. Deep-copies the first user message (the problem statement) once
    2. Removes it from the list before the parent trims (so it can't be dropped)
    3. Re-inserts it at position 0 after the parent finishes (via try/finally)
    4. Overrides apply_management to not count the pinned message toward window_size
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._pinned_content: dict | None = None  # immutable deep copy
        self._live_ref: dict | None = None  # reference to the copy currently in the list

    def apply_management(self, agent: "Agent", **kwargs: Any) -> None:
        """Only trigger trimming when non-pinned messages exceed window_size."""
        messages = agent.messages
        # Subtract 1 for the pinned message so it doesn't count toward the window
        effective = len(messages) - (1 if self._live_ref is not None else 0)
        if effective <= self.window_size:
            return
        self.reduce_context(agent)

    def reduce_context(self, agent: "Agent", e: Exception | None = None, **kwargs: Any) -> None:
        messages = agent.messages

        # Pin the first real user message (not a toolResult) on first call
        if self._pinned_content is None and messages:
            for msg in messages:
                if msg.get("role") == "user":
                    content = msg.get("content", [])
                    # Skip toolResult messages — we want the problem statement
                    if isinstance(content, list) and any(
                        isinstance(c, dict) and "toolResult" in c for c in content
                    ):
                        continue
                    self._pinned_content = copy.deepcopy(msg)
                    self._live_ref = msg  # track the original by identity
                    logger.debug(
                        "Pinned first user message (%d chars)",
                        len(str(self._pinned_content.get("content", ""))),
                    )
                    break

        # Remove pinned message before parent trims (by object identity)
        removed = False
        if self._live_ref is not None:
            for i, msg in enumerate(messages):
                if msg is self._live_ref:
                    messages.pop(i)
                    removed = True
                    break

        # Let parent trim, but always re-insert the pinned message even if parent raises
        try:
            super().reduce_context(agent, e=e, **kwargs)
        finally:
            if self._pinned_content is not None:
                fresh = copy.deepcopy(self._pinned_content)
                messages.insert(0, fresh)
                self._live_ref = fresh  # track new reference for next round
                if removed:
                    logger.debug("Re-inserted pinned first user message")
