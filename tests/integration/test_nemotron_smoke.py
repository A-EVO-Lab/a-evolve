"""Dry integration smoke test.

Runs the full ``Evolver(cycles=1)`` path with docker mocked — verifies that
``BaseTrainer.solve`` → ``TrainedModelBenchmark.evaluate_batch`` →
``TrainingEvolutionEngine.step`` → ``EvolutionLoop`` wire up correctly without
needing a real nemotron-lora-runner image.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _setup_workspace(root: Path) -> None:
    (root / "prompts").mkdir(parents=True)
    (root / "memory").mkdir()
    (root / "prompts" / "system.md").write_text("trainer\n")

    manifest = {
        "name": "smoke-trainer",
        "version": "0.1.0",
        "contract_version": "1.1",
        "agent": {
            "type": "trainer",
            "entrypoint": "agent_evolve.agents.trainers.hf_peft.HfPeftTrainer",
        },
        "training": {
            "docker_image": "smoke-runner:test",
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
    (root / "eval.yaml").write_text("inference:\n  mode: eval\n")


def _setup_data(root: Path) -> None:
    (root / "nemotron_reasoning").mkdir(parents=True)
    csv_path = root / "nemotron_reasoning" / "train.csv"
    csv_path.write_text(
        "id,prompt,answer\n"
        "q1,What is 2+2?,4\n"
        "q2,What is 3*3?,9\n"
        "q3,What is 5-2?,3\n"
    )


_REAL_SUBPROCESS_RUN = subprocess.run


class FakeDockerRun:
    """Replaces subprocess.run for ``docker run`` calls.

    Non-docker commands (``git``, ``bash``, etc.) pass through to the real
    subprocess.run unchanged.

    On `docker run … train`: creates a fake adapter directory under /out.
    On `docker run … inference`: writes a correct-answer scores.jsonl.
    """

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, cmd, **kwargs):
        if not cmd or cmd[0] != "docker":
            return _REAL_SUBPROCESS_RUN(cmd, **kwargs)
        self.calls.append(list(cmd))
        verb = cmd[cmd.index("smoke-runner:test") + 1]

        # Parse -v mounts.
        mounts = {}
        i = 0
        while i < len(cmd):
            if cmd[i] == "-v":
                spec = cmd[i + 1]
                host, rest = spec.split(":", 1)
                container = rest.rsplit(":", 1)[0] if rest.endswith(":ro") else rest
                mounts[container] = Path(host)
                i += 2
            else:
                i += 1

        out_host = mounts["/out"]

        if verb == "train":
            adapter = out_host / "adapter"
            adapter.mkdir(parents=True, exist_ok=True)
            (adapter / "adapter_config.json").write_text("{}")
        elif verb == "inference":
            prompts_path = out_host / "prompts.jsonl"
            scores_path = out_host / "scores.jsonl"
            rows = []
            if prompts_path.exists():
                for line in prompts_path.read_text().splitlines():
                    p = json.loads(line)
                    tid = p["task_id"]
                    gold = p["metadata"]["answer"]
                    rows.append({
                        "task_id": tid,
                        "prediction": f"\\boxed{{{gold}}}",
                        "raw_response": "reasoning...",
                    })
            scores_path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")

        # Simulate the stdout/stderr capture contract of subprocess.run(check=True, stdout=log).
        stdout = kwargs.get("stdout")
        if hasattr(stdout, "write"):
            stdout.write(f"FAKE docker run {verb} OK\n")

        class Completed:
            returncode = 0
        return Completed()


@pytest.fixture
def fake_docker(monkeypatch):
    fake = FakeDockerRun()
    monkeypatch.setattr(
        "agent_evolve.agents.trainers.base.subprocess.run",
        fake,
    )
    yield fake


def test_end_to_end_one_cycle(tmp_path: Path, monkeypatch, fake_docker) -> None:
    ws_seed = tmp_path / "ws_seed"
    _setup_workspace(ws_seed)

    data_dir = tmp_path / "data"
    _setup_data(data_dir)
    monkeypatch.setenv("AEVOLVE_DATA_DIR", str(data_dir))

    import agent_evolve as ae
    from agent_evolve.algorithms.training_evolve import TrainingEvolutionEngine
    from agent_evolve.benchmarks.nemo_reason import NemotronReasoningBenchmark
    from agent_evolve.benchmarks.trained_model import TrainedModelBenchmark

    benchmark = TrainedModelBenchmark(
        inner_benchmark=NemotronReasoningBenchmark(csv_path=data_dir / "nemotron_reasoning" / "train.csv"),
        eval_config_path="eval.yaml",
    )
    # Fixture only provides train.csv, so score against that split.
    engine = TrainingEvolutionEngine(batch_size=3, eval_split="train")
    # Stub the LLM so this test runs offline.
    monkeypatch.setattr(engine, "_run_llm", lambda *a, **kw: {"content": "", "usage": {}})

    work_dir = tmp_path / "evolution_workdir"
    evolver = ae.Evolver(
        agent=str(ws_seed),
        benchmark=benchmark,
        engine=engine,
        work_dir=work_dir,
    )
    result = evolver.run(cycles=1)

    assert result.cycles_completed == 1
    # Three tasks, all "correct" thanks to the fake image → score 1.0.
    assert result.final_score == pytest.approx(1.0)
    # Two docker runs: train then inference.
    verbs = [c[c.index("smoke-runner:test") + 1] for c in fake_docker.calls]
    assert verbs == ["train", "inference"]

    # Metrics file exists.
    metrics = work_dir / ws_seed.name / "evolution" / "metrics.json"
    assert metrics.exists()
    m = json.loads(metrics.read_text())
    assert m["cycles_completed"] == 1
    assert m["latest_score"] == pytest.approx(1.0)
