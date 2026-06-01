"""Docker sandbox lifecycle for PolyBench solver tasks.

PolyBench is pure reasoning (no sandbox required) when no evolved tools
exist.  When the workspace ``tools/`` directory contains evolved scripts,
each task gets a lightweight alpine sandbox (shared with the evolver)
with the evolved tools copied into ``/tools/``.
"""
from __future__ import annotations

import os
import subprocess
import tempfile

# Share the evolver sandbox image (alpine + bash + git + python3)
from ...algorithms.aevolve.tools import SANDBOX_IMAGE, _ensure_sandbox_image


def start_sandbox(task_id: str, tool_files: dict[str, str]) -> str:
    """Start a per-task Docker sandbox and copy evolved tools into it.

    Returns the container name.  Mirrors the SWE-bench pattern but uses
    the shared evolver-sandbox image.
    """
    _ensure_sandbox_image()

    ctr = f"poly-{task_id.replace('/', '_')}-{os.getpid()}"
    subprocess.run(["docker", "rm", "-f", ctr], capture_output=True)
    r = subprocess.run(
        ["docker", "run", "-d", "--name", ctr,
         "--network", "none",
         SANDBOX_IMAGE, "sleep", "infinity"],
        capture_output=True, text=True, timeout=30,
    )
    if r.returncode != 0:
        raise RuntimeError(f"Failed to start sandbox: {r.stderr}")

    subprocess.run(
        ["docker", "exec", ctr, "mkdir", "-p", "/tools"],
        capture_output=True, timeout=10,
    )
    for fname, content in tool_files.items():
        with tempfile.NamedTemporaryFile(mode="w", suffix=f"_{fname}", delete=False) as tf:
            tf.write(content)
            tmp = tf.name
        try:
            subprocess.run(
                ["docker", "cp", tmp, f"{ctr}:/tools/{fname}"],
                capture_output=True, timeout=10,
            )
        finally:
            os.unlink(tmp)
        subprocess.run(
            ["docker", "exec", ctr, "chmod", "+x", f"/tools/{fname}"],
            capture_output=True, timeout=10,
        )
    return ctr


def stop_sandbox(ctr: str | None) -> None:
    if ctr:
        subprocess.run(["docker", "rm", "-f", ctr], capture_output=True, timeout=10)
