"""ARC-AGI-3 agent -- plays interactive games via the arc-agi toolkit.

Adapted from arcprize/ARC-AGI-3-Agents and symbolica-ai/ARC-AGI-3-Agents.

Architecture follows the Symbolica Arcgentica design:
- Orchestrator manages game-level strategy across levels
- Sub-agents (explorer, solver) get bounded action budgets
- Shared Memories persist insights across all agents
- Fresh agents prevent context rot (no single conversation grows unbounded)
- Code-driven game loop guarantees all actions are used
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

from ...protocol.base_agent import BaseAgent
from ...types import Task, Trajectory
from .colors import COLOR_LEGEND, COLOR_NAMES
from .frame import Frame

logger = logging.getLogger(__name__)

os.environ.setdefault("BYPASS_TOOL_CONSENT", "true")

# Action descriptions for the LLM prompt
ACTION_DESCRIPTIONS = {
    "ACTION1": "Move Up",
    "ACTION2": "Move Down",
    "ACTION3": "Move Left",
    "ACTION4": "Move Right",
    "ACTION5": "Perform contextual action (interact/select/activate)",
    "ACTION6": "Click at coordinates (x, y) on the grid (0-63)",
    "ACTION7": "Undo last action",
    "RESET": "Restart current level",
}


class ArcAgent(BaseAgent):
    """Evolvable agent for ARC-AGI-3 interactive games.

    Uses a code-driven game loop (like the upstream ARC-AGI-3-Agents framework)
    with per-action LLM calls via Bedrock Claude. The workspace provides the
    evolvable system prompt, skills, and memory.
    """

    def __init__(
        self,
        workspace_dir: str | Path,
        model_id: str = "us.anthropic.claude-opus-4-6-v1",
        region: str = "us-west-2",
        max_tokens: int = 8000,
        max_actions: int = 5000,
    ):
        super().__init__(workspace_dir)
        self.model_id = model_id
        self.region = region
        self.max_tokens = max_tokens
        self.max_actions = max_actions
        self._client = None
        self._message_history: list[dict[str, Any]] = []
        self._max_history: int = 12  # keep last N messages to avoid context blowup

    def _get_client(self):
        """Lazy-init Bedrock client."""
        if self._client is None:
            import boto3
            self._client = boto3.client(
                "bedrock-runtime",
                region_name=self.region,
            )
        return self._client

    def solve(self, task: Task) -> Trajectory:
        """Play an ARC-AGI-3 game using the code-driven game loop."""
        game_id = task.metadata.get("game_id", task.id)
        max_actions = task.metadata.get("max_actions", self.max_actions)

        logger.info("Playing ARC-AGI-3 game: %s (budget: %d actions)", game_id, max_actions)

        # Reset per-game state
        self._message_history = []
        self._total_input_tokens = 0
        self._total_output_tokens = 0

        try:
            return self._play_game(task, game_id, max_actions)
        except ImportError as e:
            logger.error("arc-agi not installed: %s", e)
            return Trajectory(
                task_id=task.id,
                output=json.dumps({
                    "game_id": game_id,
                    "error": str(e),
                    "game_completed": False,
                    "levels_completed": 0,
                    "total_levels": 0,
                    "total_actions": 0,
                    "score": 0.0,
                }),
                steps=[{"error": str(e)}],
            )

    def play_game_on_env(self, env: Any, game_id: str, max_actions: int) -> dict:
        """Play a game on a pre-created environment (used by Swarm).

        Args:
            env: arc_agi EnvironmentWrapper (from arcade.make with scorecard_id)
            game_id: Game identifier
            max_actions: Action budget

        Returns:
            Result dict with game_id, levels_completed, total_actions, etc.
        """
        self._message_history = []
        self._total_input_tokens = 0
        self._total_output_tokens = 0

        result = self._run_game_loop(env, game_id, max_actions)

        return {
            "game_id": game_id,
            "game_completed": result.game_completed,
            "levels_completed": result.levels_completed,
            "total_levels": result.total_levels,
            "total_actions": result.total_actions,
            "per_level_actions": result.per_level_actions,
            "score": self._compute_score(result),
            "elapsed_sec": result.elapsed_sec,
            "usage": {
                "input_tokens": self._total_input_tokens,
                "output_tokens": self._total_output_tokens,
                "total_tokens": self._total_input_tokens + self._total_output_tokens,
            },
        }

    def _run_game_loop(self, env: Any, game_id: str, max_actions: int) -> Any:
        """Play a full game using the orchestrator + sub-agent pattern.

        Architecture (adapted from Symbolica's Arcgentica):
        - Orchestrator manages game-level strategy across levels
        - Sub-agents (explorer, solver) get bounded action budgets
        - Shared Memories persist insights across all agents
        - Fresh agents prevent context rot
        """
        from arcengine import GameAction

        from .game_loop import GameResult, convert_frame_data
        from .orchestrator import Orchestrator

        client = self._get_client()
        workspace_prompt = self._build_system_prompt()

        orch = Orchestrator(
            client=client,
            model_id=self.model_id,
            max_tokens=self.max_tokens,
            workspace_prompt=workspace_prompt,
        )

        # Initial reset
        raw = env.reset()
        frame, meta = convert_frame_data(raw)
        frames: list[Frame] = [frame]

        total_actions = 0
        per_level_actions: list[int] = []
        t0 = time.time()

        # Create env_step closure for the orchestrator
        def env_step(action_name: str, x: int = -1, y: int = -1) -> tuple[Frame, dict[str, Any]]:
            action = self._parse_action(action_name, meta)
            if action_name == "ACTION6" and x >= 0 and y >= 0:
                action.set_data({"x": min(x, 63), "y": min(y, 63)})

            raw_result = env.step(action)
            if isinstance(raw_result, tuple):
                raw_result = raw_result[0]
            new_frame, new_meta = convert_frame_data(raw_result)
            meta.update(new_meta)
            return new_frame, meta

        # Play through levels
        win_levels = meta.get("win_levels", 0)
        current_level = 0

        while total_actions < max_actions:
            # Check if game is won
            if current_level >= win_levels > 0:
                break
            state = meta.get("state", "")
            if "WIN" in state:
                break

            # Budget per level: distribute remaining budget, with more for early levels
            remaining = max_actions - total_actions
            if win_levels > 0:
                levels_left = win_levels - current_level
                level_budget = min(remaining, max(20, remaining // max(1, levels_left)))
            else:
                level_budget = remaining

            logger.info(
                "Level %d/%d: budget=%d actions, memories=%d",
                current_level, win_levels, level_budget, len(orch.memories),
            )

            # Play this level via orchestrator
            frames, meta, actions_used = orch.play_level(
                env_step=env_step,
                frames=frames,
                meta=meta,
                budget=level_budget,
                level=current_level,
            )
            total_actions += actions_used
            per_level_actions.append(actions_used)

            # Check if level advanced
            new_level = meta.get("levels_completed", 0)
            if new_level > current_level:
                logger.info(
                    "Level %d completed in %d actions! (memories: %d)",
                    current_level, actions_used, len(orch.memories),
                )
                current_level = new_level
            else:
                logger.info(
                    "Level %d not completed after %d actions",
                    current_level, actions_used,
                )
                # Move on to avoid infinite loop -- level didn't complete
                break

        elapsed = time.time() - t0

        # Collect token usage from orchestrator
        self._total_input_tokens += orch.total_input_tokens
        self._total_output_tokens += orch.total_output_tokens

        # Build result
        levels_completed = meta.get("levels_completed", 0)
        game_completed = (
            levels_completed > 0
            and (meta.get("state", "") == "GameState.WIN"
                 or (win_levels > 0 and levels_completed >= win_levels))
        )

        result = GameResult(
            game_id=game_id,
            game_completed=game_completed,
            levels_completed=levels_completed,
            total_levels=win_levels,
            total_actions=total_actions,
            per_level_actions=per_level_actions,
            frames=frames,
            elapsed_sec=elapsed,
        )

        logger.info(
            "Game %s done: %d actions, %d/%d levels, %.1fs, %d memories",
            game_id, total_actions, levels_completed, win_levels,
            elapsed, len(orch.memories),
        )

        return result

    def _play_game(self, task: Task, game_id: str, max_actions: int) -> Trajectory:
        """Run the code-driven game loop with per-action LLM calls."""
        import arc_agi

        # Create arcade + environment (standalone mode, no Swarm)
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

        result = self._run_game_loop(env, game_id, max_actions)

        # Build trajectory
        usage = {
            "input_tokens": self._total_input_tokens,
            "output_tokens": self._total_output_tokens,
            "total_tokens": self._total_input_tokens + self._total_output_tokens,
        }

        output = {
            "game_id": game_id,
            "game_completed": result.game_completed,
            "levels_completed": result.levels_completed,
            "total_levels": result.total_levels,
            "total_actions": result.total_actions,
            "per_level_actions": result.per_level_actions,
            "score": self._compute_score(result),
            "elapsed_sec": result.elapsed_sec,
            "usage": usage,
        }

        steps = [
            {
                "type": "action",
                "action": a.action,
                "step": a.step,
                "x": a.x,
                "y": a.y,
                "level_changed": a.level_changed,
                "levels_completed": a.levels_completed,
                "state": a.state,
            }
            for a in result.actions
        ]
        steps.append({"type": "summary", "usage": usage, **output})

        self.remember(
            f"Played {game_id}: completed={result.game_completed}, "
            f"levels={result.levels_completed}/{result.total_levels}, "
            f"actions={result.total_actions}, score={output['score']:.3f}",
            category="episodic",
            task_id=game_id,
        )

        return Trajectory(task_id=task.id, output=json.dumps(output), steps=steps)

    # ── Per-action LLM call ──────────────────────────────────────────

    def _call_llm(
        self, system_prompt: str, observation: str, meta: dict[str, Any]
    ) -> tuple[str, str]:
        """Call Bedrock Claude once to get the next action.

        Returns (action_string, reasoning_text).
        """
        client = self._get_client()

        # Add observation as user message
        self._message_history.append({
            "role": "user",
            "content": [{"text": observation}],
        })

        # Trim history to avoid context blowup
        if len(self._message_history) > self._max_history:
            self._message_history = self._message_history[-self._max_history:]

        try:
            response = client.converse(
                modelId=self.model_id,
                system=[{"text": system_prompt}],
                messages=self._message_history,
                inferenceConfig={
                    "maxTokens": self.max_tokens,
                    "temperature": 0.3,
                },
            )

            # Extract response text
            output_msg = response.get("output", {}).get("message", {})
            content = output_msg.get("content", [])
            text = ""
            for block in content:
                if "text" in block:
                    text += block["text"]

            # Track usage
            usage = response.get("usage", {})
            self._total_input_tokens += usage.get("inputTokens", 0)
            self._total_output_tokens += usage.get("outputTokens", 0)

            # Add assistant response to history
            self._message_history.append({
                "role": "assistant",
                "content": [{"text": text}],
            })

            # Parse action from response
            action_str, reasoning = self._extract_action_from_response(text)
            return action_str, reasoning

        except Exception as e:
            logger.error("LLM call failed: %s", e)
            return "RESET", f"LLM error: {e}"

    def _extract_action_from_response(self, text: str) -> tuple[str, str]:
        """Extract action name and reasoning from LLM response text."""
        # Try JSON format first: {"action": "ACTION1", "x": 0, "y": 0}
        json_match = re.search(r'\{[^{}]*"action"\s*:\s*"([^"]+)"[^{}]*\}', text)
        if json_match:
            try:
                json_str = json_match.group(0)
                parsed = json.loads(json_str)
                action = parsed.get("action", "RESET").upper()
                # Store x,y for ACTION6
                self._last_x = parsed.get("x", -1)
                self._last_y = parsed.get("y", -1)
                return action, text
            except json.JSONDecodeError:
                pass

        # Try plain action name
        for action_name in ["ACTION1", "ACTION2", "ACTION3", "ACTION4",
                            "ACTION5", "ACTION6", "ACTION7", "RESET"]:
            if action_name in text.upper():
                return action_name, text

        return "RESET", text

    def _parse_action(self, action_str: str, meta: dict[str, Any]) -> Any:
        """Convert action string to GameAction object."""
        from arcengine import GameAction

        action_str = action_str.upper().strip()
        try:
            action = GameAction.from_name(action_str)
        except (ValueError, KeyError):
            logger.warning("Invalid action '%s', defaulting to RESET", action_str)
            return GameAction.RESET

        # Set coordinates for ACTION6
        if action.is_complex():
            x = getattr(self, "_last_x", -1)
            y = getattr(self, "_last_y", -1)
            if x >= 0 and y >= 0:
                action.set_data({"x": min(x, 63), "y": min(y, 63)})
            else:
                # No coordinates provided, default to center
                action.set_data({"x": 32, "y": 32})

        return action

    # ── Observation formatting ───────────────────────────────────────

    def _format_observation(
        self, frames: list[Frame], latest: Frame, meta: dict[str, Any]
    ) -> str:
        """Build the observation text sent to the LLM each step.

        Keeps it compact -- only the current grid + diff from previous frame.
        This is called once per action, so it must be token-efficient.
        """
        parts = []

        # Status line
        levels = meta.get("levels_completed", 0)
        win_levels = meta.get("win_levels", 0)
        state = meta.get("state", "")
        available = meta.get("available_actions", [])
        step = len(frames) - 1

        parts.append(
            f"[Step {step} | Level {levels}/{win_levels} | "
            f"State: {state} | Actions: {', '.join(available)}]"
        )

        # Show diff from previous frame (most useful signal)
        if len(frames) >= 2:
            summary = latest.change_summary(frames[-2])
            parts.append(f"\nChanges: {summary}")

        # Compact grid render -- cropped to active area to save tokens
        non_bg = [c for c, n in latest.color_counts().items() if c not in (0, 5)]
        if non_bg:
            bbox = latest.bounding_box(*non_bg)
            if bbox:
                x1 = max(0, bbox[0] - 2)
                y1 = max(0, bbox[1] - 2)
                x2 = min(latest.width, bbox[2] + 2)
                y2 = min(latest.height, bbox[3] + 2)
                area_ratio = (x2 - x1) * (y2 - y1) / max(1, latest.width * latest.height)
                if area_ratio < 0.5:
                    # Significant crop savings -- show cropped with ticks
                    parts.append(f"\nGrid (active area [{x1},{y1})-[{x2},{y2}) of {latest.width}x{latest.height}):")
                    parts.append(latest.render(y_ticks=True, x_ticks=True, crop=(x1, y1, x2, y2)))
                else:
                    # Active area is most of the grid -- compact full render
                    parts.append(f"\nGrid ({latest.width}x{latest.height}, compact):")
                    parts.append(latest.render(gap=""))
            else:
                parts.append(f"\nGrid ({latest.width}x{latest.height}, compact):")
                parts.append(latest.render(gap=""))
        else:
            parts.append(f"\nGrid ({latest.width}x{latest.height}, compact):")
            parts.append(latest.render(gap=""))

        # Color legend on first frame only
        if len(frames) <= 2:
            colors = latest.color_counts()
            present = ", ".join(f"{COLOR_NAMES[c]}({c}):{n}" for c, n in sorted(colors.items()))
            parts.append(f"\nColors present: {present}")
            parts.append(f"Color legend: {COLOR_LEGEND}")

        parts.append(
            "\nRespond with a JSON object: "
            '{"action": "ACTION1", "reasoning": "why"}'
            "\nFor ACTION6 add coordinates: "
            '{"action": "ACTION6", "x": 32, "y": 32, "reasoning": "why"}'
        )

        return "\n".join(parts)

    # ── Score computation ────────────────────────────────────────────

    @staticmethod
    def _compute_score(result: Any) -> float:
        """Compute a 0-1 RHAE-inspired score."""
        levels = result.levels_completed
        total = result.total_levels
        total_actions = result.total_actions

        if levels == 0:
            return 0.0

        completion = levels / total if total > 0 else 1.0
        avg_actions = total_actions / levels
        efficiency = max(0.1, min(1.0, 1.0 - (avg_actions - 50) / 200))
        return completion * efficiency

    # ── Prompt construction (from workspace) ─────────────────────────

    def _build_system_prompt(self) -> str:
        """Assemble the system prompt from workspace files."""
        parts = [self.system_prompt]

        # Evolved prompt fragments
        fragments = self.workspace.list_fragments()
        if fragments:
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
            for skill in self.skills:
                content = self.get_skill_content(skill.name)
                if content:
                    body = content.split("---", 2)[-1].strip() if "---" in content else content
                    parts.append(f"### {skill.name}\n{skill.description}\n{body}\n")

        # Memories
        if self.memories:
            parts.append("\n\n## Lessons from Previous Games\n")
            for mem in self.memories[-10:]:
                parts.append(f"- {mem.get('content', '')}")

        return "\n".join(parts)
