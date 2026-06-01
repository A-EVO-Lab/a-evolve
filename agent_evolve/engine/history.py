"""EvolutionHistory -- query facade over observations and workspace versions.

Engines use this to look back at past cycles: what was the score curve,
what observations were collected, what did the workspace look like at an
earlier version, etc.
"""

from __future__ import annotations

import logging
from typing import Any

from ..types import CycleRecord
from .observer import Observer
from .versioning import VersionControl

logger = logging.getLogger(__name__)


class EvolutionHistory:
    """Unified query interface over observations and workspace versions.

    The loop populates this every cycle.  Engines read from it in ``step()``.
    """

    def __init__(self, observer: Observer, versioning: VersionControl):
        self._observer = observer
        self._versioning = versioning
        self._cycle_records: list[CycleRecord] = []

    # ── Cycle records ─────────────────────────────────────────

    def record_cycle(self, record: CycleRecord) -> None:
        """Called by the loop after each cycle."""
        self._cycle_records.append(record)

    @property
    def cycles(self) -> list[CycleRecord]:
        return list(self._cycle_records)

    @property
    def latest_cycle(self) -> int:
        return self._cycle_records[-1].cycle if self._cycle_records else 0

    # ── Observation queries ───────────────────────────────────

    def get_observations(
        self,
        last_n_cycles: int = 3,
        only_failures: bool = False,
    ) -> list[dict[str, Any]]:
        """Return recent observation records.

        Args:
            last_n_cycles: How many recent batches to include.
            only_failures: If True, filter to unsuccessful observations.
        """
        records = self._observer.get_recent_logs(n_batches=last_n_cycles)
        if only_failures:
            records = [r for r in records if not r.get("success", False)]
        return records

    def get_summary_stats(self) -> dict[str, Any]:
        """Aggregate stats across all observations."""
        return self._observer.get_summary_stats()

    # ── Score queries ─────────────────────────────────────────

    def get_score_curve(self) -> list[float]:
        """Return the score from each cycle, in order."""
        return [r.score for r in self._cycle_records]

    # ── Workspace version queries ─────────────────────────────

    def get_workspace_diff(self, from_label: str, to_label: str) -> str:
        """Git diff between two version labels (e.g. 'evo-2', 'evo-3')."""
        return self._versioning.get_diff(from_label, to_label)

    def read_file_at(self, version_label: str, path: str) -> str:
        """Read a workspace file as it existed at *version_label*."""
        return self._versioning.show_file_at(version_label, path)

    def list_versions(self) -> list[str]:
        """List all evo-* tags, newest first."""
        return self._versioning.list_tags()

    def get_version_log(self, n: int = 30) -> str:
        """Git log (oneline) for the workspace."""
        return self._versioning.get_log(n=n)
