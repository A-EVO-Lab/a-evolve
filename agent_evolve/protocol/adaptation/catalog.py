"""Catalog + index of an evolved harness store for solve-time retrieval.

A *catalog* is the flat list of retrievable harness artifacts the evolver
produced — skills, tools, and memory entries — each indexed by its FULL
text (name + description + body). Indexing the full body, not just the
name/description, is the key signal for routing (cf. SkillRouter: hiding
the body drops routing accuracy 31-44pp).

A *CatalogIndex* embeds the catalog once (disk-cached) and answers
per-task top-k similarity queries. It is used only by the retrieval
adaptation operators (M1/M2); nothing else in the codebase depends on it,
so importing it has no effect on existing runs.

Read-only with respect to the evolved store: the catalog only *reads*
artifacts to select from them; it never mutates the workspace.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ...contract.workspace import AgentWorkspace

logger = logging.getLogger(__name__)

# How much of a single item's text to embed (the model truncates anyway;
# this bounds memory + keeps embedding fast for very long tool sources).
_ITEM_TEXT_MAX_CHARS = 4000
_TASK_TEXT_MAX_CHARS = 512


@dataclass
class CatalogItem:
    """One retrievable harness artifact."""
    id: str          # "skill:<name>" | "tool:<name>" | "memory:<i>"
    kind: str        # "skill" | "tool" | "memory"
    name: str        # human-facing name (skill/tool name; memory category+idx)
    text: str        # full embedding text (name + description + body)


def _skill_items(ws: AgentWorkspace) -> list[CatalogItem]:
    """Enumerate skills, tolerating BOTH evolved layouts:

    - canonical ``skills/<name>/SKILL.md`` (what ``workspace.list_skills``
      recognises), and
    - flat ``skills/<name>.md`` (some evolver runs emit this; the workspace
      API skips it, so we read those files directly here).
    """
    items: list[CatalogItem] = []
    seen: set[str] = set()
    # 1. Canonical layout via the workspace API.
    for meta in ws.list_skills():
        body = ws.read_skill(meta.name) or ""
        text = f"{meta.name}\n{meta.description}\n{body}".strip()
        items.append(CatalogItem(
            id=f"skill:{meta.name}", kind="skill", name=meta.name,
            text=text[:_ITEM_TEXT_MAX_CHARS],
        ))
        seen.add(meta.name)
    # 2. Flat skills/<name>.md fallback.
    skills_dir = ws.skills_dir
    if skills_dir.exists():
        for f in sorted(skills_dir.glob("*.md")):
            name = f.stem
            if name in seen or name.startswith("_"):
                continue
            try:
                body = f.read_text()
            except Exception:
                continue
            items.append(CatalogItem(
                id=f"skill:{name}", kind="skill", name=name,
                text=f"{name}\n{body}".strip()[:_ITEM_TEXT_MAX_CHARS],
            ))
    return items


def _tool_items(ws: AgentWorkspace) -> list[CatalogItem]:
    items: list[CatalogItem] = []
    for t in ws.read_tool_registry():
        name = t.get("name", "")
        if not name:
            continue
        desc = t.get("description", "")
        src = ws.read_tool(name) or ""
        text = f"{name}\n{desc}\n{src}".strip()
        items.append(CatalogItem(
            id=f"tool:{name}", kind="tool", name=name,
            text=text[:_ITEM_TEXT_MAX_CHARS],
        ))
    return items


# Memory must be loaded with the SAME limit the solver agent uses
# (base_agent.read_all_memories(limit=200)) so that catalog memory IDs and
# the harness-filter memory IDs index the identical set. Do not change one
# without the other.
MEMORY_LIMIT = 200


def memory_text(m: dict) -> str:
    """Canonical human-readable text for a memory entry (shared helper).

    Used by BOTH the catalog (to embed) and the harness filter (to render),
    so a memory's content — and thus its content-based id — is identical on
    both sides.
    """
    body = " ".join(
        str(m.get(k, "")) for k in ("content", "insight", "text", "approach")
        if m.get(k)
    ).strip()
    return body or json.dumps(m, default=str)


def memory_id(text: str) -> str:
    """Content-based memory id (stable across processes / load orders)."""
    return "memory:" + hashlib.sha1(text.encode()).hexdigest()[:12]


def _memory_items(ws: AgentWorkspace, limit: int = MEMORY_LIMIT) -> list[CatalogItem]:
    items: list[CatalogItem] = []
    try:
        mems = ws.read_all_memories(limit=limit)
    except Exception:
        return items
    for m in mems:
        text = memory_text(m)[:_ITEM_TEXT_MAX_CHARS]
        cat = m.get("_category", "memory")
        items.append(CatalogItem(
            id=memory_id(text), kind="memory", name=cat,
            text=text,
        ))
    return items


@dataclass
class Catalog:
    """The flat list of retrievable items from an evolved store."""
    items: list[CatalogItem]

    @classmethod
    def from_workspace(
        cls,
        workspace: AgentWorkspace,
        *,
        kinds: tuple[str, ...] = ("skill", "tool", "memory"),
    ) -> "Catalog":
        items: list[CatalogItem] = []
        if "skill" in kinds:
            items += _skill_items(workspace)
        if "tool" in kinds:
            items += _tool_items(workspace)
        if "memory" in kinds:
            items += _memory_items(workspace)
        return cls(items=items)

    def content_hash(self) -> str:
        h = hashlib.sha256()
        for it in self.items:
            h.update(it.id.encode())
            h.update(b"\0")
            h.update(it.text.encode())
            h.update(b"\0")
        return h.hexdigest()[:16]

    def __len__(self) -> int:
        return len(self.items)


class CatalogIndex:
    """Embeds a catalog (disk-cached) and serves top-k similarity queries."""

    def __init__(self, catalog: Catalog, cache_dir: Path | None = None):
        self.catalog = catalog
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self._embs = None  # numpy matrix (n x d), normalized

    def build(self) -> None:
        """Embed all catalog items (load from disk cache if available)."""
        import numpy as np
        if not self.catalog.items:
            self._embs = np.zeros((0, 1), dtype=float)
            return
        cache_file = None
        if self.cache_dir is not None:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            cache_file = self.cache_dir / f"index_{self.catalog.content_hash()}.npy"
            if cache_file.exists():
                try:
                    self._embs = np.load(cache_file)
                    logger.info("Loaded adaptation index from cache: %s", cache_file)
                    return
                except Exception:
                    pass
        from .embedder import embed_texts
        self._embs = embed_texts([it.text for it in self.catalog.items], use_cache=False)
        if cache_file is not None:
            try:
                np.save(cache_file, self._embs)
            except Exception as e:
                logger.warning("Could not cache adaptation index: %s", e)

    def search(self, query: str, k: int) -> list[CatalogItem]:
        """Return the top-k catalog items most similar to the query text."""
        if self._embs is None:
            self.build()
        if not self.catalog.items:
            return []
        from .embedder import embed_query, cosine_topk
        q = embed_query(query[:_TASK_TEXT_MAX_CHARS])
        ranked = cosine_topk(q, self._embs, k)
        return [self.catalog.items[i] for i, _ in ranked]
