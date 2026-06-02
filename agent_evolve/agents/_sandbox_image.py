"""Shared Docker sandbox image builder.

A single one-time build of a small Alpine image (bash + git + the common
Python packages evolved tools / benchmark solvers need), so containers
started with ``--network none`` already have everything available offline.

Lives under ``agents/`` (beside ``_partial_trajectory.py`` /
``_harness_filter.py``) so the benchmark sandboxes and the evolver share one
definition without any benchmark depending on a specific evolution engine.
"""
from __future__ import annotations

import logging
import subprocess

logger = logging.getLogger(__name__)

SANDBOX_IMAGE = "evolver-sandbox:latest"
_SANDBOX_BASE = "alpine:latest"
_sandbox_image_ready = False


def _ensure_sandbox_image():
    """Build the sandbox image with bash+git pre-installed (idempotent).

    We build a local image once so that containers started with
    ``--network none`` already have everything they need.
    """
    global _sandbox_image_ready
    if _sandbox_image_ready:
        return
    # Check if our pre-built image exists
    result = subprocess.run(
        ["docker", "image", "inspect", SANDBOX_IMAGE],
        capture_output=True,
    )
    if result.returncode == 0:
        _sandbox_image_ready = True
        return
    # Build it from alpine + common packages evolved tools need.
    logger.info("Building sandbox image %s (one-time)...", SANDBOX_IMAGE)
    dockerfile = (
        f"FROM {_SANDBOX_BASE}\n"
        "RUN apk add --no-cache bash git python3 py3-pip curl jq \\\n"
        "    && pip3 install --break-system-packages \\\n"
        "       requests beautifulsoup4 lxml htmldate \\\n"
        "       duckduckgo-search feedparser pyyaml \\\n"
        "       numpy sympy yfinance \\\n"
        "       pycryptodome python-dateutil\n"
    )
    result = subprocess.run(
        ["docker", "build", "-t", SANDBOX_IMAGE, "-"],
        input=dockerfile,
        capture_output=True, text=True, timeout=300,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to build sandbox image: {result.stderr}")
    _sandbox_image_ready = True
    logger.info("Sandbox image built: %s", SANDBOX_IMAGE)
