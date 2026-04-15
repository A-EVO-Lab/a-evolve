"""Tests for GEPA workspace ↔ candidate serialization round-trip."""
import json
from pathlib import Path
from agent_evolve.contract.workspace import AgentWorkspace
from agent_evolve.config import EvolveConfig


def _make_workspace(tmp_path: Path) -> AgentWorkspace:
    ws = AgentWorkspace(tmp_path)
    ws.write_prompt("You are an expert agent.")
    ws.write_fragment("code-exec.md", "When using code execution, prefer Python.")
    ws.write_fragment("verification.md", "Always verify results.")
    ws.write_skill("multi-req", "---\nname: multi-req\ndescription: Handle multiple requirements\n---\n\n# Multi-Req\n\nWhen a task has multiple parts...")
    ws.write_skill("entity-verify", "---\nname: entity-verify\ndescription: Verify entities\n---\n\n# Entity Verify\n\nBefore answering...")
    ws.add_memory({"task_id": "t1", "score": 0.8}, category="episodic")
    ws.add_memory({"task_id": "t2", "score": 0.0}, category="episodic")
    ws.add_memory({"pattern": "retry on error"}, category="strategic")
    return ws


def test_build_candidate_includes_all_layers(tmp_path):
    from agent_evolve.algorithms.gepa.serialization import build_candidate
    ws = _make_workspace(tmp_path)
    config = EvolveConfig(evolve_prompts=True, evolve_skills=True, evolve_memory=True)
    candidate = build_candidate(ws, config)
    assert "system_prompt" in candidate
    assert "prompt_fragments" in candidate
    assert "skills" in candidate
    assert "memory" in candidate
    assert candidate["system_prompt"] == "You are an expert agent."


def test_build_candidate_respects_config_gates(tmp_path):
    from agent_evolve.algorithms.gepa.serialization import build_candidate
    ws = _make_workspace(tmp_path)
    config = EvolveConfig(evolve_prompts=True, evolve_skills=False, evolve_memory=False)
    candidate = build_candidate(ws, config)
    assert "system_prompt" in candidate
    assert "prompt_fragments" in candidate
    assert "skills" not in candidate
    assert "memory" not in candidate


def test_serialize_and_parse_fragments(tmp_path):
    from agent_evolve.algorithms.gepa.serialization import serialize_fragments, parse_fragments
    ws = _make_workspace(tmp_path)
    blob = serialize_fragments(ws)
    assert "=== FRAGMENT: code-exec.md ===" in blob
    assert "=== FRAGMENT: verification.md ===" in blob
    parsed = parse_fragments(blob)
    names = [name for name, _ in parsed]
    assert "code-exec.md" in names
    assert "verification.md" in names
    for name, content in parsed:
        if name == "code-exec.md":
            assert "When using code execution, prefer Python." == content


def test_serialize_and_parse_skills(tmp_path):
    from agent_evolve.algorithms.gepa.serialization import serialize_skills, parse_skills
    ws = _make_workspace(tmp_path)
    blob = serialize_skills(ws)
    assert "=== SKILL: multi-req ===" in blob
    assert "=== SKILL: entity-verify ===" in blob
    parsed = parse_skills(blob)
    names = [name for name, _ in parsed]
    assert "multi-req" in names
    assert "entity-verify" in names
    for name, content in parsed:
        if name == "multi-req":
            assert "name: multi-req" in content
            assert "# Multi-Req" in content


def test_serialize_memory_includes_category(tmp_path):
    from agent_evolve.algorithms.gepa.serialization import serialize_memory
    ws = _make_workspace(tmp_path)
    blob = serialize_memory(ws)
    lines = [line for line in blob.splitlines() if line.strip()]
    assert len(lines) == 3
    for line in lines:
        entry = json.loads(line)
        assert "_category" in entry


def test_restore_candidate_round_trip(tmp_path):
    from agent_evolve.algorithms.gepa.serialization import build_candidate, restore_candidate
    ws = _make_workspace(tmp_path)
    config = EvolveConfig(evolve_prompts=True, evolve_skills=True, evolve_memory=True)
    candidate = build_candidate(ws, config)
    restore_dir = tmp_path / "restored"
    restore_dir.mkdir()
    ws2 = AgentWorkspace(restore_dir)
    restore_candidate(ws2, candidate, config)
    assert ws2.read_prompt() == "You are an expert agent."
    assert sorted(ws2.list_fragments()) == sorted(ws.list_fragments())
    skill_names = [s.name for s in ws2.list_skills()]
    assert "multi-req" in skill_names
    assert "entity-verify" in skill_names
    memories = ws2.read_all_memories(limit=100)
    assert len(memories) == 3


def test_restore_candidate_replaces_existing_content(tmp_path):
    from agent_evolve.algorithms.gepa.serialization import restore_candidate
    ws = _make_workspace(tmp_path)
    config = EvolveConfig(evolve_prompts=True, evolve_skills=True, evolve_memory=True)
    new_candidate = {
        "system_prompt": "New prompt.",
        "prompt_fragments": "=== FRAGMENT: new-frag.md ===\nNew fragment content.",
        "skills": "=== SKILL: new-skill ===\n---\nname: new-skill\ndescription: A new skill\n---\n\n# New Skill\n\nDo new things.",
        "memory": '{"_category": "episodic", "task_id": "t99", "score": 1.0}\n',
    }
    restore_candidate(ws, new_candidate, config)
    assert ws.read_prompt() == "New prompt."
    assert ws.list_fragments() == ["new-frag.md"]
    skill_names = [s.name for s in ws.list_skills()]
    assert skill_names == ["new-skill"]
    memories = ws.read_all_memories(limit=100)
    assert len(memories) == 1
    assert memories[0]["task_id"] == "t99"


def test_restore_memory_clears_and_rewrites(tmp_path):
    from agent_evolve.algorithms.gepa.serialization import restore_memory
    ws = AgentWorkspace(tmp_path)
    ws.add_memory({"old": "data"}, category="episodic")
    ws.add_memory({"old": "strategic"}, category="strategic")
    blob = '{"_category": "episodic", "new": "entry1"}\n{"_category": "strategic", "new": "entry2"}\n'
    restore_memory(ws, blob)
    all_mem = ws.read_all_memories(limit=100)
    assert len(all_mem) == 2
    assert all(m.get("new") for m in all_mem)
    assert not any(m.get("old") for m in all_mem)
