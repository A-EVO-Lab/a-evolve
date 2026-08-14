"""Evolutionary Generality Loss (EGL) tracking.

EGL = (new_skills_created / total_tasks_solved) * 1000

High EGL = agent is learning a lot (early stage)
Low EGL  = agent has converged (reusing existing skills)

This is an A-Evolve-specific convergence metric.
"""

from __future__ import annotations


def compute_egl(new_skills: int, total_tasks: int) -> float:
    if total_tasks == 0:
        return 0.0
    return (new_skills / total_tasks) * 1000


def is_converged(egl_history: list[dict], threshold: float = 0.05, window: int = 3) -> bool:
    """Check if EGL has been below threshold for ``window`` consecutive cycles."""
    if len(egl_history) < window:
        return False
    recent = egl_history[-window:]
    return all(entry.get("egl", float("inf")) < threshold for entry in recent)
