"""MASArcAgent -- Multi-Agent System for ARC-AGI-3.

Uses a hierarchical orchestrator → subagent pattern with wiki-style
structured knowledge (append-only critical pages). Implements the
a-evolve BaseAgent protocol for integration with evolution engines.

Workspace layout:
  prompts/system.md       — orchestrator base prompt (evolvable)
  prompts/explorer.md     — explorer role prompt (evolvable)
  prompts/theorist.md     — theorist role prompt (evolvable)
  prompts/solver.md       — solver role prompt (evolvable)
  prompts/game_reference.md — game rules reference (evolvable)
  tools/orchestrator.yaml — orchestrator tool descriptions (evolvable)
  tools/explorer.yaml     — explorer tool descriptions (evolvable)
  tools/theorist.yaml     — theorist tool descriptions (evolvable)
  tools/solver.yaml       — solver tool descriptions (evolvable)
  skills/                 — learned strategies (lazy-loaded via read_skill)

Best result: 17.47% RHAE on ARC-AGI-3 (wiki_v2 configuration).
"""

from __future__ import annotations

import json
import logging
import time
import yaml
from collections import deque
from pathlib import Path
from typing import Any

from ...protocol.base_agent import BaseAgent
from ...types import Task, Trajectory
from .bedrock_agent import BedrockAgent
from .bedrock_tools import build_orchestrator_tools, build_subagent_tools
from .frame import Frame
from .memories import Memories
from .wiki import GameWiki

logger = logging.getLogger(__name__)


def _convert_frame_data(raw) -> tuple[Frame, dict]:
    """Convert arcengine FrameDataRaw/FrameData to our Frame + metadata."""
    if raw is None:
        raise ValueError("Received None frame data from environment")

    if hasattr(raw, "frame"):
        grid = raw.frame[-1] if isinstance(raw.frame, list) else raw.frame
        if hasattr(grid, "tolist"):
            grid = grid.tolist()
        elif isinstance(grid, (list, tuple)) and len(grid) > 0 and hasattr(grid[0], "tolist"):
            grid = [row.tolist() for row in grid]
    else:
        grid = [[0] * 64 for _ in range(64)]

    from arcengine import GameAction as GA

    state = getattr(raw, "state", None)
    avail = getattr(raw, "available_actions", [])
    avail_names = []
    for a in avail:
        try:
            avail_names.append(GA.from_id(a).name)
        except Exception:
            avail_names.append(str(a))

    meta = {
        "levels_completed": getattr(raw, "levels_completed", 0),
        "win_levels": getattr(raw, "win_levels", 0),
        "state": state,
        "state_name": state.name if state else "UNKNOWN",
        "available_actions": avail,
        "available_actions_names": avail_names,
        "game_id": getattr(raw, "game_id", ""),
    }

    # Store metadata ON the frame so tools can access it
    frame = Frame(grid, **meta)
    return frame, meta


class MASArcAgent(BaseAgent):
    """Multi-Agent System agent for ARC-AGI-3.

    Implements the orchestrator → explorer/theorist/solver pattern
    with wiki-style structured knowledge (append-only for critical pages).
    """

    MAX_ACTIONS: int = 350

    def __init__(
        self,
        workspace_dir: str | Path,
        model_id: str = "<evolver-model-id>",
        region: str = "us-west-2",
        max_actions: int = 350,
        use_wiki: bool = True,
        thinking_effort: str = "",
        log_dir: str | Path = "mas_logs",
    ):
        # Init these before super().__init__ because reload_from_fs() is called there
        self._role_prompts: dict[str, str] = {}
        self._game_reference: str = ""
        self._tool_descriptions: dict[str, dict[str, str]] = {}
        self._skill_bodies: dict[str, str] = {}

        self.model_id = model_id
        self.region = region
        self.max_actions = max_actions
        self.use_wiki = use_wiki
        self.thinking_effort = thinking_effort
        self.log_dir = Path(log_dir)

        super().__init__(workspace_dir)  # calls reload_from_fs → _load_workspace_config

    def _load_workspace_config(self) -> None:
        """Load role prompts, game reference, tool descriptions, and skills from workspace."""
        root = self.workspace.root

        # Role prompts
        for role in ["explorer", "theorist", "solver"]:
            path = root / "prompts" / f"{role}.md"
            if path.exists():
                self._role_prompts[role] = path.read_text().strip()

        # Game reference
        game_ref_path = root / "prompts" / "game_reference.md"
        if game_ref_path.exists():
            self._game_reference = game_ref_path.read_text().strip()

        # Tool descriptions per role
        tools_dir = root / "tools"
        if tools_dir.exists():
            for yaml_file in tools_dir.glob("*.yaml"):
                role = yaml_file.stem  # orchestrator, explorer, theorist, solver
                try:
                    data = yaml.safe_load(yaml_file.read_text())
                    if data and "tools" in data:
                        self._tool_descriptions[role] = {
                            name: info.get("description", "")
                            for name, info in data["tools"].items()
                        }
                except Exception:
                    pass

        # Lazy-load skill bodies (like SWE agent pattern)
        self._skill_bodies = {}
        for skill in self.skills:
            content = self.get_skill_content(skill.name)
            if content:
                # Extract body after YAML frontmatter
                parts = content.split("---", 2)
                body = parts[-1].strip() if len(parts) >= 3 else content.strip()
                self._skill_bodies[skill.name] = body

        logger.info(
            "MAS workspace loaded: roles=%s, game_ref=%d chars, tool_configs=%s, skills=%d",
            list(self._role_prompts.keys()),
            len(self._game_reference),
            list(self._tool_descriptions.keys()),
            len(self._skill_bodies),
        )

    def reload_from_fs(self) -> None:
        """Reload from workspace (called by evolution loop)."""
        super().reload_from_fs()
        self._load_workspace_config()

    def _get_role_prompt(self, role: str) -> str:
        """Get the full prompt for a subagent role."""
        parts = []

        # Role-specific prompt from workspace
        role_prompt = self._role_prompts.get(role, "")
        if role_prompt:
            parts.append(role_prompt)

        # Skill listing (lazy load pattern)
        if self._skill_bodies:
            parts.append("\n## Available Skills")
            parts.append("Use read_skill(skill_name) to load a skill's full procedure.")
            for skill in self.skills:
                parts.append(f"- **{skill.name}**: {skill.description}")

        # Game reference
        if self._game_reference:
            parts.append(f"\n{self._game_reference}")

        # Wiki knowledge instructions
        if self.use_wiki:
            parts.append(self._wiki_instructions())
        else:
            parts.append(self._flat_instructions())

        return "\n\n".join(parts)

    def _wiki_instructions(self) -> str:
        return """Knowledge Wiki:
  Pages: game_rules, breakthroughs, colors, current_level, current_plan,
  solved_levels, failed_attempts, level_changes.
  Most are APPEND-ONLY (game_rules, breakthroughs, solved_levels, failed_attempts, level_changes).
  Only colors, current_level, current_plan are overwritable.
  Tools: wiki_index(), wiki_read(page), wiki_write(page, content), wiki_append(page, content).
  Before starting: wiki_index() then wiki_read on relevant pages."""

    def _flat_instructions(self) -> str:
        return """Shared Memory:
  Use memories_summaries() to see what's known.
  Use memories_get(index) to read details.
  Use memories_add(summary, details) to store insights.
  Before starting: check memories_summaries()."""

    def _build_read_skill_tool(self) -> tuple[dict, callable] | None:
        """Build read_skill tool if skills are available."""
        if not self._skill_bodies:
            return None

        skill_data = dict(self._skill_bodies)

        def handle_read_skill(skill_name: str) -> str:
            if skill_name in skill_data:
                return skill_data[skill_name]
            available = ", ".join(skill_data.keys())
            return f"Skill '{skill_name}' not found. Available: {available}"

        spec = {
            "toolSpec": {
                "name": "read_skill",
                "description": "Read the full procedure for a learned skill. "
                    "Check the skills list in the system prompt for available skills.",
                "inputSchema": {
                    "json": {
                        "type": "object",
                        "properties": {
                            "skill_name": {
                                "type": "string",
                                "description": "Name of the skill to read",
                            },
                        },
                        "required": ["skill_name"],
                    }
                },
            }
        }
        return spec, handle_read_skill

    def solve(self, task: Task) -> Trajectory:
        """Play an ARC-AGI-3 game using the multi-agent orchestrator."""
        game_id = task.metadata.get("game_id", task.id)
        max_actions = task.metadata.get("max_actions", self.max_actions)

        logger.info("MAS playing game: %s (budget: %d)", game_id, max_actions)
        start_time = time.time()

        try:
            result = self._play_game(task, game_id, max_actions)
            elapsed = time.time() - start_time
            result["elapsed_sec"] = round(elapsed, 1)
            logger.info(
                "MAS finished %s: %d levels, %d actions, %.0fs",
                game_id, result["levels_completed"], result["total_actions"], elapsed,
            )
            return Trajectory(
                task_id=task.id,
                output=json.dumps(result),
                steps=result.get("steps", []),
            )
        except Exception as e:
            logger.error("MAS game %s failed: %s", game_id, e, exc_info=True)
            return Trajectory(
                task_id=task.id,
                output=json.dumps({
                    "game_id": game_id, "error": str(e),
                    "levels_completed": 0, "total_levels": 0, "total_actions": 0,
                }),
                steps=[{"error": str(e)}],
            )

    def play_game_on_env(self, env, game_id: str, max_actions: int) -> dict:
        """Play a game on a pre-created environment.

        Used by competition mode where a single Arcade instance and scorecard
        are shared across all games, and make() can only be called once per game.
        """
        return self._play_game_impl(env, game_id, max_actions)

    def _play_game(self, task: Task, game_id: str, max_actions: int) -> dict:
        """Run the multi-agent orchestrator on a single game (creates own env)."""
        from arc_agi import Arcade

        arcade = Arcade()
        env = arcade.make(game_id)
        return self._play_game_impl(env, game_id, max_actions)

    def _play_game_impl(self, env, game_id: str, max_actions: int) -> dict:
        """Core game implementation."""
        from arcengine import GameAction, GameState

        # Setup logging directory
        game_log_dir = self.log_dir / game_id
        game_log_dir.mkdir(parents=True, exist_ok=True)
        action_log_path = game_log_dir / "actions.jsonl"
        agent_trace_path = game_log_dir / "agent_trace.jsonl"
        action_log_file = open(action_log_path, "w")
        agent_trace_file = open(agent_trace_path, "w")

        def _log_action(entry: dict) -> None:
            action_log_file.write(json.dumps(entry) + "\n")
            action_log_file.flush()

        def _log_agent_event(entry: dict) -> None:
            import time as _t
            entry["ts"] = _t.strftime("%H:%M:%S")
            agent_trace_file.write(json.dumps(entry) + "\n")
            agent_trace_file.flush()

        # State tracking
        action_counter = 0
        steps = []
        last_frame: Frame | None = None
        prev_frame: Frame | None = None
        last_available: list[int] = []
        has_moves_since_reset = False
        action_history: deque[tuple[str, Frame]] = deque(maxlen=50)
        current_meta: dict = {}

        def submit_action(action_name: str, x: int = 0, y: int = 0) -> Frame:
            nonlocal action_counter, last_frame, prev_frame, last_available, has_moves_since_reset, current_meta

            upper = action_name.upper()
            if upper == "NOOP":
                assert last_frame is not None
                return last_frame

            if upper != "RESET" and action_counter >= max_actions:
                raise ValueError(f"Game action limit reached ({max_actions}).")

            action = GameAction.from_name(action_name)
            if last_available and action is not GameAction.RESET and action.value not in last_available:
                allowed = [GameAction.from_id(a).name for a in last_available]
                raise ValueError(f"{action.name} not available. Available: {allowed}")

            if action is GameAction.RESET and not has_moves_since_reset and last_frame is not None:
                return last_frame

            if action.is_complex():
                action.set_data({"x": x, "y": y})

            raw = env.step(action, data=action.action_data.model_dump(), reasoning={})
            if raw is None:
                raise RuntimeError(f"{action.name} returned no frame data.")

            frame, meta = _convert_frame_data(raw)
            current_meta = meta
            last_available = meta["available_actions"]

            if action is not GameAction.RESET:
                action_counter += 1
            has_moves_since_reset = action is not GameAction.RESET

            prev_frame = last_frame
            last_frame = frame
            action_history.append((action.name, frame))

            step_entry = {
                "action": action.name, "count": action_counter,
                "level": meta["levels_completed"],
                "win_levels": meta["win_levels"],
                "state": meta["state"].name if meta["state"] else "UNKNOWN",
                "available_actions": [GameAction.from_id(a).name for a in meta["available_actions"]],
                "color_counts": frame.color_counts(),
                "grid": [list(row) for row in frame.grid],
            }
            if action.is_complex():
                step_entry["x"] = x
                step_entry["y"] = y
            steps.append(step_entry)
            _log_action(step_entry)

            logger.info("%s - %s: count %d, level %d/%d",
                game_id, action.name, action_counter,
                meta["levels_completed"], meta["win_levels"])
            return frame

        def history(n: int = 50, wins_only: bool = False) -> list[tuple[str, Frame]]:
            return list(action_history)[-n:]

        # Double RESET
        raw = env.step(GameAction.RESET, data=GameAction.RESET.action_data.model_dump(), reasoning={})
        initial_frame, initial_meta = _convert_frame_data(raw)
        current_meta = initial_meta
        initial_frame_from_submit = submit_action("RESET")

        # Knowledge system (shared memory is hardcoded, not from workspace)
        # Wiki writes live to game_log_dir so dashboard can read it
        wiki = GameWiki(game_id=game_id, log_dir=str(game_log_dir)) if self.use_wiki else Memories()

        frame_ref: list[Frame | None] = [initial_frame_from_submit, None]
        subagents: dict[str, BedrockAgent] = {}
        agent_counter = [0]

        # Build read_skill tool (available to all agents if skills exist)
        skill_tool = self._build_read_skill_tool()

        def spawn_and_run_subagent(
            role: str, system_prompt: str, task: str,
            action_budget: int | None = None, give_submit_action: bool = True,
        ) -> str:
            agent_counter[0] += 1
            agent_id = f"{role}_{agent_counter[0]}"

            # Build prompt: orchestrator's briefing + workspace role prompt + game reference
            role_key = role.lower().split("_")[0]  # "explorer_retry" -> "explorer"
            # Map unknown roles to closest match
            if role_key not in self._role_prompts:
                if "explor" in role_key: role_key = "explorer"
                elif "theor" in role_key: role_key = "theorist"
                else: role_key = "solver"

            workspace_role_prompt = self._get_role_prompt(role_key)
            full_system = f"You are a {role} subagent for an ARC-AGI-3 game.\n\n{system_prompt}\n\n{workspace_role_prompt}"

            # Build tools (unique per role based on give_submit_action)
            sa_fn = submit_action if give_submit_action else None
            sub_tools, sub_handlers = build_subagent_tools(
                sa_fn, action_budget, history, wiki, frame_ref,
            )

            # Add read_skill tool if skills available
            if skill_tool:
                spec, handler = skill_tool
                sub_tools.append(spec)
                sub_handlers["read_skill"] = handler

            agent = BedrockAgent(
                model_id=self.model_id,
                system_prompt=full_system,
                tools=sub_tools,
                tool_handlers=sub_handlers,
                agent_id=agent_counter[0],
                game_id=game_id,
                thinking_effort=self.thinking_effort,
                max_tokens=65536 if self.thinking_effort else 16384,
            )
            subagents[agent_id] = agent

            logger.info("%s - Spawning %s (submit=%s, budget=%s)",
                game_id, agent_id, give_submit_action, action_budget)
            _log_agent_event({
                "event": "spawn", "agent_id": agent_id, "role": role,
                "role_key": role_key, "budget": action_budget,
                "give_submit_action": give_submit_action,
            })

            result = agent.call(task)

            _log_agent_event({
                "event": "finish", "agent_id": agent_id,
                "input_tokens": agent.total_input_tokens,
                "output_tokens": agent.total_output_tokens,
                "cache_read_tokens": agent.total_cache_read_tokens,
                "cache_write_tokens": agent.total_cache_write_tokens,
            })
            logger.info("%s - %s finished. %s", game_id, agent_id, agent.usage_summary)
            return f"[Agent {agent_id} completed]\n{result}"

        def call_existing_agent(
            agent_id: str, task: str, action_budget: int | None = None,
        ) -> str:
            agent = subagents.get(agent_id)
            if agent is None:
                return f"ERROR: No agent '{agent_id}'. Available: {list(subagents.keys())}"
            if action_budget is not None:
                sub_tools, sub_handlers = build_subagent_tools(
                    submit_action, action_budget, history, wiki, frame_ref,
                )
                if skill_tool:
                    spec, handler = skill_tool
                    sub_tools.append(spec)
                    sub_handlers["read_skill"] = handler
                agent.tools = sub_tools
                agent.tool_handlers = sub_handlers
            logger.info("%s - Calling %s (budget=%s)", game_id, agent_id, action_budget)
            _log_agent_event({
                "event": "call", "agent_id": agent_id, "budget": action_budget,
            })

            result = agent.call(task)

            _log_agent_event({
                "event": "call_finish", "agent_id": agent_id,
                "input_tokens": agent.total_input_tokens,
                "output_tokens": agent.total_output_tokens,
                "cache_read_tokens": agent.total_cache_read_tokens,
                "cache_write_tokens": agent.total_cache_write_tokens,
            })
            logger.info("%s - %s responded. %s", game_id, agent_id, agent.usage_summary)
            return f"[Agent {agent_id} responded]\n{result}"

        # Build orchestrator
        orch_tools, orch_handlers = build_orchestrator_tools(
            spawn_fn=spawn_and_run_subagent,
            call_fn=call_existing_agent,
            wiki=wiki,
            history_fn=history,
            frame_ref=frame_ref,
        )
        if skill_tool:
            spec, handler = skill_tool
            orch_tools.append(spec)
            orch_handlers["read_skill"] = handler

        # Orchestrator system prompt from workspace
        from .bedrock_prompts import premise
        orchestrator_prompt = premise(use_wiki=self.use_wiki)
        if self.system_prompt:
            orchestrator_prompt = f"{self.system_prompt}\n\n{orchestrator_prompt}"

        orchestrator = BedrockAgent(
            model_id=self.model_id,
            system_prompt=orchestrator_prompt,
            tools=orch_tools,
            tool_handlers=orch_handlers,
            agent_id=0,
            game_id=game_id,
            thinking_effort=self.thinking_effort,
            max_tokens=65536 if self.thinking_effort else 16384,
        )

        # Task message
        avail = []
        for a in initial_meta.get("available_actions", []):
            try:
                avail.append(GameAction.from_id(a).name)
            except Exception:
                avail.append(str(a))
        actions_str = ", ".join(avail) if avail else "ACTION1-ACTION6, RESET"

        knowledge_msg = (
            "You have a shared wiki (wiki_index, wiki_read, wiki_write, wiki_append)."
            if self.use_wiki else
            "You have shared memory tools (memories_add, memories_summaries, memories_get)."
        )

        task_msg = (
            f"You are playing `{game_id}`. "
            f"Level {initial_meta['levels_completed']}/{initial_meta['win_levels']}. "
            f"Available actions: {actions_str}\n\n"
            f"{knowledge_msg}\n\n"
            f"Plan your approach, then spawn an explorer."
        )

        logger.info("%s - Starting orchestrator", game_id)
        _log_agent_event({"event": "orchestrator_start"})

        try:
            orchestrator.call(task_msg)
        finally:
            _log_agent_event({
                "event": "orchestrator_finish",
                "input_tokens": orchestrator.total_input_tokens,
                "cache_read_tokens": orchestrator.total_cache_read_tokens,
                "cache_write_tokens": orchestrator.total_cache_write_tokens,
                "output_tokens": orchestrator.total_output_tokens,
            })

            # Dump wiki/memory state
            knowledge_path = game_log_dir / "knowledge.json"
            if isinstance(wiki, GameWiki):
                knowledge_path.write_text(json.dumps({
                    "type": "wiki",
                    "game_id": game_id,
                    "pages": {
                        name: content
                        for name, content in wiki._pages.items()
                    },
                    "history": wiki._history,
                }, indent=2))
            else:
                entries = []
                for i, m in enumerate(wiki._stack):
                    entries.append({
                        "index": i, "summary": m.summary,
                        "details": m.details, "source": m.source,
                    })
                knowledge_path.write_text(json.dumps({
                    "type": "flat", "game_id": game_id,
                    "memories": entries,
                }, indent=2))

            # Close log files
            action_log_file.close()
            agent_trace_file.close()

            logger.info("%s - Logs saved to %s", game_id, game_log_dir)

        return {
            "game_id": game_id,
            "levels_completed": current_meta.get("levels_completed", 0),
            "total_levels": current_meta.get("win_levels", 0),
            "total_actions": action_counter,
            "game_completed": current_meta.get("state") is not None
                and hasattr(current_meta["state"], "name")
                and current_meta["state"].name == "WIN",
            "agents_spawned": agent_counter[0],
            "steps": steps,
        }
