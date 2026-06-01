"""Orchestrator + sub-agent pattern for ARC-AGI-3.

Adapted from symbolica-ai/ARC-AGI-3-Agents (Arcgentica).

Architecture:
- Orchestrator manages game-level strategy across levels
- Sub-agents are spawned with bounded action budgets for specific tasks:
  - Explorer: discover game mechanics through experimentation
  - Solver: execute a strategy to complete the current level
- Shared Memories persist insights across all agents
- Fresh sub-agents prevent context rot

Each sub-agent is a fresh Bedrock conversation with:
- The game's current state
- Relevant memories from the shared database
- A bounded action budget (submit_action can only be called N times)
- A specific role/objective
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Callable

from .colors import COLOR_LEGEND, COLOR_NAMES
from .frame import Frame
from .memories import Memories
from .repl import PersistentREPL

logger = logging.getLogger(__name__)


class SubAgent:
    """A fresh LLM conversation with a bounded action budget.

    Each sub-agent gets:
    - Its own message history (no context rot from other agents)
    - A bounded number of actions it can take
    - Access to shared memories
    - A specific role and objective
    """

    def __init__(
        self,
        client: Any,
        model_id: str,
        max_tokens: int,
        role: str,
        objective: str,
        action_budget: int,
        memories: Memories,
        level: int = 0,
        max_code_calls: int = 10,
    ):
        self.client = client
        self.model_id = model_id
        self.max_tokens = max_tokens
        self.role = role
        self.objective = objective
        self.action_budget = action_budget
        self.actions_taken = 0
        self.memories = memories
        self.level = level
        self.max_code_calls = max_code_calls
        self._code_calls = 0
        self._messages: list[dict] = []
        self._total_input_tokens = 0
        self._total_output_tokens = 0
        self.repl = PersistentREPL()

    @property
    def budget_remaining(self) -> int:
        return max(0, self.action_budget - self.actions_taken)

    @property
    def budget_exhausted(self) -> bool:
        return self.actions_taken >= self.action_budget

    def build_system_prompt(self, workspace_prompt: str) -> str:
        """Build the sub-agent's system prompt."""
        parts = [workspace_prompt]

        parts.append(f"\n\n## Your Role: {self.role}")
        parts.append(f"Objective: {self.objective}")
        parts.append(f"Action budget: {self.action_budget} actions (use them wisely)")

        # Python REPL instructions
        parts.append(f"""

## Python REPL (CRITICAL -- use this!)

You have a persistent Python REPL. Write code to analyze the grid BEFORE choosing actions.
Code execution does NOT cost action budget. You get {self.max_code_calls} code calls.

To execute code, respond with a ```python block:
```python
# Find all distinct colored regions
colors = frame.color_counts()
print("Colors:", colors)

# Find red pixels and their bounding box
red_pixels = frame.find(8)
print(f"Red: {{len(red_pixels)}} pixels at {{red_pixels[:5]}}")
bbox = frame.bounding_box(8)
print(f"Red bbox: {{bbox}}")

# Compare with previous frame
if prev_frame:
    diff = frame.diff(prev_frame)
    for region in diff:
        print(f"Changed: {{region}}")
```

Pre-loaded variables (updated each step):
- `frame`: Current Frame object with .grid, .grid_np, .render(), .diff(), .find(), .color_counts(), .bounding_box()
- `grid`: numpy int8 array of current grid (64x64)
- `prev_frame`: Previous frame (or None)
- `frames`: List of all frames
- `meta`: Game metadata dict
- `np`: numpy
- State persists between code calls (variables you define are kept)

To take a game action, respond with JSON (costs 1 action):
{{"action": "ACTION1", "reasoning": "why"}}

STRATEGY: Always analyze with code first, then act. Code is free, actions are expensive.""")

        # Include shared memories
        mem_text = self.memories.format_for_prompt(max_entries=15)
        if len(self.memories) > 0:
            parts.append(f"\n\n## Shared Knowledge from Other Agents\n{mem_text}")
            parts.append(
                "\nBefore exploring, check if another agent already discovered "
                "what you need. Add new findings with CONFIRMED: or HYPOTHESIS: prefix."
            )

        return "\n".join(parts)

    def choose_action(
        self, frames: list[Frame], latest: Frame, meta: dict[str, Any],
        workspace_prompt: str,
    ) -> tuple[str, str]:
        """Get one action from this sub-agent via LLM call.

        The LLM can respond with:
        1. A ```python code block → executed in REPL (FREE, no action cost)
        2. A JSON action → returned as game action (costs 1 action)

        Code blocks loop: execute code, feed output back, ask again.
        This continues until the LLM emits a game action or hits code call limit.

        Returns (action_string, full_response_text).
        """
        if self.budget_exhausted:
            return "NOOP", "Budget exhausted"

        # Update REPL with current game state
        self.repl.update_frame(latest, frames, meta)

        system_prompt = self.build_system_prompt(workspace_prompt)
        observation = format_observation(
            frames, latest, meta,
            budget_remaining=self.budget_remaining,
            role=self.role,
        )

        # Add observation as user message
        self._messages.append({
            "role": "user",
            "content": [{"text": observation}],
        })

        # Loop: LLM may emit code blocks before choosing an action
        all_response_text = ""
        for _ in range(self.max_code_calls + 1):
            # Trim history
            if len(self._messages) > 20:
                self._messages = self._messages[-20:]

            try:
                response = self.client.converse(
                    modelId=self.model_id,
                    system=[{"text": system_prompt}],
                    messages=self._messages,
                    inferenceConfig={
                        "maxTokens": self.max_tokens,
                        "temperature": 0.3,
                    },
                )

                content = response.get("output", {}).get("message", {}).get("content", [])
                text = "".join(b.get("text", "") for b in content)
                all_response_text += text + "\n"

                usage = response.get("usage", {})
                self._total_input_tokens += usage.get("inputTokens", 0)
                self._total_output_tokens += usage.get("outputTokens", 0)

                self._messages.append({
                    "role": "assistant",
                    "content": [{"text": text}],
                })

                # Extract memories from any response
                self._extract_memories(text)

                # Check if response contains a code block
                code = extract_code_block(text)
                if code and self._code_calls < self.max_code_calls:
                    # Execute code in REPL (FREE -- no action cost)
                    self._code_calls += 1
                    result = self.repl.exec(code)
                    logger.debug(
                        "%s code exec #%d: %s",
                        self.role, self._code_calls,
                        result.output[:100] if result.success else result.error[:100],
                    )

                    # Feed code output back as user message
                    output_text = str(result)
                    if len(output_text) > 2000:
                        output_text = output_text[:2000] + "\n... [truncated]"
                    self._messages.append({
                        "role": "user",
                        "content": [{"text": f"[Code output #{self._code_calls}]:\n{output_text}\n\nNow choose: more ```python analysis, or a game action JSON."}],
                    })
                    continue  # Loop back for next response

                # No code block (or code limit reached) → extract game action
                action_str = extract_action(text)
                self.actions_taken += 1
                return action_str, all_response_text

            except Exception as e:
                logger.error("SubAgent %s LLM error: %s", self.role, e)
                return "RESET", f"LLM error: {e}"

        # Code call limit exhausted without an action -- force one
        self.actions_taken += 1
        return "RESET", all_response_text

    def _extract_memories(self, text: str) -> None:
        """Extract MEMORY: entries from the agent's response."""
        for line in text.split("\n"):
            line = line.strip()
            if line.upper().startswith("MEMORY:"):
                content = line[7:].strip()
                # Split on first | to get summary|details
                if "|" in content:
                    summary, details = content.split("|", 1)
                else:
                    summary = content[:80]
                    details = content
                self.memories.add(
                    summary=summary.strip(),
                    details=details.strip(),
                    source=self.role,
                    level=self.level,
                )

    def debrief(self) -> str:
        """Ask the agent to summarize what it learned before being retired."""
        if not self._messages:
            return ""

        self._messages.append({
            "role": "user",
            "content": [{"text": (
                "You are being retired. Summarize your KEY FINDINGS as MEMORY: entries "
                "(one per line) so the next agent can benefit.\n"
                "Format: MEMORY: short summary | CONFIRMED/HYPOTHESIS: detailed explanation\n"
                "Only include genuinely useful discoveries, not obvious things."
            )}],
        })

        try:
            system = self.build_system_prompt("")
            response = self.client.converse(
                modelId=self.model_id,
                system=[{"text": system}],
                messages=self._messages[-8:],
                inferenceConfig={"maxTokens": 2000, "temperature": 0.2},
            )
            content = response.get("output", {}).get("message", {}).get("content", [])
            text = "".join(b.get("text", "") for b in content)

            usage = response.get("usage", {})
            self._total_input_tokens += usage.get("inputTokens", 0)
            self._total_output_tokens += usage.get("outputTokens", 0)

            self._extract_memories(text)
            return text
        except Exception as e:
            logger.warning("Debrief failed for %s: %s", self.role, e)
            return ""


class Orchestrator:
    """Manages sub-agents playing an ARC-AGI-3 game.

    Phases per level:
    1. EXPLORE: Spawn explorer with small budget to discover mechanics
    2. HYPOTHESIZE: Orchestrator reviews memories and forms strategy
    3. SOLVE: Spawn solver with remaining budget to execute strategy
    4. If solver fails, iterate with new explorer/solver cycle

    The orchestrator itself is an LLM that decides:
    - When to spawn/retire sub-agents
    - How much budget to give each
    - What objective each agent should pursue
    - When to move to the next phase
    """

    def __init__(
        self,
        client: Any,
        model_id: str,
        max_tokens: int = 8000,
        workspace_prompt: str = "",
    ):
        self.client = client
        self.model_id = model_id
        self.max_tokens = max_tokens
        self.workspace_prompt = workspace_prompt
        self.memories = Memories()
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self._sub_agents: list[SubAgent] = []

    def _create_sub_agent(
        self, role: str, objective: str, budget: int, level: int = 0,
    ) -> SubAgent:
        """Create a fresh sub-agent."""
        agent = SubAgent(
            client=self.client,
            model_id=self.model_id,
            max_tokens=self.max_tokens,
            role=role,
            objective=objective,
            action_budget=budget,
            memories=self.memories,
            level=level,
        )
        self._sub_agents.append(agent)
        logger.info(
            "Spawned %s (budget=%d, memories=%d)",
            role, budget, len(self.memories),
        )
        return agent

    def _retire_agent(self, agent: SubAgent) -> None:
        """Debrief and retire a sub-agent, collecting its token usage."""
        agent.debrief()
        self.total_input_tokens += agent._total_input_tokens
        self.total_output_tokens += agent._total_output_tokens
        logger.info(
            "Retired %s: %d actions, %d memories added",
            agent.role, agent.actions_taken, len(self.memories),
        )

    def play_level(
        self,
        env_step: Callable[[str, int, int], tuple[Frame, dict[str, Any]]],
        frames: list[Frame],
        meta: dict[str, Any],
        budget: int,
        level: int,
    ) -> tuple[list[Frame], dict[str, Any], int]:
        """Play one level using explore→solve phases.

        Args:
            env_step: Callable that takes (action_name, x, y) and returns (new_frame, meta).
            frames: Frame history (mutable -- new frames are appended).
            meta: Current game metadata.
            budget: Total action budget for this level.
            level: Current level number.

        Returns:
            (updated frames, updated meta, actions_used)
        """
        actions_used = 0
        remaining = budget

        # Phase 1: EXPLORE -- discover mechanics with small budget
        explore_budget = min(remaining, max(8, remaining // 4))
        explorer = self._create_sub_agent(
            role=f"explorer-L{level}",
            objective=(
                f"Explore level {level} mechanics. Try each available action 1-2 times. "
                "Watch the diff carefully. Identify: player, interactive objects, goal, "
                "movement rules. Save findings as MEMORY: entries."
            ),
            budget=explore_budget,
            level=level,
        )

        actions_used += self._run_sub_agent(
            explorer, env_step, frames, meta,
        )
        remaining -= actions_used
        self._retire_agent(explorer)

        # Check if level was completed during exploration
        if self._level_completed(meta, level):
            return frames, meta, actions_used

        # Phase 2: SOLVE -- execute strategy with remaining budget
        # Give solver most of remaining budget, keep small reserve for retry
        if remaining <= 0:
            return frames, meta, actions_used

        solve_budget = min(remaining, max(10, remaining * 3 // 4))
        remaining -= solve_budget

        solver = self._create_sub_agent(
            role=f"solver-L{level}",
            objective=(
                f"Complete level {level}. Use the shared knowledge to execute an "
                "efficient strategy. Don't re-explore -- trust the memories. "
                "If stuck after 10 actions, try RESET and a different approach."
            ),
            budget=solve_budget,
            level=level,
        )

        actions_used += self._run_sub_agent(
            solver, env_step, frames, meta,
        )
        self._retire_agent(solver)

        # Check completion
        if self._level_completed(meta, level):
            return frames, meta, actions_used

        # Phase 3: RETRY -- if budget remains, try once more with fresh agent
        remaining = budget - actions_used
        if remaining > 5:
            retry = self._create_sub_agent(
                role=f"retry-L{level}",
                objective=(
                    f"Previous agents failed to complete level {level}. "
                    "Review all memories and try a fundamentally DIFFERENT approach. "
                    "Start with RESET to get a clean state."
                ),
                budget=remaining,
                level=level,
            )
            actions_used += self._run_sub_agent(
                retry, env_step, frames, meta,
            )
            self._retire_agent(retry)

        return frames, meta, actions_used

    def _run_sub_agent(
        self,
        agent: SubAgent,
        env_step: Callable[[str, int, int], tuple[Frame, dict[str, Any]]],
        frames: list[Frame],
        meta: dict[str, Any],
    ) -> int:
        """Run a sub-agent until its budget is exhausted or level changes."""
        initial_level = meta.get("levels_completed", 0)
        actions = 0

        while not agent.budget_exhausted:
            action_str, response_text = agent.choose_action(
                frames, frames[-1], meta, self.workspace_prompt,
            )

            if action_str == "NOOP":
                break

            # Parse x, y for ACTION6
            x, y = -1, -1
            json_match = re.search(r'\{[^{}]*"action"[^{}]*\}', response_text)
            if json_match:
                try:
                    parsed = json.loads(json_match.group(0))
                    x = parsed.get("x", -1)
                    y = parsed.get("y", -1)
                except json.JSONDecodeError:
                    pass

            # Step the environment
            new_frame, meta = env_step(action_str, x, y)
            frames.append(new_frame)
            actions += 1

            # Check if level completed
            if meta.get("levels_completed", 0) > initial_level:
                logger.info(
                    "%s completed level %d in %d actions!",
                    agent.role, initial_level, actions,
                )
                break

            # Check game over -- auto-reset
            state = meta.get("state", "")
            if "GAME_OVER" in state:
                new_frame, meta = env_step("RESET", -1, -1)
                frames.append(new_frame)
                actions += 1

        return actions

    @staticmethod
    def _level_completed(meta: dict[str, Any], expected_level: int) -> bool:
        return meta.get("levels_completed", 0) > expected_level


def format_observation(
    frames: list[Frame],
    latest: Frame,
    meta: dict[str, Any],
    budget_remaining: int = 0,
    role: str = "",
) -> str:
    """Build compact observation text for a sub-agent.

    Token-efficient: only current grid + diff from previous frame.
    """
    parts = []

    levels = meta.get("levels_completed", 0)
    win_levels = meta.get("win_levels", 0)
    state = meta.get("state", "")
    available = meta.get("available_actions", [])
    step = len(frames) - 1

    parts.append(
        f"[Step {step} | Level {levels}/{win_levels} | State: {state} | "
        f"Budget: {budget_remaining} | Actions: {', '.join(available)}]"
    )

    # Diff from previous frame
    if len(frames) >= 2:
        summary = latest.change_summary(frames[-2])
        parts.append(f"\nChanges: {summary}")

    # Compact grid -- cropped to active area
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
                parts.append(f"\nGrid (active [{x1},{y1})-[{x2},{y2}) of {latest.width}x{latest.height}):")
                parts.append(latest.render(y_ticks=True, x_ticks=True, crop=(x1, y1, x2, y2)))
            else:
                parts.append(f"\nGrid ({latest.width}x{latest.height}, compact):")
                parts.append(latest.render(gap=""))
        else:
            parts.append(f"\nGrid ({latest.width}x{latest.height}, compact):")
            parts.append(latest.render(gap=""))
    else:
        parts.append(f"\nGrid ({latest.width}x{latest.height}, compact):")
        parts.append(latest.render(gap=""))

    # Color legend on first observation only
    if len(frames) <= 2:
        colors = latest.color_counts()
        present = ", ".join(f"{COLOR_NAMES[c]}({c}):{n}" for c, n in sorted(colors.items()))
        parts.append(f"\nColors: {present}")
        parts.append(f"Legend: {COLOR_LEGEND}")

    parts.append(
        "\nOptions:"
        "\n1. Analyze with code (FREE): ```python\\ncode here\\n```"
        '\n2. Take action (costs 1): {"action": "ACTION1", "reasoning": "why"}'
        '\n   For ACTION6: {"action": "ACTION6", "x": 32, "y": 32, "reasoning": "why"}'
        "\n3. Save finding: MEMORY: summary | details"
        "\nTip: Always analyze with code first, then act."
    )

    return "\n".join(parts)


def extract_code_block(text: str) -> str | None:
    """Extract a ```python code block from LLM response.

    Returns the code string if found, None otherwise.
    Only matches python-fenced blocks to avoid capturing JSON examples.
    """
    # Match ```python ... ``` blocks
    match = re.search(r'```python\s*\n(.*?)```', text, re.DOTALL)
    if match:
        code = match.group(1).strip()
        if code:
            return code
    return None


def extract_action(text: str) -> str:
    """Extract action name from LLM response text.

    Skips any python code blocks to avoid matching action names in code comments.
    """
    # Remove code blocks before searching for actions
    clean = re.sub(r'```python.*?```', '', text, flags=re.DOTALL)

    # Try JSON
    json_match = re.search(r'\{[^{}]*"action"\s*:\s*"([^"]+)"[^{}]*\}', clean)
    if json_match:
        return json_match.group(1).upper()

    # Try plain action name
    for name in ["RESET", "ACTION7", "ACTION6", "ACTION5",
                 "ACTION4", "ACTION3", "ACTION2", "ACTION1"]:
        if name in clean.upper():
            return name

    return "RESET"
