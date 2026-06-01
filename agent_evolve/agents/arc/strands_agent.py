"""Strands-based ARC-AGI-3 agent -- single agent with all game actions as tools.

A simpler baseline agent that uses strands SDK directly. Each game action
becomes a tool the LLM can call. The strands Agent manages the conversation
loop internally -- no orchestrator or sub-agents.

This is the "base" agent for quickly getting started on ARC-AGI-3.
For the more advanced orchestrator+REPL approach, see agent.py.

Usage::

    from agent_evolve.agents.arc.strands_agent import StrandsArcAgent

    agent = StrandsArcAgent("seed_workspaces/arc")
    traj = agent.solve(task)
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from strands import Agent, tool
from strands.models import BedrockModel

from ...protocol.base_agent import BaseAgent
from ...types import Task, Trajectory
from .colors import COLOR_LEGEND, COLOR_NAMES
from .frame import Frame
from .game_loop import GameResult, convert_frame_data

logger = logging.getLogger(__name__)

os.environ.setdefault("BYPASS_TOOL_CONSENT", "true")


class StrandsArcAgent(BaseAgent):
    """Strands-based ARC-AGI-3 agent with all game actions as tools.

    Each available game action (ACTION1-7, RESET) becomes a strands @tool.
    The agent also has tools for grid analysis and Python code execution.
    The strands Agent drives the conversation -- it keeps calling tools
    until it decides to stop or the action budget is exhausted.
    """

    def __init__(
        self,
        workspace_dir: str | Path,
        model_id: str = "<evolver-model-id>",
        region: str = "us-west-2",
        max_tokens: int = 8000,
        max_actions: int = 500,
    ):
        super().__init__(workspace_dir)
        self.model_id = model_id
        self.region = region
        self.max_tokens = max_tokens
        self.max_actions = max_actions

    def solve(self, task: Task) -> Trajectory:
        """Play an ARC-AGI-3 game using strands tools."""
        game_id = task.metadata.get("game_id", task.id)
        max_actions = task.metadata.get("max_actions", self.max_actions)

        logger.info("Playing %s with strands agent (budget: %d)", game_id, max_actions)

        try:
            return self._play(task, game_id, max_actions)
        except ImportError as e:
            logger.error("Missing dependency: %s", e)
            return Trajectory(
                task_id=task.id,
                output=json.dumps({"game_id": game_id, "error": str(e),
                                   "game_completed": False, "score": 0.0}),
                steps=[{"error": str(e)}],
            )

    def _play(self, task: Task, game_id: str, max_actions: int) -> Trajectory:
        import arc_agi
        from arcengine import GameAction, GameState

        # Setup arcade + env
        arcade_kwargs: dict[str, Any] = {}
        api_key = task.metadata.get("api_key")
        if api_key:
            arcade_kwargs["arc_api_key"] = api_key
        op_mode = task.metadata.get("operation_mode", "normal")
        if op_mode != "normal":
            from arc_agi import OperationMode
            arcade_kwargs["operation_mode"] = getattr(OperationMode, op_mode.upper())

        arcade = arc_agi.Arcade(**arcade_kwargs)
        env = arcade.make(game_id, render_mode=None)

        # Game state shared across tool closures
        frames: list[Frame] = []
        action_trace: list[dict] = []
        state: dict[str, Any] = {
            "done": False,
            "action_count": 0,
            "levels_completed": 0,
            "win_levels": 0,
            "available_actions": [],
            "game_state": "NOT_PLAYED",
        }

        # Initial reset
        raw = env.reset()
        frame, meta = convert_frame_data(raw)
        frames.append(frame)
        state.update(meta)

        # REPL for code execution
        from .repl import PersistentREPL
        repl = PersistentREPL()
        repl.update_frame(frame, frames, meta)

        # ── Helper to execute a game action ──────────────────────────
        def _do_action(action_name: str, x: int = -1, y: int = -1) -> str:
            if state["done"]:
                return "Game is over."
            if state["action_count"] >= max_actions:
                state["done"] = True
                return f"Budget exhausted ({max_actions} actions)."

            try:
                ga = GameAction.from_name(action_name)
            except (ValueError, KeyError):
                return f"Invalid action: {action_name}. Available: {state['available_actions']}"

            if ga.is_complex() and x >= 0 and y >= 0:
                ga.set_data({"x": min(x, 63), "y": min(y, 63)})

            prev_levels = state["levels_completed"]
            raw_result = env.step(ga)
            if isinstance(raw_result, tuple):
                raw_result = raw_result[0]

            new_frame, new_meta = convert_frame_data(raw_result)
            frames.append(new_frame)
            state.update(new_meta)
            state["action_count"] += 1

            # Update REPL
            repl.update_frame(new_frame, frames, new_meta)

            level_changed = state["levels_completed"] > prev_levels
            if "WIN" in state.get("game_state", ""):
                state["done"] = True
            elif state["win_levels"] > 0 and state["levels_completed"] >= state["win_levels"]:
                state["done"] = True

            action_trace.append({
                "type": "action",
                "action": action_name,
                "step": state["action_count"],
                "x": x if action_name == "ACTION6" else None,
                "y": y if action_name == "ACTION6" else None,
                "level_changed": level_changed,
                "levels_completed": state["levels_completed"],
                "game_state": state.get("game_state", ""),
            })

            # Build response
            parts = []
            if level_changed:
                parts.append(f"*** LEVEL {state['levels_completed']} COMPLETE! ***")
            if state["done"]:
                parts.append("*** GAME WON! ***" if state["levels_completed"] > 0 else "*** GAME OVER ***")

            parts.append(f"Action: {action_name} (#{state['action_count']}/{max_actions})")
            parts.append(f"Level: {state['levels_completed']}/{state['win_levels']}")

            if len(frames) >= 2:
                parts.append(f"Changes: {new_frame.change_summary(frames[-2])}")

            # Compact grid
            non_bg = [c for c, n in new_frame.color_counts().items() if c not in (0, 5)]
            if non_bg:
                bbox = new_frame.bounding_box(*non_bg)
                if bbox:
                    x1 = max(0, bbox[0] - 2)
                    y1 = max(0, bbox[1] - 2)
                    x2 = min(new_frame.width, bbox[2] + 2)
                    y2 = min(new_frame.height, bbox[3] + 2)
                    area = (x2 - x1) * (y2 - y1)
                    total = new_frame.width * new_frame.height
                    if area < total * 0.5:
                        parts.append(f"\nGrid (active [{x1},{y1})-[{x2},{y2})):")
                        parts.append(new_frame.render(y_ticks=True, x_ticks=True, crop=(x1, y1, x2, y2)))
                    else:
                        parts.append(f"\nGrid ({new_frame.width}x{new_frame.height}):")
                        parts.append(new_frame.render(gap=""))

            return "\n".join(parts)

        # ── Define strands tools ─────────────────────────────────────

        @tool
        def observe() -> str:
            """Get the current game state: grid, colors, available actions, level progress.
            Call this before your first action and whenever you need to see the full state.
            """
            if not frames:
                return "(no observation)"
            f = frames[-1]
            parts = [
                f"=== Game State ===",
                f"Level: {state['levels_completed']}/{state['win_levels']}",
                f"Status: {state.get('game_state', '?')}",
                f"Actions used: {state['action_count']}/{max_actions}",
                f"Available actions: {', '.join(state['available_actions'])}",
            ]
            # Color distribution
            colors = f.color_counts()
            present = ", ".join(f"{COLOR_NAMES[c]}({c}):{n}" for c, n in sorted(colors.items()))
            parts.append(f"Colors: {present}")

            # Grid
            non_bg = [c for c, n in colors.items() if c not in (0, 5)]
            if non_bg:
                bbox = f.bounding_box(*non_bg)
                if bbox:
                    x1 = max(0, bbox[0] - 2)
                    y1 = max(0, bbox[1] - 2)
                    x2 = min(f.width, bbox[2] + 2)
                    y2 = min(f.height, bbox[3] + 2)
                    if (x2 - x1) * (y2 - y1) < f.width * f.height * 0.5:
                        parts.append(f"\nGrid (active [{x1},{y1})-[{x2},{y2})):")
                        parts.append(f.render(y_ticks=True, x_ticks=True, crop=(x1, y1, x2, y2)))
                    else:
                        parts.append(f"\nGrid ({f.width}x{f.height}):")
                        parts.append(f.render(gap=""))
                else:
                    parts.append(f.render(gap=""))
            else:
                parts.append(f.render(gap=""))

            # Diff
            if len(frames) >= 2:
                parts.append(f"\nLast change: {f.change_summary(frames[-2])}")

            parts.append(f"\nColor legend: {COLOR_LEGEND}")
            return "\n".join(parts)

        @tool
        def action1() -> str:
            """Move UP (or game-specific direction 1). Only available in keyboard games."""
            return _do_action("ACTION1")

        @tool
        def action2() -> str:
            """Move DOWN (or game-specific direction 2). Only available in keyboard games."""
            return _do_action("ACTION2")

        @tool
        def action3() -> str:
            """Move LEFT (or game-specific direction 3). Only available in keyboard games."""
            return _do_action("ACTION3")

        @tool
        def action4() -> str:
            """Move RIGHT (or game-specific direction 4). Only available in keyboard games."""
            return _do_action("ACTION4")

        @tool
        def action5() -> str:
            """Contextual interaction: select, activate, rotate, or execute.
            What this does depends on the game. Try it near objects to discover its effect."""
            return _do_action("ACTION5")

        @tool
        def action6(x: int, y: int) -> str:
            """Click at position (x, y) on the 64x64 grid.
            Coordinates: x=0 is left edge, x=63 is right edge.
            y=0 is top edge, y=63 is bottom edge.

            Args:
                x: Column to click (0-63, left to right)
                y: Row to click (0-63, top to bottom)
            """
            return _do_action("ACTION6", x, y)

        @tool
        def action7() -> str:
            """Undo the last action. Not available in all games."""
            return _do_action("ACTION7")

        @tool
        def reset_level() -> str:
            """Restart the current level from scratch.
            Use when stuck or when you want to try a different strategy.
            Does NOT cost an action in your budget."""
            if state["done"]:
                return "Game is over."
            raw_result = env.reset()
            if isinstance(raw_result, tuple):
                raw_result = raw_result[0]
            new_frame, new_meta = convert_frame_data(raw_result)
            frames.append(new_frame)
            state.update(new_meta)
            repl.update_frame(new_frame, frames, new_meta)
            return f"Level reset.\n{new_frame.render(gap='')}"

        @tool
        def analyze(colors: str = "", crop: str = "") -> str:
            """Analyze the current grid in detail.

            Args:
                colors: Comma-separated color indices to find (e.g. "8,14" for red,green).
                    Returns pixel locations matching these colors.
                crop: Sub-region to render as "x1,y1,x2,y2" (e.g. "10,20,30,40").
            """
            if not frames:
                return "(no grid)"
            f = frames[-1]
            parts = []
            if colors:
                try:
                    cids = [int(c.strip()) for c in colors.split(",")]
                    pixels = f.find(*cids)
                    names = [COLOR_NAMES[c] for c in cids if c < 16]
                    parts.append(f"Pixels matching {', '.join(names)}: {len(pixels)} found")
                    for px, py, pv in pixels[:50]:
                        parts.append(f"  ({px},{py}) = {pv} ({COLOR_NAMES[pv]})")
                    if len(pixels) > 50:
                        parts.append(f"  ... and {len(pixels)-50} more")
                    bbox = f.bounding_box(*cids)
                    if bbox:
                        parts.append(f"Bounding box: [{bbox[0]},{bbox[1]})-[{bbox[2]},{bbox[3]})")
                except ValueError:
                    parts.append(f"Invalid colors: {colors}")
            if crop:
                try:
                    coords = tuple(int(c.strip()) for c in crop.split(","))
                    if len(coords) == 4:
                        parts.append(f"\nCropped [{coords[0]},{coords[1]})-[{coords[2]},{coords[3]}):")
                        parts.append(f.render(y_ticks=True, x_ticks=True, crop=coords))
                except ValueError:
                    parts.append(f"Invalid crop: {crop}")
            if not parts:
                parts = [f"Grid: {f.width}x{f.height}",
                         f"Colors: {f.color_counts()}"]
            return "\n".join(parts)

        @tool
        def run_code(code: str) -> str:
            """Execute Python code to analyze the grid programmatically.
            This is FREE -- does not cost any action budget.

            Pre-loaded variables:
            - frame: Current Frame object (.find(), .diff(), .color_counts(), .bounding_box())
            - grid: numpy int8 array of the 64x64 grid
            - prev_frame: Previous frame (or None)
            - np: numpy

            Use print() to see output.

            Args:
                code: Python code to execute
            """
            result = repl.exec(code)
            return str(result)

        # ── Filter tools to only available actions ───────────────────

        action_tool_map = {
            "ACTION1": action1, "ACTION2": action2, "ACTION3": action3,
            "ACTION4": action4, "ACTION5": action5, "ACTION6": action6,
            "ACTION7": action7,
        }

        available = state["available_actions"]
        tools = [observe, analyze, run_code, reset_level]
        for act_name in available:
            if act_name in action_tool_map:
                tools.append(action_tool_map[act_name])

        # Add read_skill if skills exist
        if self.skills:
            skill_data = {}
            for sk in self.skills:
                content = self.get_skill_content(sk.name)
                if content:
                    body = content.split("---", 2)[-1].strip() if "---" in content else content
                    skill_data[sk.name] = body

            @tool
            def read_skill(skill_name: str) -> str:
                """Read a learned skill's procedure.
                Args:
                    skill_name: Name of the skill
                """
                if skill_name in skill_data:
                    return skill_data[skill_name]
                return f"Not found. Available: {', '.join(skill_data.keys())}"

            tools.append(read_skill)

        # ── Build strands agent ──────────────────────────────────────

        model = BedrockModel(
            model_id=self.model_id,
            region_name=self.region,
            max_tokens=self.max_tokens,
        )

        system_prompt = self._build_system_prompt(available)
        agent = Agent(model=model, system_prompt=system_prompt, tools=tools)

        # ── Play ─────────────────────────────────────────────────────

        user_prompt = self._build_user_prompt(task, frames[0], available)

        t0 = time.time()
        try:
            response = agent(user_prompt)
        except Exception as e:
            logger.error("Strands agent error: %s", e)
            response = None
        elapsed = time.time() - t0

        # If agent stopped but game not done and budget remains, re-prompt
        reprompt_count = 0
        while not state["done"] and state["action_count"] < max_actions and reprompt_count < 5:
            reprompt_count += 1
            remaining = max_actions - state["action_count"]
            logger.info("Re-prompting: %d actions remaining, level %d/%d",
                        remaining, state["levels_completed"], state["win_levels"])
            try:
                response = agent(
                    f"Continue playing! You have {remaining} actions left. "
                    f"Level {state['levels_completed']}/{state['win_levels']}. "
                    f"Call observe() to see the current state, then keep taking actions."
                )
            except Exception as e:
                logger.error("Re-prompt error: %s", e)
                break
        elapsed = time.time() - t0

        # ── Build output ─────────────────────────────────────────────

        usage = {}
        if response:
            try:
                u = response.metrics.accumulated_usage
                usage = {
                    "input_tokens": u.get("inputTokens", 0),
                    "output_tokens": u.get("outputTokens", 0),
                    "total_tokens": u.get("totalTokens", 0),
                }
            except Exception:
                pass

        levels = state["levels_completed"]
        total_actions = state["action_count"]
        win_levels = state["win_levels"]
        game_completed = levels > 0 and (
            "WIN" in state.get("game_state", "")
            or (win_levels > 0 and levels >= win_levels)
        )

        score = 0.0
        if levels > 0:
            completion = levels / win_levels if win_levels > 0 else 1.0
            avg = total_actions / levels
            efficiency = max(0.1, min(1.0, 1.0 - (avg - 50) / 200))
            score = completion * efficiency

        output = {
            "game_id": game_id,
            "game_completed": game_completed,
            "levels_completed": levels,
            "total_levels": win_levels,
            "total_actions": total_actions,
            "score": score,
            "elapsed_sec": elapsed,
            "usage": usage,
        }

        action_trace.append({"type": "summary", **output})

        self.remember(
            f"Played {game_id}: completed={game_completed}, "
            f"levels={levels}/{win_levels}, actions={total_actions}, score={score:.3f}",
            category="episodic",
            task_id=game_id,
        )

        return Trajectory(task_id=task.id, output=json.dumps(output), steps=action_trace)

    # ── Prompt construction ──────────────────────────────────────────

    def _build_system_prompt(self, available_actions: list[str]) -> str:
        parts = [self.system_prompt]

        # Tell the agent which actions are available as tools
        parts.append(f"\n\n## Available Actions for This Game\n")
        action_descs = {
            "ACTION1": "action1() - Move Up / Direction 1",
            "ACTION2": "action2() - Move Down / Direction 2",
            "ACTION3": "action3() - Move Left / Direction 3",
            "ACTION4": "action4() - Move Right / Direction 4",
            "ACTION5": "action5() - Contextual interaction (select/activate/rotate)",
            "ACTION6": "action6(x, y) - Click at grid position (0-63)",
            "ACTION7": "action7() - Undo last action",
        }
        for act in available_actions:
            if act in action_descs:
                parts.append(f"- {action_descs[act]}")
        parts.append(f"\nAlso available: observe(), analyze(colors, crop), "
                     f"run_code(code), reset_level()")

        # Evolved fragments
        fragments = self.workspace.list_fragments()
        for frag_name in fragments:
            content = self.workspace.read_fragment(frag_name)
            if content and content.strip():
                marker = f"<!-- evolve:{frag_name.removesuffix('.md')} -->"
                if marker not in self.system_prompt:
                    parts.append(f"\n\n## {frag_name.removesuffix('.md').replace('_', ' ').title()}")
                    parts.append(content)

        # Skills
        if self.skills:
            parts.append("\n\n## Learned Skills\n")
            for sk in self.skills:
                parts.append(f"- **{sk.name}**: {sk.description}")

        # Memories
        if self.memories:
            parts.append("\n\n## Lessons from Previous Games\n")
            for mem in self.memories[-10:]:
                parts.append(f"- {mem.get('content', '')}")

        return "\n".join(parts)

    def _build_user_prompt(
        self, task: Task, initial_frame: Frame, available_actions: list[str]
    ) -> str:
        game_id = task.metadata.get("game_id", task.id)
        max_actions = task.metadata.get("max_actions", self.max_actions)

        action_list = ", ".join(available_actions)

        return f"""\
{task.input}

Game: {game_id}
Available actions: {action_list}
Action budget: {max_actions}

Start by calling observe() to see the initial grid state.
Then use run_code() to analyze the grid with Python (FREE, doesn't cost actions).
Experiment with actions to discover the rules, then solve efficiently.
Keep playing until you complete all levels or run out of budget.
"""
