"""GEPA evaluator bridge — connects GEPA's optimization loop to A-Evolve's TrialRunner.

Provides:
- make_evaluator(): serial evaluator for single-threaded GEPA
- make_parallel_evaluator(): thread-safe worker pool for parallel GEPA
- build_side_info(): structured ASI for GEPA's reflection LLM
- compress_trajectory(): bounded trajectory compression (head+tail heuristic, no LLM call)
"""

from __future__ import annotations

import json
import shutil
import threading
from collections import Counter
from typing import TYPE_CHECKING, Any

from .serialization import restore_candidate

if TYPE_CHECKING:
    from ...config import EvolveConfig
    from ...contract.workspace import AgentWorkspace
    from ...engine.trial import TrialRunner
    from ...types import Observation, Task, Trajectory


def make_evaluator(
    trial: TrialRunner, config: EvolveConfig
) -> Any:
    """Create a serial evaluator for GEPA's optimize_anything."""
    def evaluator(candidate: dict[str, str], task: Task) -> tuple[float, dict]:
        restore_candidate(trial.agent.workspace, candidate, config)
        trial.agent.reload_from_fs()
        obs = trial.run_single(task)
        return obs.feedback.score, build_side_info(obs)
    return evaluator


def make_parallel_evaluator(
    trial: TrialRunner,
    workspace: AgentWorkspace,
    n_workers: int,
    config: EvolveConfig,
) -> tuple[Any, Any]:
    """Create a thread-safe parallel evaluator with a worker pool."""
    from ...contract.workspace import AgentWorkspace as _AgentWorkspace
    from ...engine.trial import TrialRunner as _TrialRunner

    agent = trial.agent
    benchmark = trial.benchmark

    worker_pool: list[tuple[_AgentWorkspace, Any, _TrialRunner]] = []
    worker_available = [True] * n_workers
    worker_lock = threading.Lock()

    for i in range(n_workers):
        worker_dir = workspace.root.parent / f"gepa_worker_{i}"
        shutil.copytree(workspace.root, worker_dir, dirs_exist_ok=True)
        worker_agent = type(agent)(worker_dir)
        worker_trial = _TrialRunner(worker_agent, benchmark)
        worker_pool.append((_AgentWorkspace(worker_dir), worker_agent, worker_trial))

    def get_worker() -> tuple[int, tuple[_AgentWorkspace, Any, _TrialRunner]]:
        with worker_lock:
            for i, available in enumerate(worker_available):
                if available:
                    worker_available[i] = False
                    return i, worker_pool[i]
        raise RuntimeError("No GEPA worker available")

    def release_worker(idx: int) -> None:
        with worker_lock:
            worker_available[idx] = True

    def evaluator(candidate: dict[str, str], task: Task) -> tuple[float, dict]:
        idx, (ws, ag, tr) = get_worker()
        try:
            restore_candidate(ws, candidate, config)
            ag.reload_from_fs()
            obs = tr.run_single(task)
            return obs.feedback.score, build_side_info(obs)
        finally:
            release_worker(idx)

    def cleanup() -> None:
        for ws, _, _ in worker_pool:
            shutil.rmtree(ws.root, ignore_errors=True)

    return evaluator, cleanup


def build_side_info(obs: Observation) -> dict:
    """Build structured ASI (Actionable Side Information) for GEPA's reflection LLM."""
    return {
        "Input": {
            "Task ID": obs.task.id,
            "Task": _truncate(obs.task.input, 500),
        },
        "Generated Outputs": {
            "Output": _truncate(obs.trajectory.output, 1000),
            "Agent Trace": compress_trajectory(obs.trajectory),
        },
        "Feedback": {
            "Status": "PASS" if obs.feedback.success else "FAIL",
            "Score": obs.feedback.score,
            "Detail": _truncate(obs.feedback.detail, 1000),
            "Raw": _truncate_dict(obs.feedback.raw, 500),
        },
        "scores": {
            "correctness": obs.feedback.score,
        },
    }


def compress_trajectory(trajectory: Trajectory) -> str:
    """Compress trajectory into a bounded string for GEPA's reflection LLM."""
    parts: list[str] = []
    if trajectory.conversation:
        parts.append(_compress_conversation(trajectory.conversation))
    if trajectory.steps:
        parts.append(_compress_steps(trajectory.steps))
    combined = "\n\n".join(parts)
    return _truncate(combined, 3000)


def _compress_conversation(conversation: list[dict]) -> str:
    """Extract key events from the conversation message history."""
    events: list[str] = []
    prev_cmd = ""
    for msg in conversation:
        role = msg.get("role", "")
        if role == "assistant":
            for tc in msg.get("tool_calls", []):
                fn = tc.get("function", "")
                args = tc.get("arguments", {})
                cmd = (
                    args.get("cmd", "")
                    or args.get("command", "")
                    or args.get("code", "")
                )
                answer = args.get("answer", "")
                if fn in ("submit", "task_submit"):
                    events.append(f"[submit] {answer}")
                elif cmd:
                    prev_cmd = cmd[:200]
                    events.append(f"[call] {fn}({prev_cmd})")
        elif role == "tool":
            content = (msg.get("content") or "").strip()
            is_error = any(
                s in content[:200].lower()
                for s in ["error:", "traceback", "timeout", "no such file", "command not found"]
            )
            if is_error:
                events.append(f"[error] cmd={prev_cmd[:80]} → {content[:300]}")
    if not events:
        return "No conversation data."
    n = len(events)
    head = events[:5]
    tail = events[-5:] if n > 10 else []
    errors = [e for e in events[5:-5] if e.startswith("[error]")] if n > 10 else []
    parts = [f"Conversation trace ({n} events):"]
    parts.append("--- Approach ---")
    parts.extend(head)
    if errors:
        parts.append(f"--- Errors ({len(errors)}) ---")
        parts.extend(errors[:10])
    if tail:
        parts.append("--- Final actions ---")
        parts.extend(tail)
    return "\n".join(parts)


def _compress_steps(steps: list[dict]) -> str:
    """Summarize structured step list into a compact string."""
    if not steps:
        return ""
    tool_steps = [s for s in steps if isinstance(s, dict) and "tool" in s]
    n = len(tool_steps)
    if n == 0:
        return f"Steps: {len(steps)} entries, no tool calls."
    actions = Counter(s.get("action", s.get("tool", "unknown")) for s in tool_steps)
    return f"Steps: {n} tool calls — {dict(actions)}"


def _truncate(text: str | None, limit: int) -> str:
    """Truncation: head + '...' + tail for long strings."""
    if not text:
        return ""
    if len(text) <= limit:
        return text
    half = limit // 2
    return text[:half] + "\n...\n" + text[-half:]


def _truncate_dict(d: dict, char_limit: int) -> str:
    """Truncate a dict to a readable string within char_limit."""
    if not d:
        return ""
    text = json.dumps(d, indent=1, default=str)
    return _truncate(text, char_limit)
