#!/usr/bin/env python3
"""Quick eval: play ARC-AGI-3 games with Opus 4.6 via Bedrock."""

import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from agent_evolve.agents.arc.agent import ArcAgent
from agent_evolve.benchmarks.arc_agi3.benchmark import ArcAgi3Benchmark
from agent_evolve.types import Task

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger("eval_arc")

WORKSPACE = str(Path(__file__).resolve().parent.parent.parent / "seed_workspaces" / "arc")
MODEL_ID = "us.anthropic.claude-opus-4-6-v1"
REGION = "us-west-2"
MAX_ACTIONS = 150  # more generous budget
NUM_GAMES = 2


def main():
    # Load benchmark tasks -- pick easier games
    bench = ArcAgi3Benchmark(
        max_actions_per_game=MAX_ACTIONS,
        game_filter="sb26",  # easiest game (15-31 actions/level human baseline)
    )
    tasks = bench.get_tasks(split="test", limit=NUM_GAMES)

    # If sb26 filter gives only 1, also grab another easy one
    if len(tasks) < NUM_GAMES:
        bench2 = ArcAgi3Benchmark(
            max_actions_per_game=MAX_ACTIONS,
            game_filter="r11l",  # click-only, 7-45 actions/level
        )
        tasks2 = bench2.get_tasks(split="test", limit=1)
        tasks.extend(tasks2[:NUM_GAMES - len(tasks)])

    for t in tasks:
        t.metadata["max_actions"] = MAX_ACTIONS

    logger.info("Got %d tasks: %s", len(tasks),
                [f"{t.id} ({t.metadata.get('title', '')})" for t in tasks])

    # Create agent
    agent = ArcAgent(
        workspace_dir=WORKSPACE,
        model_id=MODEL_ID,
        region=REGION,
        max_tokens=8000,
        max_actions=MAX_ACTIONS,
    )

    results = []
    for i, task in enumerate(tasks):
        logger.info("=" * 60)
        logger.info("Game %d/%d: %s (%s) tags=%s", i + 1, len(tasks),
                     task.id, task.metadata.get("title", ""), task.metadata.get("tags", []))
        logger.info("=" * 60)

        t0 = time.time()
        traj = agent.solve(task)
        elapsed = time.time() - t0

        feedback = bench.evaluate(task, traj)

        result = {"game_id": task.id, "title": task.metadata.get("title", "")}
        try:
            traj_data = json.loads(traj.output)
            result.update(traj_data)
        except json.JSONDecodeError:
            pass
        result["feedback_score"] = feedback.score
        result["feedback_success"] = feedback.success

        results.append(result)

        logger.info("Result: %s | score=%.3f | levels=%d/%d | actions=%d | time=%.1fs",
                     "PASS" if feedback.success else "FAIL",
                     feedback.score,
                     result.get("levels_completed", 0),
                     result.get("total_levels", 0),
                     result.get("total_actions", 0),
                     elapsed)
        logger.info("Feedback:\n%s", feedback.detail)

    # Summary
    print("\n" + "=" * 60)
    print("EVALUATION SUMMARY")
    print("=" * 60)
    for r in results:
        levels = f"{r.get('levels_completed', '?')}/{r.get('total_levels', '?')}"
        actions = r.get("total_actions", "?")
        usage = r.get("usage", {})
        tokens = usage.get("total_tokens", 0)
        print(f"  {r['game_id']} ({r['title']}): "
              f"{'PASS' if r.get('feedback_success') else 'FAIL'} | "
              f"score={r.get('feedback_score', 0):.3f} | "
              f"levels={levels} | actions={actions} | "
              f"tokens={tokens:,}")

    avg = sum(r.get("feedback_score", 0) for r in results) / len(results) if results else 0
    print(f"\nAverage score: {avg:.3f}")

    out_path = Path(__file__).parent / "eval_results.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"Results saved to {out_path}")


if __name__ == "__main__":
    main()
