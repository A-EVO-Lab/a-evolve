"""Shared memories database for ARC-AGI-3 sub-agents.

Adapted from symbolica-ai/ARC-AGI-3-Agents (scope/memories.py).
Standalone implementation -- no agentica SDK dependency.
Uses a simple list-based store with structured entries.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class Memory:
    """A piece of knowledge shared across all sub-agents.

    Attributes:
        summary: Short description for quick scanning.
        details: Full details. Prefix with CONFIRMED: or HYPOTHESIS: to
            indicate confidence level.
        source: Which agent/phase created this memory.
        level: Game level when this was learned.
    """

    summary: str
    details: str
    source: str = ""
    level: int = 0
    timestamp: datetime = field(default_factory=datetime.now)


class Memories:
    """Thread-safe shared knowledge database for sub-agents.

    All sub-agents share one Memories instance. Insights discovered by
    one agent are immediately available to all others.

    Usage::

        memories = Memories()
        memories.add("Blue blocks move with ACTION1", "CONFIRMED: tested 3x", source="explorer-1")
        memories.add("Red might be the goal", "HYPOTHESIS: only seen once", source="explorer-1")

        # Quick scan
        for line in memories.summaries():
            print(line)

        # Full detail
        mem = memories.get(0)
        print(mem.details)

        # Search
        results = memories.search("blue")
    """

    def __init__(self) -> None:
        self._stack: list[Memory] = []
        self._lock = threading.Lock()

    def add(self, summary: str, details: str, source: str = "", level: int = 0) -> int:
        """Add a memory. Returns its index."""
        with self._lock:
            mem = Memory(summary=summary, details=details, source=source, level=level)
            self._stack.append(mem)
            return len(self._stack) - 1

    def get(self, index: int) -> Memory:
        """Get a memory by index."""
        with self._lock:
            return self._stack[index]

    def summaries(self) -> list[str]:
        """One-line summaries for all memories."""
        with self._lock:
            return [
                f"[{i}] ({m.source}) {m.summary}"
                for i, m in enumerate(self._stack)
            ]

    def search(self, query: str) -> list[tuple[int, Memory]]:
        """Simple keyword search across summaries and details."""
        query_lower = query.lower()
        with self._lock:
            return [
                (i, m) for i, m in enumerate(self._stack)
                if query_lower in m.summary.lower() or query_lower in m.details.lower()
            ]

    def for_level(self, level: int) -> list[tuple[int, Memory]]:
        """Get all memories for a specific level."""
        with self._lock:
            return [(i, m) for i, m in enumerate(self._stack) if m.level == level]

    def evict(self, index: int) -> None:
        """Remove a memory by index."""
        with self._lock:
            if 0 <= index < len(self._stack):
                self._stack.pop(index)

    def format_for_prompt(self, max_entries: int = 20) -> str:
        """Format memories as text for inclusion in LLM prompts."""
        with self._lock:
            if not self._stack:
                return "No memories yet."
            entries = self._stack[-max_entries:]
            lines = [f"=== Shared Knowledge ({len(self._stack)} entries) ==="]
            for i, m in enumerate(entries):
                idx = len(self._stack) - len(entries) + i
                lines.append(f"[{idx}] {m.summary}")
                lines.append(f"    {m.details}")
            return "\n".join(lines)

    def __len__(self) -> int:
        with self._lock:
            return len(self._stack)

    def __repr__(self) -> str:
        return f"Memories({len(self)} entries)"
