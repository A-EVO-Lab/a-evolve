"""Prompts for TrainingEvolutionEngine.

The evolver LLM sees (a) current YAMLs, (b) recent eval scores, (c) train-log
tails, (d) memory notes, (e) constraints, and can mutate the workspace via
the shared ``workspace_bash`` tool.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...contract.workspace import AgentWorkspace
    from ...engine.history import EvolutionHistory


TRAINING_EVOLVER_SYSTEM_PROMPT = """\
You are the training-recipe evolver for a-evolve.

Your job: propose small, well-motivated edits to ``training_config.yaml`` and
``data_config.yaml`` in the agent's workspace, given the recent training and
evaluation signal. Your goal is to improve downstream benchmark score over
subsequent cycles.

Workspace layout (under ``cwd``):
  - training_config.yaml   ← EDITABLE
  - data_config.yaml       ← EDITABLE
  - memory/                ← EDITABLE (jsonl notes)
  - base_ckpt.yaml         ← DO NOT EDIT
  - manifest.yaml          ← DO NOT EDIT
  - train.py               ← DO NOT EDIT (image dispatches off YAML)
  - eval.yaml              ← DO NOT EDIT

Hard constraints for each cycle:
  1. Change at most ONE of: lr, lora.rank, lora.alpha, lora.target_modules,
     warmup_steps, total_steps, global_batch_size. Pick the change most
     likely to help given the signal.
  2. If you change ``lr``, the new value MUST be <= 0.5x or >= 2x the current
     value. Tiny nudges are forbidden — they don't move the loss landscape.
  3. Never add new top-level YAML keys; only change values for existing keys.
  4. Never touch files marked DO NOT EDIT.

Use the ``workspace_bash`` tool to:
  - Read current YAMLs: ``cat training_config.yaml``.
  - Apply edits: prefer ``python -c "import yaml, ..."``  over sed; validate
    by re-parsing the file afterwards.
  - Verify your change: ``git diff training_config.yaml data_config.yaml``.
  - Record a short note in ``memory/training.jsonl`` describing what you
    changed and why.

End your turn when ``git diff`` shows only the intended change and you have
added a memory note. If the evidence is insufficient for a confident change,
leave the YAMLs untouched and add a memory note explaining what you need.
"""


def _read_text(path: Path, max_chars: int = 4000) -> str:
    if not path.exists():
        return "(missing)"
    text = path.read_text()
    if len(text) > max_chars:
        text = text[:max_chars] + "\n... [truncated]"
    return text


def _yaml_diff(workspace_diff: str) -> str:
    """Filter a git diff to *.yaml sections only."""
    lines = workspace_diff.splitlines()
    out: list[str] = []
    include = False
    for line in lines:
        if line.startswith("diff --git"):
            include = ".yaml" in line
        if include:
            out.append(line)
    return "\n".join(out) if out else "(no yaml-level diff)"


def build_training_evolution_prompt(
    workspace: "AgentWorkspace",
    history: "EvolutionHistory",
    cycle_num: int,
    memory: list[dict],
    last_train_log_tails: list[str],
    last_scores: list[float],
) -> str:
    """Build the user message shown to the evolver LLM."""

    root = workspace.root
    training_yaml = _read_text(root / "training_config.yaml")
    data_yaml = _read_text(root / "data_config.yaml")

    score_line = ", ".join(f"{s:.3f}" for s in last_scores) if last_scores else "(none yet)"

    # YAML-only diff between the two most recent evolve tags, if any.
    yaml_diff_block = "(no prior cycles)"
    if cycle_num >= 3:
        try:
            raw = history.get_workspace_diff(f"evo-{cycle_num - 2}", f"evo-{cycle_num - 1}")
            yaml_diff_block = _yaml_diff(raw)
        except Exception as e:
            yaml_diff_block = f"(diff unavailable: {e})"

    log_tails_block = "\n\n".join(
        f"--- cycle {cycle_num - len(last_train_log_tails) + i} train.log tail ---\n{tail}"
        for i, tail in enumerate(last_train_log_tails)
    ) or "(no train logs yet)"

    memory_block = "\n".join(json.dumps(m, ensure_ascii=False) for m in memory[-20:]) or "(empty)"

    return f"""\
# Training evolution cycle {cycle_num}

## Score curve (last cycles)
{score_line}

## Current training_config.yaml
```yaml
{training_yaml}
```

## Current data_config.yaml
```yaml
{data_yaml}
```

## Previous cycle's YAML-level diff
```diff
{yaml_diff_block}
```

## Recent train.log tails
```
{log_tails_block}
```

## Training memory (last 20 notes)
```jsonl
{memory_block}
```

## Your task
Apply at most ONE YAML change per the constraints in the system prompt, or
leave untouched with a memory note. Then end your turn.
"""
