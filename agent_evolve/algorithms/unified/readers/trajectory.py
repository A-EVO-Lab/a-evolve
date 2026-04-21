"""TrajectoryCompressor — failure-focused summary of each conversation trace.

Reference: ``agent_evolve/algorithms/adaptive_skill/prompts.py`` lines 129-223
(``_compress_trajectory``). Output is an *independent reimplementation* so the
unified package does not import from legacy engine modules (see DEC-2
resolution in the plan). Output format is canonicalized for byte-stable
LLM prompts:

    * first 3 commands (approach)
    * each error with the command that triggered it (up to all)
    * repeated commands (>=3 occurrences) sorted by command text
    * final 3 commands
    * submit value if present

Emitted as an already-rendered multi-line string per observation to keep
prompt serialization deterministic.
"""

from __future__ import annotations

from typing import Any

from ..registry import register_reader


_ERROR_MARKERS_LOWER = ("error:", "traceback", "no such file", "command not found")
_ERROR_MARKERS_UPPER = ("ERROR:", "TIMEOUT")


def _compress_one(conversation: list[dict[str, Any]]) -> str:
    events: list[dict[str, str]] = []
    prev_cmd = ""
    for msg in conversation:
        role = msg.get("role", "")
        if role == "assistant":
            for tc in msg.get("tool_calls", []):
                fn = tc.get("function", "") or ""
                args = tc.get("arguments", {}) or {}
                cmd = (
                    args.get("cmd", "")
                    or args.get("command", "")
                    or args.get("code", "")
                )
                answer = args.get("answer", "")
                if fn in ("submit", "task_submit"):
                    events.append({"type": "submit", "value": str(answer)})
                elif cmd:
                    prev_cmd = str(cmd)[:200]
                    events.append({"type": "cmd", "fn": fn, "cmd": prev_cmd})
        elif role == "tool":
            content = (msg.get("content") or "").strip()
            low = content.lower()
            is_error = (
                any(m in content for m in _ERROR_MARKERS_UPPER)
                or any(m in low[:120] for m in _ERROR_MARKERS_LOWER)
                or "timed out" in low[:80]
            )
            if is_error:
                events.append(
                    {
                        "type": "error",
                        "cmd": prev_cmd,
                        "output": content[:300],
                    }
                )

    parts: list[str] = []
    n_cmds = sum(1 for e in events if e["type"] == "cmd")
    n_errors = sum(1 for e in events if e["type"] == "error")
    submitted = any(e["type"] == "submit" for e in events)
    parts.append(
        f"Commands: {n_cmds}, Errors: {n_errors}, Submitted: {submitted}"
    )

    # First 3 commands (approach).
    cmds_seen = 0
    for e in events:
        if e["type"] == "cmd":
            cmds_seen += 1
            if cmds_seen <= 3:
                parts.append(f"[start] {e.get('fn', '')}({e['cmd']})")

    # Errors.
    if n_errors > 0:
        parts.append(f"\n--- Errors ({n_errors}) ---")
        for e in events:
            if e["type"] == "error":
                parts.append(f"  cmd: {e.get('cmd', '?')}")
                parts.append(f"  err: {e['output'][:200]}")

    # Repeated commands (sorted for deterministic output).
    cmd_list = [e["cmd"] for e in events if e["type"] == "cmd"]
    cmd_counts: dict[str, int] = {}
    for c in cmd_list:
        cmd_counts[c] = cmd_counts.get(c, 0) + 1
    loops = sorted((c, n) for c, n in cmd_counts.items() if n >= 3)
    if loops:
        parts.append("\n--- Repeated commands ---")
        for c, n in loops:
            parts.append(f"  {c} (x{n})")

    # Last 3 commands.
    last_cmds = [e for e in events if e["type"] == "cmd"][-3:]
    if last_cmds:
        parts.append("\n--- Final commands ---")
        for e in last_cmds:
            parts.append(f"  {e.get('fn', '')}({e['cmd']})")

    if submitted:
        submit_events = [e for e in events if e["type"] == "submit"]
        if submit_events:
            parts.append(f"\n[submitted] {submit_events[-1].get('value', '')}")

    return "\n".join(parts)


@register_reader("TrajectoryCompressor")
class TrajectoryCompressor:
    """Output keys:

        "per_task": list of {"task_id": str, "compressed": str}, sorted by task_id
    """

    def read(
        self,
        observations: list,
        workspace: Any,
        history: Any,
        config: Any,
        context: Any,
        state: dict[str, Any],
    ) -> dict[str, Any]:
        out: list[dict[str, Any]] = []
        for obs in observations:
            conv = list(getattr(obs.trajectory, "conversation", []) or [])
            out.append(
                {
                    "task_id": getattr(obs.task, "id", ""),
                    "compressed": _compress_one(conv),
                }
            )
        out.sort(key=lambda d: d["task_id"])
        return {"per_task": out}
