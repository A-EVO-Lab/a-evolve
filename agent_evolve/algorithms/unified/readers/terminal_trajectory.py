"""TerminalTrajectoryReader -- TB-style trajectory evidence.

This reader preserves the Terminal-Bench adaptive-skill evidence shape inside
the unified atom model. It intentionally exposes only behavior-derived signals
and compressed trajectories; pass/fail and verifier feedback remain masked when
``trajectory_only`` is enabled.
"""

from __future__ import annotations

from typing import Any

from ..registry import register_reader


def _conversation_from_trajectory(trajectory: Any) -> list[dict[str, Any]]:
    conv = list(getattr(trajectory, "conversation", []) or [])
    if conv:
        return conv
    for step in list(getattr(trajectory, "steps", []) or []):
        step_conv = step.get("conversation") if isinstance(step, dict) else None
        if step_conv:
            return list(step_conv)
    return []


def _extract_trajectory_signals(conversation: list[dict[str, Any]]) -> dict[str, Any]:
    n_turns = 0
    n_tool_calls = 0
    n_errors = 0
    n_timeouts = 0
    tools_used: dict[str, int] = {}
    commands_run: list[str] = []
    submitted = False
    submit_value = ""
    error_messages: list[str] = []

    for msg in conversation:
        role = msg.get("role", "")
        if role == "assistant":
            n_turns += 1
            for tc in msg.get("tool_calls", []) or []:
                n_tool_calls += 1
                fn = tc.get("function", "")
                tools_used[fn] = tools_used.get(fn, 0) + 1
                args = tc.get("arguments", {}) or {}
                cmd = args.get("cmd", "") or args.get("command", "")
                if cmd:
                    commands_run.append(str(cmd)[:80])
                if fn in ("submit", "task_submit"):
                    submitted = True
                    submit_value = args.get("answer", "")
        elif role == "tool":
            content = msg.get("content") or ""
            low = content.lower()
            if "ERROR:" in content or "error:" in low[:50]:
                n_errors += 1
                error_messages.append(content[:100])
            if "timed out" in low or "timeout" in low:
                n_timeouts += 1

    cmd_counts: dict[str, int] = {}
    for cmd in commands_run:
        cmd_counts[cmd] = cmd_counts.get(cmd, 0) + 1
    repeated_commands = [cmd for cmd, count in cmd_counts.items() if count >= 3]

    return {
        "n_turns": n_turns,
        "n_tool_calls": n_tool_calls,
        "n_errors": n_errors,
        "n_timeouts": n_timeouts,
        "tools_used": tools_used,
        "submitted": submitted,
        "submit_value": submit_value,
        "repeated_commands": repeated_commands,
        "error_snippets": error_messages[:5],
    }


def _compress_terminal_trajectory(conversation: list[dict[str, Any]]) -> str:
    """Compress a trajectory using the legacy Terminal-Bench format."""
    events: list[dict[str, str]] = []
    prev_cmd = ""

    for msg in conversation:
        role = msg.get("role", "")
        if role == "assistant":
            for tc in msg.get("tool_calls", []):
                fn = tc.get("function", "")
                args = tc.get("arguments", {})
                cmd = args.get("cmd", "") or args.get("command", "") or args.get("code", "")
                answer = args.get("answer", "")
                if fn in ("submit", "task_submit"):
                    events.append({"type": "submit", "value": answer})
                elif cmd:
                    prev_cmd = cmd[:200]
                    events.append({"type": "cmd", "fn": fn, "cmd": prev_cmd})
        elif role == "tool":
            content = (msg.get("content") or "").strip()
            is_error = (
                "ERROR:" in content
                or "error:" in content[:80].lower()
                or "Traceback" in content[:200]
                or "TIMEOUT" in content.upper()[:50]
                or "timed out" in content.lower()[:80]
                or "No such file" in content[:100]
                or "command not found" in content[:100]
            )
            if is_error:
                events.append({
                    "type": "error",
                    "cmd": prev_cmd,
                    "output": content[:300],
                })

    parts: list[str] = []
    n_cmds = sum(1 for e in events if e["type"] == "cmd")
    n_errors = sum(1 for e in events if e["type"] == "error")
    submitted = any(e["type"] == "submit" for e in events)

    parts.append(f"Commands: {n_cmds}, Errors: {n_errors}, Submitted: {submitted}")

    cmds_seen = 0
    for e in events:
        if e["type"] == "cmd":
            cmds_seen += 1
            if cmds_seen <= 3:
                parts.append(f"[start] {e['fn']}({e['cmd']})")

    if n_errors > 0:
        parts.append(f"\n--- Errors ({n_errors}) ---")
        for e in events:
            if e["type"] == "error":
                parts.append(f"  cmd: {e.get('cmd', '?')}")
                parts.append(f"  err: {e['output'][:200]}")

    cmd_list = [e["cmd"] for e in events if e["type"] == "cmd"]
    cmd_counts: dict[str, int] = {}
    for c in cmd_list:
        cmd_counts[c] = cmd_counts.get(c, 0) + 1
    loops = {c: n for c, n in cmd_counts.items() if n >= 3}
    if loops:
        parts.append(f"\n--- Repeated commands ---")
        for c, n in loops.items():
            parts.append(f"  {c} (x{n})")

    last_cmds = [e for e in events if e["type"] == "cmd"][-3:]
    if last_cmds:
        parts.append(f"\n--- Final commands ---")
        for e in last_cmds:
            parts.append(f"  {e['fn']}({e['cmd']})")

    if submitted:
        submit_events = [e for e in events if e["type"] == "submit"]
        if submit_events:
            parts.append(f"\n[submitted] {submit_events[-1].get('value', '')}")

    return "\n".join(parts)


@register_reader("TerminalTrajectoryReader")
class TerminalTrajectoryReader:
    """Emit Terminal-Bench trajectory summaries in observation order."""

    def read(
        self,
        observations: list,
        workspace: Any,
        history: Any,
        config: Any,
        context: Any,
        state: dict[str, Any],
    ) -> dict[str, Any]:
        per_task: list[dict[str, Any]] = []
        for obs in observations:
            trajectory = getattr(obs, "trajectory", None)
            conversation = _conversation_from_trajectory(trajectory)
            per_task.append(
                {
                    "task_id": getattr(getattr(obs, "task", None), "id", ""),
                    "signals": _extract_trajectory_signals(conversation),
                    "compressed_trajectory": _compress_terminal_trajectory(conversation),
                }
            )
        return {"per_task": per_task, "n_tasks": len(per_task)}
