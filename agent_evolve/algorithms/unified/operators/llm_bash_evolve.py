"""LLMBashEvolve — single LLM call with bash access that mutates the workspace.

Reference: ``agent_evolve/algorithms/adaptive_skill/engine.py`` lines 153-185
(``_run_llm``) and ``agent_evolve/algorithms/adaptive_skill/tools.py``
(``BASH_TOOL_SPEC`` / ``make_workspace_bash`` / ``create_default_llm``).
Independent reimplementation under ``unified/`` with identical bash spec and
behaviour. Prompt input is built from the EvidenceContext using canonical
JSON serialization (``sort_keys=True``, fixed float format) per AC-8.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from ..registry import register_operator
from ..types import MutationReport

logger = logging.getLogger(__name__)


BASH_TOOL_SPEC = {
    "name": "workspace_bash",
    "description": (
        "Execute a bash command in the agent workspace directory. "
        "Use this to read/write skills, prompts, memory files, and inspect git history."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The bash command to execute in the workspace directory.",
            },
        },
        "required": ["command"],
    },
}


DEFAULT_EVOLVER_SYSTEM_PROMPT = """\
You are a meta-learning agent that improves another agent by modifying its workspace files.

The workspace follows a standard directory structure:
- prompts/system.md  -- the agent's system prompt
- skills/*/SKILL.md  -- reusable skill definitions
- skills/_drafts/    -- draft skills from the solver
- memory/*.jsonl     -- episodic and semantic memory
- tools/             -- tool implementations

Your job each cycle:
1. Analyze task observation logs -- identify patterns, common failures, recurring themes
2. Review draft skills -- refine into real skills, merge with existing, or discard
3. Improve the system prompt if needed
4. Update memory with high-level insights, prune redundant entries
5. Use the provided bash tool to read/write files in the workspace
6. Verify your changes with `git diff` before finishing

Guidelines:
- Quality over quantity. Only create skills that genuinely help future tasks.
- Skills use SKILL.md format with YAML frontmatter (name, description).
- Keep memory concise and actionable.
- When modifying files, use precise edits.
"""


def _make_workspace_bash(workspace_root: str | Path):
    def bash(command: str) -> str:
        try:
            result = subprocess.run(
                ["bash", "-c", command],
                capture_output=True,
                text=True,
                timeout=60,
                cwd=str(workspace_root),
            )
            output = (result.stdout + result.stderr).strip()
            return output if output else "(no output)"
        except subprocess.TimeoutExpired:
            return "ERROR: Command timed out."
        except Exception as exc:  # noqa: BLE001
            return f"ERROR: {exc}"

    return bash


def _resolve_llm(model: str, region: str):
    if (
        model.startswith("openai:")
        or model.startswith("/")
        or model.startswith("file:")
    ):
        from ..openai_compat import OpenAICompatProvider

        base_url = (
            os.environ.get("EVOLVER_OPENAI_BASE_URL")
            or os.environ.get("OPENAI_BASE_URL")
        )
        if (model.startswith("/") or model.startswith("file:")) and not base_url:
            raise ValueError(
                "Local/path evolver models require EVOLVER_OPENAI_BASE_URL "
                "or OPENAI_BASE_URL pointing at an OpenAI-compatible server."
            )
        return (
            OpenAICompatProvider(
                model=model.removeprefix("openai:").removeprefix("file:"),
                base_url=base_url,
            ),
            "openai",
        )
    if "." in model and ("anthropic" in model or "amazon" in model or "meta" in model):
        from agent_evolve.llm.bedrock import BedrockProvider

        return BedrockProvider(model_id=model, region=region), "bedrock"
    if model.startswith("claude"):
        from agent_evolve.llm.anthropic import AnthropicProvider

        return AnthropicProvider(model=model), "anthropic"
    if model.startswith(("gpt-", "o1", "o3")):
        from ..openai_compat import OpenAICompatProvider

        return OpenAICompatProvider(model=model), "openai"
    from agent_evolve.llm.bedrock import BedrockProvider

    return BedrockProvider(model_id=model, region=region), "bedrock"


def _canonical_json(obj: Any) -> str:
    """Canonical JSON: sorted keys, ensure_ascii off, stable float formatting."""

    def _default(o: Any) -> Any:
        if hasattr(o, "__dict__"):
            return o.__dict__
        return str(o)

    return json.dumps(obj, sort_keys=True, ensure_ascii=False, default=_default)


def _build_permission_block(
    scope: dict[str, Any] | None,
    state: dict[str, Any] | None,
    protected_skills: list[str] | None = None,
) -> str:
    scope = scope or {}
    state = state or {}
    lines = ["### Workspace Permissions"]

    if scope.get("prompts") == "rw":
        lines.append("- You MAY modify prompts/system.md.")
    else:
        lines.append("- You MUST NOT modify files under prompts/.")

    if scope.get("skills") == "rw":
        max_skills = state.get("max_skills")
        protect_skills = bool(state.get("protect_skills", False))
        if protect_skills:
            lines.append(
                "- You MAY create NEW skills under skills/, but MUST NOT modify "
                "or delete existing skills."
            )
            if protected_skills:
                lines.append(
                    "- Existing protected skills: "
                    + ", ".join(sorted(protected_skills))
                )
        else:
            lines.append("- You MAY create/modify/delete skills under skills/.")
        if max_skills is not None:
            lines.append(f"- Maximum total non-draft skills: {max_skills}.")
    else:
        lines.append("- You MUST NOT modify files under skills/.")

    if scope.get("memory") in ("rw", "append"):
        lines.append("- You MAY append concise entries under memory/.")
    else:
        lines.append("- You MUST NOT modify files under memory/.")

    if scope.get("tools") == "rw":
        lines.append("- You MAY modify files under tools/.")
    else:
        lines.append("- You MUST NOT modify files under tools/.")

    if bool(state.get("skills_only", False)):
        lines.append("- This is a skills-only run: skills/ is the only mutable artifact family.")

    return "\n".join(lines)


def _build_user_prompt(
    evidence: dict[str, Any],
    cycle_num: int,
    scope: dict[str, Any] | None = None,
    state: dict[str, Any] | None = None,
    protected_skills: list[str] | None = None,
) -> str:
    """Canonicalize the EvidenceContext so mocked-LLM diffs are byte-stable."""
    payload = {"cycle": cycle_num, "evidence": {k: evidence[k] for k in sorted(evidence)}}
    return (
        f"## Evolution Cycle #{cycle_num}\n\n"
        + _build_permission_block(scope, state, protected_skills)
        + "\n\n"
        "### Evidence Context (canonicalized JSON)\n```json\n"
        + _canonical_json(payload)
        + "\n```\n\n### Instructions\n"
        "1. Review the evidence above — identify patterns, common failures, recurring themes.\n"
        "2. Use the workspace_bash tool to read/write files in the workspace.\n"
        "3. Obey the workspace permissions exactly.\n"
        "4. Prefer small, targeted skill additions; avoid rewriting large prompt sections.\n"
        "5. Verify your changes with `git diff` before finishing.\n"
    )


def _snapshot_tree(root: Path) -> dict[str, bytes] | None:
    if not root.exists():
        return None
    snapshot: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            snapshot[str(path.relative_to(root))] = path.read_bytes()
    return snapshot


def _restore_tree(root: Path, snapshot: dict[str, bytes] | None) -> None:
    if snapshot is None:
        if root.exists():
            shutil.rmtree(root)
        return

    root.mkdir(parents=True, exist_ok=True)
    wanted = set(snapshot)
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_file() and str(path.relative_to(root)) not in wanted:
            path.unlink()
    for rel, content in snapshot.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_dir() and not any(path.iterdir()):
            path.rmdir()


def _enforce_post_scope(
    workspace: Any,
    scope: dict[str, Any],
    state: dict[str, Any],
    snapshots: dict[str, dict[str, bytes] | None],
    protected_skill_snapshots: dict[str, dict[str, bytes] | None],
    skills_before: set[str],
) -> list[str]:
    restored: list[str] = []
    root = Path(workspace.root)

    artifact_dirs = {
        "prompts": root / "prompts",
        "memory": root / "memory",
        "tools": root / "tools",
    }
    for artifact, path in artifact_dirs.items():
        if scope.get(artifact) not in ("rw", "append"):
            current = _snapshot_tree(path)
            if current != snapshots.get(artifact):
                _restore_tree(path, snapshots.get(artifact))
                restored.append(artifact)

    if scope.get("skills") != "rw":
        skills_path = root / "skills"
        current = _snapshot_tree(skills_path)
        if current != snapshots.get("skills"):
            _restore_tree(skills_path, snapshots.get("skills"))
            restored.append("skills")
        return restored

    if bool(state.get("protect_skills", False)):
        for name, snapshot in protected_skill_snapshots.items():
            skill_dir = root / "skills" / name
            if _snapshot_tree(skill_dir) != snapshot:
                _restore_tree(skill_dir, snapshot)
                restored.append(f"protected_skill:{name}")

    max_skills_raw = state.get("max_skills")
    if max_skills_raw is not None:
        try:
            max_skills = int(max_skills_raw)
        except (TypeError, ValueError):
            max_skills = 0
        if max_skills > 0:
            skills = [s.name for s in workspace.list_skills()]
            if len(skills) > max_skills:
                added = sorted(set(skills) - skills_before)
                removable = added + [s for s in sorted(skills) if s not in added]
                if bool(state.get("protect_skills", False)):
                    removable = [s for s in removable if s not in skills_before]
                for name in removable:
                    if len(workspace.list_skills()) <= max_skills:
                        break
                    workspace.delete_skill(name)
                    restored.append(f"max_skills_removed:{name}")

    return restored


@register_operator("LLMBashEvolve")
class LLMBashEvolve:
    """Single LLM+bash pass that mutates the workspace.

    State keys:
        ``state["cycle_num"]`` — monotonically incremented across cycles.
        ``state["model_id"]`` / ``state["region"]`` — optional overrides.
        ``state["max_tokens"]`` — optional override.
        ``state["mock"]`` — for tests: a callable taking the prompt and
            returning a string, bypassing the real LLM.
    """

    WRITES: frozenset[str] = frozenset({"prompts", "skills", "memory", "tools"})

    DEFAULT_MODEL = "us.anthropic.claude-opus-4-6-v1"
    DEFAULT_REGION = "us-west-2"
    DEFAULT_MAX_TOKENS = 16384

    def apply(
        self,
        workspace: Any,
        context: Any,
        scope: dict[str, Any],
        state: dict[str, Any],
    ) -> MutationReport:
        cycle_num = int(state.get("cycle_num", 0)) + 1
        state["cycle_num"] = cycle_num
        skills_before = {s.name for s in workspace.list_skills()}
        protected_skill_snapshots = {
            name: _snapshot_tree(Path(workspace.root) / "skills" / name)
            for name in skills_before
        }
        snapshots = {
            "prompts": _snapshot_tree(Path(workspace.root) / "prompts"),
            "skills": _snapshot_tree(Path(workspace.root) / "skills"),
            "memory": _snapshot_tree(Path(workspace.root) / "memory"),
            "tools": _snapshot_tree(Path(workspace.root) / "tools"),
        }

        evidence = {
            k: v
            for k, v in dict(getattr(context, "entries", {})).items()
            if not str(k).startswith("__")
        }
        user_prompt = _build_user_prompt(
            evidence,
            cycle_num,
            scope=scope,
            state=state,
            protected_skills=sorted(skills_before),
        )
        max_tokens = int(state.get("max_tokens", self.DEFAULT_MAX_TOKENS))
        bash_fn = _make_workspace_bash(workspace.root)

        # Resolution priority for the LLM backend:
        # 1. ``state["llm_provider"]`` — full provider object; if it exposes
        #    ``converse_loop`` the bash tool path is used, otherwise
        #    ``.complete()`` is used.
        # 2. ``state["mock"]`` — string-only shortcut for simple tests
        #    that only need the LLM to return a fixed reply. No bash.
        # 3. Real provider constructed from ``state["model_id"]``.
        provider = state.get("llm_provider")
        mock = state.get("mock")

        if provider is not None:
            try:
                converse_loop = getattr(provider, "converse_loop", None)
                if callable(converse_loop):
                    response = converse_loop(
                        system_prompt=DEFAULT_EVOLVER_SYSTEM_PROMPT,
                        user_message=user_prompt,
                        tools=[BASH_TOOL_SPEC],
                        tool_executor={"workspace_bash": bash_fn},
                        max_tokens=max_tokens,
                    )
                    response_content = response.content
                else:
                    from agent_evolve.llm.base import LLMMessage

                    response = provider.complete(
                        [
                            LLMMessage(role="system", content=DEFAULT_EVOLVER_SYSTEM_PROMPT),
                            LLMMessage(role="user", content=user_prompt),
                        ],
                        max_tokens=max_tokens,
                    )
                    response_content = response.content
            except Exception as exc:  # noqa: BLE001
                logger.error("LLMBashEvolve: provider call failed: %s", exc)
                return MutationReport(
                    operator_name="LLMBashEvolve",
                    count=0,
                    details={"error": str(exc)[:200]},
                )
        elif callable(mock):
            response_content = mock(user_prompt)
        else:
            model = state.get("model_id", self.DEFAULT_MODEL)
            region = state.get("region", self.DEFAULT_REGION)
            try:
                llm, kind = _resolve_llm(model, region)
            except ImportError as e:
                logger.warning("LLMBashEvolve: provider unavailable (%s)", e)
                return MutationReport(
                    operator_name="LLMBashEvolve",
                    count=0,
                    details={"error": f"provider unavailable: {e}"},
                )
            try:
                converse_loop = getattr(llm, "converse_loop", None)
                if callable(converse_loop):
                    response = converse_loop(
                        system_prompt=DEFAULT_EVOLVER_SYSTEM_PROMPT,
                        user_message=user_prompt,
                        tools=[BASH_TOOL_SPEC],
                        tool_executor={"workspace_bash": bash_fn},
                        max_tokens=max_tokens,
                    )
                    response_content = response.content
                else:
                    from agent_evolve.llm.base import LLMMessage

                    response = llm.complete(
                        [
                            LLMMessage(role="system", content=DEFAULT_EVOLVER_SYSTEM_PROMPT),
                            LLMMessage(role="user", content=user_prompt),
                        ],
                        max_tokens=max_tokens,
                    )
                    response_content = response.content
            except Exception as exc:  # noqa: BLE001
                logger.error("LLMBashEvolve: LLM call failed: %s", exc)
                return MutationReport(
                    operator_name="LLMBashEvolve",
                    count=0,
                    details={"error": str(exc)[:200]},
                )

        restored = _enforce_post_scope(
            workspace,
            scope,
            state,
            snapshots,
            protected_skill_snapshots,
            skills_before,
        )
        skills_after = {s.name for s in workspace.list_skills()}
        added = sorted(skills_after - skills_before)
        removed = sorted(skills_before - skills_after)
        try:
            workspace.clear_drafts()
        except Exception:
            pass

        return MutationReport(
            operator_name="LLMBashEvolve",
            count=len(added) + len(removed),
            details={
                "cycle": cycle_num,
                "skills_added": added,
                "skills_removed": removed,
                "scope_restored": restored,
                "response_len": len(response_content or ""),
            },
        )
