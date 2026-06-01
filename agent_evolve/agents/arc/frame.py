"""Frame wrapper with grid inspection helpers.

Adapted from symbolica-ai/ARC-AGI-3-Agents (scope/frame.py).
Standalone -- no agentica or arc-agi SDK dependency at import time.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np


@dataclass(slots=True)
class DiffRegion:
    """A contiguous region of changed cells between two frames."""

    x0: int
    y0: int
    x1: int
    y1: int
    changes: list[tuple[int, int, int, int]]  # (x, y, old_val, new_val)

    @property
    def width(self) -> int:
        return self.x1 - self.x0

    @property
    def height(self) -> int:
        return self.y1 - self.y0

    @property
    def count(self) -> int:
        return len(self.changes)

    def __repr__(self) -> str:
        return f"DiffRegion(x=[{self.x0},{self.x1}) y=[{self.y0},{self.y1}), {self.count} changes)"


def _cluster_changes(
    changes: list[tuple[int, int, int, int]], margin: int = 2
) -> list[DiffRegion]:
    """Group changes into contiguous regions within *margin* pixels."""
    if not changes:
        return []

    sorted_changes = sorted(changes, key=lambda c: (c[1], c[0]))
    regions: list[list[tuple[int, int, int, int]]] = []
    boxes: list[list[int]] = []

    for change in sorted_changes:
        x, y = change[0], change[1]
        merged = False
        for i, box in enumerate(boxes):
            if (x >= box[0] - margin and x <= box[2] + margin
                    and y >= box[1] - margin and y <= box[3] + margin):
                regions[i].append(change)
                box[0] = min(box[0], x)
                box[1] = min(box[1], y)
                box[2] = max(box[2], x)
                box[3] = max(box[3], y)
                merged = True
                break
        if not merged:
            regions.append([change])
            boxes.append([x, y, x, y])

    # Merge overlapping boxes
    merged_any = True
    while merged_any:
        merged_any = False
        i = 0
        while i < len(boxes):
            j = i + 1
            while j < len(boxes):
                bi, bj = boxes[i], boxes[j]
                if (bi[0] - margin <= bj[2] + margin
                        and bi[2] + margin >= bj[0] - margin
                        and bi[1] - margin <= bj[3] + margin
                        and bi[3] + margin >= bj[1] - margin):
                    regions[i].extend(regions[j])
                    bi[0] = min(bi[0], bj[0])
                    bi[1] = min(bi[1], bj[1])
                    bi[2] = max(bi[2], bj[2])
                    bi[3] = max(bi[3], bj[3])
                    regions.pop(j)
                    boxes.pop(j)
                    merged_any = True
                else:
                    j += 1
            i += 1

    return [
        DiffRegion(x0=box[0], y0=box[1], x1=box[2] + 1, y1=box[3] + 1, changes=region)
        for region, box in zip(regions, boxes)
    ]


class Frame:
    """Lightweight frame wrapper with grid inspection helpers.

    Works with raw grid data (list of lists of ints) -- does not require
    arcengine.FrameData at construction time.
    """

    def __init__(self, grid: Sequence[Sequence[int]], **metadata: Any) -> None:
        self.grid: tuple[tuple[int, ...], ...] = tuple(tuple(row) for row in grid)
        self.metadata = metadata
        self._grid_array: np.ndarray | None = None

    @property
    def grid_np(self) -> np.ndarray:
        if self._grid_array is None:
            arr = np.array(self.grid, dtype=np.int8)
            arr.flags.writeable = False
            self._grid_array = arr
        return self._grid_array

    @property
    def width(self) -> int:
        return len(self.grid[0]) if self.grid else 0

    @property
    def height(self) -> int:
        return len(self.grid)

    def render(
        self,
        keys: str = "0123456789abcdef",
        gap: str = " ",
        y_ticks: bool = False,
        x_ticks: bool = False,
        crop: tuple[int, int, int, int] | None = None,
    ) -> str:
        """Render the grid as a text string."""
        x1, y1, x2, y2 = crop or (0, 0, self.width, self.height)
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(self.width, x2), min(self.height, y2)

        lines: list[str] = []
        pad = "       " if y_ticks else ""

        if x_ticks:
            lines.append(pad + gap.join(str(c // 10) for c in range(x1, x2)))
            lines.append(pad + gap.join(str(c % 10) for c in range(x1, x2)))
            data_width = len(gap.join("x" for _ in range(x1, x2)))
            lines.append(pad + "-" * data_width)

        for y in range(y1, y2):
            row = gap.join(keys[self.grid[y][x]] for x in range(x1, x2))
            if y_ticks:
                lines.append(f"y={y:>2d} | {row}")
            else:
                lines.append(row)

        return "\n".join(lines)

    def diff(self, other: Frame, margin: int = 2) -> list[DiffRegion]:
        """Cells that changed between other (old) and self (new)."""
        changes: list[tuple[int, int, int, int]] = []
        for y in range(min(self.height, other.height)):
            self_row, other_row = self.grid[y], other.grid[y]
            for x in range(min(len(self_row), len(other_row))):
                if self_row[x] != other_row[x]:
                    changes.append((x, y, other_row[x], self_row[x]))
        return _cluster_changes(changes, margin=margin)

    def change_summary(self, other: Frame, margin: int = 2) -> str:
        """One-line-per-region summary of changes from other to self."""
        regions = self.diff(other, margin=margin)
        if not regions:
            return "No changes."
        total = sum(r.count for r in regions)
        lines = [f"{total} cells changed across {len(regions)} region(s):"]
        for r in regions:
            counts: dict[tuple[int, int], int] = {}
            for _, _, old, new in r.changes:
                key = (old, new)
                counts[key] = counts.get(key, 0) + 1
            transitions = sorted(counts.items(), key=lambda kv: -kv[1])
            parts = ", ".join(f"{o}->{n} x{c}" for (o, n), c in transitions)
            lines.append(f"  [{r.x0},{r.y0})-[{r.x1},{r.y1}): {r.count} cells -- {parts}")
        return "\n".join(lines)

    def find(self, *colors: int) -> list[tuple[int, int, int]]:
        """All pixels matching any of the given color values. Returns [(x, y, value), ...]."""
        g = self.grid_np
        mask = np.isin(g, colors)
        ys, xs = np.where(mask)
        vals = g[ys, xs]
        return [(int(x), int(y), int(v)) for y, x, v in zip(ys, xs, vals)]

    def color_counts(self) -> dict[int, int]:
        """Count of each color value present in the grid."""
        bins = np.bincount(self.grid_np.ravel(), minlength=16)
        return {int(c): int(n) for c, n in enumerate(bins) if n}

    def bounding_box(self, *colors: int) -> tuple[int, int, int, int] | None:
        """Tight bounding box (x1, y1, x2, y2 exclusive) of matching pixels."""
        mask = np.isin(self.grid_np, colors)
        ys, xs = np.where(mask)
        if len(ys) == 0:
            return None
        return (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)

    def render_diff(
        self,
        other: "Frame",
        keys: str = "0123456789abcdef",
        gap: str = " ",
        crop: "tuple[int, int, int, int] | str | None" = None,
    ) -> str:
        """Render a visual diff. Call as new_frame.render_diff(old_frame).

        Changed cells show their new value; unchanged cells show '.'.
        """
        regions = self.diff(other)
        if not regions:
            return "No changes."

        all_changes: dict[tuple[int, int], int] = {}
        total = 0
        for region in regions:
            for x, y, _, new_val in region.changes:
                all_changes[(x, y)] = new_val
            total += region.count

        if crop == "auto":
            min_x = min(r.x0 for r in regions)
            min_y = min(r.y0 for r in regions)
            max_x = max(r.x1 for r in regions)
            max_y = max(r.y1 for r in regions)
        elif isinstance(crop, tuple):
            min_x, min_y, max_x, max_y = crop
        else:
            min_x, min_y = 0, 0
            max_x, max_y = self.width, self.height

        header = f"{total} changes in {len(regions)} region{'s' if len(regions) != 1 else ''}"
        lines: list[str] = [header]
        pad = "       "
        lines.append(pad + gap.join(str(c // 10) for c in range(min_x, max_x)))
        lines.append(pad + gap.join(str(c % 10) for c in range(min_x, max_x)))
        data_width = len(gap.join("x" for _ in range(min_x, max_x)))
        lines.append(pad + "-" * data_width)

        for y in range(min_y, max_y):
            cells: list[str] = []
            for x in range(min_x, max_x):
                if (x, y) in all_changes:
                    cells.append(keys[all_changes[(x, y)]])
                else:
                    cells.append(".")
            lines.append(f"y={y:>2d} | {gap.join(cells)}")

        return "\n".join(lines)

    def __repr__(self) -> str:
        return f"Frame({self.width}x{self.height}, colors={len(self.color_counts())})"
