"""Evolver -- the simple top-level API for Agent Evolve.

Usage::

    import agent_evolve as ae

    evolver = ae.Evolver(
        agent="./seed_workspaces/swe",
        benchmark="swe-verified",
    )
    results = evolver.run(cycles=10)

    # Custom engine:
    from agent_evolve.algorithms.skillforge import AEvolveEngine

    evolver = ae.Evolver(
        agent="swe",
        benchmark="swe-verified",
        engine=AEvolveEngine(config),
    )
"""

from __future__ import annotations

__all__ = ["Evolver", "EvolveConfig", "EvolutionResult"]

import importlib
import logging
import shutil
from pathlib import Path

from .benchmarks.base import BenchmarkAdapter
from .config import EvolveConfig
from .contract.manifest import Manifest
from .contract.schema import validate_workspace
from .engine.base import EvolutionEngine
from .engine.loop import EvolutionLoop
from .protocol.base_agent import BaseAgent
from .types import EvolutionResult

logger = logging.getLogger(__name__)

# Registry of built-in benchmark names -> classes
_BENCHMARK_REGISTRY: dict[str, str] = {
    "swe-verified": "agent_evolve.benchmarks.swe_verified.SweVerifiedBenchmark",
    "mcp-atlas": "agent_evolve.benchmarks.mcp_atlas.McpAtlasBenchmark",
    "hle": "agent_evolve.benchmarks.hle.HleBenchmark",
    "terminal2": "agent_evolve.benchmarks.terminal2.Terminal2Benchmark",
    "terminal-bench": "agent_evolve.benchmarks.terminal2.Terminal2Benchmark",
    "skill-bench": "agent_evolve.benchmarks.skill_bench.SkillBenchBenchmark",
    "arc-agi-3": "agent_evolve.benchmarks.arc_agi3.ArcAgi3Benchmark",
    "arc-agi3": "agent_evolve.benchmarks.arc_agi3.ArcAgi3Benchmark",
    "arc": "agent_evolve.benchmarks.arc_agi3.ArcAgi3Benchmark",
}

# Registry of seed workspace names -> paths (relative to package root)
_SEED_REGISTRY: dict[str, str] = {
    "swe": "swe",
    "swe-verified": "swe",
    "mcp": "mcp",
    "mcp-atlas": "mcp",
    "reasoning": "reasoning",
    "hle": "reasoning",
    "terminal": "terminal",
    "terminal2": "terminal",
    "terminal-bench": "terminal",
    "clawcode": "clawcode",
    "claw-code": "clawcode",
    "arc": "arc",
    "arc-agi-3": "arc",
    "arc-mas": "arc-mas",
    "arc-agi-3-mas": "arc-mas",
    "arc-agi3": "arc",
    "mcp-mh": "mcp_mh",
}


class Evolver:
    """Top-level API for running agent evolution.

    Args:
        agent: One of:
            - str/Path to an agent workspace directory
            - str benchmark name (uses built-in seed workspace)
            - BaseAgent instance
        benchmark: One of:
            - str benchmark name ("swe-verified", "mcp-atlas", etc.)
            - BenchmarkAdapter instance
        config: EvolveConfig or path to YAML config file.
        engine: An EvolutionEngine instance.  Defaults to AEvolveEngine.
        work_dir: Directory for the working copy of the workspace.
            Defaults to "./evolution_workdir".
    """

    def __init__(
        self,
        agent: str | Path | BaseAgent,
        benchmark: str | BenchmarkAdapter,
        config: EvolveConfig | str | Path | None = None,
        engine: EvolutionEngine | None = None,
        work_dir: str | Path = "./evolution_workdir",
    ):
        self.config = self._resolve_config(config)
        self.benchmark = self._resolve_benchmark(benchmark)
        self.agent = self._resolve_agent(agent, work_dir)

        resolved_engine = engine or self._default_engine()
        self._loop = EvolutionLoop(self.agent, self.benchmark, resolved_engine, self.config)

    def _default_engine(self) -> EvolutionEngine:
        from .algorithms.skillforge import AEvolveEngine

        return AEvolveEngine(self.config)

    def run(self, cycles: int | None = None) -> EvolutionResult:
        """Run the evolution loop."""
        return self._loop.run(cycles=cycles)

    # ── Resolution helpers ───────────────────────────────────────────

    @staticmethod
    def _resolve_config(config: EvolveConfig | str | Path | None) -> EvolveConfig:
        if config is None:
            return EvolveConfig()
        if isinstance(config, EvolveConfig):
            return config
        return EvolveConfig.from_yaml(config)

    @staticmethod
    def _resolve_benchmark(benchmark: str | BenchmarkAdapter) -> BenchmarkAdapter:
        if isinstance(benchmark, BenchmarkAdapter):
            return benchmark
        dotted_path = _BENCHMARK_REGISTRY.get(benchmark)
        if not dotted_path:
            raise ValueError(
                f"Unknown benchmark: {benchmark!r}. "
                f"Available: {list(_BENCHMARK_REGISTRY.keys())}"
            )
        return _import_class(dotted_path)()

    def _resolve_agent(self, agent: str | Path | BaseAgent, work_dir: str | Path) -> BaseAgent:
        if isinstance(agent, BaseAgent):
            return agent

        workspace_path = self._resolve_workspace_path(agent, work_dir)

        errors = validate_workspace(workspace_path)
        if errors:
            raise ValueError(f"Invalid workspace at {workspace_path}: {errors}")

        manifest = Manifest.from_yaml(workspace_path / "manifest.yaml")
        if manifest.entrypoint:
            agent_cls = _import_class(manifest.entrypoint)
            return agent_cls(workspace_path)

        raise ValueError(
            f"No entrypoint in manifest.yaml at {workspace_path}. "
            "Either set agent.entrypoint or pass a BaseAgent instance."
        )

    def _resolve_workspace_path(self, agent: str | Path, work_dir: str | Path) -> Path:
        """Resolve agent arg to a workspace path, copying seed if needed."""
        work_dir = Path(work_dir)
        agent_path = Path(agent)

        # Direct path to an existing workspace
        if agent_path.is_dir() and (agent_path / "manifest.yaml").exists():
            dest = work_dir / agent_path.name
            if not dest.exists():
                shutil.copytree(agent_path, dest)
            return dest

        # Named seed workspace
        seed_name = _SEED_REGISTRY.get(str(agent))
        if seed_name:
            seed_dir = Path(__file__).parent.parent / "seed_workspaces" / seed_name
            if seed_dir.exists():
                dest = work_dir / seed_name
                if not dest.exists():
                    shutil.copytree(seed_dir, dest)
                return dest

        # Fallback: treat as a direct path
        if agent_path.is_dir():
            return agent_path

        raise ValueError(
            f"Cannot resolve agent: {agent!r}. "
            "Pass a workspace directory path, a seed name, or a BaseAgent instance."
        )


def _import_class(dotted_path: str) -> type:
    """Dynamically import a class from a dotted path like 'package.module.ClassName'."""
    module_path, class_name = dotted_path.rsplit(".", 1)
    module = importlib.import_module(module_path)
    return getattr(module, class_name)
