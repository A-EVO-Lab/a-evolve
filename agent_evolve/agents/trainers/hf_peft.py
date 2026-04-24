"""HfPeftTrainer -- LoRA/PEFT training via HuggingFace ``transformers.Trainer``.

All logic lives in ``BaseTrainer``. This subclass only overrides log parsing
to understand HF Trainer's dict-per-line output format::

    {'loss': 1.23, 'learning_rate': 1e-4, 'epoch': 0.12}
    {'eval_loss': 1.05, 'epoch': 1.0}
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path
from typing import Any

from .base import BaseTrainer

logger = logging.getLogger(__name__)


class HfPeftTrainer(BaseTrainer):
    """LoRA trainer; identical to BaseTrainer except for log parsing."""

    def _summarize_log(self, log_path: Path, max_entries: int = 50) -> list[dict[str, Any]]:
        if not log_path.exists():
            return []
        entries: list[dict[str, Any]] = []
        with open(log_path) as f:
            for line in f:
                line = line.strip()
                if not (line.startswith("{") and line.endswith("}")):
                    continue
                try:
                    d = ast.literal_eval(line)
                except (ValueError, SyntaxError):
                    continue
                if not isinstance(d, dict):
                    continue
                kind = "eval" if any(k.startswith("eval_") for k in d) else "train_step"
                entries.append({"type": kind, **{str(k): v for k, v in d.items()}})
        if not entries:
            # Fall back to the regex-based parent parser for non-dict logs.
            return super()._summarize_log(log_path, max_entries=max_entries)
        return entries[-max_entries:]
