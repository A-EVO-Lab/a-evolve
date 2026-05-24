"""Built-in evolution algorithm implementations."""

from .skillforge import AEvolveEngine
from .adaptive_skill import AdaptiveSkillEngine
from .meta_harness import MetaHarnessEngine
from .propose_curate import ProposeCurateEngine

try:
    from .adaptive_evolve import AdaptiveEvolveEngine
except ImportError:
    AdaptiveEvolveEngine = None

try:
    from .mas_adaptive_skill import MasAdaptiveSkillEngine
except ImportError:
    MasAdaptiveSkillEngine = None

__all__ = [
    "AEvolveEngine",
    "AdaptiveEvolveEngine",
    "AdaptiveSkillEngine",
    "MasAdaptiveSkillEngine",
    "MetaHarnessEngine",
    "ProposeCurateEngine",
]
