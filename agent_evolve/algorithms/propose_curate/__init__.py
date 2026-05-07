"""ProposeCurateEngine -- shared evolution engine for propose+curate pipelines.

Used by OSWorld and CL-bench (and any future benchmarks that follow the pattern):
  1. Solver proposes skills after each task (in-context)
  2. Per-topic/context curator merges proposals into the skill library
  3. General curator identifies cross-topic failure patterns
"""

from .engine import ProposeCurateEngine

__all__ = ["ProposeCurateEngine"]
