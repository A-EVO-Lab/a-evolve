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
    ROOT / "evo_harness" / "cl_bench.py",
    ROOT / "evo_harness" / "swe_bench.py",
    ROOT / "evo_harness" / "tau_bench.py",
    ROOT / "evo_harness" / "terminal_bench.py",
    ROOT / "evo_harness" / "webarena_infinity.py",
]
REQUIRED_FIGURES = [ROOT / "figures" / "main.jpg"]


def main() -> int:
    missing = [str(path.relative_to(ROOT)) for path in REQUIRED_ENTRY_POINTS if not path.is_file()]
    if missing:
        raise SystemExit(f"Missing benchmark entry points: {', '.join(missing)}")
    missing_figures = [str(path.relative_to(ROOT)) for path in REQUIRED_FIGURES if not path.is_file()]
    if missing_figures:
        raise SystemExit(f"Missing paper figures: {', '.join(missing_figures)}")
    if (ROOT / "examples").exists():
        raise SystemExit("Legacy examples/ directory must not exist in the release")

    for path in REQUIRED_ENTRY_POINTS:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if not any(isinstance(node, ast.FunctionDef) and node.name == "main" for node in tree.body):
            raise SystemExit(f"Missing main() entry point: {path.relative_to(ROOT)}")

    python_files = sorted((ROOT / "agent_evolve").rglob("*.py"))
    python_files.extend(sorted((ROOT / "evo_harness").rglob("*.py")))
    for path in python_files:
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    json_files = sorted((ROOT / "seed_skills").rglob("*.json"))
    for path in json_files:
        json.loads(path.read_text(encoding="utf-8"))

    jsonl_files = sorted((ROOT / "seed_skills").rglob("*.jsonl"))
    for path in jsonl_files:
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.strip():
                try:
                    json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{path}:{line_number}: {exc}") from exc

    skill_files = sorted((ROOT / "seed_skills").rglob("SKILL.md"))
    if not skill_files:
        raise SystemExit("No seed skills found")
    for path in skill_files:
        content = path.read_text(encoding="utf-8")
        if not content.startswith("---") or "description:" not in content:
            raise SystemExit(f"Invalid skill frontmatter: {path.relative_to(ROOT)}")

    with (ROOT / "pyproject.toml").open("rb") as handle:
        tomllib.load(handle)

    shell_files = sorted((ROOT / "scripts").glob("*.sh"))
    if len(shell_files) != 5:
        raise SystemExit(f"Expected exactly five benchmark launchers, found {len(shell_files)}")
    for path in shell_files:
        subprocess.run(["bash", "-n", str(path)], check=True)
        command = path.read_text(encoding="utf-8")
        if "--use-seed-skills" in command or "--no-seed-skills" in command:
            raise SystemExit(f"Paper launcher must use the default no-seed setting: {path.name}")

    webarena_launcher = (ROOT / "scripts" / "run_webarena_evolve.sh").read_text(encoding="utf-8")
    if "--evolve-all" in webarena_launcher:
        raise SystemExit("WebArena paper launcher must not enable --evolve-all")

    print(
        f"Validated {len(python_files)} Python files, "
        f"{len(json_files) + len(jsonl_files)} JSON/JSONL files, "
        f"{len(skill_files)} seed skills, and {len(shell_files)} shell scripts."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
