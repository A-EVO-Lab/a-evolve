"""Solve-time adaptation: pluggable operators that project the evolved
harness store into a per-task harness.

Operators (selected via ``--adaptation`` / the registry):
  - whole_store    (M0) — full dump, no selection
  - tree_routing   (M4) — agentic whole-branch routing (in algorithms/navigation)
  - retrieval      (M1) — dense top-k over a skill/tool/memory catalog
  - agentic_filter (M2) — M1 candidates + LLM rerank

Submodules: base (protocol + WholeStoreAdaptation), catalog (+ index),
embedder (pluggable BGE/SkillRouter), operators (M1/M2), prompts (M2
selection prompt), registry (build_adapter — the plug-in point).
"""

from __future__ import annotations

from .base import Adaptation, WholeStoreAdaptation
from .registry import build_adapter

__all__ = ["Adaptation", "WholeStoreAdaptation", "build_adapter"]
