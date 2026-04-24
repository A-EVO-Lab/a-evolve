"""TrainingEvolutionEngine -- evolves (training_config.yaml, data_config.yaml).

Per cycle:
  1. trainer.solve(synthetic_task)      → docker run train   → ckpt
  2. benchmark.get_tasks(...)
  3. benchmark.evaluate_batch(...)      → docker run inference → scores
  4. aggregate mean score, append to memory
  5. LLM + workspace_bash proposes one YAML patch
  6. hash YAMLs pre/post to detect mutation; emit StepResult

Sets ``manages_own_evaluation = True`` so the outer EvolutionLoop skips its
per-task solve+evaluate batch — the engine owns that flow.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ...config import EvolveConfig
from ...contract.workspace import AgentWorkspace
from ...engine.base import EvolutionEngine
from ...llm.base import LLMMessage, LLMProvider
from ...types import Observation, StepResult, Task
from ..skillforge.tools import BASH_TOOL_SPEC, create_default_llm, make_workspace_bash
from .prompts import TRAINING_EVOLVER_SYSTEM_PROMPT, build_training_evolution_prompt

if TYPE_CHECKING:
    from ...agents.trainers.base import BaseTrainer
    from ...benchmarks.training_base import TrainingBenchmarkAdapter
    from ...engine.history import EvolutionHistory
    from ...engine.trial import TrialRunner

logger = logging.getLogger(__name__)

_YAML_FILES = ("training_config.yaml", "data_config.yaml")


def _hash_yaml(path: Path) -> str:
    if not path.exists():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _snapshot_yamls(root: Path) -> dict[str, str]:
    return {name: _hash_yaml(root / name) for name in _YAML_FILES}


def _tail_text(path: Path, n_lines: int = 40) -> str:
    if not path.exists():
        return "(missing)"
    try:
        lines = path.read_text().splitlines()
    except Exception as e:
        return f"(unreadable: {e})"
    return "\n".join(lines[-n_lines:])


class TrainingEvolutionEngine(EvolutionEngine):
    """LLM-guided evolution of YAML training recipes."""

    def __init__(
        self,
        cycles: int = 20,
        config: EvolveConfig | None = None,
        llm: LLMProvider | None = None,
        batch_size: int = 50,
        eval_split: str = "val",
    ):
        self._cycles_hint = cycles
        self.config = config or EvolveConfig()
        self._llm = llm
        self.batch_size = batch_size
        self.eval_split = eval_split

    # Tell the outer loop we drive solve()+evaluate ourselves.
    @property
    def manages_own_evaluation(self) -> bool:
        return True

    @property
    def llm(self) -> LLMProvider:
        if self._llm is None:
            self._llm = create_default_llm(self.config)
        return self._llm

    # ── Main cycle step ───────────────────────────────────────────────

    def step(
        self,
        workspace: AgentWorkspace,
        observations: list[Observation],
        history: "EvolutionHistory",
        trial: "TrialRunner",
    ) -> StepResult:
        trainer: "BaseTrainer" = trial.agent  # type: ignore[assignment]
        benchmark: "TrainingBenchmarkAdapter" = trial.benchmark  # type: ignore[assignment]

        cycle_num = history.latest_cycle + 1

        # 1. Train
        train_task = Task(id=f"{cycle_num:03d}", input="train", metadata={})
        logger.info("[cycle %d] solve() → docker train", cycle_num)
        trajectory = trainer.solve(train_task)

        # 2. Get eval tasks (held-out by default; don't score on train split)
        tasks = benchmark.get_tasks(split=self.eval_split, limit=self.batch_size)

        # 3. Evaluate batch
        logger.info("[cycle %d] evaluate_batch over %d tasks", cycle_num, len(tasks))
        feedbacks = benchmark.evaluate_batch(tasks, trajectory, trainer)

        # 4. Aggregate + memorize
        mean_score = (
            sum(f.score for f in feedbacks) / len(feedbacks) if feedbacks else 0.0
        )
        success_rate = (
            sum(1 for f in feedbacks if f.success) / len(feedbacks) if feedbacks else 0.0
        )
        logger.info(
            "[cycle %d] mean_score=%.3f success_rate=%.3f", cycle_num, mean_score, success_rate
        )

        memory_entry = {
            "cycle": cycle_num,
            "score": round(mean_score, 4),
            "success_rate": round(success_rate, 4),
            "n_tasks": len(tasks),
            "training_config_hash": _hash_yaml(workspace.root / "training_config.yaml")[:12],
            "data_config_hash": _hash_yaml(workspace.root / "data_config.yaml")[:12],
        }
        workspace.add_memory(memory_entry, category="training")

        # 5. LLM-guided mutation
        memory = workspace.read_memories(category="training", limit=40)
        last_scores = [
            m.get("score", 0.0)
            for m in memory
            if isinstance(m.get("score"), (int, float))
        ][-10:]

        # Tail logs from up to 3 most recent cycles of this trainer's work_root.
        log_tails: list[str] = []
        work_root = getattr(trainer, "work_root", None)
        if work_root is not None:
            for c in range(max(1, cycle_num - 2), cycle_num + 1):
                tail = _tail_text(Path(work_root) / f"cycle-{c:03d}" / "train.log")
                log_tails.append(tail)

        prompt = build_training_evolution_prompt(
            workspace=workspace,
            history=history,
            cycle_num=cycle_num,
            memory=memory,
            last_train_log_tails=log_tails,
            last_scores=last_scores,
        )

        pre_hashes = _snapshot_yamls(workspace.root)
        response = self._run_llm(prompt, workspace.root)
        llm_usage = response.get("usage", {})
        post_hashes = _snapshot_yamls(workspace.root)

        changed = [k for k in _YAML_FILES if pre_hashes.get(k) != post_hashes.get(k)]
        mutated = bool(changed)

        return StepResult(
            mutated=mutated,
            summary=(
                f"cycle {cycle_num}: score={mean_score:.3f} "
                f"{'mutated[' + ','.join(changed) + ']' if changed else 'no-mutation'}"
            ),
            metadata={
                "cycle": cycle_num,
                "training_score": mean_score,
                "success_rate": success_rate,
                "n_tasks": len(tasks),
                "yaml_files_changed": changed,
                "ckpt_path": trajectory.output,
                "usage": llm_usage,
            },
        )

    # ── LLM runner (same pattern as skillforge.AEvolveEngine._run_llm) ──

    def _run_llm(self, prompt: str, workspace_root: Path) -> dict[str, Any]:
        bash_fn = make_workspace_bash(workspace_root)

        try:
            from ...llm.bedrock import BedrockProvider

            if isinstance(self.llm, BedrockProvider):
                response = self.llm.converse_loop(
                    system_prompt=TRAINING_EVOLVER_SYSTEM_PROMPT,
                    user_message=prompt,
                    tools=[BASH_TOOL_SPEC],
                    tool_executor={"workspace_bash": lambda command: bash_fn(command)},
                    max_tokens=self.config.evolver_max_tokens,
                )
                return {
                    "content": response.content,
                    "usage": response.usage,
                }
        except ImportError:
            pass

        messages = [
            LLMMessage(role="system", content=TRAINING_EVOLVER_SYSTEM_PROMPT),
            LLMMessage(role="user", content=prompt),
        ]
        response = self.llm.complete(
            messages, max_tokens=self.config.evolver_max_tokens
        )
        return {
            "content": response.content,
            "usage": response.usage,
        }
