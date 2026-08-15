"""Configuration used by the CL-Bench EVO-HARNESS pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class EvolveConfig:
    """Minimal evolver configuration passed through the CL-Bench loop."""

    extra: dict[str, Any] = field(default_factory=dict)
