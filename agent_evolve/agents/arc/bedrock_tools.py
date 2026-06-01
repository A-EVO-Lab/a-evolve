"""
Tool definitions and handler builders for orchestrator and subagents.
Each builder returns (tool_specs, handler_dict) for use with BedrockAgent.
"""

import json
import logging
from typing import Any, Callable

from .frame import Frame
from .memories import Memories
from .wiki import GameWiki

logger = logging.getLogger()


def _tool(name: str, description: str, properties: dict, required: list[str] | None = None):
    """Helper to build a Bedrock toolSpec."""
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
    }
    if required:
        schema["required"] = required
    return {
        "toolSpec": {
            "name": name,
            "description": description,
            "inputSchema": {"json": schema},
        }
    }


def _format_frame(frame: Frame, prev_frame: Frame | None = None,
                   budget_used: int | None = None, budget_limit: int | None = None) -> str:
    """Serialize a Frame to a human-readable text string for tool responses."""
    lc = frame.metadata.get("levels_completed", 0)
    wl = frame.metadata.get("win_levels", 0)
    state = frame.metadata.get("state_name", "UNKNOWN")
    avail = frame.metadata.get("available_actions_names", [])

    lines = [f"Frame({frame.width}x{frame.height}, level={lc}/{wl}, state={state}, actions=[{', '.join(avail)}])"]

    prev_lc = prev_frame.metadata.get("levels_completed", 0) if prev_frame else lc
    if lc > prev_lc:
        lines.append(
            f"*** LEVEL COMPLETE! Now on level {lc}/{wl}. ***"
        )

    if prev_frame is not None and prev_lc == lc:
        summary = frame.change_summary(prev_frame)
        lines.append(f"Changes: {summary}")
    elif prev_frame is not None and prev_lc != lc:
        lines.append(
            f"Level changed ({prev_lc} -> {lc}). "
            "New grid loaded — render it to see the new layout."
        )

    if budget_used is not None and budget_limit is not None:
        remaining = budget_limit - budget_used
        lines.append(f"Budget: {budget_used}/{budget_limit} actions used, {remaining} remaining")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Subagent tools
# ---------------------------------------------------------------------------

def build_subagent_tools(
    submit_action_fn: Callable | None,
    action_budget: int | None,
    history_fn: Callable,
    wiki: GameWiki,
    frame_ref: list,  # [current_frame, prev_frame] — mutable shared state
) -> tuple[list[dict], dict[str, Callable]]:
    """
    Build tools for a subagent (explorer, tester, solver).

    frame_ref is a mutable list [current_frame, prev_frame] that is updated
    by the submit_action handler when actions are taken.
    """
    specs: list[dict] = []
    handlers: dict[str, Callable] = {}

    # --- submit_action (if allowed) ---
    if submit_action_fn is not None:
        budget_used = [0]
        budget_limit = action_budget

        def handle_submit_action(action_name: str, x: int = 0, y: int = 0) -> str:
            upper = action_name.upper()
            if upper != "NOOP" and upper != "RESET":
                if budget_limit is not None and budget_used[0] >= budget_limit:
                    return (
                        f"ERROR: Action budget exhausted. All {budget_limit} actions "
                        f"have been used. Return your findings to the orchestrator."
                    )

            try:
                new_frame = submit_action_fn(action_name, x, y)
            except Exception as e:
                return f"ERROR: {e}"

            prev = frame_ref[0]
            frame_ref[1] = prev
            frame_ref[0] = new_frame

            if upper != "NOOP" and upper != "RESET":
                budget_used[0] += 1

            return _format_frame(
                new_frame, prev,
                budget_used=budget_used[0],
                budget_limit=budget_limit,
            )

        specs.append(_tool(
            "submit_action",
            "Submit a game action and receive the new frame state. "
            "Returns frame info, change summary, and budget status. "
            "NOOP returns current frame without taking an action.",
            {
                "action_name": {
                    "type": "string",
                    "description": "One of: RESET, ACTION1, ACTION2, ACTION3, ACTION4, ACTION5, ACTION6, NOOP",
                },
                "x": {
                    "type": "integer",
                    "description": "X coordinate (0-63), only for ACTION6. Default 0.",
                },
                "y": {
                    "type": "integer",
                    "description": "Y coordinate (0-63), only for ACTION6. Default 0.",
                },
            },
            required=["action_name"],
        ))
        handlers["submit_action"] = handle_submit_action

        # --- run_action_sequence (batch actions) ---
        def handle_run_action_sequence(actions: list[dict]) -> str:
            results = []
            for i, act in enumerate(actions):
                name = act.get("action_name", "NOOP")
                ax = act.get("x", 0)
                ay = act.get("y", 0)
                result = handle_submit_action(name, ax, ay)
                results.append(f"Action {i+1} ({name}): {result}")
                # Stop early on WIN, GAME_OVER, or budget exhaustion
                cur = frame_ref[0]
                if cur is not None:
                    state_name = cur.state.name
                    if state_name in ("WIN", "GAME_OVER"):
                        results.append(f"Stopped early: state={state_name}")
                        break
                if "budget exhausted" in result.lower():
                    results.append("Stopped: budget exhausted")
                    break
            return "\n---\n".join(results)

        specs.append(_tool(
            "run_action_sequence",
            "Execute a sequence of game actions in order. "
            "Stops early on WIN, GAME_OVER, or budget exhaustion. "
            "Returns a summary of each action's result.",
            {
                "actions": {
                    "type": "array",
                    "description": "List of actions to execute in order.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "action_name": {"type": "string"},
                            "x": {"type": "integer"},
                            "y": {"type": "integer"},
                        },
                        "required": ["action_name"],
                    },
                },
            },
            required=["actions"],
        ))
        handlers["run_action_sequence"] = handle_run_action_sequence

    # --- get_history ---
    def handle_get_history(n: int = 50, wins_only: bool = False) -> str:
        entries = history_fn(n, wins_only)
        if not entries:
            return "No action history yet."
        lines = []
        for action_name, frame in entries:
            lines.append(
                f"{action_name} -> level={frame.metadata.get('levels_completed',0)}/{frame.metadata.get('win_levels',0)}, "
                f"state={frame.metadata.get('state_name','UNKNOWN')}"
            )
        return "\n".join(lines)

    specs.append(_tool(
        "get_history",
        "Return recent action history (all agents). Useful to review what happened.",
        {
            "n": {"type": "integer", "description": "Number of recent entries (default 50)"},
            "wins_only": {"type": "boolean", "description": "If true, only level-completing actions"},
        },
    ))
    handlers["get_history"] = handle_get_history

    # --- render_frame ---
    def handle_render_frame(
        crop_x1: int | None = None, crop_y1: int | None = None,
        crop_x2: int | None = None, crop_y2: int | None = None,
    ) -> str:
        cur = frame_ref[0]
        if cur is None:
            return "No frame available yet. Call submit_action('RESET') first."
        crop = None
        if crop_x1 is not None and crop_y1 is not None and crop_x2 is not None and crop_y2 is not None:
            crop = (crop_x1, crop_y1, crop_x2, crop_y2)
        return cur.render(y_ticks=True, x_ticks=True, crop=crop)

    specs.append(_tool(
        "render_frame",
        "Render the current game grid as text. Optionally crop to a sub-region.",
        {
            "crop_x1": {"type": "integer", "description": "Crop left X (optional)"},
            "crop_y1": {"type": "integer", "description": "Crop top Y (optional)"},
            "crop_x2": {"type": "integer", "description": "Crop right X exclusive (optional)"},
            "crop_y2": {"type": "integer", "description": "Crop bottom Y exclusive (optional)"},
        },
    ))
    handlers["render_frame"] = handle_render_frame

    # --- render_diff ---
    def handle_render_diff(crop: str | None = None) -> str:
        cur = frame_ref[0]
        prev = frame_ref[1]
        if cur is None or prev is None:
            return "Need at least two frames to compute a diff."
        crop_val: tuple[int, int, int, int] | str | None = None
        if crop == "auto":
            crop_val = "auto"
        elif crop is not None:
            try:
                parts = [int(x.strip()) for x in crop.split(",")]
                if len(parts) == 4:
                    crop_val = tuple(parts)  # type: ignore
            except ValueError:
                pass
        return cur.render_diff(prev, crop=crop_val)

    specs.append(_tool(
        "render_diff",
        "Render a visual diff showing what changed between the current and previous frame. "
        "Changed cells show new value, unchanged show '.'. "
        "Use crop='auto' to zoom to changes, or 'x1,y1,x2,y2' for a specific region.",
        {
            "crop": {
                "type": "string",
                "description": "Crop mode: 'auto' or 'x1,y1,x2,y2'. Omit for full grid.",
            },
        },
    ))
    handlers["render_diff"] = handle_render_diff

    # --- change_summary ---
    def handle_change_summary() -> str:
        cur = frame_ref[0]
        prev = frame_ref[1]
        if cur is None or prev is None:
            return "Need at least two frames for a change summary."
        return cur.change_summary(prev)

    specs.append(_tool(
        "change_summary",
        "Quick one-line-per-region summary of what changed between current and previous frame.",
        {},
    ))
    handlers["change_summary"] = handle_change_summary

    # --- find_colors ---
    def handle_find_colors(colors: str) -> str:
        cur = frame_ref[0]
        if cur is None:
            return "No frame available."
        try:
            color_list = [int(c.strip()) for c in colors.split(",")]
        except ValueError:
            return f"ERROR: colors must be comma-separated integers, got '{colors}'"
        results = cur.find(*color_list)
        if not results:
            return f"No pixels found with colors {color_list}"
        lines = [f"Found {len(results)} pixels:"]
        # Limit output to first 200 to avoid context bloat
        for x, y, v in results[:200]:
            lines.append(f"  ({x}, {y}) = {v}")
        if len(results) > 200:
            lines.append(f"  ... and {len(results) - 200} more")
        return "\n".join(lines)

    specs.append(_tool(
        "find_colors",
        "Find all pixels matching given color values. Returns list of (x, y, value).",
        {
            "colors": {
                "type": "string",
                "description": "Comma-separated color values (0-15), e.g. '5,8,14'",
            },
        },
        required=["colors"],
    ))
    handlers["find_colors"] = handle_find_colors

    # --- color_counts ---
    def handle_color_counts() -> str:
        cur = frame_ref[0]
        if cur is None:
            return "No frame available."
        counts = cur.color_counts()
        from .colors import COLOR_NAMES
        lines = []
        for color_val, count in sorted(counts.items()):
            name = COLOR_NAMES[color_val] if color_val < len(COLOR_NAMES) else "?"
            lines.append(f"  {color_val} ({name}): {count}")
        return "\n".join(lines)

    specs.append(_tool(
        "color_counts",
        "Count of each color value present in the current grid.",
        {},
    ))
    handlers["color_counts"] = handle_color_counts

    # --- bounding_box ---
    def handle_bounding_box(colors: str) -> str:
        cur = frame_ref[0]
        if cur is None:
            return "No frame available."
        try:
            color_list = [int(c.strip()) for c in colors.split(",")]
        except ValueError:
            return f"ERROR: colors must be comma-separated integers, got '{colors}'"
        bb = cur.bounding_box(*color_list)
        if bb is None:
            return f"No pixels found with colors {color_list}"
        return f"Bounding box: x=[{bb[0]}, {bb[2]}), y=[{bb[1]}, {bb[3]})"

    specs.append(_tool(
        "bounding_box",
        "Tight bounding box of pixels matching given colors.",
        {
            "colors": {
                "type": "string",
                "description": "Comma-separated color values (0-15), e.g. '5,8,14'",
            },
        },
        required=["colors"],
    ))
    handlers["bounding_box"] = handle_bounding_box

    # --- knowledge tools (wiki or flat memories) ---
    if isinstance(wiki, GameWiki):
        _add_wiki_tools(specs, handlers, wiki)
    else:
        _add_memory_tools(specs, handlers, wiki)

    return specs, handlers


# ---------------------------------------------------------------------------
# Orchestrator tools
# ---------------------------------------------------------------------------

def build_orchestrator_tools(
    spawn_fn: Callable,
    call_fn: Callable,
    wiki: GameWiki,
    history_fn: Callable,
    frame_ref: list,
) -> tuple[list[dict], dict[str, Callable]]:
    """Build tools for the orchestrator agent."""
    specs: list[dict] = []
    handlers: dict[str, Callable] = {}

    # --- spawn_and_run_subagent ---
    specs.append(_tool(
        "spawn_and_run_subagent",
        "Create a new subagent with a role, system prompt, and task. "
        "Runs the subagent to completion and returns its final report. "
        "Set give_submit_action=true for agents that play the game (explorers, testers, solvers). "
        "Set give_submit_action=false for theorists (analysis only). "
        "action_budget limits game actions the subagent can take.",
        {
            "role": {
                "type": "string",
                "description": "Agent role: explorer, theorist, tester, solver, or custom name",
            },
            "system_prompt": {
                "type": "string",
                "description": "System prompt for the subagent. Include relevant game context.",
            },
            "task": {
                "type": "string",
                "description": "The task/instructions for the subagent to execute.",
            },
            "action_budget": {
                "type": "integer",
                "description": "Max game actions allowed (ACTION1-6 only; NOOP/RESET are free). "
                               "Required if give_submit_action is true.",
            },
            "give_submit_action": {
                "type": "boolean",
                "description": "Whether the subagent can take game actions. Default true.",
            },
        },
        required=["role", "system_prompt", "task"],
    ))
    handlers["spawn_and_run_subagent"] = spawn_fn

    # --- call_existing_agent ---
    specs.append(_tool(
        "call_existing_agent",
        "Call a previously spawned subagent again with a new task. "
        "The agent retains context from prior calls. "
        "Pass a fresh action_budget if it needs to take more actions.",
        {
            "agent_id": {
                "type": "string",
                "description": "The agent ID returned from a previous spawn_and_run_subagent call.",
            },
            "task": {
                "type": "string",
                "description": "New task/instructions for the existing agent.",
            },
            "action_budget": {
                "type": "integer",
                "description": "Fresh action budget for this call (optional).",
            },
        },
        required=["agent_id", "task"],
    ))
    handlers["call_existing_agent"] = call_fn

    # --- render_frame (orchestrator can inspect the grid) ---
    def handle_render_frame(
        crop_x1: int | None = None, crop_y1: int | None = None,
        crop_x2: int | None = None, crop_y2: int | None = None,
    ) -> str:
        cur = frame_ref[0]
        if cur is None:
            return "No frame available yet."
        crop = None
        if crop_x1 is not None and crop_y1 is not None and crop_x2 is not None and crop_y2 is not None:
            crop = (crop_x1, crop_y1, crop_x2, crop_y2)
        return cur.render(y_ticks=True, x_ticks=True, crop=crop)

    specs.append(_tool(
        "render_frame",
        "Render the current game grid as text. Helps you understand the game state when briefing subagents.",
        {
            "crop_x1": {"type": "integer", "description": "Crop left X (optional)"},
            "crop_y1": {"type": "integer", "description": "Crop top Y (optional)"},
            "crop_x2": {"type": "integer", "description": "Crop right X exclusive (optional)"},
            "crop_y2": {"type": "integer", "description": "Crop bottom Y exclusive (optional)"},
        },
    ))
    handlers["render_frame"] = handle_render_frame

    # --- get_history ---
    def handle_get_history(n: int = 50, wins_only: bool = False) -> str:
        entries = history_fn(n, wins_only)
        if not entries:
            return "No action history yet."
        lines = []
        for action_name, frame in entries:
            lines.append(
                f"{action_name} -> level={frame.metadata.get('levels_completed',0)}/{frame.metadata.get('win_levels',0)}, "
                f"state={frame.metadata.get('state_name','UNKNOWN')}"
            )
        return "\n".join(lines)

    specs.append(_tool(
        "get_history",
        "Return recent action history from all agents.",
        {
            "n": {"type": "integer", "description": "Number of recent entries (default 50)"},
            "wins_only": {"type": "boolean", "description": "If true, only level-completing actions"},
        },
    ))
    handlers["get_history"] = handle_get_history

    # --- knowledge tools (wiki or flat memories) ---
    if isinstance(wiki, GameWiki):
        _add_wiki_tools(specs, handlers, wiki)
    else:
        _add_memory_tools(specs, handlers, wiki)

    return specs, handlers


# ---------------------------------------------------------------------------
# Shared wiki tools (used by both orchestrator and subagents)
# ---------------------------------------------------------------------------

def _add_wiki_tools(
    specs: list[dict],
    handlers: dict[str, Callable],
    wiki: GameWiki,
) -> None:
    """Add wiki knowledge base tools to specs/handlers."""

    def handle_wiki_index() -> str:
        return wiki.index()

    specs.append(_tool(
        "wiki_index",
        "List all wiki pages with a one-line summary of their content. "
        "Use this to see what knowledge exists before reading specific pages.",
        {},
    ))
    handlers["wiki_index"] = handle_wiki_index

    def handle_wiki_read(page: str) -> str:
        return wiki.read(page)

    specs.append(_tool(
        "wiki_read",
        "Read the full content of a specific wiki page. "
        "Standard pages: mechanics, colors, win_condition, current_level, "
        "strategy, failed_attempts, solved_levels, observations.",
        {
            "page": {
                "type": "string",
                "description": "Page name to read (e.g. 'mechanics', 'colors', 'strategy')",
            },
        },
        required=["page"],
    ))
    handlers["wiki_read"] = handle_wiki_read

    def handle_wiki_write(page: str, content: str) -> str:
        return wiki.write(page, content)

    specs.append(_tool(
        "wiki_write",
        "Write (overwrite) a wiki page with updated knowledge. "
        "Use this when you have new or corrected information that replaces "
        "what was previously on the page. For example, update 'mechanics' "
        "after discovering what each action does, or update 'colors' after "
        "mapping the color palette. Do NOT use this for log-style pages "
        "(failed_attempts, solved_levels) — use wiki_append for those.",
        {
            "page": {
                "type": "string",
                "description": "Page name to write (e.g. 'mechanics', 'colors', 'strategy')",
            },
            "content": {
                "type": "string",
                "description": "Full page content (overwrites previous content)",
            },
        },
        required=["page", "content"],
    ))
    handlers["wiki_write"] = handle_wiki_write

    def handle_wiki_append(page: str, content: str) -> str:
        return wiki.append(page, content)

    specs.append(_tool(
        "wiki_append",
        "Append an entry to a wiki page. Use this for log-style pages: "
        "failed_attempts (what you tried that didn't work and why) and "
        "solved_levels (summary of how each level was beaten). "
        "A timestamp separator is added automatically.",
        {
            "page": {
                "type": "string",
                "description": "Page name to append to (e.g. 'failed_attempts', 'solved_levels')",
            },
            "content": {
                "type": "string",
                "description": "Content to append",
            },
        },
        required=["page", "content"],
    ))
    handlers["wiki_append"] = handle_wiki_append


def _add_memory_tools(
    specs: list[dict],
    handlers: dict[str, Callable],
    memories: Memories,
) -> None:
    """Add flat memory tools (baseline). Used when use_wiki=False."""

    def handle_memories_add(summary: str, details: str) -> str:
        memories.add(summary, details)
        count = len(memories._stack) if hasattr(memories, '_stack') else len(getattr(memories, 'stack', []))
        return f"Memory added. Total memories: {count}"

    specs.append(_tool(
        "memories_add",
        "Store an insight in the shared memory database. "
        "Other agents can read this. Clearly label CONFIRMED vs HYPOTHESIS.",
        {
            "summary": {"type": "string", "description": "Short one-line summary"},
            "details": {"type": "string", "description": "Full details of the insight"},
        },
        required=["summary", "details"],
    ))
    handlers["memories_add"] = handle_memories_add

    def handle_memories_summaries() -> str:
        sums = memories.summaries()
        if not sums:
            return "No memories stored yet."
        return "\n".join(sums)

    specs.append(_tool(
        "memories_summaries",
        "List short summaries of all stored memories. Check before adding to avoid duplicates.",
        {},
    ))
    handlers["memories_summaries"] = handle_memories_summaries

    def handle_memories_get(index: int) -> str:
        try:
            m = memories.get(index)
            return f"[{index}] {m.summary}\n\nDetails: {m.details}\n\nTimestamp: {m.timestamp}"
        except IndexError:
            count = len(memories._stack) if hasattr(memories, '_stack') else len(getattr(memories, 'stack', []))
            return f"ERROR: No memory at index {index}. Total: {count}"

    specs.append(_tool(
        "memories_get",
        "Retrieve full details of a specific memory by index.",
        {
            "index": {"type": "integer", "description": "Memory index (from memories_summaries)"},
        },
        required=["index"],
    ))
    handlers["memories_get"] = handle_memories_get
