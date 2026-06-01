"""Evolution metrics: EC, AULC, EGL."""

from __future__ import annotations


def evolution_capacity(scores: list[float]) -> float:
    """EC(N) = score(N) - score(0). Performance improvement over baseline."""
    if len(scores) < 2:
        return 0.0
    return scores[-1] - scores[0]


def area_under_learning_curve(scores: list[float]) -> float:
    """AULC(N) = mean of running average scores."""
    if not scores:
        return 0.0
    running = []
    cumsum = 0.0
    for i, s in enumerate(scores, 1):
        cumsum += s
        running.append(cumsum / i)
    return sum(running) / len(running)
