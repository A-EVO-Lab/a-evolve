"""Execution backends for SkillBench tasks.

This module provides a backend abstraction so SkillBench tasks can be
solved through native Docker execution or through Harbor.
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
import hashlib
import shlex
import subprocess
import tempfile
import time
import uuid
from concurrent.futures import TimeoutError as FuturesTimeoutError
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Protocol

import yaml

from ...llm.base import LLMMessage, LLMResponse
from ...llm.bedrock import BedrockProvider
from ...types import Task, Trajectory
from .docker_env import SkillBenchContainer, VerificationResult, build_image
from .official_terminus import (
    DEFAULT_SKILL_DIRS as OFFICIAL_SKILL_DIRS,
    SkillDocLoader as OfficialSkillDocLoader,
    TerminusJSONPlainParser,
)

logger = logging.getLogger(__name__)

_TOP_LEVEL_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)

_LEGACY_SKILL_DIRS = [
    "/root/.agents/skills",
    "/root/.claude/skills",
    "/root/.codex/skills",
    "/root/.terminus/skills",
]

_RETRYABLE_FAILURE_CLASSES = {
    "throttled",
    "model_error",
    "docker_error",
    "container_missing",
    "infrastructure_error",
    "runtime_exception",
    "agent_runtime_error",
}

_NON_RETRYABLE_FAILURE_CLASSES = {
    "none",
    "agent_timeout",
    "verifier_timeout",
    "reward_missing",
    "reward_parse_error",
    "assertion",
    "module_missing",
    "file_missing",
    "test_fail",
    "verifier_fail",
    "harbor_timeout",
    "harbor_config_error",
}


def _safe_slug(value: str, default: str) -> str:
    """Convert arbitrary text into a safe slug for docker names/tags."""
    slug = re.sub(r"[^a-z0-9_.-]+", "-", value.strip().lower()).strip("-.")
    return slug or default


def _clamp_reward(value: float | int | None) -> float:
    if value is None:
        return 0.0
    try:
        reward = float(value)
    except (TypeError, ValueError):
        return 0.0
    if reward < 0.0:
        return 0.0
    if reward > 1.0:
        return 1.0
    return reward


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit <= 16:
        return text[:limit]
    head = limit // 2
    tail = limit - head - 13
    return f"{text[:head]}\n...[truncated]...\n{text[-tail:]}"


def _parse_top_level_frontmatter(text: str) -> dict[str, Any]:
    match = _TOP_LEVEL_FRONTMATTER_RE.match(text)
    if not match:
        return {}
    try:
        parsed = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _read_skill_category(skill_md: Path) -> str:
    try:
        text = skill_md.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    frontmatter = _parse_top_level_frontmatter(text)
    category = frontmatter.get("category", "")
    return category.strip().lower() if isinstance(category, str) else ""


def _normalize_category(cat: str) -> str:
    """Canonicalize category-like strings for formatting-only matching.

    This intentionally fixes only superficial formatting mismatches:
    - case differences
    - hyphen / underscore / punctuation separator differences
    - repeated whitespace

    It does NOT try to solve semantic taxonomy mismatches such as
    "financial modeling" vs "financial-analysis". Historical evidence from
    older 0310 logs may reflect the pre-fix behavior and should be treated
    with that context in mind.
    """
    lowered = cat.lower().strip()
    collapsed = re.sub(r"[^a-z0-9]+", " ", lowered)
    return re.sub(r"\s+", " ", collapsed).strip()


def _categories_overlap(task_category: str, skill_category: str) -> bool:
    if not task_category or not skill_category:
        return False
    tc = _normalize_category(task_category)
    sc = _normalize_category(skill_category)
    return tc in sc or sc in tc


def _select_workspace_skills_for_task(
    ws_skills: Path,
    task_category: str,
    *,
    seed_skills: set[str],
    max_general_skills: int = 0,  # 0 = no cap (inject all)
    task_input: str = "",
) -> list[Path]:
    """Select workspace skills to inject into the container.

    When *max_general_skills* is 0 (default), injects ALL skills — the LLM
    discovers them via ``list_skills`` and loads relevant ones.

    When *max_general_skills* > 0, uses keyword-overlap scoring to select
    the most relevant skills.  Approach from terminal-agent evolution
    (bing_dev v10/v22): "More skills ≠ better — filtering prevents dilution."
    """
    all_skills: list[Path] = []
    for skill_dir in sorted(ws_skills.iterdir()):
        skill_md = skill_dir / "SKILL.md"
        if not skill_dir.is_dir() or not skill_md.exists():
            continue
        all_skills.append(skill_dir)

    if max_general_skills <= 0:
        return all_skills

    # ── Keyword-overlap selection ─────────────────────────────────
    task_lower = task_input.lower() if task_input else ""
    scored: list[tuple[int, Path]] = []

    for skill_dir in all_skills:
        score = 0
        skill_md = skill_dir / "SKILL.md"

        # Keywords from skill directory name (e.g., "energy-market" → ["energy", "market"])
        name_keywords = skill_dir.name.replace("-", " ").replace("_", " ").split()

        # Keywords from frontmatter description
        desc_keywords: list[str] = []
        try:
            text = skill_md.read_text(encoding="utf-8", errors="replace")[:1000]
            fm = _parse_top_level_frontmatter(text)
            desc = fm.get("description", "")
            if isinstance(desc, str):
                desc_keywords = desc.lower().split()
        except Exception:
            pass

        # Score: count keyword matches (words > 3 chars to avoid noise)
        all_keywords = name_keywords + desc_keywords
        if task_lower:
            score = sum(1 for kw in all_keywords if len(kw) > 3 and kw.lower() in task_lower)

        # Category match bonus (+5)
        skill_cat = _read_skill_category(skill_md)
        if _categories_overlap(task_category, skill_cat):
            score += 5

        scored.append((score, skill_dir))

    scored.sort(key=lambda x: x[0], reverse=True)

    # Only inject skills with score >= 2 (at least 2 keyword matches)
    selected = [s for sc, s in scored[:max_general_skills] if sc >= 2]

    if selected:
        top_info = [(sc, s.name) for sc, s in scored[:max(max_general_skills, 5)] if sc >= 2]
        logger.info(
            "Skill selection: %d/%d skills (top: %s)",
            len(selected), len(all_skills),
            [(name, sc) for sc, name in top_info[:5]],
        )
    else:
        logger.info("Skill selection: no relevant skills (vanilla prompt)")

    return selected


def _get_task_skill_dir(task_skills_dir: str | Path | None, task_id: str) -> Path | None:
    if not task_skills_dir:
        return None
    candidate = Path(task_skills_dir) / task_id
    skill_md = candidate / "SKILL.md"
    if candidate.is_dir() and skill_md.exists():
        return candidate
    return None


_SUMMARIZE_PROMPT = """\
Summarize the conversation so far in bullet points. Include:
- What commands were run and their key results
- What files were created or modified
- What errors or issues were encountered
- What approach is being taken
- What remains to be done

Be concise. Output ONLY the summary bullets, nothing else."""


def _manage_conversation(
    messages: list[LLMMessage],
    *,
    window_size: int = 130,
    preserve_recent: int = 20,
    provider: "BedrockProvider | None" = None,
    model_id: str = "",
    region: str = "us-west-2",
    max_tokens: int = 16384,
) -> list[LLMMessage]:
    """Summarizing conversation manager following the SWE-bench pattern.

    Instead of dropping old messages (losing information), summarizes them
    into a single message using the LLM.  Only triggers when messages exceed
    window_size.  Always preserves pinned prefix (system + first user message)
    and the most recent ``preserve_recent`` messages.

    Falls back to sliding-window drop if summarization fails.
    """
    # Find the pinned prefix: system messages + first user message
    pin_end = 0
    for i, m in enumerate(messages):
        pin_end = i + 1
        if m.role == "user":
            break

    rest = messages[pin_end:]
    if len(rest) <= window_size:
        return messages

    # Split: messages to summarize vs messages to keep
    keep_count = max(preserve_recent, 4)
    to_summarize = rest[:-keep_count]
    to_keep = rest[-keep_count:]

    if not to_summarize:
        return messages

    # Try to summarize via LLM
    try:
        if provider is None:
            from ...llm.bedrock import BedrockProvider as _BP
            provider = _BP(model_id=model_id, region=region)

        # Build summarization request from old messages
        summary_text_parts = []
        for m in to_summarize:
            role = m.role
            content = (m.content or "")[:500]  # cap each message
            summary_text_parts.append(f"[{role}]: {content}")

        summary_input = "\n".join(summary_text_parts[-30:])  # last 30 of old msgs

        sum_messages = [
            LLMMessage(role="user", content=f"{_SUMMARIZE_PROMPT}\n\n{summary_input}"),
        ]
        sum_response = provider.complete(sum_messages, max_tokens=1024, temperature=0.2)
        summary = sum_response.content or "(summary failed)"

        summary_msg = LLMMessage(
            role="user",
            content=f"[Conversation Summary — {len(to_summarize)} earlier messages]\n{summary}",
        )

        logger.info(
            "Conversation manager: summarized %d old messages into 1, keeping %d pinned + %d recent",
            len(to_summarize), pin_end, len(to_keep),
        )
        return messages[:pin_end] + [summary_msg] + to_keep

    except Exception as e:
        # Fallback: simple sliding window drop
        logger.warning(
            "Summarization failed (%s), falling back to sliding window drop", e,
        )
        dropped = len(rest) - window_size
        return messages[:pin_end] + rest[-window_size:]


def _tail_text(text: str, *, max_lines: int = 120, max_chars: int = 6000) -> str:
    lines = text.splitlines()
    tail = "\n".join(lines[-max_lines:])
    if len(tail) > max_chars:
        tail = tail[-max_chars:]
    return tail


def _unique_preserve_order(values: list[str]) -> list[str]:
    seen: dict[str, bool] = {}
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen[value] = True
        result.append(value)
    return result


def _extract_json_object(text: str) -> dict[str, Any] | None:
    """Extract and parse the first JSON object from *text*."""
    if not text:
        return None

    starts = [i for i, ch in enumerate(text) if ch == "{"]
    for start in starts:
        depth = 0
        in_string = False
        escape = False
        for idx in range(start, len(text)):
            ch = text[idx]
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
                continue

            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start : idx + 1]
                    try:
                        parsed = json.loads(candidate)
                    except json.JSONDecodeError:
                        break
                    if isinstance(parsed, dict):
                        return parsed
                    break
    return None


def _classify_failure_from_text(text: str) -> str:
    lowered = (text or "").lower()
    if "throttlingexception" in lowered or "too many tokens" in lowered:
        return "throttled"
    if "no such container" in lowered:
        return "container_missing"
    if "docker" in lowered and ("error" in lowered or "failed" in lowered):
        return "docker_error"
    if "bedrock" in lowered and ("error" in lowered or "exception" in lowered):
        return "model_error"
    if "traceback" in lowered or "exception" in lowered:
        return "runtime_exception"
    return "infrastructure_error"


def _is_response_truncated(response: LLMResponse) -> bool:
    """Check if a Bedrock response was truncated due to max_tokens."""
    if isinstance(response.raw, dict):
        return response.raw.get("stopReason") == "max_tokens"
    return False


def _call_llm_with_retry(
    provider: BedrockProvider,
    messages: list[LLMMessage],
    max_tokens: int,
    temperature: float,
    max_retries: int = 6,  # default is 3, changed to 6
    min_wait: float = 4.0,
    max_wait: float = 15.0,
) -> LLMResponse:
    """LLM-level retry wrapper matching official LiteLLM retry behavior."""
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            return provider.complete(messages, max_tokens=max_tokens, temperature=temperature)
        except Exception as e:
            err_lower = str(e).lower()
            if any(kw in err_lower for kw in ("authentication", "credential", "context", "window")):
                raise
            last_exc = e
            wait = min(max_wait, min_wait * (2 ** attempt))
            logger.warning(
                "LLM call failed (attempt %d/%d): %s, retrying in %.1fs",
                attempt + 1, max_retries, e, wait,
            )
            time.sleep(wait)
    raise last_exc  # type: ignore[misc]


def _build_step(
    *,
    llm_output: str,
    usage: dict[str, Any],
    passed: bool,
    reward_float: float,
    pass_binary: bool,
    eval_output: str,
    verifier_tail: str,
    failure_class: str,
    backend: str,
    raw_job_path: str | None,
    comparison_key: str,
    timed_out: bool,
    loaded_skills: list[str] | None = None,
    skill_tool_events: list[dict[str, Any]] | None = None,
    attempt: int,
) -> dict[str, Any]:
    return {
        "llm_output": _truncate(llm_output, 8000),
        "usage": usage,
        "passed": passed,
        "score": _clamp_reward(reward_float),
        "reward_float": _clamp_reward(reward_float),
        "pass_binary": bool(pass_binary),
        "eval_output": _truncate(eval_output, 10000),
        "verifier_tail": _truncate(verifier_tail, 8000),
        "failure_class": failure_class,
        "backend": backend,
        "raw_job_path": raw_job_path,
        "comparison_key": comparison_key,
        "timed_out": timed_out,
        "loaded_skills": loaded_skills or [],
        "skill_tool_events": skill_tool_events or [],
        "attempt": attempt,
    }


def _extract_skill_description(skill_content: str) -> str:
    match = re.search(r"(?im)^\s*description\s*:\s*(.+?)\s*$", skill_content)
    if match:
        return match.group(1).strip()
    first_line = next((ln.strip() for ln in skill_content.splitlines() if ln.strip()), "")
    return first_line[:200] if first_line else "No description"


def _discover_available_skills(container: SkillBenchContainer) -> list[dict[str, str]]:
    discovered: dict[str, dict[str, str]] = {}
    for sdir in _LEGACY_SKILL_DIRS:
        stdout, _, rc = container.exec_command(f"ls -1 {shlex.quote(sdir)} 2>/dev/null", timeout=20)
        if rc != 0 or not stdout.strip():
            continue
        for raw_name in stdout.splitlines():
            name = raw_name.strip()
            if not name or name in discovered:
                continue
            skill_path = f"{sdir}/{name}/SKILL.md"
            try:
                content = container.read_file(skill_path)
            except FileNotFoundError:
                continue
            discovered[name] = {
                "name": name,
                "description": _extract_skill_description(content),
                "location": f"{sdir}/{name}",
            }
    return sorted(discovered.values(), key=lambda item: item["name"])


def _load_skill(container: SkillBenchContainer, skill_name: str) -> tuple[str | None, str | None]:
    for sdir in _LEGACY_SKILL_DIRS:
        skill_path = f"{sdir}/{skill_name}/SKILL.md"
        try:
            content = container.read_file(skill_path)
        except FileNotFoundError:
            continue
        if content.strip():
            return content, sdir
    return None, None


def _sanitize_terminal_output(text: str) -> str:
    lines = []
    for line in text.splitlines():
        if line in {
            "bash: cannot set terminal process group (-1): Inappropriate ioctl for device",
            "bash: no job control in this shell",
        }:
            continue
        lines.append(line)
    cleaned = "\n".join(lines).strip()
    return cleaned


def _format_loaded_references(name: str, references: list[tuple[str, str]]) -> str:
    lines = [f"Loaded references for skill: {name}"]
    for filename, content in references:
        lines.append(f"- {filename}\n{content}")
    return "\n".join(lines).strip()


def _format_loaded_skills_block(
    loaded_skills: dict[str, str],
    loaded_references: dict[str, list[tuple[str, str]]],
) -> str:
    if not loaded_skills:
        return "No skills loaded."
    sections: list[str] = []
    for name, content in loaded_skills.items():
        sections.append(f"Loaded skill: {name}\n---\n{content}")
        refs = loaded_references.get(name)
        if refs:
            sections.append(_format_loaded_references(name, refs))
    return "\n\n".join(sections).strip()


def _build_skill_prompt_prefix_json(
    skills_metadata: list[dict[str, str]],
    loaded_skills: dict[str, str],
    loaded_references: dict[str, list[tuple[str, str]]],
) -> str:
    skills_prompt = ""
    if skills_metadata:
        skills_json = json.dumps(skills_metadata, indent=2, ensure_ascii=False)
        skills_prompt = f"available_skills:\n{skills_json}\n"
    loaded_block = _format_loaded_skills_block(loaded_skills, loaded_references)
    return f"{skills_prompt}LOADED SKILLS:\n{loaded_block}\n\n"


def _inject_skill_prompt(prompt: str, skill_block: str) -> str:
    anchor = "\n\nTask Description:\n"
    if anchor in prompt:
        return prompt.replace(anchor, f"\n\n{skill_block}{anchor}")
    return f"{prompt}\n\n{skill_block}"


def _extract_skill_tool_call_json(response: str) -> dict[str, Any] | None:
    if "load_skill" not in response:
        return None
    try:
        json_match = re.search(
            r'\{\s*"load_skill[^"]*"\s*:\s*"[^"]+"\s*\}',
            response,
            re.DOTALL,
        )
        if not json_match:
            return None
        payload = json.loads(json_match.group())
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


class SkillBenchExecutionBackend(Protocol):
    """Protocol for backend-specific task execution."""

    backend_name: str

    def solve(self, task: Task) -> Trajectory:
        """Solve one SkillBench task and return a normalized trajectory."""


class NativeSkillBenchBackend:
    """Native execution via strands or Terminus2-style loop + task Docker environment."""

    backend_name = "native"

    def __init__(
        self,
        build_agent: Callable[[list], Any],
        remember: Callable[..., None],
        *,
        model_id: str,
        region: str,
        max_tokens: int,
        base_system_prompt: str = "",
        native_profile: str = "terminus2",
        retry_max: int = 6,
        retry_min_wait_sec: float = 1.0,
        retry_max_wait_sec: float = 120.0,
        workspace_skills_dir: str | None = None,
        task_skills_dir: str | None = None,
        write_episodic_memory: bool = True,
        skill_select_limit: int = 0,
    ):
        self._build_agent = build_agent
        self._remember = remember
        self.write_episodic_memory = write_episodic_memory
        self.skill_select_limit = skill_select_limit
        self.model_id = model_id
        self.region = region
        self.max_tokens = max_tokens
        self.base_system_prompt = base_system_prompt
        self.workspace_skills_dir = workspace_skills_dir
        self.task_skills_dir = task_skills_dir
        profile = (native_profile or "terminus2").strip().lower()
        if profile not in {"strands", "terminus2", "terminus2_legacy"}:
            profile = "terminus2"
        self.native_profile = profile
        self.retry_max = max(0, int(retry_max))
        self.retry_min_wait_sec = max(0.1, float(retry_min_wait_sec))
        self.retry_max_wait_sec = max(self.retry_min_wait_sec, float(retry_max_wait_sec))

        self._terminus_legacy_template = ""
        self._terminus_legacy_template_path = (
            Path(__file__).resolve().parent / "prompts" / "terminus-json-plain.txt"
        )
        if self._terminus_legacy_template_path.exists():
            self._terminus_legacy_template = self._terminus_legacy_template_path.read_text(
                encoding="utf-8"
            )

        self._terminus_official_template = ""
        self._terminus_official_template_path = (
            Path(__file__).resolve().parent
            / "official_terminus"
            / "prompt-templates"
            / "terminus-json-plain.txt"
        )
        self._terminus_official_template_sha256 = ""
        if self._terminus_official_template_path.exists():
            template_bytes = self._terminus_official_template_path.read_bytes()
            self._terminus_official_template = template_bytes.decode("utf-8")
            self._terminus_official_template_sha256 = hashlib.sha256(template_bytes).hexdigest()

    def solve(self, task: Task) -> Trajectory:
        attempt = 0
        while True:
            trajectory = self._solve_once(task=task, attempt=attempt)
            step = trajectory.steps[-1] if trajectory.steps else {}
            pass_binary = bool(step.get("pass_binary", step.get("passed", False)))
            failure_class = str(step.get("failure_class", "infrastructure_error"))

            if pass_binary:
                return trajectory
            if attempt >= self.retry_max:
                return trajectory
            if not self._is_retryable_failure(failure_class):
                return trajectory

            wait_sec = self._compute_backoff(attempt)
            logger.warning(
                "Retrying native solve for %s (profile=%s, attempt=%d/%d, failure_class=%s, wait=%.1fs)",
                task.id,
                self.native_profile,
                attempt + 1,
                self.retry_max,
                failure_class,
                wait_sec,
            )
            time.sleep(wait_sec)
            attempt += 1

    def _solve_once(self, *, task: Task, attempt: int) -> Trajectory:
        dockerfile_dir = task.metadata.get("dockerfile_dir", "")
        test_sh_path = task.metadata.get("test_sh_path", "")
        test_py_path = task.metadata.get("test_py_path")
        build_timeout = int(task.metadata.get("build_timeout_sec", 600))
        agent_timeout = int(task.metadata.get("agent_timeout_sec", 900))
        verifier_timeout = int(task.metadata.get("verifier_timeout_sec", 900))
        cpus = task.metadata.get("cpus", 1)
        memory = task.metadata.get("memory", "4g")
        comparison_key = task.metadata.get("comparison_key", task.id)

        if isinstance(memory, (int, float)):
            memory = f"{int(memory)}m"

        container: SkillBenchContainer | None = None
        try:
            run_id = _safe_slug(os.environ.get("SKILLBENCH_RUN_ID", ""), "")
            if not run_id:
                run_id = uuid.uuid4().hex[:12]
            task_slug = _safe_slug(task.id, "task")

            image_tag = build_image(
                dockerfile_dir,
                tag=f"skillbench-{task_slug}:{run_id}",
                timeout=build_timeout,
            )
            container = SkillBenchContainer(
                image=image_tag,
                container_name=f"sb-{task_slug}-{run_id}",
                cpus=cpus,
                memory=str(memory),
            )
            container.start()
            container.copy_tests(test_sh_path, test_py_path)

            # Inject workspace skills — filtered by relevance or all.
            n_injected_skills = 0
            if self.workspace_skills_dir:
                ws_skills = Path(self.workspace_skills_dir)
                if ws_skills.is_dir():
                    task_category = task.metadata.get("category", "").lower()
                    to_inject = _select_workspace_skills_for_task(
                        ws_skills,
                        task_category,
                        seed_skills=set(),
                        max_general_skills=self.skill_select_limit,
                        task_input=task.input,
                    )

                    for skill_dir in to_inject:
                        for target in _LEGACY_SKILL_DIRS:
                            container.exec_command(
                                f"mkdir -p {target}/{skill_dir.name}",
                                timeout=10,
                            )
                            container.copy_into(
                                str(skill_dir) + "/.",
                                f"{target}/{skill_dir.name}/",
                            )
                    logger.info(
                        "Injected %d/%d workspace skills: %s",
                        len(to_inject),
                        sum(1 for d in ws_skills.iterdir()
                            if d.is_dir() and (d / "SKILL.md").exists()),
                        [d.name for d in to_inject],
                    )

                    n_injected_skills = len(to_inject)

            # Inject task-specific skills (dual library)
            if self.task_skills_dir:
                task_skill_dir = _get_task_skill_dir(self.task_skills_dir, task.id)
                if task_skill_dir is not None and not task_skill_dir.name.startswith("_"):
                    for target in _LEGACY_SKILL_DIRS:
                        container.exec_command(
                            f"mkdir -p {target}/{task_skill_dir.name}",
                            timeout=10,
                        )
                        container.copy_into(
                            str(task_skill_dir) + "/.",
                            f"{target}/{task_skill_dir.name}/",
                        )
                    logger.info(
                        "Injected task-specific skills: %s", [task_skill_dir.name],
                    )

            if self.native_profile == "strands":
                run_result = self._run_strands_profile(
                    task_input=task.input,
                    container=container,
                    agent_timeout=agent_timeout,
                )
            elif self.native_profile == "terminus2_legacy":
                run_result = self._run_terminus2_legacy_profile(
                    task=task,
                    container=container,
                    agent_timeout=agent_timeout,
                )
            else:
                run_result = self._run_terminus2_official_profile(
                    task=task,
                    container=container,
                    agent_timeout=agent_timeout,
                )

            verification = container.run_verification(timeout=verifier_timeout)
            run_result["n_injected_skills"] = n_injected_skills
            return self._build_native_trajectory(
                task=task,
                comparison_key=comparison_key,
                run_result=run_result,
                verification=verification,
                attempt=attempt,
            )

        except Exception as e:
            logger.error("Native solve() failed for %s: %s", task.id, e)
            failure_class = _classify_failure_from_text(str(e))
            step = _build_step(
                llm_output="",
                usage={},
                passed=False,
                reward_float=0.0,
                pass_binary=False,
                eval_output=str(e),
                verifier_tail=_tail_text(str(e)),
                failure_class=failure_class,
                backend=self.backend_name,
                raw_job_path=None,
                comparison_key=comparison_key,
                timed_out=False,
                loaded_skills=[],
                skill_tool_events=[],
                attempt=attempt,
            )
            return Trajectory(task_id=task.id, output="", steps=[step])

        finally:
            if container is not None:
                try:
                    container.stop()
                except Exception:
                    pass

    def _build_native_trajectory(
        self,
        *,
        task: Task,
        comparison_key: str,
        run_result: dict[str, Any],
        verification: VerificationResult,
        attempt: int,
    ) -> Trajectory:
        output = str(run_result.get("output", ""))
        usage = run_result.get("usage", {}) or {}
        timed_out = bool(run_result.get("timed_out", False))
        failure_hint = run_result.get("failure_hint")

        reward_float = _clamp_reward(verification.reward_float)
        pass_binary = bool(verification.pass_binary)
        passed = pass_binary

        eval_output = verification.eval_output
        if run_result.get("agent_error"):
            eval_output = f"[agent_error]\n{run_result['agent_error']}\n\n{eval_output}".strip()

        verifier_tail = verification.verifier_tail
        if run_result.get("agent_error"):
            verifier_tail = _tail_text(f"{run_result['agent_error']}\n\n{verification.eval_output}")

        if passed:
            failure_class = "none"
        elif timed_out:
            failure_class = "agent_timeout"
        elif failure_hint:
            failure_class = str(failure_hint)
        else:
            failure_class = verification.failure_class

        loaded_skills = _unique_preserve_order(
            [
                str(event.get("name"))
                for event in run_result.get("skill_tool_events", [])
                if event.get("tool") == "load_skill" and event.get("status") == "loaded"
            ]
            + list(run_result.get("loaded_skills", []))
        )

        # GAP#3: log which skills the solver actually used
        _n_injected = run_result.get("n_injected_skills", 0)
        if loaded_skills:
            logger.info(
                "  [%s] skills_used: %s (%d/%d injected)",
                task.id, loaded_skills, len(loaded_skills), _n_injected,
            )
        else:
            logger.debug("  [%s] no skills loaded by solver", task.id)

        step = _build_step(
            llm_output=output,
            usage=usage,
            passed=passed,
            reward_float=reward_float,
            pass_binary=pass_binary,
            eval_output=eval_output,
            verifier_tail=verifier_tail,
            failure_class=failure_class,
            backend=self.backend_name,
            raw_job_path=None,
            comparison_key=comparison_key,
            timed_out=timed_out,
            loaded_skills=loaded_skills,
            skill_tool_events=run_result.get("skill_tool_events", []),
            attempt=attempt,
        )
        step["native_impl"] = run_result.get("native_impl", self.native_profile)
        step["parser_warning"] = str(run_result.get("parser_warning", ""))
        step["parse_error"] = str(run_result.get("parse_error", ""))
        step["skills_loaded"] = list(run_result.get("skills_loaded", loaded_skills))
        step["references_loaded"] = list(run_result.get("references_loaded", []))
        step["prompt_template_source"] = run_result.get("prompt_template_source")
        step["prompt_template_sha256"] = run_result.get("prompt_template_sha256")
        episode_trace = run_result.get("episode_trace")
        if isinstance(episode_trace, list):
            step["episode_trace"] = episode_trace
        raw_episode_count = run_result.get("episode_count")
        if isinstance(raw_episode_count, int):
            episode_count = max(0, raw_episode_count)
        elif isinstance(episode_trace, list):
            episode_count = len(episode_trace)
        else:
            episode_count = 0
        step["episode_count"] = episode_count
        step["episode_trace_path"] = run_result.get("episode_trace_path")

        # Build episodic memory (only if enabled)
        if self.write_episodic_memory:
            category_str = task.metadata.get("category", "unknown")
            mem_parts = [
                f"{'PASS' if passed else 'FAIL'} {task.id} [{category_str}]"
                f" score={reward_float:.3f} {failure_class}",
            ]
            if not passed and verifier_tail:
                err_lines = [
                    ln.strip() for ln in verifier_tail.splitlines()
                    if ln.strip() and any(
                        kw in ln.lower()
                        for kw in ("assert", "error", "fail", "expected")
                    )
                ]
                if err_lines:
                    mem_parts.append("; ".join(err_lines[:2])[:150])

            self._remember(
                " | ".join(mem_parts),
                category="episodic",
                task_id=task.id,
                backend=f"{self.backend_name}:{self.native_profile}",
                timed_out=timed_out,
                reward_float=reward_float,
                pass_binary=pass_binary,
                failure_class=failure_class,
            )

        return Trajectory(task_id=task.id, output=output, steps=[step])

    def _run_strands_profile(
        self,
        *,
        task_input: str,
        container: SkillBenchContainer,
        agent_timeout: int,
    ) -> dict[str, Any]:
        from .tools import create_container_tools

        skill_tool_events: list[dict[str, Any]] = []

        def _on_skill_event(event: dict[str, Any]) -> None:
            skill_tool_events.append(event)

        tools = create_container_tools(lambda: container, on_skill_event=_on_skill_event)
        agent = self._build_agent(tools)
        logger.info("Solving with native strands profile and %d tools", len(tools))

        timed_out = False
        output = ""
        usage: dict[str, Any] = {}
        agent_error: str | None = None
        failure_hint: str | None = None

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(agent, task_input)
            try:
                response = future.result(timeout=max(1, int(agent_timeout)))
                output = str(response)
                try:
                    accumulated = response.metrics.accumulated_usage
                    usage = {
                        "input_tokens": accumulated.get("inputTokens", 0),
                        "output_tokens": accumulated.get("outputTokens", 0),
                        "total_tokens": accumulated.get("totalTokens", 0),
                    }
                except Exception:
                    usage = {}
            except FuturesTimeoutError:
                timed_out = True
                failure_hint = "agent_timeout"
                output = (
                    f"Native SkillBench solve timed out after {agent_timeout}s "
                    "(strands profile)."
                )
                logger.warning(output)
                future.cancel()
            except Exception as e:
                agent_error = str(e)
                failure_hint = _classify_failure_from_text(agent_error)
                output = f"Native strands solve error: {agent_error}"
                logger.warning("Native strands solve error: %s", agent_error)

        loaded_skills = [
            str(e.get("name"))
            for e in skill_tool_events
            if e.get("tool") == "load_skill" and e.get("status") == "loaded"
        ]
        loaded_skills = _unique_preserve_order(loaded_skills)

        return {
            "output": output,
            "usage": usage,
            "timed_out": timed_out,
            "failure_hint": failure_hint,
            "agent_error": agent_error,
            "loaded_skills": loaded_skills,
            "skills_loaded": loaded_skills,
            "references_loaded": [],
            "native_impl": "strands",
            "parser_warning": "",
            "parse_error": "",
            "prompt_template_source": None,
            "prompt_template_sha256": None,
            "skill_tool_events": skill_tool_events,
            "episode_trace": [],
            "episode_count": 0,
            "episode_trace_path": None,
        }

    def _run_terminus2_legacy_profile(
        self,
        *,
        task: Task,
        container: SkillBenchContainer,
        agent_timeout: int,
    ) -> dict[str, Any]:
        logger.info("Solving with native terminus2_legacy profile")

        if not self._terminus_legacy_template:
            return {
                "output": "",
                "usage": {},
                "timed_out": False,
                "failure_hint": "agent_runtime_error",
                "agent_error": "Legacy Terminus2 prompt template is missing.",
                "loaded_skills": [],
                "skills_loaded": [],
                "references_loaded": [],
                "native_impl": "terminus2_legacy",
                "parser_warning": "",
                "parse_error": "",
                "prompt_template_source": str(self._terminus_legacy_template_path),
                "prompt_template_sha256": None,
                "skill_tool_events": [],
            }

        provider = BedrockProvider(model_id=self.model_id, region=self.region)
        available_skills = _discover_available_skills(container)
        loaded_skills_map: dict[str, str] = {}
        skill_tool_events: list[dict[str, Any]] = [
            {
                "tool": "list_skills",
                "found": bool(available_skills),
                "skills": [item["name"] for item in available_skills],
            }
        ]

        messages: list[LLMMessage] = []
        if self.base_system_prompt.strip():
            messages.append(LLMMessage(role="system", content=self.base_system_prompt.strip()))

        def _skills_block() -> str:
            available_json = json.dumps(available_skills, indent=2, ensure_ascii=False)
            if loaded_skills_map:
                loaded_text = "\n\n".join(
                    f"Loaded skill: {name}\n---\n{content}"
                    for name, content in loaded_skills_map.items()
                )
            else:
                loaded_text = "No skills loaded."
            return f"available_skills:\n{available_json}\n\nLOADED SKILLS:\n{loaded_text}"

        terminal_state = "Current Terminal Screen:\n"
        first_prompt = self._terminus_legacy_template.format(
            instruction=task.input,
            terminal_state=terminal_state,
            skills_block=_skills_block(),
        )
        messages.append(LLMMessage(role="user", content=first_prompt))

        usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        llm_output = ""
        agent_error: str | None = None

        deadline = time.time() + max(1, int(agent_timeout))
        max_episodes = 64

        for _episode in range(max_episodes):
            if time.time() >= deadline:
                return {
                    "output": llm_output,
                    "usage": usage,
                    "timed_out": True,
                    "failure_hint": "agent_timeout",
                    "agent_error": "Terminus2 profile exceeded agent timeout.",
                    "loaded_skills": list(loaded_skills_map.keys()),
                    "skill_tool_events": skill_tool_events,
                }

            try:
                response = provider.complete(
                    messages,
                    max_tokens=self.max_tokens,
                    temperature=0.0,
                )
            except Exception as e:
                agent_error = str(e)
                return {
                    "output": llm_output,
                    "usage": usage,
                    "timed_out": False,
                    "failure_hint": _classify_failure_from_text(agent_error),
                    "agent_error": agent_error,
                    "loaded_skills": list(loaded_skills_map.keys()),
                    "skill_tool_events": skill_tool_events,
                }

            usage["input_tokens"] += int(response.usage.get("input_tokens", 0))
            usage["output_tokens"] += int(response.usage.get("output_tokens", 0))
            usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"]

            response_text = response.content or ""
            llm_output = response_text or llm_output
            messages.append(LLMMessage(role="assistant", content=response_text))

            payload = _extract_json_object(response_text)
            if payload is None:
                terminal_state = (
                    "ERROR: Response is not valid JSON. Return a JSON object containing "
                    "analysis/plan/commands/task_complete only."
                )
                messages.append(LLMMessage(role="user", content=terminal_state))
                continue

            load_skill_value = payload.get("load_skill")
            if isinstance(load_skill_value, str):
                skill_name = load_skill_value.strip()
                content, source_dir = _load_skill(container, skill_name)
                if content:
                    loaded_skills_map[skill_name] = content
                    skill_tool_events.append(
                        {
                            "tool": "load_skill",
                            "name": skill_name,
                            "status": "loaded",
                            "source_dir": source_dir,
                        }
                    )
                    terminal_state = f"Loaded skill: {skill_name}\n---\n{content}\n\nCurrent Terminal Screen:\n"
                else:
                    skill_tool_events.append(
                        {
                            "tool": "load_skill",
                            "name": skill_name,
                            "status": "not_found",
                        }
                    )
                    terminal_state = f"Skill not found: {skill_name}\n\nCurrent Terminal Screen:\n"
                messages.append(LLMMessage(role="user", content=_truncate(terminal_state, 24000)))
                continue

            raw_commands = payload.get("commands")
            if not isinstance(raw_commands, list):
                terminal_state = "ERROR: Missing required field 'commands' (array)."
                messages.append(LLMMessage(role="user", content=terminal_state))
                continue

            cmd_outputs: list[str] = []
            for cmd in raw_commands:
                if not isinstance(cmd, dict):
                    continue
                keystrokes = str(cmd.get("keystrokes", ""))
                duration = cmd.get("duration", 1.0)
                try:
                    duration_sec = float(duration)
                except (TypeError, ValueError):
                    duration_sec = 1.0

                if not keystrokes.strip():
                    wait_sec = max(0.0, min(60.0, duration_sec))
                    if wait_sec > 0:
                        remaining = max(0.0, deadline - time.time())
                        time.sleep(min(wait_sec, remaining))
                    continue

                remaining_sec = int(max(1, deadline - time.time()))
                if remaining_sec <= 1:
                    return {
                        "output": llm_output,
                        "usage": usage,
                        "timed_out": True,
                        "failure_hint": "agent_timeout",
                        "agent_error": "Terminus2 command loop exceeded agent timeout.",
                        "loaded_skills": list(loaded_skills_map.keys()),
                        "skill_tool_events": skill_tool_events,
                    }

                if duration_sec >= 60:
                    cmd_timeout = max(1, remaining_sec)
                else:
                    cmd_timeout = max(1, min(900, remaining_sec))

                stdout, stderr, rc = container.exec_command(keystrokes, timeout=cmd_timeout)
                combined = _sanitize_terminal_output((stdout or "") + (stderr or ""))
                snippet = f"$ {keystrokes.rstrip()}"
                if combined:
                    snippet = f"{snippet}\n{combined}"
                if rc != 0:
                    snippet = f"{snippet}\n[exit_code={rc}]"
                cmd_outputs.append(snippet)

            if cmd_outputs:
                terminal_state = "New Terminal Output:\n" + "\n\n".join(cmd_outputs)
            else:
                terminal_state = "Current Terminal Screen:\n"
            terminal_state = _truncate(terminal_state, 24000)

            if bool(payload.get("task_complete", False)):
                break
            messages.append(LLMMessage(role="user", content=terminal_state))

        return {
            "output": llm_output,
            "usage": usage,
            "timed_out": False,
            "failure_hint": None,
            "agent_error": agent_error,
            "loaded_skills": list(loaded_skills_map.keys()),
            "skills_loaded": list(loaded_skills_map.keys()),
            "references_loaded": [],
            "native_impl": "terminus2_legacy",
            "parser_warning": "",
            "parse_error": "",
            "prompt_template_source": str(self._terminus_legacy_template_path),
            "prompt_template_sha256": None,
            "skill_tool_events": skill_tool_events,
        }

    def _handle_official_skill_tool_calls_json(
        self,
        *,
        response: str,
        skill_loader: OfficialSkillDocLoader,
        skill_dirs: list[Path],
        loaded_skills: dict[str, str],
        loaded_references: dict[str, list[tuple[str, str]]],
        skill_tool_events: list[dict[str, Any]],
    ) -> str | None:
        payload = _extract_skill_tool_call_json(response)
        if payload is None:
            return None

        outputs: list[str] = []

        if "load_skill" in payload:
            name = str(payload["load_skill"]).strip()
            if name:
                skill_text = skill_loader.load_skill(name, skill_dirs)
                if skill_text:
                    loaded_skills[name] = skill_text
                    outputs.append(f"Loaded skill: {name}\n---\n{skill_text}")
                    skill_tool_events.append({
                        "tool": "load_skill",
                        "name": name,
                        "status": "loaded",
                    })
                else:
                    outputs.append(f"Skill not found: {name}")
                    skill_tool_events.append({
                        "tool": "load_skill",
                        "name": name,
                        "status": "not_found",
                    })

        if "load_skill_reference" in payload:
            name = str(payload["load_skill_reference"]).strip()
            if name:
                references = skill_loader.load_references(name, skill_dirs)
                if references:
                    loaded_references[name] = references
                    outputs.append(_format_loaded_references(name, references))
                    skill_tool_events.append({
                        "tool": "load_skill_reference",
                        "name": name,
                        "status": "loaded",
                        "count": len(references),
                    })
                else:
                    outputs.append(f"No references found for skill: {name}")
                    skill_tool_events.append({
                        "tool": "load_skill_reference",
                        "name": name,
                        "status": "not_found",
                    })

        return "\n\n".join(outputs).strip() if outputs else None

    def _execute_official_commands(
        self,
        *,
        container: SkillBenchContainer,
        commands: list[Any],
        task_agent_timeout_sec: int,
    ) -> str:
        outputs: list[str] = []
        for command in commands:
            keystrokes = str(getattr(command, "keystrokes", ""))
            duration_sec = float(getattr(command, "duration", 1.0))
            if not keystrokes.strip():
                continue

            if duration_sec >= 60:
                timeout_sec = int(task_agent_timeout_sec or 900)
            else:
                requested_duration = int(duration_sec) if duration_sec >= 1 else 1
                timeout_sec = max(900, requested_duration)

            stdout, stderr, _ = container.exec_command(keystrokes, timeout=timeout_sec)
            combined = "\n".join(
                part for part in (stdout, stderr) if part
            ).strip()
            if combined:
                filtered = _sanitize_terminal_output(combined)
                if filtered:
                    # Cap individual command output to prevent context overflow
                    outputs.append(_truncate(filtered, 12000))
        if not outputs:
            return "Current Terminal Screen:\n"
        return _truncate("New Terminal Output:\n" + "\n\n".join(outputs), 24000)

    def _run_terminus2_official_profile(
        self,
        *,
        task: Task,
        container: SkillBenchContainer,
        agent_timeout: int,
    ) -> dict[str, Any]:
        logger.info("Solving with native terminus2 (official parity) profile")

        if not self._terminus_official_template:
            return {
                "output": "",
                "usage": {},
                "timed_out": False,
                "failure_hint": "agent_runtime_error",
                "agent_error": "Official Terminus2 prompt template is missing.",
                "loaded_skills": [],
                "skills_loaded": [],
                "references_loaded": [],
                "native_impl": "terminus2_official",
                "parser_warning": "",
                "parse_error": "",
                "prompt_template_source": str(self._terminus_official_template_path),
                "prompt_template_sha256": self._terminus_official_template_sha256,
                "skill_tool_events": [],
                "episode_trace": [],
                "episode_count": 0,
                "episode_trace_path": None,
            }

        provider = BedrockProvider(model_id=self.model_id, region=self.region)
        parser = TerminusJSONPlainParser()
        skill_loader = OfficialSkillDocLoader(container=container)
        skill_dirs = list(OFFICIAL_SKILL_DIRS)
        _ = skill_loader.build_index(skill_dirs)
        skills_metadata = [
            {
                "name": skill.name,
                "description": skill.description or "No description provided.",
                "location": skill.location,
            }
            for skill in skill_loader.get_metadata()
        ]

        loaded_skills: dict[str, str] = {}
        loaded_references: dict[str, list[tuple[str, str]]] = {}
        skill_tool_events: list[dict[str, Any]] = [
            {
                "tool": "list_skills",
                "found": bool(skills_metadata),
                "skills": [item["name"] for item in skills_metadata],
            }
        ]
        parser_warnings: list[str] = []
        parser_errors: list[str] = []
        last_parse_error = ""
        episode_trace: list[dict[str, Any]] = []

        def _warning_text() -> str:
            return "\n".join(_unique_preserve_order([w for w in parser_warnings if w])).strip()

        def _parse_error_text() -> str:
            return "\n".join(_unique_preserve_order([e for e in parser_errors if e])).strip()

        initial_skill_block = _build_skill_prompt_prefix_json(
            skills_metadata,
            loaded_skills,
            loaded_references,
        )
        terminal_state = "Current Terminal Screen:\n"
        if "{skills_block}" in self._terminus_official_template:
            initial_prompt = self._terminus_official_template.format(
                instruction=task.input,
                terminal_state=terminal_state,
                skills_block=initial_skill_block,
            )
        else:
            initial_prompt = self._terminus_official_template.format(
                instruction=task.input,
                terminal_state=terminal_state,
            )
            initial_prompt = _inject_skill_prompt(initial_prompt, initial_skill_block)

        messages: list[LLMMessage] = []
        if self.base_system_prompt.strip():
            messages.append(LLMMessage(role="system", content=self.base_system_prompt.strip()))
        messages.append(LLMMessage(role="user", content=initial_prompt))
        prompt = initial_prompt
        episode = 0

        usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        llm_output = ""
        agent_error: str | None = None
        deadline = time.time() + max(1, int(agent_timeout))

        while True:
            if time.time() >= deadline:
                return {
                    "output": llm_output,
                    "usage": usage,
                    "timed_out": True,
                    "failure_hint": "agent_timeout",
                    "agent_error": "Terminus2 official profile exceeded agent timeout.",
                    "loaded_skills": list(loaded_skills.keys()),
                    "skills_loaded": sorted(loaded_skills.keys()),
                    "references_loaded": sorted(loaded_references.keys()),
                    "native_impl": "terminus2_official",
                    "parser_warning": _warning_text(),
                    "parse_error": _parse_error_text() or last_parse_error,
                    "prompt_template_source": str(self._terminus_official_template_path),
                    "prompt_template_sha256": self._terminus_official_template_sha256,
                    "skill_tool_events": skill_tool_events,
                    "episode_trace": episode_trace,
                    "episode_count": len(episode_trace),
                    "episode_trace_path": None,
                }

            try:
                messages = _manage_conversation(
                    messages,
                    provider=provider,
                    model_id=self.model_id,
                    region=self.region,
                    max_tokens=self.max_tokens,
                )
                response = _call_llm_with_retry(
                    provider, messages,
                    max_tokens=self.max_tokens, temperature=0.7,
                )
            except Exception as e:
                agent_error = str(e)
                return {
                    "output": llm_output,
                    "usage": usage,
                    "timed_out": False,
                    "failure_hint": _classify_failure_from_text(agent_error),
                    "agent_error": agent_error,
                    "loaded_skills": list(loaded_skills.keys()),
                    "skills_loaded": sorted(loaded_skills.keys()),
                    "references_loaded": sorted(loaded_references.keys()),
                    "native_impl": "terminus2_official",
                    "parser_warning": _warning_text(),
                    "parse_error": _parse_error_text() or last_parse_error,
                    "prompt_template_source": str(self._terminus_official_template_path),
                    "prompt_template_sha256": self._terminus_official_template_sha256,
                    "skill_tool_events": skill_tool_events,
                    "episode_trace": episode_trace,
                    "episode_count": len(episode_trace),
                    "episode_trace_path": None,
                }

            usage["input_tokens"] += int(response.usage.get("input_tokens", 0))
            usage["output_tokens"] += int(response.usage.get("output_tokens", 0))
            usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"]

            response_text = response.content or ""
            llm_output = response_text or llm_output
            messages.append(LLMMessage(role="assistant", content=response_text))
            episode_trace.append(
                {
                    "episode": episode,
                    "prompt": prompt,
                    "response": response_text,
                }
            )

            was_truncated = _is_response_truncated(response)

            skill_output = self._handle_official_skill_tool_calls_json(
                response=response_text,
                skill_loader=skill_loader,
                skill_dirs=skill_dirs,
                loaded_skills=loaded_skills,
                loaded_references=loaded_references,
                skill_tool_events=skill_tool_events,
            )
            if skill_output:
                prompt = f"{skill_output}\n\nCurrent Terminal Screen:\n"
                messages.append(LLMMessage(role="user", content=prompt))
                continue

            parsed = parser.parse_response(response_text)
            if parsed.warning:
                parser_warnings.append(parsed.warning)
            if parsed.error:
                last_parse_error = parsed.error
                parser_errors.append(parsed.error)
                if was_truncated:
                    prompt = (
                        "ERROR: Your response was truncated because it exceeded the maximum output length. "
                        "None of your requested actions were performed.\n\n"
                        f"Parse error: {parsed.error}\n\n"
                        "Please re-issue your response with fewer commands or shorter output. "
                        "Break large tasks into smaller chunks."
                    )
                else:
                    prompt = (
                        "Previous response had parsing errors:\n"
                        f"{parsed.error}\n\n"
                        "Please fix these issues and provide a proper response."
                    )
                messages.append(LLMMessage(role="user", content=prompt))
                continue

            prompt = self._execute_official_commands(
                container=container,
                commands=parsed.commands,
                task_agent_timeout_sec=int(task.metadata.get("agent_timeout_sec", 900)),
            )
            messages.append(LLMMessage(role="user", content=prompt))

            episode += 1
            if parsed.is_task_complete:
                break
            if episode >= 512:
                agent_error = "Terminus2 official profile reached max episodes (512)."
                break

        return {
            "output": llm_output,
            "usage": usage,
            "timed_out": False,
            "failure_hint": None if not agent_error else _classify_failure_from_text(agent_error),
            "agent_error": agent_error,
            "loaded_skills": list(loaded_skills.keys()),
            "skills_loaded": sorted(loaded_skills.keys()),
            "references_loaded": sorted(loaded_references.keys()),
            "native_impl": "terminus2_official",
            "parser_warning": _warning_text(),
            "parse_error": _parse_error_text() or last_parse_error,
            "prompt_template_source": str(self._terminus_official_template_path),
            "prompt_template_sha256": self._terminus_official_template_sha256,
            "skill_tool_events": skill_tool_events,
            "episode_trace": episode_trace,
            "episode_count": len(episode_trace),
            "episode_trace_path": None,
        }

    def _is_retryable_failure(self, failure_class: str) -> bool:
        if failure_class in _NON_RETRYABLE_FAILURE_CLASSES:
            return False
        return failure_class in _RETRYABLE_FAILURE_CLASSES

    def _compute_backoff(self, attempt: int) -> float:
        base = self.retry_min_wait_sec * (2 ** attempt)
        clamped = min(base, self.retry_max_wait_sec)
        jitter = random.uniform(0.85, 1.15)
        return max(self.retry_min_wait_sec, min(self.retry_max_wait_sec, clamped * jitter))


class HarborSkillBenchBackend:
    """Harbor-compatible execution backend.

    Runs `harbor run` in a SkillsBench/Harbor repo and maps trial output
    files into a normalized Trajectory.
    """

    backend_name = "harbor"

    def __init__(
        self,
        *,
        harbor_repo: str,
        harbor_config_template: str | None = None,
        harbor_agent_import_path: str,
        harbor_model_name: str,
        harbor_jobs_dir: str,
        harbor_timeout_sec: int = 1800,
        harbor_uv_cmd: str = "uv run harbor run",
        region: str = "us-west-2",
    ):
        self.harbor_repo = Path(harbor_repo).resolve()
        self.harbor_config_template = (
            Path(harbor_config_template).resolve()
            if harbor_config_template
            else None
        )
        self.harbor_agent_import_path = harbor_agent_import_path
        self.harbor_model_name = harbor_model_name
        self.harbor_jobs_dir = Path(harbor_jobs_dir).resolve()
        self.harbor_timeout_sec = harbor_timeout_sec
        self.harbor_uv_cmd = harbor_uv_cmd
        self.region = region

    def solve(self, task: Task) -> Trajectory:
        comparison_key = task.metadata.get("comparison_key", task.id)
        task_dir = Path(task.metadata.get("task_dir", "")).resolve()
        if not task_dir.exists():
            step = _build_step(
                llm_output="",
                usage={},
                passed=False,
                reward_float=0.0,
                pass_binary=False,
                eval_output="Missing task_dir in metadata",
                verifier_tail="Missing task_dir in metadata",
                failure_class="harbor_config_error",
                backend=self.backend_name,
                raw_job_path=None,
                comparison_key=comparison_key,
                timed_out=False,
                attempt=0,
            )
            step["error"] = f"Task directory not found for Harbor run: {task_dir}"
            return Trajectory(task_id=task.id, output="", steps=[step])

        if not self.harbor_repo.exists():
            step = _build_step(
                llm_output="",
                usage={},
                passed=False,
                reward_float=0.0,
                pass_binary=False,
                eval_output="Harbor repo path missing",
                verifier_tail="Harbor repo path missing",
                failure_class="harbor_config_error",
                backend=self.backend_name,
                raw_job_path=None,
                comparison_key=comparison_key,
                timed_out=False,
                attempt=0,
            )
            step["error"] = f"Harbor repo not found: {self.harbor_repo}"
            return Trajectory(task_id=task.id, output="", steps=[step])

        task_name = task_dir.name
        dataset_root = str(task_dir.parent)
        timestamp = int(time.time())
        job_name = f"aev2-harbor-{task_name}-{timestamp}"
        self.harbor_jobs_dir.mkdir(parents=True, exist_ok=True)

        config_data = self._build_config(job_name, dataset_root, task_name)
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".yaml",
            prefix="aev2-skillbench-harbor-",
            delete=False,
        ) as tmp:
            yaml.safe_dump(config_data, tmp, sort_keys=False)
            config_path = Path(tmp.name)

        cmd = [*shlex.split(self.harbor_uv_cmd), "-c", str(config_path)]
        try:
            result = subprocess.run(
                cmd,
                cwd=str(self.harbor_repo),
                capture_output=True,
                text=True,
                timeout=int(self.harbor_timeout_sec),
            )
        except subprocess.TimeoutExpired as e:
            step = _build_step(
                llm_output="",
                usage={},
                passed=False,
                reward_float=0.0,
                pass_binary=False,
                eval_output=str(e),
                verifier_tail=_tail_text(str(e)),
                failure_class="harbor_timeout",
                backend=self.backend_name,
                raw_job_path=None,
                comparison_key=comparison_key,
                timed_out=True,
                attempt=0,
            )
            step["error"] = f"Harbor run timed out after {self.harbor_timeout_sec}s"
            return Trajectory(task_id=task.id, output="", steps=[step])
        finally:
            try:
                config_path.unlink(missing_ok=True)
            except Exception:
                pass

        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            stdout = (result.stdout or "").strip()
            eval_output = "\n".join([stdout[-1500:], stderr[-1500:]]).strip()
            step = _build_step(
                llm_output="",
                usage={},
                passed=False,
                reward_float=0.0,
                pass_binary=False,
                eval_output=eval_output,
                verifier_tail=_tail_text(eval_output),
                failure_class="harbor_runtime_error",
                backend=self.backend_name,
                raw_job_path=None,
                comparison_key=comparison_key,
                timed_out=False,
                attempt=0,
            )
            step["error"] = f"Harbor run failed (rc={result.returncode})"
            return Trajectory(task_id=task.id, output="", steps=[step])

        job_dir = self._resolve_job_dir(job_name)
        return self.parse_harbor_job(
            job_dir=job_dir,
            task_id=task.id,
            task_name=task_name,
            comparison_key=comparison_key,
        )

    def _build_config(
        self,
        job_name: str,
        dataset_root: str,
        task_name: str,
    ) -> dict[str, Any]:
        if self.harbor_config_template and self.harbor_config_template.exists():
            with open(self.harbor_config_template) as f:
                base = yaml.safe_load(f) or {}
        else:
            base = {}

        base["job_name"] = job_name
        base["jobs_dir"] = str(self.harbor_jobs_dir)
        base["n_attempts"] = 1
        base.setdefault("timeout_multiplier", 1.0)
        base.setdefault("debug", False)
        base.setdefault(
            "orchestrator",
            {
                "type": "local",
                "n_concurrent_trials": 1,
                "quiet": True,
                "retry": {
                    "max_retries": 6,
                    "include_exceptions": None,
                    "exclude_exceptions": [],
                    "wait_multiplier": 1.0,
                    "min_wait_sec": 1.0,
                    "max_wait_sec": 120.0,
                },
                "kwargs": {},
            },
        )
        base.setdefault(
            "environment",
            {
                "type": "docker",
                "import_path": None,
                "force_build": False,
                "delete": True,
                "override_cpus": None,
                "override_memory_mb": None,
                "override_storage_mb": None,
                "override_gpus": None,
                "kwargs": {},
            },
        )
        base.setdefault(
            "verifier",
            {"override_timeout_sec": None, "max_timeout_sec": None, "disable": False},
        )
        base.setdefault("metrics", [])
        base["agents"] = [{
            "name": "terminus-2-skills",
            "import_path": self.harbor_agent_import_path,
            "model_name": self.harbor_model_name,
            "override_timeout_sec": None,
            "override_setup_timeout_sec": None,
            "max_timeout_sec": None,
            "kwargs": {"aws_region_name": self.region},
        }]
        base["datasets"] = [{
            "task_names": [task_name],
            "exclude_task_names": [],
            "path": dataset_root,
        }]
        return base

    def _resolve_job_dir(self, job_name: str) -> Path:
        direct = self.harbor_jobs_dir / job_name
        if direct.exists():
            return direct
        candidates = sorted(
            self.harbor_jobs_dir.glob(f"{job_name}*"),
            key=lambda p: p.stat().st_mtime if p.exists() else 0,
            reverse=True,
        )
        if candidates:
            return candidates[0]
        return direct

    @staticmethod
    def parse_harbor_job(
        *,
        job_dir: Path,
        task_id: str,
        task_name: str | None = None,
        comparison_key: str | None = None,
    ) -> Trajectory:
        """Parse a Harbor job directory into a normalized trajectory."""
        comparison_key = comparison_key or task_id
        if not job_dir.exists():
            step = _build_step(
                llm_output="",
                usage={},
                passed=False,
                reward_float=0.0,
                pass_binary=False,
                eval_output="Missing Harbor output directory",
                verifier_tail="Missing Harbor output directory",
                failure_class="harbor_runtime_error",
                backend="harbor",
                raw_job_path=str(job_dir),
                comparison_key=comparison_key,
                timed_out=False,
                attempt=0,
            )
            step["error"] = f"Harbor job directory not found: {job_dir}"
            return Trajectory(task_id=task_id, output="", steps=[step])

        result_paths = sorted(job_dir.rglob("result.json"))
        if not result_paths:
            step = _build_step(
                llm_output="",
                usage={},
                passed=False,
                reward_float=0.0,
                pass_binary=False,
                eval_output="result.json missing",
                verifier_tail="result.json missing",
                failure_class="harbor_runtime_error",
                backend="harbor",
                raw_job_path=str(job_dir),
                comparison_key=comparison_key,
                timed_out=False,
                attempt=0,
            )
            step["error"] = f"No result.json found under Harbor job dir: {job_dir}"
            return Trajectory(task_id=task_id, output="", steps=[step])

        trial_dir = HarborSkillBenchBackend._select_trial_dir(
            result_paths=result_paths,
            task_name=task_name or task_id,
        )
        result_json_path = trial_dir / "result.json"
        try:
            result_data = json.loads(result_json_path.read_text())
        except Exception as e:
            result_data = {"parse_error": str(e)}

        score = HarborSkillBenchBackend._extract_score(result_data)
        if score is None:
            score = HarborSkillBenchBackend._extract_reward_txt(trial_dir)
        reward_float = _clamp_reward(score)

        passed = HarborSkillBenchBackend._extract_passed(result_data, reward_float)
        pass_binary = bool(passed or reward_float >= 1.0)

        eval_output = HarborSkillBenchBackend._extract_eval_output(result_data, trial_dir)
        llm_output = HarborSkillBenchBackend._extract_agent_output(trial_dir)

        failure_class = "none" if pass_binary else HarborSkillBenchBackend._classify_harbor_failure(eval_output)

        step = _build_step(
            llm_output=llm_output,
            usage={},
            passed=pass_binary,
            reward_float=reward_float,
            pass_binary=pass_binary,
            eval_output=eval_output,
            verifier_tail=_tail_text(eval_output),
            failure_class=failure_class,
            backend="harbor",
            raw_job_path=str(trial_dir),
            comparison_key=comparison_key,
            timed_out=False,
            attempt=0,
        )
        return Trajectory(task_id=task_id, output=llm_output, steps=[step])

    @staticmethod
    def _select_trial_dir(result_paths: list[Path], task_name: str) -> Path:
        task_name_l = task_name.lower()
        # Prefer exact trial directory match (task-name__hash) over job dir
        for rp in result_paths:
            if rp.parent.name.lower().startswith(task_name_l + "__"):
                return rp.parent
        for rp in result_paths:
            if task_name_l in rp.parent.name.lower():
                return rp.parent
        return result_paths[0].parent

    @staticmethod
    def _extract_score(result_data: Any) -> float | None:
        if not isinstance(result_data, dict):
            return None
        for key in ("reward", "score", "final_score"):
            value = result_data.get(key)
            if isinstance(value, (int, float)):
                return float(value)

        for key in ("agent_result", "verifier_result", "result", "metadata", "rewards"):
            nested = result_data.get(key)
            if isinstance(nested, dict):
                nested_score = HarborSkillBenchBackend._extract_score(nested)
                if nested_score is not None:
                    return nested_score

        return None

    @staticmethod
    def _extract_passed(result_data: Any, score: float | None) -> bool:
        if isinstance(result_data, dict):
            for key in ("passed", "success"):
                value = result_data.get(key)
                if isinstance(value, bool):
                    return value
            for key in ("agent_result", "verifier_result", "result"):
                nested = result_data.get(key)
                if isinstance(nested, dict):
                    nested_passed = HarborSkillBenchBackend._extract_passed(nested, score)
                    if nested_passed:
                        return True

        if score is None:
            return False
        return score >= 1.0

    @staticmethod
    def _extract_reward_txt(trial_dir: Path) -> float | None:
        reward_path = trial_dir / "verifier" / "reward.txt"
        if not reward_path.exists():
            return None
        try:
            raw = reward_path.read_text().strip()
            return float(raw)
        except Exception:
            return None

    @staticmethod
    def _extract_eval_output(result_data: Any, trial_dir: Path) -> str:
        if isinstance(result_data, dict):
            for key in ("detail", "message", "stderr", "stdout"):
                value = result_data.get(key)
                if isinstance(value, str) and value.strip():
                    return value
            for key in ("agent_result", "verifier_result", "result"):
                nested = result_data.get(key)
                if isinstance(nested, dict):
                    nested_text = HarborSkillBenchBackend._extract_eval_output(nested, trial_dir)
                    if nested_text:
                        return nested_text
        reward_path = trial_dir / "verifier" / "reward.txt"
        if reward_path.exists():
            return f"reward.txt={reward_path.read_text().strip()}"
        return json.dumps(result_data, default=str)[:2000]

    @staticmethod
    def _extract_agent_output(trial_dir: Path) -> str:
        trajectory_path = trial_dir / "agent" / "trajectory.json"
        if trajectory_path.exists():
            try:
                data = json.loads(trajectory_path.read_text())
                if isinstance(data, list) and data:
                    last = data[-1]
                    if isinstance(last, dict):
                        for key in ("response", "content", "output", "text"):
                            value = last.get(key)
                            if isinstance(value, str):
                                return value
                return json.dumps(data, default=str)[:4000]
            except Exception:
                pass
        return ""

    @staticmethod
    def _classify_harbor_failure(eval_output: str) -> str:
        lowered = (eval_output or "").lower()
        if "timeout" in lowered or "timed out" in lowered:
            return "verifier_timeout"
        if "assertionerror" in lowered or re.search(r"\bassert\b", lowered):
            return "assertion"
        if "modulenotfounderror" in lowered or "no module named" in lowered:
            return "module_missing"
        if "filenotfounderror" in lowered or "no such file or directory" in lowered:
            return "file_missing"
        if "failed" in lowered and "pytest" in lowered:
            return "test_fail"
        return "verifier_fail"
