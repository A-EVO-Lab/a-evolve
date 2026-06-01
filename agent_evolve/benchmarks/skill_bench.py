"""Backward-compatibility alias — real module is benchmarks.skillbench.skill_bench."""

from .skillbench.skill_bench import *  # noqa: F401,F403
from .skillbench.skill_bench import SkillBenchBenchmark

__all__ = ["SkillBenchBenchmark"]
