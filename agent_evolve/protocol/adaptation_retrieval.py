"""Retrieval-based solve-time adaptation operators (M1, M2).

These implement the ``Adaptation`` protocol (``select`` / ``materialize``)
but, unlike branch routing, they do NOT mutate the workspace. Instead they
record a per-task *filter* — the subset of catalog items (skills/tools/
memory) relevant to the task — which the solve loop threads into
``args_dict["task_filters"]`` and the worker applies at harness-assembly
time (see ``agents/_harness_filter.py``).

``select`` returns the key ``"main"`` for every task so that ALL tasks stay
in a single materialization group (one ``build_prompts`` call, no workspace
churn) — the per-task granularity is carried as data, not as the group key.

M1 (``RetrievalAdaptation``): dense top-k over the catalog.
M2 (``AgenticFilterAdaptation``): M1 candidates -> LLM rerank/select.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..types import Task
    from .catalog import CatalogIndex, CatalogItem

logger = logging.getLogger(__name__)


class RetrievalAdaptation:
    """M1: per-task dense retrieval of the top-k relevant harness items."""

    name = "retrieval"

    def __init__(self, index: "CatalogIndex", top_k: int = 8):
        self._index = index
        self._top_k = top_k
        self._sel: dict[str, list] = {}     # task_id -> [CatalogItem]
        self._lock = threading.Lock()

    # ── Adaptation protocol ──────────────────────────────────────────
    def select(self, store_path: Path, task: "Task") -> str:
        items = self._retrieve(task)
        with self._lock:
            self._sel[task.id] = items
        return "main"  # keep all tasks in one materialization group

    def materialize(self, store_path: Path, key: str, workspace) -> None:
        # No workspace mutation; the filter is applied in the worker.
        return None

    # ── Per-task filter accessor (read by the solve loop) ────────────
    def task_filter(self, task_id: str) -> dict | None:
        """Return {"skills": [...], "tools": [...], "memory": [...]} or None.

        None means "no filtering" (worker uses the full harness). We only
        return None if selection somehow never ran for this task.
        """
        with self._lock:
            items = self._sel.get(task_id)
        if items is None:
            return None
        f: dict[str, list[str]] = {"skills": [], "tools": [], "memory": []}
        for it in items:
            if it.kind == "skill":
                f["skills"].append(it.name)
            elif it.kind == "tool":
                f["tools"].append(it.name)
            elif it.kind == "memory":
                f["memory"].append(it.id)
        return f

    # ── Retrieval ────────────────────────────────────────────────────
    def _retrieve(self, task: "Task") -> list["CatalogItem"]:
        query = self._query_text(task)
        try:
            return self._index.search(query, self._top_k)
        except Exception as e:
            logger.warning("Retrieval failed for %s: %s", task.id, e)
            return []

    @staticmethod
    def _query_text(task: "Task") -> str:
        meta = task.metadata or {}
        prefix = " ".join(
            str(meta[k]) for k in ("category", "domain", "event")
            if meta.get(k)
        )
        return f"{prefix}\n{task.input}".strip()


class AgenticFilterAdaptation(RetrievalAdaptation):
    """M2: retrieve a candidate pool (M1), then LLM-select the subset."""

    name = "agentic_filter"

    def __init__(
        self,
        index: "CatalogIndex",
        top_k: int = 16,      # candidate pool size
        keep_k: int = 8,      # fallback subset size on LLM failure
        llm=None,
    ):
        super().__init__(index, top_k)
        self._keep_k = keep_k
        self._llm = llm

    def _retrieve(self, task: "Task") -> list["CatalogItem"]:
        candidates = super()._retrieve(task)
        if not candidates or self._llm is None:
            return candidates[: self._keep_k]
        try:
            return self._llm_select(task, candidates)
        except Exception as e:
            logger.warning("Agentic filter LLM failed for %s; "
                           "falling back to top-%d: %s", task.id, self._keep_k, e)
            return candidates[: self._keep_k]

    def _llm_select(self, task: "Task", candidates: list["CatalogItem"]) -> list["CatalogItem"]:
        from .adaptation_prompts import (
            SELECTION_SYSTEM_PROMPT, build_selection_prompt, parse_selection,
        )
        from ..llm.bedrock import BedrockProvider
        if not isinstance(self._llm, BedrockProvider):
            return candidates[: self._keep_k]
        prompt = build_selection_prompt(self._query_text(task), candidates)
        resp = self._llm.converse_loop(
            system_prompt=SELECTION_SYSTEM_PROMPT,
            user_message=prompt,
            tools=[], tool_executor={},
            max_tokens=512, temperature=0.0,
        )
        chosen_ids = parse_selection(resp.content, valid_ids={c.id for c in candidates})
        if not chosen_ids:
            return candidates[: self._keep_k]
        by_id = {c.id: c for c in candidates}
        return [by_id[i] for i in chosen_ids if i in by_id]
