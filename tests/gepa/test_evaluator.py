"""Tests for GEPA evaluator, side info, and trajectory compression."""
from unittest.mock import MagicMock, patch
from agent_evolve.config import EvolveConfig
from agent_evolve.types import Feedback, Observation, Task, Trajectory


def _make_observation(
    score: float = 0.8,
    success: bool = True,
    output: str = "result",
    conversation: list | None = None,
    steps: list | None = None,
) -> Observation:
    return Observation(
        task=Task(id="t1", input="do something" * 50),
        trajectory=Trajectory(
            task_id="t1", output=output,
            steps=steps or [], conversation=conversation or [],
        ),
        feedback=Feedback(
            success=success, score=score, detail="Good work on this task.",
            raw={"per_claim": [{"claim": "c1", "score": 1.0}]},
        ),
    )


def test_build_side_info_structure():
    from agent_evolve.algorithms.gepa.evaluator import build_side_info
    obs = _make_observation()
    info = build_side_info(obs)
    assert "Input" in info
    assert "Generated Outputs" in info
    assert "Feedback" in info
    assert "scores" in info
    assert info["Input"]["Task ID"] == "t1"
    assert info["Feedback"]["Status"] == "PASS"
    assert info["scores"]["correctness"] == 0.8


def test_build_side_info_fail_status():
    from agent_evolve.algorithms.gepa.evaluator import build_side_info
    obs = _make_observation(success=False, score=0.0)
    info = build_side_info(obs)
    assert info["Feedback"]["Status"] == "FAIL"


def test_truncate_short_string():
    from agent_evolve.algorithms.gepa.evaluator import _truncate
    assert _truncate("hello", 100) == "hello"


def test_truncate_long_string():
    from agent_evolve.algorithms.gepa.evaluator import _truncate
    text = "a" * 1000
    result = _truncate(text, 100)
    assert len(result) <= 110
    assert "\n...\n" in result


def test_truncate_empty():
    from agent_evolve.algorithms.gepa.evaluator import _truncate
    assert _truncate("", 100) == ""
    assert _truncate(None, 100) == ""


def test_truncate_dict_empty():
    from agent_evolve.algorithms.gepa.evaluator import _truncate_dict
    assert _truncate_dict({}, 500) == ""


def test_truncate_dict_small():
    from agent_evolve.algorithms.gepa.evaluator import _truncate_dict
    result = _truncate_dict({"key": "value"}, 500)
    assert "key" in result
    assert "value" in result


def test_compress_trajectory_empty():
    from agent_evolve.algorithms.gepa.evaluator import compress_trajectory
    traj = Trajectory(task_id="t1", output="out")
    result = compress_trajectory(traj)
    assert isinstance(result, str)


def test_compress_trajectory_with_conversation():
    from agent_evolve.algorithms.gepa.evaluator import compress_trajectory
    conversation = [
        {"role": "assistant", "tool_calls": [{"function": "bash", "arguments": {"cmd": "ls -la"}}]},
        {"role": "tool", "content": "file1.txt\nfile2.txt"},
        {"role": "assistant", "tool_calls": [{"function": "bash", "arguments": {"cmd": "cat file1.txt"}}]},
        {"role": "tool", "content": "Error: No such file or directory"},
        {"role": "assistant", "tool_calls": [{"function": "submit", "arguments": {"answer": "42"}}]},
    ]
    traj = Trajectory(task_id="t1", output="42", conversation=conversation)
    result = compress_trajectory(traj)
    assert "Conversation trace" in result
    assert "[call] bash" in result
    assert "[error]" in result
    assert "[submit] 42" in result


def test_compress_trajectory_bounded():
    from agent_evolve.algorithms.gepa.evaluator import compress_trajectory
    conversation = []
    for i in range(100):
        conversation.append({"role": "assistant", "tool_calls": [{"function": "bash", "arguments": {"cmd": f"command_{i} " * 50}}]})
        conversation.append({"role": "tool", "content": f"output_{i} " * 100})
    traj = Trajectory(task_id="t1", output="done", conversation=conversation)
    result = compress_trajectory(traj)
    assert len(result) <= 3100


def test_compress_steps_with_tools():
    from agent_evolve.algorithms.gepa.evaluator import _compress_steps
    steps = [
        {"tool": "bash", "action": "execute"},
        {"tool": "bash", "action": "execute"},
        {"tool": "python", "action": "run"},
    ]
    result = _compress_steps(steps)
    assert "3 tool calls" in result


def test_compress_steps_empty():
    from agent_evolve.algorithms.gepa.evaluator import _compress_steps
    assert _compress_steps([]) == ""


def test_make_evaluator_calls_restore_and_run():
    from agent_evolve.algorithms.gepa.evaluator import make_evaluator
    config = EvolveConfig()
    trial = MagicMock()
    obs = _make_observation()
    trial.run_single.return_value = obs
    trial.agent.workspace = MagicMock()
    trial.agent.reload_from_fs.return_value = None
    evaluator = make_evaluator(trial, config)
    with patch("agent_evolve.algorithms.gepa.evaluator.restore_candidate") as mock_restore:
        task = Task(id="t1", input="do something")
        score, side_info = evaluator({"system_prompt": "test"}, task)
    assert score == 0.8
    assert "scores" in side_info
    mock_restore.assert_called_once()
    trial.agent.reload_from_fs.assert_called_once()
    trial.run_single.assert_called_once_with(task)
