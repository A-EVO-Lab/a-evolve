"""Public exports for the SkillBench agent package."""

from __future__ import annotations

from typing import Any

__all__ = [
    "SkillBenchAgent",
    "SkillBenchEvolver",
    "SkillBenchEvolutionLoop",
    "NativeSkillBenchBackend",
    "HarborSkillBenchBackend",
]


def __getattr__(name: str) -> Any:
    if name == "SkillBenchAgent":
        from .agent import SkillBenchAgent

        return SkillBenchAgent
    if name == "SkillBenchEvolver":
        from .evolver import SkillBenchEvolver

        return SkillBenchEvolver
    if name == "SkillBenchEvolutionLoop":
        from .loop import SkillBenchEvolutionLoop

        return SkillBenchEvolutionLoop
    if name == "NativeSkillBenchBackend":
        from .backends import NativeSkillBenchBackend

        return NativeSkillBenchBackend
    if name == "HarborSkillBenchBackend":
        from .backends import HarborSkillBenchBackend

        return HarborSkillBenchBackend
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
