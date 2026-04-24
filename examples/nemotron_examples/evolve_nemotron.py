"""End-to-end Nemotron reasoning evolution driver.

Prereqs:
  - ``AEVOLVE_DATA_DIR`` points at a directory containing
    ``nemotron_reasoning/train.csv`` (columns: id, prompt, answer).
  - A pre-built docker image ``nemotron-lora-runner:<tag>`` is available
    locally. See ``seed_workspaces/nemotron30b_reasoning_trainer/IMAGE_REQUIREMENTS.md``.

Smoke command (1 cycle, 20 eval tasks):
  python examples/nemotron_examples/evolve_nemotron.py --cycles 1 --limit 20
"""

from __future__ import annotations

import argparse
import logging

import agent_evolve as ae
from agent_evolve.algorithms.training_evolve import TrainingEvolutionEngine
from agent_evolve.benchmarks.nemo_reason import NemotronReasoningBenchmark
from agent_evolve.benchmarks.trained_model import TrainedModelBenchmark


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cycles", type=int, default=20)
    parser.add_argument("--limit", type=int, default=200,
                        help="Number of eval tasks per cycle.")
    parser.add_argument("--workspace", type=str,
                        default="./seed_workspaces/nemotron30b_reasoning_trainer")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    benchmark = TrainedModelBenchmark(
        inner_benchmark=NemotronReasoningBenchmark(split="train", limit=args.limit),
        eval_config_path="eval.yaml",
    )
    engine = TrainingEvolutionEngine(cycles=args.cycles, batch_size=args.limit)

    evolver = ae.Evolver(
        agent=args.workspace,
        benchmark=benchmark,
        engine=engine,
    )
    result = evolver.run(cycles=args.cycles)
    print(f"Completed {result.cycles_completed} cycles. Final score: {result.final_score:.3f}")
    print(f"Score history: {result.score_history}")


if __name__ == "__main__":
    main()
