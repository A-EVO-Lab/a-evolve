from __future__ import annotations

import csv
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent_evolve.benchmarks.nemo_reason import NemotronReasoningBenchmark
from agent_evolve.types import Task


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["id", "prompt", "answer"])
        writer.writeheader()
        for r in rows:
            writer.writerow(r)


def test_get_tasks_reads_csv(tmp_path: Path) -> None:
    csv_path = tmp_path / "train.csv"
    _write_csv(csv_path, [
        {"id": "q1", "prompt": "What is 2+2?", "answer": "4"},
        {"id": "q2", "prompt": "What is 3*3?", "answer": "9"},
    ])
    bench = NemotronReasoningBenchmark(csv_path=csv_path)
    tasks = bench.get_tasks(split="train", limit=10)
    assert len(tasks) == 2
    assert tasks[0].id == "q1"
    assert tasks[0].input == "What is 2+2?"
    assert tasks[0].metadata["answer"] == "4"


def test_evaluate_row_boxed_match() -> None:
    bench = NemotronReasoningBenchmark.__new__(NemotronReasoningBenchmark)
    task = Task(id="q1", input="...", metadata={"answer": "42"})
    row = {"task_id": "q1", "prediction": "the answer is \\boxed{42}", "raw_response": ""}
    fb = bench.evaluate_row(task, row)
    assert fb.success is True
    assert fb.score == 1.0


def test_evaluate_row_boxed_no_match() -> None:
    bench = NemotronReasoningBenchmark.__new__(NemotronReasoningBenchmark)
    task = Task(id="q1", input="...", metadata={"answer": "42"})
    row = {"task_id": "q1", "prediction": "\\boxed{43}", "raw_response": ""}
    fb = bench.evaluate_row(task, row)
    assert fb.success is False
    assert fb.score == 0.0


def test_evaluate_row_extracts_last_boxed() -> None:
    bench = NemotronReasoningBenchmark.__new__(NemotronReasoningBenchmark)
    task = Task(id="q1", input="...", metadata={"answer": "7"})
    row = {
        "task_id": "q1",
        "prediction": "First attempt \\boxed{5}. On reflection: \\boxed{7}",
        "raw_response": "",
    }
    fb = bench.evaluate_row(task, row)
    assert fb.success is True


def test_evaluate_row_normalizes_whitespace() -> None:
    bench = NemotronReasoningBenchmark.__new__(NemotronReasoningBenchmark)
    task = Task(id="q1", input="...", metadata={"answer": "hello world"})
    row = {"task_id": "q1", "prediction": "\\boxed{HELLO WORLD}", "raw_response": ""}
    fb = bench.evaluate_row(task, row)
    assert fb.success is True


def test_evaluate_raises_not_implemented() -> None:
    bench = NemotronReasoningBenchmark.__new__(NemotronReasoningBenchmark)
    import pytest
    from agent_evolve.types import Trajectory

    with pytest.raises(NotImplementedError):
        bench.evaluate(Task(id="x", input="x"), Trajectory(task_id="x", output=""))
