from __future__ import annotations

import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent_evolve.agents.trainers.base import BaseTrainer
from agent_evolve.agents.trainers.hf_peft import HfPeftTrainer


def _write_minimal_workspace(root: Path, docker_image: str = "nemotron-lora-runner:test") -> None:
    (root / "prompts").mkdir(parents=True)
    (root / "memory").mkdir()
    (root / "prompts" / "system.md").write_text("trainer\n")

    manifest = {
        "name": "test-trainer",
        "version": "0.1.0",
        "contract_version": "1.1",
        "agent": {
            "type": "trainer",
            "entrypoint": "agent_evolve.agents.trainers.hf_peft.HfPeftTrainer",
        },
        "training": {
            "docker_image": docker_image,
            "train_args": [],
            "inference_args": [],
            "ckpt_path": "adapter",
        },
        "evolvable_layers": ["training_config.yaml", "data_config.yaml", "memory"],
        "reload_strategy": "hot",
    }
    (root / "manifest.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False))
    (root / "training_config.yaml").write_text("optimizer:\n  lr: 1.0e-4\n")
    (root / "data_config.yaml").write_text("sources: []\n")
    (root / "base_ckpt.yaml").write_text("uri: 'hf://foo/bar'\n")


def test_reload_from_fs_reads_manifest_training(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    _write_minimal_workspace(ws, docker_image="nemotron-lora-runner:unit")
    trainer = HfPeftTrainer(ws, work_root=tmp_path / "work")
    assert trainer.docker_image == "nemotron-lora-runner:unit"
    assert trainer.ckpt_rel_path == "adapter"
    assert trainer.training_config["optimizer"]["lr"] == 1.0e-4
    assert trainer.data_config == {"sources": []}


def test_summarize_log_hf_trainer_format(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    _write_minimal_workspace(ws)
    trainer = HfPeftTrainer(ws, work_root=tmp_path / "work")

    log = tmp_path / "train.log"
    log.write_text(
        "Starting training\n"
        "{'loss': 1.23, 'learning_rate': 0.0001, 'epoch': 0.1}\n"
        "{'loss': 1.10, 'learning_rate': 0.00008, 'epoch': 0.2}\n"
        "{'eval_loss': 1.05, 'epoch': 1.0}\n"
        "Done\n"
    )
    entries = trainer._summarize_log(log)
    assert len(entries) == 3
    assert entries[0]["type"] == "train_step"
    assert entries[0]["loss"] == 1.23
    assert entries[-1]["type"] == "eval"
    assert entries[-1]["eval_loss"] == 1.05


def test_summarize_log_regex_fallback(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    _write_minimal_workspace(ws)
    trainer = BaseTrainer(ws, work_root=tmp_path / "work")

    log = tmp_path / "train.log"
    log.write_text(
        "step=10 loss=2.50 lr=1e-4\n"
        "step=20 loss=2.10 lr=1e-4\n"
        "step=30 val_bpb=1.19\n"
    )
    entries = trainer._summarize_log(log)
    assert len(entries) == 3
    assert entries[0]["loss"] == 2.50
    assert entries[-1]["type"] == "eval"
    assert entries[-1]["val_bpb"] == 1.19


def test_solve_missing_docker_image_raises(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    _write_minimal_workspace(ws, docker_image="")
    trainer = HfPeftTrainer(ws, work_root=tmp_path / "work")
    import pytest
    from agent_evolve.types import Task

    with pytest.raises(RuntimeError, match="docker_image"):
        trainer.solve(Task(id="001", input="train"))
