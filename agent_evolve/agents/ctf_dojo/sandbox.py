"""Docker sandbox lifecycle for CTF-Dojo solver tasks.

Separated from the main solver so the image build / container helpers
can be imported lazily and so module-level side effects (the "image is
ready" cache) survive across every ProcessPool worker that imports them.
"""
from __future__ import annotations

import logging
import os
import subprocess
import tempfile
import time
from pathlib import Path

# Reuse the evolver's alpine sandbox image for fallback paths.
from ...algorithms.aevolve.tools import SANDBOX_IMAGE

logger = logging.getLogger(__name__)

# CTF solver image: glibc Ubuntu so compiled challenge binaries run.
CTF_SOLVER_IMAGE = "ctf-solver:latest"
_ctf_solver_ready = False


def _project_root() -> Path:
    """Walk up from this file until we hit a ``.git`` or ``pyproject.toml``.

    V2's original helper hard-coded ``Path(__file__).parent.parent.parent``
    which was sensitive to the file's depth.  In the fork this file lives
    four levels deeper than in V2, so the hard-coded depth would break;
    walk up instead.
    """
    here = Path(__file__).resolve()
    for parent in [here, *here.parents]:
        if (parent / ".git").exists() or (parent / "pyproject.toml").exists():
            return parent
    return here.parent


def _ensure_ctf_solver_image() -> None:
    """Build the Ubuntu-based CTF solver image (glibc compatible)."""
    global _ctf_solver_ready
    if _ctf_solver_ready:
        return
    # Already built?
    r = subprocess.run(["docker", "image", "inspect", CTF_SOLVER_IMAGE],
                       capture_output=True)
    if r.returncode == 0:
        _ctf_solver_ready = True
        return
    # Try the official ctf-archive Dockerfile first
    dockerfile = _project_root() / "data" / "ctf-archive" / "Dockerfile"
    if dockerfile.exists():
        logger.info("Building CTF solver image from %s …", dockerfile)
        r = subprocess.run(
            ["docker", "build", "-t", CTF_SOLVER_IMAGE, "-f", str(dockerfile), "."],
            capture_output=True, text=True, timeout=600,
            cwd=str(dockerfile.parent),
        )
        if r.returncode == 0:
            _ctf_solver_ready = True
            return
    # Fallback: minimal Ubuntu image with glibc + pip
    logger.info("Building CTF solver image (Ubuntu fallback) …")
    r = subprocess.run(
        ["docker", "build", "-t", CTF_SOLVER_IMAGE, "-"],
        input=(
            "FROM ubuntu:20.04\n"
            "ENV DEBIAN_FRONTEND=noninteractive\n"
            "RUN apt-get update && apt-get install -y --no-install-recommends "
            "python3 python3-pip bash git nmap gdb file binutils "
            "libffi-dev build-essential && apt-get clean\n"
        ),
        capture_output=True, text=True, timeout=600,
    )
    if r.returncode != 0:
        raise RuntimeError(f"Failed to build CTF solver image: {r.stderr}")
    _ctf_solver_ready = True


def start_ctf_sandbox(task_id: str, challenge_path: str, tool_files: dict) -> str:
    """Start a sandbox container for CTF challenge solving.

    Returns the container name, or a marker string beginning with
    ``__CONTAINER_FAILED__`` when Docker is unavailable.
    """
    try:
        _ensure_ctf_solver_image()
    except Exception:
        # If solver image build fails, fall through — docker run below will
        # either succeed with the evolver sandbox or mark the container as
        # failed.
        pass

    ctr = f"ctf-{task_id.replace('/', '_')}-{os.getpid()}"
    try:
        subprocess.run(["docker", "rm", "-f", ctr], capture_output=True, timeout=15)
    except subprocess.TimeoutExpired:
        # Async-kill and keep going
        subprocess.Popen(["docker", "rm", "-f", ctr],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    mounts = []
    challenge_files_available = False
    if Path(challenge_path).exists():
        mounts = ["-v", f"{os.path.abspath(challenge_path)}:/challenge:ro"]
        challenge_files_available = True

    # Retry Docker container startup
    max_retries = 3
    last_error = None
    container_started = False

    for attempt in range(max_retries):
        try:
            r = subprocess.run(
                ["docker", "run", "-d", "--name", ctr,
                 "--network", "host",  # allow connecting to challenge servers
                 *mounts,
                 CTF_SOLVER_IMAGE, "sleep", "infinity"],
                capture_output=True, text=True, timeout=60,
            )
            if r.returncode == 0:
                container_started = True
                break
            else:
                last_error = f"Docker run failed (attempt {attempt + 1}/{max_retries}): {r.stderr}"
        except subprocess.TimeoutExpired as e:
            last_error = f"Docker run timeout (attempt {attempt + 1}/{max_retries}): {e}"

        # Clean up failed container before retry
        try:
            subprocess.run(["docker", "rm", "-f", ctr], capture_output=True, timeout=15)
        except subprocess.TimeoutExpired:
            subprocess.Popen(["docker", "rm", "-f", ctr],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        if attempt < max_retries - 1:
            backoff = min(2 ** attempt, 30)
            time.sleep(backoff)

    # Fallback 1: simpler container without host network
    if not container_started:
        try:
            r = subprocess.run(
                ["docker", "run", "-d", "--name", ctr, *mounts, SANDBOX_IMAGE, "sleep", "infinity"],
                capture_output=True, text=True, timeout=300,
            )
            if r.returncode == 0:
                container_started = True
            else:
                last_error = f"Fallback simple container failed: {r.stderr}"
        except subprocess.TimeoutExpired as e:
            last_error = f"Fallback container timeout: {e}"

    # Fallback 2: minimal container without mounts
    if not container_started:
        try:
            r = subprocess.run(
                ["docker", "run", "-d", "--name", ctr, SANDBOX_IMAGE, "sleep", "infinity"],
                capture_output=True, text=True, timeout=120,
            )
            if r.returncode == 0:
                container_started = True
                challenge_files_available = False
            else:
                last_error = f"Minimal container failed: {r.stderr}"
        except subprocess.TimeoutExpired as e:
            last_error = f"Minimal container timeout: {e}"

    if not container_started:
        return f"__CONTAINER_FAILED__{last_error}"

    # Ensure /challenge directory exists
    try:
        subprocess.run(
            ["docker", "exec", ctr, "mkdir", "-p", "/challenge"],
            capture_output=True, timeout=30,
        )
    except subprocess.TimeoutExpired:
        pass

    if not challenge_files_available:
        try:
            subprocess.run(
                ["docker", "exec", ctr, "bash", "-c",
                 'echo "No challenge files available - files may need to be downloaded from CTF archive" > /challenge/README.txt'],
                capture_output=True, timeout=30,
            )
        except subprocess.TimeoutExpired:
            pass

    # Install extra CTF tools
    try:
        subprocess.run(
            ["docker", "exec", ctr, "bash", "-c",
             "apt-get update -qq && apt-get install -y -qq nmap gdb file binutils 2>/dev/null || true"],
            capture_output=True, timeout=180,
        )
    except subprocess.TimeoutExpired:
        pass

    # Copy evolved tools into /tools
    if tool_files:
        try:
            subprocess.run(
                ["docker", "exec", ctr, "mkdir", "-p", "/tools"],
                capture_output=True, timeout=30,
            )
        except subprocess.TimeoutExpired:
            pass
        for fname, content in tool_files.items():
            with tempfile.NamedTemporaryFile(mode="w", suffix=f"_{fname}", delete=False) as tf:
                tf.write(content)
                tmp = tf.name
            try:
                subprocess.run(
                    ["docker", "cp", tmp, f"{ctr}:/tools/{fname}"],
                    capture_output=True, timeout=30,
                )
            except subprocess.TimeoutExpired:
                pass
            finally:
                os.unlink(tmp)

    return ctr


def stop_ctf_sandbox(ctr: str | None) -> None:
    if not ctr or ctr.startswith("__CONTAINER_FAILED__"):
        return
    try:
        subprocess.run(["docker", "rm", "-f", ctr],
                       capture_output=True, timeout=15)
    except (subprocess.TimeoutExpired, Exception):
        subprocess.Popen(["docker", "rm", "-f", ctr],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
