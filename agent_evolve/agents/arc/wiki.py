"""
Structured wiki knowledge base for cross-agent knowledge persistence.
Replaces flat Memories list with topic-based pages that agents can
read selectively and update in place.

Inspired by Karpathy's LLM wiki pattern:
https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f
"""

import json
import threading
import time
from pathlib import Path

__all__ = ["GameWiki"]

# Pre-defined page schema — agents can also create custom pages
DEFAULT_PAGES = {
    "game_rules": "",          # APPEND-ONLY. Universal mechanics + win condition. Never overwrite — append corrections.
    "breakthroughs": "",       # APPEND-ONLY. Key discoveries that change everything.
    "colors": "",              # Overwrite OK. Color→role mapping.
    "current_level": "",       # Overwrite OK. Current level layout + positions.
    "current_plan": "",        # Overwrite OK. What to try next.
    "solved_levels": "",       # APPEND-ONLY. Detailed per-level solutions with action sequences.
    "failed_attempts": "",     # APPEND-ONLY. Concise: what failed + why (1-2 lines each).
    "level_changes": "",       # APPEND-ONLY. What changed between levels.
}

# Pages that should only be appended to, never overwritten
APPEND_ONLY_PAGES = frozenset({
    "game_rules", "breakthroughs", "solved_levels", "failed_attempts", "level_changes",
})


class GameWiki:
    """
    Structured wiki knowledge base for a single ARC-AGI-3 game.

    Pages are organized by topic. Agents read specific pages they need
    rather than scanning a flat list. Pages can be overwritten (for
    updating knowledge) or appended to (for logs).

    Thread-safe. Persists to disk for debugging and dashboard.
    """

    def __init__(self, game_id: str = "", log_dir: str | Path = "wiki_log") -> None:
        self._game_id = game_id
        self._lock = threading.Lock()
        self._pages: dict[str, str] = dict(DEFAULT_PAGES)
        self._log_dir = Path(log_dir)
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._history: list[dict] = []  # Full write trajectory
        self._seq: int = 0  # Global sequence counter across all appends

    def write(self, page: str, content: str) -> str:
        """
        Write (overwrite) a wiki page. Use this when you have updated
        knowledge that replaces the old content.

        For append-only pages (game_rules, breakthroughs, solved_levels,
        failed_attempts, level_changes), this redirects to append() to
        prevent accidental knowledge destruction.

        Returns confirmation message.
        """
        if page in APPEND_ONLY_PAGES:
            return self.append(page, content)
        with self._lock:
            self._history.append({
                "op": "write",
                "page": page,
                "content": content,
            })
            self._pages[page] = content
            self._flush_to_disk()
        return f"Page '{page}' updated ({len(content)} chars)."

    def append(self, page: str, content: str) -> str:
        """
        Append to a wiki page. Use this for log-style pages like
        failed_attempts and solved_levels where you want to accumulate entries.

        Adds a sequence number separator between entries.
        """
        with self._lock:
            self._seq += 1
            entry = f"\n--- [{self._seq}] ---\n{content}"
            self._history.append({
                "op": "append",
                "page": page,
                "seq": self._seq,
                "content": content,
            })
            self._pages.setdefault(page, "")
            self._pages[page] += entry
            self._flush_to_disk()
        return f"Appended to '{page}'."

    def read(self, page: str) -> str:
        """
        Read a specific wiki page. Returns the full content.
        """
        with self._lock:
            content = self._pages.get(page)
        if content is None:
            available = ", ".join(sorted(self._pages.keys()))
            return f"Page '{page}' does not exist. Available pages: {available}"
        if not content:
            return f"Page '{page}' is empty. No content has been written yet."
        return content

    def index(self) -> str:
        """
        List all wiki pages with a one-line summary (first line of content).
        Use this to see what knowledge exists before reading specific pages.
        """
        with self._lock:
            pages = dict(self._pages)

        lines = []
        for name in sorted(pages.keys()):
            content = pages[name]
            if not content:
                lines.append(f"  {name}: (empty)")
            else:
                # First non-empty line as summary
                first_line = ""
                for line in content.strip().split("\n"):
                    line = line.strip()
                    if line and not line.startswith("---"):
                        first_line = line[:100]
                        break
                lines.append(f"  {name}: {first_line}")
        return "\n".join(lines)

    def _flush_to_disk(self) -> None:
        """Persist wiki state to JSON for debugging / dashboard."""
        if not self._game_id:
            return
        path = self._log_dir / f"{self._game_id}.json"
        data = {
            "game_id": self._game_id,
            "pages": {
                name: {
                    "content": content,
                    "length": len(content),
                }
                for name, content in self._pages.items()
            },
            "history": self._history,
        }
        try:
            path.write_text(json.dumps(data, indent=2))
        except Exception:
            pass

    def __repr__(self) -> str:
        non_empty = sum(1 for v in self._pages.values() if v)
        return f"GameWiki(<{non_empty}/{len(self._pages)} pages>)"
