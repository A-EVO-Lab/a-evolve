"""CTF-Dojo cybersecurity benchmark adapter.

Loads CTF challenges from a processed ctf-archive directory (after running
CTF-Forge) and evaluates flag submissions via SHA256 hash comparison.

658 challenges from 40+ CTF events (2011-2025), categories: crypto, pwn, rev,
misc, forensics, web.

Prerequisites:
  1. Clone ctf-archive: git clone https://github.com/pwncollege/ctf-archive.git
  2. Run CTF-Forge: python ctf_forge.py (generates challenge.json, Dockerfiles)
  3. Build Docker images: docker-compose build (for server-based challenges)

Temporal ordering: year extracted from event name (e.g., 0ctf2017 -> 2017).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
from pathlib import Path

from ...types import Feedback, Task, Trajectory
from ..base import BenchmarkAdapter

logger = logging.getLogger(__name__)


def _extract_year(event: str) -> int:
    """Extract year from CTF event name (e.g., '0ctf2017' -> 2017)."""
    m = re.search(r"(20\d{2})", event)
    return int(m.group(1)) if m else 2020


class CtfDojoBenchmark(BenchmarkAdapter):
    """CTF-Dojo benchmark adapter.

    Loads challenges from a ctf_archive.json catalog file and the
    corresponding challenge directories.
    """

    def __init__(
        self,
        catalog_path: str = "ctf_archive.json",
        archive_dir: str = "ctf-archive",
        shuffle: bool = False,
        holdout_ratio: float = 0.0,
        eval_timeout: int = 300,
        exp_tag: str = "",
        category_filter: str | None = None,
    ):
        self.catalog_path = catalog_path
        self.archive_dir = archive_dir
        self.shuffle = shuffle
        self.holdout_ratio = holdout_ratio
        self.eval_timeout = eval_timeout
        self.exp_tag = exp_tag
        self.category_filter = category_filter
        self._cache: dict[str, list[dict]] = {}
        self._split_done = False

    def get_tasks(self, split: str = "test", limit: int = 0) -> list[Task]:
        rows = self._load_split(split)
        if limit > 0:
            rows = rows[:limit]
        tasks = []
        for row in rows:
            cid = row["id"]
            tasks.append(Task(
                id=cid,
                input=row["description"],
                metadata={
                    "event": row["event"],
                    "challenge": row["challenge"],
                    "category": row["category"],
                    "year": row["year"],
                    "path": row["path"],
                    "compose": row.get("compose", False),
                    "flag_sha256": row.get("flag_sha256", ""),
                    "files": row.get("files", []),
                },
            ))
        return tasks

    def evaluate(self, task: Task, trajectory: Trajectory) -> Feedback:
        """Evaluate by comparing submitted flag's SHA256 against stored hash."""
        output = trajectory.output.strip()
        metadata = task.metadata
        instance_id = task.id
        flag_sha256 = metadata.get("flag_sha256", "")

        if not output:
            return Feedback(
                success=False, score=0.0,
                detail=f"No flag submitted for {instance_id}",
                raw={"instance_id": instance_id, "reason": "empty_output"},
            )

        if not flag_sha256:
            return Feedback(
                success=False, score=0.0,
                detail=f"No flag hash available for {instance_id}",
                raw={"instance_id": instance_id, "reason": "no_flag_hash"},
            )

        # Extract flag from output (look for flag{...} pattern or use full output)
        flag = _extract_flag(output)

        # Compare SHA256 — try the extracted flag first, then the content
        # inside braces (some challenges hash the raw key, not the wrapper).
        flag_hash = hashlib.sha256(flag.encode()).hexdigest()
        correct = flag_hash == flag_sha256
        if not correct:
            inner = re.search(r"\{([^}]+)\}", flag)
            if inner:
                inner_hash = hashlib.sha256(inner.group(1).encode()).hexdigest()
                if inner_hash == flag_sha256:
                    correct = True
                    flag = inner.group(1)

        return Feedback(
            success=correct,
            score=1.0 if correct else 0.0,
            detail=(
                f"{'CORRECT' if correct else 'WRONG'} flag for {instance_id} "
                f"({metadata['category']})"
            ),
            raw={
                "instance_id": instance_id,
                "submitted_flag": flag[:50],
                "hash_match": correct,
            },
        )

    # ── Data loading ─────────────────────────────────────────────────

    def _load_split(self, split: str) -> list[dict]:
        if not self._split_done:
            self._do_split()
        if split in self._cache:
            return self._cache[split]
        return self._cache.get("train", [])

    def _do_split(self) -> None:
        import random

        catalog_path = Path(self.catalog_path)
        if not catalog_path.exists():
            logger.error("Catalog not found: %s", catalog_path)
            self._cache["test"] = self._cache["train"] = self._cache["holdout"] = []
            self._split_done = True
            return

        with open(catalog_path) as f:
            catalog = json.load(f)

        all_challenges = []
        for cid, info in catalog.items():
            category = info.get("category", "")
            if self.category_filter and self.category_filter.lower() != category.lower():
                continue

            event = info.get("event", "")
            challenge_path = info.get("path", "")
            year = _extract_year(event)

            # Read description and flag hash from challenge directory
            desc = ""
            flag_sha256 = ""
            flag = ""
            compose = False
            files = []

            challenge_dir = Path(challenge_path)
            if not challenge_dir.exists():
                challenge_dir = Path(self.archive_dir) / event / info.get("challenge", "")

            # Try challenge.json first (generated by ctf_forge.py)
            cj_path = challenge_dir / "challenge.json"
            if cj_path.exists():
                try:
                    with open(cj_path) as f:
                        cj = json.load(f)
                    desc = cj.get("description", "")
                    compose = cj.get("compose", False)
                    files = cj.get("files", [])
                    flag = cj.get("flag", "")
                except Exception:
                    pass

            # Try DESCRIPTION.md
            if not desc:
                desc_path = challenge_dir / "DESCRIPTION.md"
                if desc_path.exists():
                    try:
                        desc = desc_path.read_text()
                    except Exception:
                        pass

            if not desc:
                # Fallback to catalog description
                desc = info.get("description", f"CTF Challenge: {info.get('challenge', cid)} ({category})")

            # Read flag hash
            for hash_name in ["flag.sha256", ".flag.sha256", "flag.sha256.txt"]:
                hash_path = challenge_dir / hash_name
                if hash_path.exists():
                    try:
                        flag_sha256 = hash_path.read_text().strip().split()[0]
                    except Exception:
                        pass
                    break

            # If flag is known directly, compute hash
            if flag and not flag_sha256:
                flag_sha256 = hashlib.sha256(flag.encode()).hexdigest()

            # Fallback to catalog flag hash if challenge directory doesn't exist
            if not flag_sha256:
                catalog_hash = info.get("flag_sha256", "").strip()
                if catalog_hash:
                    # Remove any trailing exclamation marks or other artifacts
                    flag_sha256 = catalog_hash.rstrip('!')

            # Fallback to catalog files list if not loaded from directory
            if not files:
                files = info.get("files", [])

            all_challenges.append({
                "id": cid,
                "event": event,
                "challenge": info.get("challenge", ""),
                "category": category,
                "year": year,
                "path": str(challenge_dir),
                "description": desc,
                "compose": compose,
                "flag_sha256": flag_sha256,
                "flag": flag,
                "files": files,
            })

        if self.shuffle:
            random.shuffle(all_challenges)
        else:
            # Sort by year (temporal), then event, then challenge name
            all_challenges.sort(key=lambda r: (r["year"], r["event"], r["challenge"]))

        n_holdout = 0 if self.holdout_ratio == 0.0 else max(1, int(len(all_challenges) * self.holdout_ratio))
        self._cache["holdout"] = all_challenges[:n_holdout]
        self._cache["train"] = all_challenges[n_holdout:]
        self._cache["test"] = all_challenges

        self._split_done = True
        logger.info(
            "Loaded %d challenges from %s (train=%d, holdout=%d)",
            len(all_challenges), self.catalog_path,
            len(self._cache["train"]), len(self._cache["holdout"]),
        )


def _extract_flag(text: str) -> str:
    """Extract flag from agent output. Looks for flag{...} patterns.

    Uses a single regex that captures the full flag including any event-specific
    prefix (e.g. actf{...}, DUCTF{...}, picoCTF{...}).  The prefix is an
    optional group of word characters so that bare ``flag{...}`` still matches.
    """
    # Match optional word-char prefix followed by CTF/flag/FLAG{...}
    # This correctly captures actf{}, hsctf{}, DUCTF{}, CCTF{}, picoCTF{}, etc.
    m = re.search(r"(\w*(?:flag|ctf)\{[^}]+\})", text, re.IGNORECASE)
    if m:
        return m.group(1)
    # If no pattern found, use the last non-empty line
    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    return lines[-1] if lines else text.strip()
