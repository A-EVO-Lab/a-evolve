#!/usr/bin/env python3
"""Dependency-free structural checks for the EVO-HARNESS release."""

from __future__ import annotations

import ast
import json
import subprocess
import sys
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
REQUIRED_ENTRY_POINTS = [
    ROOT / "examples" / "evolve_cl_bench.py",
    ROOT / "examples" / "evolve_swe.py",
    ROOT / "examples" / "evolve_tau_bench.py",
    ROOT / "examples" / "evolve_terminal.py",
    ROOT / "examples" / "evolve_webarena_infinity.py",
]


def main() -> int:
    missing = [str(path.relative_to(ROOT)) for path in REQUIRED_ENTRY_POINTS if not path.is_file()]
    if missing:
        raise SystemExit(f"Missing benchmark entry points: {', '.join(missing)}")

    python_files = sorted((ROOT / "agent_evolve").rglob("*.py"))
    python_files.extend(sorted((ROOT / "examples").rglob("*.py")))
    for path in python_files:
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    json_files = sorted((ROOT / "seed_workspaces").rglob("*.json"))
    for path in json_files:
        json.loads(path.read_text(encoding="utf-8"))

    jsonl_files = sorted((ROOT / "seed_workspaces").rglob("*.jsonl"))
    for path in jsonl_files:
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.strip():
                try:
                    json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{path}:{line_number}: {exc}") from exc

    with (ROOT / "pyproject.toml").open("rb") as handle:
        tomllib.load(handle)

    shell_files = sorted((ROOT / "scripts").glob("*.sh"))
    for path in shell_files:
        subprocess.run(["bash", "-n", str(path)], check=True)

    print(
        f"Validated {len(python_files)} Python files, "
        f"{len(json_files) + len(jsonl_files)} JSON/JSONL files, "
        f"and {len(shell_files)} shell scripts."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
