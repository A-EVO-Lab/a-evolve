"""Tests for AgentWorkspace.task_skills_dir isolation (AC-11 / task26).

Two parallel skill registries live under the workspace root:

- ``workspace/skills/`` — library of reusable skills, persistent across tasks
- ``workspace/task_skills/`` — per-task skill fragments, scoped to a single task

These two directories MUST NOT leak into each other: a write to task_skills
never appears in list_skills, and a delete/list over task_skills never
touches skills. This file enforces that invariant.

Also verifies that the existing unified operators that scan ``skills/``
do not accidentally pick up task skills, which would break the Phase 2
``GenerateTaskSkill`` operator once it lands.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from agent_evolve.contract.workspace import AgentWorkspace


# ── Fixtures ───────────────────────────────────────────────────


@pytest.fixture
def workspace(tmp_path: Path) -> AgentWorkspace:
    ws = AgentWorkspace(tmp_path)
    return ws


# ── Round-trip + isolation primitives ─────────────────────────


def test_task_skills_dir_is_expected_path(workspace):
    assert workspace.task_skills_dir == workspace.root / "task_skills"


def test_write_read_roundtrip(workspace):
    workspace.write_task_skill("task-042", "# task 42 body\n")
    body = workspace.read_task_skill("task-042")
    assert body == "# task 42 body\n"


def test_write_task_skill_creates_only_under_task_skills(workspace):
    workspace.write_task_skill("task-042", "body")
    # Lives under task_skills_dir.
    assert (workspace.task_skills_dir / "task-042" / "SKILL.md").exists()
    # Does NOT bleed into skills_dir.
    assert not (workspace.skills_dir / "task-042").exists()
    # list_skills() must not include it.
    assert "task-042" not in [s.name for s in workspace.list_skills()]


def test_list_task_skills_returns_only_task_skills(workspace):
    workspace.write_task_skill("task-001", "# body 1")
    workspace.write_task_skill("task-002", "# body 2")
    # Also seed a regular skill in skills/ — must not appear in list_task_skills.
    workspace.write_skill("general", "---\nname: general\ndescription: d\n---\nbody\n")

    result = workspace.list_task_skills()
    assert set(result.keys()) == {"task-001", "task-002"}
    for meta in result.values():
        assert meta.path.startswith("task_skills/")


def test_list_task_skills_skips_private_subdirs(workspace):
    workspace.write_task_skill("task-ok", "body")
    # Create a sibling "_drafts"-style dir; it must be ignored.
    (workspace.task_skills_dir / "_private" / "SKILL.md").parent.mkdir(
        parents=True, exist_ok=True
    )
    (workspace.task_skills_dir / "_private" / "SKILL.md").write_text("hidden")
    result = workspace.list_task_skills()
    assert set(result.keys()) == {"task-ok"}


def test_delete_task_skill_removes_only_that_task(workspace):
    workspace.write_task_skill("keep", "body")
    workspace.write_task_skill("drop", "body")
    workspace.delete_task_skill("drop")

    remaining = workspace.list_task_skills()
    assert set(remaining.keys()) == {"keep"}
    assert not (workspace.task_skills_dir / "drop").exists()
    assert (workspace.task_skills_dir / "keep").exists()


def test_delete_task_skill_never_touches_skills_dir(workspace):
    workspace.write_skill(
        "persistent", "---\nname: persistent\ndescription: d\n---\n\nbody"
    )
    workspace.write_task_skill("persistent", "# task-scoped body")
    # Delete the task skill with the SAME name as a general skill.
    workspace.delete_task_skill("persistent")
    # General skill MUST still exist.
    assert "persistent" in [s.name for s in workspace.list_skills()]
    assert not (workspace.task_skills_dir / "persistent").exists()


def test_read_task_skill_missing_returns_empty_string(workspace):
    assert workspace.read_task_skill("nope") == ""


def test_read_skill_never_returns_task_skill_content(workspace):
    workspace.write_task_skill("shared", "# TASK body\n")
    # read_skill looks at workspace/skills/<name>/SKILL.md — task_skills is a
    # different tree, so must return "".
    assert workspace.read_skill("shared") == ""


# ── Operator isolation ───────────────────────────────────────


def test_sanity_check_ignores_task_skills(workspace):
    """SanityCheck scans skills/; it must NOT prune or dedup task_skills."""
    from agent_evolve.algorithms.unified.registry import get_operator
    from agent_evolve.algorithms.unified.types import EvidenceContext

    # Seed a tiny (usually pruned) body in task_skills.
    workspace.write_task_skill("tiny-task", "---\nname: tiny-task\ndescription: d\n---\n\ni\n")
    # Also seed a regular skill with valid body so SanityCheck has something real to look at.
    workspace.write_skill(
        "valid-skill",
        "---\nname: valid-skill\ndescription: d\n---\n\nsome body long enough content",
    )

    op = get_operator("SanityCheck")
    report = op.apply(
        workspace,
        EvidenceContext(),
        {"skills": "rw", "prompts": "rw"},
        {"seed_prompt": ""},
    )
    # After SanityCheck, the task skill must still exist.
    assert workspace.read_task_skill("tiny-task").strip() != ""
    # The real skill with sufficient body should remain too.
    assert "valid-skill" in [s.name for s in workspace.list_skills()]


def test_prune_skills_never_removes_task_skills(workspace):
    """PruneSkills only touches list_skills()/list_fragments() inventory."""
    from agent_evolve.algorithms.unified.registry import get_operator
    from agent_evolve.algorithms.unified.types import EvidenceContext

    # Seed 4 task skills.
    for i in range(4):
        workspace.write_task_skill(f"task-{i}", f"# task {i}\n")
    # Seed only 1 regular skill (below the 3-item trigger for PruneSkills).
    workspace.write_skill("one", "---\nname: one\ndescription: d\n---\nbody\n")

    op = get_operator("PruneSkills")
    state = {"mock_pruner": lambda prompt: "REMOVE: task-0\nREMOVE: task-1\n"}
    report = op.apply(
        workspace,
        EvidenceContext(),
        {"skills": "rw", "prompts": "rw"},
        state,
    )
    # No-op due to <3 items in the skills/ + fragments inventory.
    assert report.count == 0
    # All 4 task skills must still exist — PruneSkills cannot see them.
    assert set(workspace.list_task_skills().keys()) == {
        "task-0",
        "task-1",
        "task-2",
        "task-3",
    }


def test_auto_seed_skills_never_writes_into_task_skills(workspace, tmp_path):
    """AutoSeedSkills writes into skills/<name>/SKILL.md, never into task_skills/."""
    from agent_evolve.algorithms.unified.registry import get_operator
    from agent_evolve.algorithms.unified.types import EvidenceContext

    ctx = EvidenceContext()
    ctx.entries["PatternDetector"] = {
        "patterns": [
            {
                "pattern_name": "multi_requirement_miss",
                "count": 5,
                "task_ids": ["a", "b", "c"],
                "description": "",
                "suggested_fix": "",
            }
        ]
    }
    ctx.entries["ClaimTypeAnalyzer"] = {"by_type": {}, "weakest": []}
    op = get_operator("AutoSeedSkills")
    op.apply(workspace, ctx, {"skills": "rw"}, {})
    assert (workspace.skills_dir / "multi-requirement-handler" / "SKILL.md").exists()
    # Not in task_skills_dir.
    assert not (workspace.task_skills_dir / "multi-requirement-handler").exists()
