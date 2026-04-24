"""Dispatcher: translates the docker argv (``train`` | ``inference``) into a
call to the workspace's ``train.py`` or the bundled ``inference.py``.

Contract:
  - ``train``      → exec ``python /workspace/train.py``
  - ``inference``  → exec ``python /opt/aevolve/inference.py --config ...``

Both verbs exit 0 on success, non-zero on failure. stdout/stderr stream to the
host's captured log file.
"""

from __future__ import annotations

import os
import subprocess
import sys


def _die(msg: str, code: int = 2) -> None:
    print(f"[entrypoint] ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def run_train(extra_args: list[str]) -> int:
    os.environ.setdefault("AEVOLVE_VERB", "train")
    driver = "/workspace/train.py"
    if not os.path.exists(driver):
        _die(f"missing {driver} (workspace must include train.py)")
    cmd = ["python", driver, *extra_args]
    print(f"[entrypoint] train → {' '.join(cmd)}", flush=True)
    return subprocess.run(cmd).returncode


def run_inference(extra_args: list[str]) -> int:
    os.environ.setdefault("AEVOLVE_VERB", "inference")
    cmd = ["python", "/opt/aevolve/inference.py", *extra_args]
    print(f"[entrypoint] inference → {' '.join(cmd)}", flush=True)
    return subprocess.run(cmd).returncode


def main() -> None:
    argv = sys.argv[1:]
    if not argv:
        _die("missing verb; expected 'train' or 'inference'")

    verb, rest = argv[0], argv[1:]
    if verb == "train":
        sys.exit(run_train(rest))
    if verb == "inference":
        sys.exit(run_inference(rest))
    _die(f"unknown verb {verb!r}; expected 'train' or 'inference'")


if __name__ == "__main__":
    main()
