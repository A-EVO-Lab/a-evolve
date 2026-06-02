"""FutureX per-task sandbox + shared search-throttle primitives.

Split out of ``solver.py`` so the module-level constants (``_search_lock``,
``_last_search_time``, throttle intervals) and the ``.env`` auto-loader
are imported by *every* ProcessPool/ThreadPool worker that runs a solver,
independent of whether the caller reaches through the legacy
``backends.futurex`` shim or the canonical ``agents.futurex.solver`` path.

Both entry points share the same module namespace because Python's
import cache dedupes a single file.  The throttle lock must be defined
exactly once, or two worker threads will each get their own lock and
exceed the Wikipedia/DDGS rate limits.
"""
from __future__ import annotations

import logging
import os
import subprocess
import tempfile
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

# ── .env auto-loader (module import side-effect) ───────────────────────
# Walk up to find the project root (contains ``.git`` or ``pyproject.toml``)
# instead of hard-coding a relative depth — the fork lives deeper than the
# V2 root, so ``parent.parent.parent`` would miss.
def _project_root() -> Path:
    here = Path(__file__).resolve()
    for parent in [here, *here.parents]:
        if (parent / ".git").exists() or (parent / "pyproject.toml").exists():
            return parent
    return here.parent


_env_file = _project_root() / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip())

# ── Module-level search throttles (shared across all workers) ──────────
# Wikipedia API: ~200 req/min → 0.15s gap between calls is safe.
# DDGS fans out to multiple engines per query → 1s gap avoids soft bans.
_search_lock = threading.Lock()
_last_search_time = [0.0]
_WIKI_THROTTLE = 0.15
_DDGS_THROTTLE = 1.0


# ── Docker sandbox lifecycle ────────────────────────────────────────────

# Reuse the evolver's alpine sandbox image.
from ._sandbox_image import SANDBOX_IMAGE, _ensure_sandbox_image  # noqa: E402


_FORWARD_ENV_KEYS = [
    "SERPER_API_KEY", "JINA_API_KEY", "JINA_BASE_URL",
    "EXA_API_KEY",
]


def start_sandbox(task_id: str, tool_files: dict, sandbox_network: str = "none",
                   cutoff_date: str = "") -> str:
    """Start a per-task Docker sandbox and copy evolved tools into it."""
    _ensure_sandbox_image()
    ctr = f"fx-{task_id.replace('/', '_')}-{os.getpid()}"
    subprocess.run(["docker", "rm", "-f", ctr], capture_output=True)
    cmd = ["docker", "run", "-d", "--name", ctr,
           "--add-host", "datasets-server.huggingface.co:127.0.0.1",
           "--add-host", "huggingface.co:127.0.0.1",
           "--network", sandbox_network]
    for key in _FORWARD_ENV_KEYS:
        val = os.environ.get(key)
        if val:
            cmd.extend(["-e", f"{key}={val}"])
    if cutoff_date:
        cmd.extend(["-e", f"FUTUREX_CUTOFF_DATE={cutoff_date}"])
    cmd.extend([SANDBOX_IMAGE, "sleep", "infinity"])
    r = subprocess.run(
        cmd,
        capture_output=True, text=True, timeout=30,
    )
    if r.returncode != 0:
        raise RuntimeError(f"Failed to start sandbox: {r.stderr}")

    # Create directories for all files. Flat names (e.g. "finance_data.py")
    # go into /tools/; paths with "/" (e.g. "infra/sources/finance.py") are
    # placed at their absolute path inside the container.
    dirs_needed = {"/tools"}
    for rel_path in tool_files:
        if "/" in rel_path:
            dirs_needed.add("/" + rel_path.rsplit("/", 1)[0])
    for d in sorted(dirs_needed):
        subprocess.run(
            ["docker", "exec", ctr, "mkdir", "-p", d],
            capture_output=True, timeout=10,
        )

    for rel_path, content in tool_files.items():
        target = f"/{rel_path}" if "/" in rel_path else f"/tools/{rel_path}"
        safe_suffix = "_" + rel_path.replace("/", "_")
        with tempfile.NamedTemporaryFile(mode="w", suffix=safe_suffix, delete=False) as tf:
            tf.write(content)
            tmp = tf.name
        try:
            subprocess.run(
                ["docker", "cp", tmp, f"{ctr}:{target}"],
                capture_output=True, timeout=10,
            )
        finally:
            os.unlink(tmp)
        subprocess.run(
            ["docker", "exec", ctr, "chmod", "+x", target],
            capture_output=True, timeout=10,
        )
    return ctr


def stop_sandbox(ctr: str | None) -> None:
    if ctr:
        subprocess.run(["docker", "rm", "-f", ctr], capture_output=True, timeout=10)
