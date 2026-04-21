"""AC-5 static import-ban enforcement.

DEC-1/DEC-2 require that nothing under ``agent_evolve/algorithms/unified/``
imports from any legacy evolution-engine package. This test fails the build
on any occurrence. Runs as a plain pytest rather than shelling out so CI
works in environments without ``grep``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


_LEGACY_PACKAGES = ("adaptive_evolve", "adaptive_skill", "guided_synth", "skillforge")


def _unified_root() -> Path:
    here = Path(__file__).resolve()
    return here.parent.parent / "agent_evolve" / "algorithms" / "unified"


def _iter_python_files(root: Path):
    for p in root.rglob("*.py"):
        # Skip pycache artefacts if present.
        if "__pycache__" in p.parts:
            continue
        yield p


IMPORT_RE = re.compile(
    r"^\s*(?:from|import)\s+([A-Za-z0-9_\.]+)",
    re.MULTILINE,
)


def _find_banned_imports(text: str) -> list[str]:
    banned: list[str] = []
    for match in IMPORT_RE.finditer(text):
        module = match.group(1)
        for pkg in _LEGACY_PACKAGES:
            # Full module prefix forms.
            if module.endswith(f".{pkg}") or f".{pkg}." in module:
                banned.append(module)
                break
            if module.startswith(f"agent_evolve.algorithms.{pkg}"):
                banned.append(module)
                break
    return banned


def test_unified_tree_has_no_legacy_imports():
    root = _unified_root()
    offenders: dict[str, list[str]] = {}
    for file in _iter_python_files(root):
        text = file.read_text(encoding="utf-8")
        banned = _find_banned_imports(text)
        if banned:
            offenders[str(file.relative_to(root))] = banned
    if offenders:
        detail = "\n".join(
            f"  {fp}: {imports}" for fp, imports in sorted(offenders.items())
        )
        pytest.fail(
            "Forbidden legacy imports found in unified/:\n"
            f"{detail}\n"
            "Unified atoms must be independent reimplementations; see DEC-1/DEC-2 in plan_v1.md."
        )


def test_import_ban_regex_catches_typical_forms():
    """Self-test on the regex: ensure it flags the usual patterns."""
    samples = [
        "from agent_evolve.algorithms.adaptive_evolve.engine import X",
        "from agent_evolve.algorithms.adaptive_skill import Y",
        "import agent_evolve.algorithms.guided_synth.engine",
        "from ...algorithms.skillforge.engine import Z",
    ]
    for s in samples:
        banned = _find_banned_imports(s)
        assert banned, f"Regex missed a banned import: {s!r}"


def test_import_ban_allows_unrelated_imports():
    """Negative: make sure the regex does NOT flag legitimate imports."""
    samples = [
        "import os",
        "from typing import Any",
        "from ..contract.workspace import AgentWorkspace",
        "from ...engine.base import EvolutionEngine",
        "from ...types import StepResult",
    ]
    for s in samples:
        banned = _find_banned_imports(s)
        assert not banned, f"Regex false-positive on: {s!r} -> {banned}"
