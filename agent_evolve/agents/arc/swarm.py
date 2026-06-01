"""Swarm orchestrator for ARC-AGI-3.

Adapted from arcprize/ARC-AGI-3-Agents (agents/swarm.py).
Manages scorecards, spawns one agent thread per game, and collects results.
Integrates with a-evolve's BaseAgent.solve() interface.
"""

from __future__ import annotations

import json
import logging
from threading import Thread
from typing import Any, Callable, Optional

import arc_agi
from arc_agi import Arcade, OperationMode
from arc_agi.scorecard import EnvironmentScorecard

logger = logging.getLogger(__name__)


class Swarm:
    """Orchestration for many agents playing many ARC-AGI-3 games.

    Follows the arcprize/ARC-AGI-3-Agents Swarm pattern:
    - Opens a single scorecard
    - Creates one environment per game (all sharing the scorecard)
    - Runs agent functions in parallel threads
    - Closes scorecard and reports results

    Usage::

        swarm = Swarm(
            arcade=arc_agi.Arcade(),
            games=["sb26-xxx", "r11l-yyy"],
            tags=["experiment-1"],
        )
        results = swarm.run(play_fn)

    Where ``play_fn(env, game_id) -> dict`` is called once per game in its own thread.
    """

    def __init__(
        self,
        arcade: Arcade | None = None,
        api_key: str | None = None,
        operation_mode: str = "normal",
        games: list[str] | None = None,
        tags: list[str] | None = None,
    ):
        # Initialize arcade
        kwargs: dict[str, Any] = {}
        if api_key:
            kwargs["arc_api_key"] = api_key

        mode_map = {
            "normal": None,
            "offline": OperationMode.OFFLINE,
            "online": OperationMode.ONLINE,
            "competition": OperationMode.COMPETITION,
        }
        op_mode = mode_map.get(operation_mode)
        if op_mode:
            kwargs["operation_mode"] = op_mode

        self._arcade = arcade or Arcade(**kwargs)
        self.tags = list(tags or [])
        self.games = games or []
        self.card_id: str | None = None
        self._scorecard: EnvironmentScorecard | None = None

    def discover_games(self, game_filter: str | None = None) -> list[str]:
        """Fetch available games from the arcade API."""
        envs = self._arcade.get_environments()
        game_ids = [e.game_id for e in envs]
        if game_filter:
            game_ids = [g for g in game_ids if game_filter in g]
        self.games = game_ids
        logger.info("Discovered %d games", len(game_ids))
        return game_ids

    def run(
        self,
        play_fn: Callable[[Any, str], dict],
        max_parallel: int | None = None,
    ) -> tuple[list[dict], EnvironmentScorecard | None]:
        """Run all games with the given play function.

        Args:
            play_fn: Called as play_fn(env, game_id) -> result_dict.
                Each call runs in its own thread.
            max_parallel: Max concurrent threads. None = all at once.

        Returns:
            (list of result dicts, scorecard or None)
        """
        if not self.games:
            logger.warning("No games to play")
            return [], None

        # Open scorecard
        self.card_id = self._arcade.open_scorecard(tags=self.tags)
        logger.info("Opened scorecard: %s", self.card_id)

        # Create environments (one per game, all sharing scorecard)
        envs = {}
        for game_id in self.games:
            try:
                env = self._arcade.make(game_id, scorecard_id=self.card_id)
                envs[game_id] = env
            except Exception as e:
                logger.error("Failed to create env for %s: %s", game_id, e)

        # Run agents in threads
        results: dict[str, dict] = {}

        def _worker(game_id: str, env: Any) -> None:
            try:
                result = play_fn(env, game_id)
                results[game_id] = result
            except Exception as e:
                logger.error("Agent failed on %s: %s", game_id, e)
                results[game_id] = {"game_id": game_id, "error": str(e)}

        threads = []
        for game_id, env in envs.items():
            t = Thread(target=_worker, args=(game_id, env), daemon=True)
            threads.append(t)

        # Start threads (optionally in batches)
        if max_parallel and max_parallel < len(threads):
            # Run in batches
            for i in range(0, len(threads), max_parallel):
                batch = threads[i:i + max_parallel]
                for t in batch:
                    t.start()
                for t in batch:
                    t.join()
        else:
            # All at once
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        # Close scorecard
        scorecard = None
        if self.card_id:
            try:
                scorecard = self._arcade.close_scorecard(self.card_id)
                self._scorecard = scorecard
                if scorecard:
                    logger.info("--- SCORECARD ---")
                    logger.info(json.dumps(scorecard.model_dump(), indent=2))
            except Exception as e:
                logger.error("Failed to close scorecard: %s", e)
            self.card_id = None

        # Return results in game order
        ordered = [results.get(g, {"game_id": g, "error": "not run"}) for g in self.games]
        return ordered, scorecard

    def cleanup(self) -> None:
        """Close scorecard if still open."""
        if self.card_id:
            try:
                self._arcade.close_scorecard(self.card_id)
            except Exception:
                pass
            self.card_id = None
