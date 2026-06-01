"""Custom conversation manager that pins the first user message during trimming."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from strands.agent.agent import Agent

from strands.agent.conversation_manager import SlidingWindowConversationManager
from strands.types.exceptions import ContextWindowOverflowException

logger = logging.getLogger(__name__)


class PinnedFirstMessageManager(SlidingWindowConversationManager):
    """Sliding window that always preserves the first user message.

    Strands' default SlidingWindowConversationManager trims from the
    front of the message list, which can drop the initial user message
    (i.e. the task input) after enough tool-call rounds.  This subclass
    pulls the first user message out before trimming, then re-inserts it
    at position 0 so the model never loses sight of the original task.
    """

    def reduce_context(self, agent: "Agent", e: Exception | None = None, **kwargs: Any) -> None:
        messages = agent.messages

        if not messages:
            raise ContextWindowOverflowException("No messages to trim!") from e

        # Save the first user message if it exists
        pinned = messages[0] if messages[0].get("role") == "user" else None

        # Let the parent do its normal trimming
        super().reduce_context(agent, e=e, **kwargs)

        # Re-insert the pinned message if it was trimmed away
        if pinned is not None and (not agent.messages or agent.messages[0] is not pinned):
            agent.messages.insert(0, pinned)
