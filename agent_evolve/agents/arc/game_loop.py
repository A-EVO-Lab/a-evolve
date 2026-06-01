"""Code-driven game loop for ARC-AGI-3.

Adapted from arcprize/ARC-AGI-3-Agents (agents/agent.py).
Provides the core game loop that calls choose_action() per step,
separated from the LLM logic so different strategies can plug in.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from arcengine import FrameData, FrameDataRaw, GameAction, GameState

from .frame import Frame

logger = logging.getLogger(__name__)


@dataclass
class ActionRecord:
    """Record of a single action taken in the game."""

    step: int
    action: str
    x: int | None = None
    y: int | None = None
    reward: float = 0.0
    level_changed: bool = False
    levels_completed: int = 0
    state: str = ""
    reasoning: str = ""


@dataclass
class GameResult:
    """Result of playing a complete game."""

    game_id: str
    game_completed: bool = False
    levels_completed: int = 0
    total_levels: int = 0
    total_actions: int = 0
    per_level_actions: list[int] = field(default_factory=list)
    actions: list[ActionRecord] = field(default_factory=list)
    frames: list[Frame] = field(default_factory=list)
    elapsed_sec: float = 0.0
    error: str | None = None


def convert_frame_data(raw: FrameDataRaw | Any) -> tuple[Frame, dict[str, Any]]:
    """Convert raw arc-agi FrameData to our Frame + metadata."""
    if hasattr(raw, "frame"):
        grid = raw.frame[-1] if isinstance(raw.frame, list) else raw.frame
        if hasattr(grid, "tolist"):
            grid = grid.tolist()

        available = []
        if hasattr(raw, "available_actions"):
            available = [GameAction.from_id(a).name for a in raw.available_actions]

        meta = {
            "levels_completed": getattr(raw, "levels_completed", 0),
            "win_levels": getattr(raw, "win_levels", 0),
            "state": str(getattr(raw, "state", "UNKNOWN")),
            "available_actions": available,
            "game_id": getattr(raw, "game_id", ""),
        }
        return Frame(grid, **meta), meta
    # Fallback
    if isinstance(raw, (list, tuple)):
        return Frame(raw), {}
    return Frame([[0] * 64] * 64), {}


def run_game(
    env: Any,
    game_id: str,
    choose_action: Callable[[list[Frame], Frame, dict[str, Any]], GameAction],
    is_done: Callable[[list[Frame], Frame, dict[str, Any]], bool],
    max_actions: int = 5000,
    on_action: Callable[[ActionRecord, Frame], None] | None = None,
) -> GameResult:
    """Run the code-driven game loop.

    This follows the arcprize/ARC-AGI-3-Agents Agent.main() pattern:
    a tight while loop that calls choose_action() once per step.

    Args:
        env: arc_agi EnvironmentWrapper from arcade.make()
        game_id: Game identifier string
        choose_action: Called each step with (frames, latest_frame, meta) -> GameAction.
            This is where the LLM call happens.
        is_done: Called each step with (frames, latest_frame, meta) -> bool.
        max_actions: Action budget.
        on_action: Optional callback after each action for logging/tracing.
    """
    result = GameResult(game_id=game_id)
    frames: list[Frame] = []
    meta: dict[str, Any] = {}
    action_counter = 0
    current_level_actions = 0
    t0 = time.time()

    try:
        # Initial reset
        raw = env.reset()
        frame, meta = convert_frame_data(raw)
        frames.append(frame)

        while not is_done(frames, frame, meta) and action_counter < max_actions:
            # Ask the strategy for the next action
            action = choose_action(frames, frame, meta)

            # Execute in environment
            raw = env.step(action)
            if isinstance(raw, tuple):
                raw = raw[0]

            prev_levels = meta.get("levels_completed", 0)
            frame, meta = convert_frame_data(raw)
            frames.append(frame)

            action_counter += 1
            current_level_actions += 1

            # Detect level transition
            new_levels = meta.get("levels_completed", 0)
            level_changed = new_levels > prev_levels
            if level_changed:
                result.per_level_actions.append(current_level_actions)
                current_level_actions = 0

            # Record action
            action_name = action.name if hasattr(action, "name") else str(action)
            x_val = None
            y_val = None
            if hasattr(action, "action_data") and action.action_data:
                data = action.action_data
                if hasattr(data, "model_dump"):
                    d = data.model_dump()
                    x_val = d.get("x")
                    y_val = d.get("y")

            record = ActionRecord(
                step=action_counter,
                action=action_name,
                x=x_val,
                y=y_val,
                level_changed=level_changed,
                levels_completed=new_levels,
                state=meta.get("state", ""),
                reasoning=getattr(action, "reasoning", ""),
            )
            result.actions.append(record)

            if on_action:
                on_action(record, frame)

            logger.debug(
                "%s - %s: step %d, levels %d, state %s",
                game_id, action_name, action_counter,
                new_levels, meta.get("state", ""),
            )

    except Exception as e:
        logger.error("Game %s error: %s", game_id, e)
        result.error = str(e)

    result.total_actions = action_counter
    result.levels_completed = meta.get("levels_completed", 0)
    result.total_levels = meta.get("win_levels", 0)
    result.frames = frames
    result.elapsed_sec = time.time() - t0
    result.game_completed = (
        result.levels_completed > 0
        and (meta.get("state") == "GameState.WIN"
             or result.levels_completed >= result.total_levels > 0)
    )

    # Flush remaining level actions
    if current_level_actions > 0:
        result.per_level_actions.append(current_level_actions)

    logger.info(
        "Game %s done: %d actions, %d/%d levels, %.1fs",
        game_id, result.total_actions, result.levels_completed,
        result.total_levels, result.elapsed_sec,
    )

    return result
