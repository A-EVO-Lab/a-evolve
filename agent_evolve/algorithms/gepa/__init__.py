"""GEPA (Genetic-Pareto) evolution engine for A-Evolve."""

try:
    from .engine import GEPAEngine
except ImportError:
    GEPAEngine = None  # type: ignore[assignment,misc]

__all__ = ["GEPAEngine"]
