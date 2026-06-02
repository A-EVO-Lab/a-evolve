"""Shared helper for writing a per-task partial trajectory snapshot.

Each solver's Strands agent registers a post-tool hook that persists a
minimal JSON blob after every tool call.  If the solver's Future is
cancelled (batch deadline fired or per-task timeout), the harness reads
this partial snapshot instead of fabricating a ``turns: 0`` record.

Writes are atomic (temp file + rename) and overwrite the previous
snapshot — the latest one always reflects the most recent tool call.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Callable


def _conversation_extractor_default(agent: Any) -> list[dict[str, Any]]:
    """Default Strands message → JSON-serialisable conversation extractor.

    Solvers may pass a benchmark-specific ``_extract_conv`` instead.
    """
    msgs = getattr(agent, "messages", None) or []
    out: list[dict[str, Any]] = []
    for m in msgs:
        role = m.get("role") if isinstance(m, dict) else None
        content = m.get("content") if isinstance(m, dict) else None
        out.append({"role": role, "content": content})
    return out


def install_partial_writer(
    agent: Any,
    *,
    task_id: str,
    out_dir: Path,
    turn_counter: list[int],
    start_time: float,
    extract_conversation: Callable[[Any], list[dict[str, Any]]] | None = None,
) -> Path:
    """Register hooks that write a partial trajectory + per-tool timings.

    Two callbacks:
      * ``BeforeToolCallEvent`` stashes the start time on a closure var.
      * ``AfterToolCallEvent`` records ``{tool, duration}`` for this call
        and rewrites the snapshot.

    Args:
        agent: The Strands ``Agent`` instance.
        task_id: The task's instance id (used in the filename).
        out_dir: Directory where the final ``trajectory_*.json`` is written.
        turn_counter: The solver's ``[int]`` box tracking completed tool
            calls (same list the ``BeforeToolCallEvent`` turn limiter
            mutates).  Read, not mutated, here.
        start_time: The solver's ``time.time()`` at agent-call start;
            used to compute elapsed seconds.
        extract_conversation: Optional serializer; falls back to a
            generic message-dict extractor.

    Returns:
        The Path the snapshot will be written to.  The caller can delete
        it on happy-path completion.
    """
    from strands.hooks.events import AfterToolCallEvent, BeforeToolCallEvent

    sid = task_id.replace("/", "_")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"partial_{sid}.json"
    extractor = extract_conversation or _conversation_extractor_default

    tool_timings: list[dict[str, Any]] = []
    call_start: dict[str, float] = {}  # tool_use_id -> start_time

    def _on_tool_start(event: BeforeToolCallEvent) -> None:
        try:
            tu = getattr(event, "tool_use", None) or {}
            tid = tu.get("toolUseId") or tu.get("tool_use_id")
            if tid:
                call_start[tid] = time.time()
        except Exception:
            pass

    def _on_tool_call(event: AfterToolCallEvent) -> None:
        # Record this tool call's duration.
        try:
            tu = getattr(event, "tool_use", None) or {}
            tid = tu.get("toolUseId") or tu.get("tool_use_id")
            started = call_start.pop(tid, None) if tid else None
            if started is not None:
                tool_timings.append({
                    "tool": tu.get("name", "?"),
                    "duration_s": round(time.time() - started, 3),
                })
        except Exception:
            pass
        # Rewrite snapshot.
        try:
            snap = {
                "instance_id": task_id,
                "turns": int(turn_counter[0]) if turn_counter else 0,
                "elapsed": time.time() - start_time,
                "status": "in_progress",
                "tool_timings": list(tool_timings),
                "conversation": extractor(event.agent),
            }
        except Exception:
            # Never let snapshot logic break the solver.
            return
        try:
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(json.dumps(snap, ensure_ascii=False, default=str))
            os.replace(tmp, path)
        except Exception:
            pass

    agent.hooks.add_callback(BeforeToolCallEvent, _on_tool_start)
    agent.hooks.add_callback(AfterToolCallEvent, _on_tool_call)
    return path


def summarise_tool_latency(
    tool_timings: list[dict[str, Any]] | None,
    *,
    top_n: int = 3,
) -> str:
    """Compact per-tool latency summary for the evolution prompt.

    Input is the list produced by ``install_partial_writer`` (list of
    ``{"tool": name, "duration_s": float}``).  Output is a single-line
    string like ``"web_search: 22× mean 18.4s max 31s; bash: 5× mean 0.2s"``
    so the evolver can see which tools are the actual time sinks.

    Returns ``""`` when no timings are available.
    """
    if not tool_timings:
        return ""
    by_tool: dict[str, list[float]] = {}
    for t in tool_timings:
        name = t.get("tool") or "?"
        dur = t.get("duration_s")
        if isinstance(dur, (int, float)):
            by_tool.setdefault(name, []).append(float(dur))
    if not by_tool:
        return ""
    # Sort tools by total time spent (descending) — the most time-costly first.
    entries = []
    for name, durs in by_tool.items():
        if not durs:
            continue
        total = sum(durs)
        mean = total / len(durs)
        entries.append((total, name, len(durs), mean, max(durs)))
    entries.sort(reverse=True)
    top = entries[:top_n]
    parts = [
        f"{name}: {n}× mean {mean:.1f}s max {mx:.0f}s"
        for (_total, name, n, mean, mx) in top
    ]
    return "; ".join(parts)


def read_partial_trajectory(
    out_dir: Path,
    task_id: str,
) -> dict[str, Any] | None:
    """Read a per-task partial snapshot if it exists; return None otherwise.

    The harness calls this when a Future is cancelled to enrich the
    synthetic record before it reaches the observer.
    """
    sid = task_id.replace("/", "_")
    path = Path(out_dir) / f"partial_{sid}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def clear_partial_trajectory(
    out_dir: Path,
    task_id: str,
) -> dict[str, Any] | None:
    """Read + delete the partial snapshot.

    Returns the final snapshot dict (or ``None`` if absent/unreadable)
    so the solver can carry forward fields like ``tool_timings`` on the
    happy path without re-deriving them.
    """
    sid = task_id.replace("/", "_")
    path = Path(out_dir) / f"partial_{sid}.json"
    snap: dict[str, Any] | None = None
    try:
        snap = json.loads(path.read_text())
    except FileNotFoundError:
        return None
    except Exception:
        snap = None
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except Exception:
        pass
    return snap
