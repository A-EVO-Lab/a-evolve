"""Grid-to-image rendering for ARC-AGI-3 frames.

Adapted from arcprize/ARC-AGI-3-Agents (multimodal.py).
Converts 64x64 int grids to PNG images for multimodal LLM input.
"""

from __future__ import annotations

import base64
import io
from typing import Any, Sequence

import numpy as np

from .colors import PALETTE_RGBA

_SCALE = 2  # 64 px -> 128 px
_TARGET_SIZE = 64 * _SCALE


def grid_to_image(grid: Sequence[Sequence[int]]) -> Any:
    """Convert a 64x64 int grid (values 0-15) to a PIL Image.

    Returns a 128x128 RGBA PIL Image with nearest-neighbor upscaling
    to maintain crisp pixel art. Returns None if PIL is not available.
    """
    try:
        from PIL import Image
    except ImportError:
        return None

    raw = bytearray()
    for row in grid:
        for idx in row:
            raw.extend(PALETTE_RGBA[min(idx, 15)])

    h = len(grid)
    w = len(grid[0]) if grid else 64
    img = Image.frombytes("RGBA", (w, h), bytes(raw))
    img = img.resize((_TARGET_SIZE, _TARGET_SIZE), Image.NEAREST)
    return img


def image_to_base64(img: Any) -> str:
    """Return a base-64 encoded PNG string from a PIL Image."""
    buffer = io.BytesIO()
    img.save(buffer, format="PNG", optimize=True)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def image_diff(img_a: Any, img_b: Any) -> Any:
    """Create a visual diff between two PIL Images.

    Changed pixels are highlighted in red on a black background.
    Returns a PIL Image, or None if PIL is not available.
    """
    try:
        from PIL import Image
    except ImportError:
        return None

    a = np.asarray(img_a.convert("RGB"))
    b = np.asarray(img_b.convert("RGB"))

    if a.shape != b.shape:
        return None

    diff_mask = np.any(a != b, axis=-1)
    if not diff_mask.any():
        return Image.new("RGB", (a.shape[1], a.shape[0]), (0, 0, 0))

    diff_img = np.zeros_like(a)
    diff_img[diff_mask] = (255, 0, 0)
    return Image.fromarray(diff_img)


def grid_to_base64(grid: Sequence[Sequence[int]]) -> str | None:
    """Convenience: grid -> base64 PNG string. Returns None if PIL unavailable."""
    img = grid_to_image(grid)
    if img is None:
        return None
    return image_to_base64(img)
