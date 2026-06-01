"""FutureX temporal prediction benchmark package.

Adapter + data loader.  The live search pipeline that prevents label
leakage lives in ``agent_evolve.agents.futurex.solver`` (Wikipedia
revision API + DDGS+htmldate + FRED).
"""

from .data_loader import (
    FutureXDataLoader,
    FutureXTask,
    get_futurex_stats,
    load_futurex_tasks,
)
from .futurex import FutureXBenchmark, create_futurex_benchmark

__all__ = [
    "FutureXBenchmark",
    "FutureXDataLoader",
    "FutureXTask",
    "create_futurex_benchmark",
    "load_futurex_tasks",
    "get_futurex_stats",
]
