"""Basic ARC-AGI-3 agent -- minimal, built from the official toolkit docs.

No code_exec, no image input. Pure text-based LLM agent with a
code-driven game loop. Each step: format observation as text,
call LLM once for one action, execute in env, repeat.

Built following https://docs.arcprize.org/toolkit/overview

Usage::

    from agent_evolve.agents.arc.basic_agent import BasicArcAgent

    agent = BasicArcAgent("seed_workspaces/arc")
    traj = agent.solve(task)
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

import boto3
from arcengine import GameAction, GameState

from ...protocol.base_agent import BaseAgent
from ...types import Task, Trajectory
from .colors import COLOR_NAMES, COLOR_LEGEND
from .frame import Frame
from .game_loop import convert_frame_data

logger = logging.getLogger(__name__)

# ── Action metadata ──────────────────────────────────────────────

ACTION_DESC = {
    "ACTION1": "Direction 1 (typically Up)",
    "ACTION2": "Direction 2 (typically Down)",
    "ACTION3": "Direction 3 (typically Left)",
    "ACTION4": "Direction 4 (typically Right)",
    "ACTION5": "Interact / Select / Activate",
    "ACTION6": "Click at coordinates (x, y) on the 64x64 grid",
    "ACTION7": "Undo last action",
}


class BasicArcAgent(BaseAgent):
    """Minimal ARC-AGI-3 agent. Text observations, one LLM call per action.

    Follows the official toolkit pattern:
      arcade = Arcade()
      env = arcade.make(game_id)
      obs = env.reset()
      while playing:
          action = choose_action(obs)  # LLM call
          obs = env.step(action)
    """

    def __init__(
        self,
        workspace_dir: str | Path,
        model_id: str = "<evolver-model-id>",
        region: str = "us-west-2",
        max_tokens: int = 4096,
        max_actions: int = 500,
    ):
        super().__init__(workspace_dir)
        self.model_id = model_id
        self.region = region
        self.max_tokens = max_tokens
        self.max_actions = max_actions

    def solve(self, task: Task) -> Trajectory:
        """Play an ARC-AGI-3 game and return the trajectory."""
        import arc_agi

        game_id = task.metadata.get("game_id", task.id)
        max_actions = task.metadata.get("max_actions", self.max_actions)

        logger.info("Playing %s (budget: %d actions)", game_id, max_actions)

        # ── Setup: Arcade + Environment ──────────────────────────
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

        # ── Reset ────────────────────────────────────────────────
        raw = env.reset()
        frame, meta = convert_frame_data(raw)

        frames: list[Frame] = [frame]
        action_trace: list[dict] = []
        action_count = 0

        # Available actions for this game
        avail = meta.get("available_actions", [])

        # Bedrock client
        client = boto3.client("bedrock-runtime", region_name=self.region)

        # System prompt from workspace
        system_prompt = self._build_system_prompt(avail)

        # Conversation history (sliding window)
        messages: list[dict] = []
        max_history = 20

        # ── Game loop ────────────────────────────────────────────
        while action_count < max_actions:
            state_str = meta.get("state", "")

            # Auto-reset on NOT_PLAYED or GAME_OVER
            if "NOT_PLAYED" in state_str or "GAME_OVER" in state_str:
                raw = env.step(GameAction.RESET)
                if isinstance(raw, tuple):
                    raw = raw[0]
                frame, meta = convert_frame_data(raw)
                frames.append(frame)
                action_trace.append({
                    "type": "action", "action": "RESET",
                    "step": action_count,
                    "levels_completed": meta.get("levels_completed", 0),
                    "state": meta.get("state", ""),
                })
                continue

            # Check win
            if "WIN" in state_str:
                logger.info("Game won!")
                break
            wl = meta.get("win_levels", 0)
            lc = meta.get("levels_completed", 0)
            if wl > 0 and lc >= wl:
                logger.info("All %d levels completed!", wl)
                break

            # ── Build observation text ───────────────────────────
            observation = self._format_observation(frames, frame, meta, action_count, max_actions)

            # ── LLM call: one action per step ────────────────────
            messages.append({"role": "user", "content": [{"text": observation}]})

            # Trim history
            if len(messages) > max_history:
                messages = messages[-max_history:]
                # Ensure starts with user
                while messages and messages[0]["role"] != "user":
                    messages.pop(0)

            try:
                resp = client.converse(
                    modelId=self.model_id,
                    system=[{"text": system_prompt}],
                    messages=messages,
                    inferenceConfig={"maxTokens": self.max_tokens, "temperature": 0.3},
                )
            except Exception as e:
                logger.error("LLM error: %s", e)
                messages = []  # reset conversation on error
                continue

            # Extract response text
            content = resp.get("output", {}).get("message", {}).get("content", [])
            text = "".join(b.get("text", "") for b in content)

            messages.append({"role": "assistant", "content": [{"text": text}]})

            # ── Parse action from LLM response ───────────────────
            action_name, x, y = self._parse_action(text, avail)

            if action_name == "RESET":
                raw = env.reset()
                if isinstance(raw, tuple):
                    raw = raw[0]
                frame, meta = convert_frame_data(raw)
                frames.append(frame)
                action_trace.append({
                    "type": "action", "action": "RESET",
                    "step": action_count,
                    "levels_completed": meta.get("levels_completed", 0),
                    "state": meta.get("state", ""),
                })
                continue

            # Execute action
            try:
                ga = GameAction.from_name(action_name)
                if ga.is_complex() and x >= 0 and y >= 0:
                    ga.set_data({"x": min(x, 63), "y": min(y, 63)})

                raw = env.step(ga, reasoning={"thought": text[:200]})
                if isinstance(raw, tuple):
                    raw = raw[0]
            except Exception as e:
                logger.warning("Action %s failed: %s", action_name, e)
                continue

            prev_levels = meta.get("levels_completed", 0)
            new_frame, new_meta = convert_frame_data(raw)
            frames.append(new_frame)
            meta.update(new_meta)
            action_count += 1

            level_changed = meta.get("levels_completed", 0) > prev_levels
            if level_changed:
                logger.info("Level %d completed!", meta.get("levels_completed", 0))

            action_trace.append({
                "type": "action",
                "action": action_name,
                "step": action_count,
                "x": x if action_name == "ACTION6" else None,
                "y": y if action_name == "ACTION6" else None,
                "level_changed": level_changed,
                "levels_completed": meta.get("levels_completed", 0),
                "state": meta.get("state", ""),
            })

            frame = new_frame

        # ── Build result ─────────────────────────────────────────
        levels = meta.get("levels_completed", 0)
        win_levels = meta.get("win_levels", 0)
        game_completed = levels > 0 and (
            "WIN" in meta.get("state", "")
            or (win_levels > 0 and levels >= win_levels)
        )

        score = 0.0
        if levels > 0:
            completion = levels / win_levels if win_levels > 0 else 1.0
            avg = action_count / levels
            score = completion * max(0.1, min(1.0, 1.0 - (avg - 50) / 200))

        output = {
            "game_id": game_id,
            "game_completed": game_completed,
            "levels_completed": levels,
            "total_levels": win_levels,
            "total_actions": action_count,
            "score": score,
        }

        self.remember(
            f"Played {game_id}: completed={game_completed}, "
            f"levels={levels}/{win_levels}, actions={action_count}",
            category="episodic",
            task_id=game_id,
        )

        return Trajectory(task_id=task.id, output=json.dumps(output), steps=action_trace)

    # ── Observation formatting ───────────────────────────────────

    def _format_observation(
        self,
        frames: list[Frame],
        current: Frame,
        meta: dict,
        action_count: int,
        max_actions: int,
    ) -> str:
        """Format the current state as compact text for the LLM."""
        parts = [
            f"[Step {action_count}/{max_actions} | "
            f"Level {meta.get('levels_completed', 0)}/{meta.get('win_levels', 0)} | "
            f"Actions: {', '.join(meta.get('available_actions', []))}]",
        ]

        # Diff from previous frame
        if len(frames) >= 2:
            diff = current.change_summary(frames[-2])
            parts.append(f"Last change: {diff}")

        # Compact grid -- crop to active area if possible
        non_bg = [c for c, n in current.color_counts().items() if c not in (0, 5)]
        if non_bg:
            bbox = current.bounding_box(*non_bg)
            if bbox:
                x1 = max(0, bbox[0] - 1)
                y1 = max(0, bbox[1] - 1)
                x2 = min(current.width, bbox[2] + 1)
                y2 = min(current.height, bbox[3] + 1)
                area = (x2 - x1) * (y2 - y1)
                total = current.width * current.height
                if area < total * 0.6:
                    parts.append(f"Grid (active [{x1},{y1})-[{x2},{y2})):")
                    parts.append(current.render(y_ticks=True, x_ticks=True,
                                                crop=(x1, y1, x2, y2)))
                else:
                    parts.append(f"Grid ({current.width}x{current.height}):")
                    parts.append(current.render(gap=""))
            else:
                parts.append(current.render(gap=""))
        else:
            parts.append(current.render(gap=""))

        # Color counts on first observation
        if len(frames) <= 2:
            colors = current.color_counts()
            parts.append("Colors: " + ", ".join(
                f"{COLOR_NAMES[c]}({c}):{n}" for c, n in sorted(colors.items())))
            parts.append(f"Legend: {COLOR_LEGEND}")

        # Response format instruction
        parts.append(
            '\nRespond with JSON: {"action": "ACTION1", "reasoning": "why"}'
            '\nFor ACTION6: {"action": "ACTION6", "x": 32, "y": 15, "reasoning": "why"}'
            '\nTo restart: {"action": "RESET", "reasoning": "why"}'
        )

        return "\n".join(parts)

    # ── Prompt construction ──────────────────────────────────────

    def _build_system_prompt(self, available_actions: list[str]) -> str:
        """Build system prompt from workspace + available actions."""
        parts = [self.system_prompt]

        parts.append("\n\n## Actions Available in This Game\n")
        for act in available_actions:
            desc = ACTION_DESC.get(act, act)
            parts.append(f"- {act}: {desc}")
        parts.append("- RESET: Restart current level")

        # Evolved fragments
        for frag_name in self.workspace.list_fragments():
            content = self.workspace.read_fragment(frag_name)
            if content and content.strip():
                parts.append(f"\n\n## {frag_name.removesuffix('.md').replace('_', ' ').title()}")
                parts.append(content)

        # Skills injected directly (no read_skill tool)
        if self.skills:
            parts.append("\n\n## Learned Skills\n")
            for sk in self.skills:
                content = self.get_skill_content(sk.name)
                if content:
                    body = content.split("---", 2)[-1].strip() if "---" in content else content
                    parts.append(f"### {sk.name}\n{sk.description}\n{body}\n")

        # Memories injected directly
        if self.memories:
            parts.append("\n\n## Lessons from Previous Games\n")
            for mem in self.memories[-10:]:
                parts.append(f"- {mem.get('content', '')}")

        return "\n".join(parts)

    # ── Action parsing ───────────────────────────────────────────

    @staticmethod
    def _parse_action(text: str, available: list[str]) -> tuple[str, int, int]:
        """Extract action name and optional x,y from LLM response.

        Returns (action_name, x, y). x/y are -1 if not provided.
        """
        x, y = -1, -1

        # Try JSON parse
        json_match = re.search(r'\{[^{}]*"action"\s*:\s*"([^"]+)"[^{}]*\}', text)
        if json_match:
            try:
                parsed = json.loads(json_match.group(0))
                action = parsed.get("action", "").upper()
                x = int(parsed.get("x", -1))
                y = int(parsed.get("y", -1))
                if action in available or action == "RESET":
                    return action, x, y
            except (json.JSONDecodeError, ValueError):
                pass

        # Fallback: scan for action names
        text_upper = text.upper()
        for act in ["ACTION7", "ACTION6", "ACTION5", "ACTION4",
                     "ACTION3", "ACTION2", "ACTION1", "RESET"]:
            if act in text_upper and (act in available or act == "RESET"):
                return act, x, y

        # Default: first available action
        return available[0] if available else "RESET", -1, -1
