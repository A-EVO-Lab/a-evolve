"""Built-in evolution algorithm implementations."""

from .skillforge import AEvolveEngine
from .adaptive_skill import AdaptiveSkillEngine
from .hippocampal_evolve import HippocampalEvolveEngine

try:
    from .adaptive_evolve import AdaptiveEvolveEngine
except ImportError:
    AdaptiveEvolveEngine = None

__all__ = [
    "AEvolveEngine",
    "AdaptiveEvolveEngine",
    "AdaptiveSkillEngine",
    "HippocampalEvolveEngine",
]
