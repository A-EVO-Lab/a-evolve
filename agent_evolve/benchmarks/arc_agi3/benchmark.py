"""ARC-AGI-3 benchmark adapter.

Integrates the ARC-AGI-3 interactive game benchmark with a-evolve's
evolution loop. Agents play games from the ARC Prize arcade and are
scored on Relative Human Action Efficiency (RHAE).

Requires: pip install arc-agi
"""

from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass
from typing import Any

from ...types import Feedback, Task, Trajectory
from ..base import BenchmarkAdapter

logger = logging.getLogger(__name__)


@dataclass
class GameResult:
    """Parsed result from an agent's game trajectory."""

    game_id: str
    levels_completed: int
    total_levels: int
    total_actions: int
    game_completed: bool
    per_level_actions: list[int]
    score: float  # 0.0-1.0 RHAE-style score
    raw_info: dict[str, Any]


class ArcAgi3Benchmark(BenchmarkAdapter):
    """ARC-AGI-3 interactive game benchmark.

    Loads games from the ARC Prize arcade via the ``arc-agi`` package.
    Each task represents a game the agent must play through by selecting
    actions (movement, interaction, targeting) and is scored on how
    efficiently it completes levels relative to human baselines.

    Args:
        api_key: ARC API key (optional, env var ARC_API_KEY also works).
        operation_mode: "normal", "offline", "online", or "competition".
        game_filter: Only include games whose ID contains this substring.
        tag_filter: Only include games with this tag.
        max_actions_per_game: Action budget per game (default 5000).
        holdout_ratio: Fraction of games reserved for gating.
        shuffle: Whether to shuffle game order.
        seed: Random seed for reproducible splits.
    """

    def __init__(
        self,
        api_key: str | None = None,
        operation_mode: str = "normal",
        game_filter: str | None = None,
        tag_filter: str | None = None,
        max_actions_per_game: int = 5000,
        holdout_ratio: float = 0.2,
        shuffle: bool = True,
        seed: int = 42,
    ):
        self.api_key = api_key
        self.operation_mode = operation_mode
        self.game_filter = game_filter
        self.tag_filter = tag_filter
        self.max_actions_per_game = max_actions_per_game
        self.holdout_ratio = holdout_ratio
        self.shuffle = shuffle
        self.seed = seed
        self._cache: dict[str, list[dict]] = {}
        self._split_done = False

    def get_tasks(self, split: str = "train", limit: int = 10) -> list[Task]:
        """Return game tasks from the ARC-AGI-3 arcade.

        Each Task represents a game. The agent's solve() method should
        play through the game using the arc-agi toolkit.
        """
        rows = self._load_split(split)
        tasks = []
        for row in rows[:limit]:
            game_id = row["game_id"]
            tasks.append(Task(
                id=game_id,
                input=self._build_task_prompt(row),
                metadata={
                    "game_id": game_id,
                    "title": row.get("title", game_id),
                    "tags": row.get("tags", []),
                    "max_actions": self.max_actions_per_game,
                    "operation_mode": self.operation_mode,
                    "api_key": self.api_key,
                },
            ))
        return tasks

    def evaluate(self, task: Task, trajectory: Trajectory) -> Feedback:
        """Evaluate the agent's game performance from its trajectory.

        The trajectory.output should be a JSON string containing the game
        result (levels completed, actions taken, etc.). The trajectory.steps
        contain per-action traces for the evolver.
        """
        game_id = task.id
        result = self._parse_game_result(game_id, trajectory)

        if result is None:
            return Feedback(
                success=False,
                score=0.0,
                detail=f"Failed to parse game result for {game_id}. "
                       f"Output: {trajectory.output[:500]}",
                raw={"game_id": game_id, "reason": "parse_error"},
            )

        # Compute score based on completion and efficiency
        score = result.score
        success = result.game_completed

        # Build rich diagnostic detail for the evolver
        detail_parts = [
            f"Game: {game_id} ({task.metadata.get('title', '')})",
            f"Result: {'COMPLETED' if result.game_completed else 'INCOMPLETE'}",
            f"Levels: {result.levels_completed}/{result.total_levels}",
            f"Total actions: {result.total_actions}",
            f"Score (RHAE): {score:.3f}",
        ]

        if result.per_level_actions:
            detail_parts.append("Per-level actions: " +
                                ", ".join(str(a) for a in result.per_level_actions))

        # Include action pattern analysis for the evolver
        if trajectory.steps:
            action_counts = self._count_actions(trajectory.steps)
            if action_counts:
                detail_parts.append("Action distribution: " +
                                    ", ".join(f"{k}={v}" for k, v in
                                              sorted(action_counts.items(),
                                                     key=lambda x: -x[1])))

            # Identify potential inefficiencies
            inefficiencies = self._detect_inefficiencies(trajectory.steps)
            if inefficiencies:
                detail_parts.append("Inefficiencies detected:")
                for ineff in inefficiencies[:5]:
                    detail_parts.append(f"  - {ineff}")

        detail = "\n".join(detail_parts)

        return Feedback(
            success=success,
            score=score,
            detail=detail,
            raw={
                "game_id": game_id,
                "levels_completed": result.levels_completed,
                "total_levels": result.total_levels,
                "total_actions": result.total_actions,
                "game_completed": result.game_completed,
                "per_level_actions": result.per_level_actions,
            },
        )

    # ── Task prompt construction ─────────────────────────────────────

    @staticmethod
    def _build_task_prompt(row: dict) -> str:
        """Build the task prompt describing the game to solve."""
        game_id = row["game_id"]
        title = row.get("title", game_id)
        tags = row.get("tags", [])

        prompt = f"""\
Play the ARC-AGI-3 game: {title} (ID: {game_id})

This is an interactive game where you must complete all levels by taking actions.
You will observe the game state as a grid and must choose actions to progress.

Game tags: {', '.join(tags) if tags else 'none'}

Available actions:
- ACTION1 through ACTION4: Directional inputs (typically up/down/left/right)
- ACTION5: Context-dependent interaction (select, rotate, execute)
- ACTION6: Coordinate-based targeting (requires x,y position)
- ACTION7: Undo (if supported by this game)
- RESET: Restart the current level

Your goal is to complete all levels as efficiently as possible (fewest actions).
Use the game tools to observe the state and take actions.
"""
        return prompt

    # ── Result parsing ───────────────────────────────────────────────

    @staticmethod
    def _parse_game_result(game_id: str, trajectory: Trajectory) -> GameResult | None:
        """Parse the game result from trajectory output."""
        try:
            # Try JSON parse first
            data = json.loads(trajectory.output)
            return GameResult(
                game_id=game_id,
                levels_completed=data.get("levels_completed", 0),
                total_levels=data.get("total_levels", 1),
                total_actions=data.get("total_actions", 0),
                game_completed=data.get("game_completed", False),
                per_level_actions=data.get("per_level_actions", []),
                score=data.get("score", 0.0),
                raw_info=data,
            )
        except (json.JSONDecodeError, TypeError):
            pass

        # Fallback: try to extract from steps
        if trajectory.steps:
            last_step = trajectory.steps[-1] if trajectory.steps else {}
            return GameResult(
                game_id=game_id,
                levels_completed=last_step.get("levels_completed", 0),
                total_levels=last_step.get("total_levels", 1),
                total_actions=len([s for s in trajectory.steps
                                   if s.get("type") == "action"]),
                game_completed=last_step.get("game_completed", False),
                per_level_actions=last_step.get("per_level_actions", []),
                score=last_step.get("score", 0.0),
                raw_info=last_step,
            )

        return None

    # ── Analysis helpers for evolver feedback ────────────────────────

    @staticmethod
    def _count_actions(steps: list[dict]) -> dict[str, int]:
        """Count action frequencies from trajectory steps."""
        counts: dict[str, int] = {}
        for step in steps:
            action = step.get("action", "")
            if action:
                counts[action] = counts.get(action, 0) + 1
        return counts

    @staticmethod
    def _detect_inefficiencies(steps: list[dict]) -> list[str]:
        """Detect common inefficiency patterns in the action sequence."""
        issues = []
        actions = [s.get("action", "") for s in steps if s.get("action")]

        if not actions:
            return issues

        # Detect oscillation (back-and-forth)
        opposites = {
            "ACTION1": "ACTION2", "ACTION2": "ACTION1",
            "ACTION3": "ACTION4", "ACTION4": "ACTION3",
        }
        oscillations = 0
        for i in range(len(actions) - 1):
            if opposites.get(actions[i]) == actions[i + 1]:
                oscillations += 1
        if oscillations > 3:
            issues.append(f"Oscillation detected: {oscillations} back-and-forth action pairs")

        # Detect excessive resets
        resets = actions.count("RESET")
        if resets > 2:
            issues.append(f"Excessive resets: {resets} (suggests confusion about game mechanics)")

        # Detect long runs of same action
        max_run = 1
        current_run = 1
        run_action = ""
        for i in range(1, len(actions)):
            if actions[i] == actions[i - 1]:
                current_run += 1
                if current_run > max_run:
                    max_run = current_run
                    run_action = actions[i]
            else:
                current_run = 1
        if max_run > 10:
            issues.append(f"Repeated action: {run_action} x{max_run} consecutive times")

        # Detect high action count without progress
        if len(actions) > 100:
            progress_steps = [s for s in steps if s.get("level_changed")]
            if not progress_steps:
                issues.append(f"No level progress after {len(actions)} actions")

        return issues

    # ── Dataset loading & splitting ──────────────────────────────────

    def _load_split(self, split: str) -> list[dict]:
        if not self._split_done:
            self._do_split()
        if split in self._cache:
            return self._cache[split]
        return self._cache.get("train", [])

    def _do_split(self) -> None:
        """Load available games from the ARC-AGI-3 arcade and split."""
        games = self._discover_games()

        if self.game_filter:
            games = [g for g in games if self.game_filter in g["game_id"]]
        if self.tag_filter:
            games = [g for g in games
                     if self.tag_filter in g.get("tags", [])]

        if self.shuffle:
            random.Random(self.seed).shuffle(games)

        n_holdout = max(1, int(len(games) * self.holdout_ratio))
        self._cache["holdout"] = games[:n_holdout]
        self._cache["train"] = games[n_holdout:]
        self._cache["test"] = games

        self._split_done = True
        logger.info(
            "Loaded %d ARC-AGI-3 games (train=%d, holdout=%d)",
            len(games), len(self._cache["train"]), len(self._cache["holdout"]),
        )

    def _discover_games(self) -> list[dict]:
        """Discover available games from the arc-agi toolkit."""
        try:
            import arc_agi

            mode_map = {
                "normal": None,
                "offline": "OFFLINE",
                "online": "ONLINE",
                "competition": "COMPETITION",
            }

            kwargs: dict[str, Any] = {}
            if self.api_key:
                kwargs["arc_api_key"] = self.api_key

            op_mode = mode_map.get(self.operation_mode)
            if op_mode:
                from arc_agi import OperationMode
                kwargs["operation_mode"] = getattr(OperationMode, op_mode)

            arcade = arc_agi.Arcade(**kwargs)
            env_list = arcade.get_environments()

            games = []
            for env_info in env_list:
                game = {
                    "game_id": env_info.game_id if hasattr(env_info, "game_id") else str(env_info),
                    "title": getattr(env_info, "title", str(env_info)),
                    "tags": getattr(env_info, "tags", []),
                }
                games.append(game)

            logger.info("Discovered %d games from ARC-AGI-3 arcade", len(games))
            return games

        except ImportError:
            logger.warning(
                "arc-agi package not installed. Install with: pip install arc-agi"
            )
            return self._load_fallback_games()

    @staticmethod
    def _load_fallback_games() -> list[dict]:
        """Return a small set of known game IDs for offline/testing use."""
        known_games = [
            {"game_id": "ls20", "title": "LS-20", "tags": ["reasoning"]},
            {"game_id": "ft09", "title": "FT-09", "tags": ["logic"]},
            {"game_id": "vc33", "title": "VC-33", "tags": ["orchestration"]},
        ]
        logger.info("Using %d fallback games (arc-agi not installed)", len(known_games))
        return known_games
