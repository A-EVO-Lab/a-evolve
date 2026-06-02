"""MCP-Atlas evolution runner using the Unified Engine.

Thin counterpart to ``adaptive_evolve_all.py``. Where legacy uses
``AdaptiveEvolveEngine.evolve()`` with a custom batch loop, this
runner goes through ``EvolutionLoop + UnifiedEngine`` with the
``per_claim`` recipe branch (matches ``AdaptiveEvolveEngine.step()``).

**Axis parity with legacy:**

- Observation: same — ``Observation.feedback.raw["per_claim"]`` + hallucination hints
- Update pipeline (same order as legacy):
  ``[FixHallucinations, AutoSeedSkills, LLMBashEvolve, SanityCheck]``
- Verify: ``NoVerify`` (matches legacy ``step()`` path — stagnation gate only
  fires in legacy standalone ``evolve()`` API, not in loop)
- Output: ``prompts/system.md``, ``skills/<name>/SKILL.md``, ``memory/episodic.jsonl``
  (memory pruning nested in ``FixHallucinations`` to match legacy ordering)
- Scope: ``{prompts: rw, skills: rw, memory: append}``

See ``docs/algorithms/unified-equivalence-audit.md`` +
``docs/mcp-atlas-demo-unified.md`` for the full audit and usage guide.

Usage:
    python run_adaptive_evolve_all_unified.py \\
        --cycles 3 --batch-size 30 --output-dir logs/unified_mcp
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import os
import shutil
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

# Strands SDK uses recursive event_loop dispatch + recursive JSON telemetry
# serialization; Python's default limit (1000) is too shallow for long tool chains.
sys.setrecursionlimit(10000)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ARTIFACT_ROOT = REPO_ROOT / "examples" / "harness-disentangling"
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(ARTIFACT_ROOT))

from agent_evolve.agents.mcp import McpAgent
from agent_evolve.agents.mcp.docker_env import McpAtlasContainer, pull_image
from agent_evolve.agents.mcp.key_registry import KeyRegistry
from agent_evolve.agents.mcp.mcp_client import McpClientWrapper
from agent_evolve.algorithms.unified import UnifiedEngine
from agent_evolve.algorithms.unified.operators.llm_bash_evolve import _resolve_llm
from agent_evolve.benchmarks.mcp_atlas import McpAtlasBenchmark
from agent_evolve.config import EvolveConfig
from agent_evolve.engine.loop import EvolutionLoop
from agent_evolve.llm.bedrock import BedrockProvider
from examples.mcp_examples.adaptive_evolve_all import CodeExecMcpAgent

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Per-task max-tool-use interrupt
#
# Some solvers (notably sonnet46 on tasks needing airtable_list_tables) enter
# a pathological loop calling the same tool with missing args, never giving
# up. Strands' main agent loop is iterative (no RecursionError ceiling like
# the older recursive version), so without a cap a single task can grind for
# hours. This adds an opt-in cap: when AfterToolCallEvent fires N times in a
# single solve() call, we raise TaskInterruptedException from the hook. The
# exception propagates out of agent(...), out of solve(...), and is caught
# by engine/loop.py::_solve_and_evaluate_one which logs and returns None.
# None observations are dropped from cycle_score, so interrupted tasks do
# NOT count toward final_score / pass_rate. They are recorded separately to
# results.metrics.json + interrupted_tasks.jsonl for visibility.
# ─────────────────────────────────────────────────────────────────────────────

class TaskInterruptedException(Exception):
    """Raised from an AfterToolCallEvent hook when a single solve() call
    exceeds --max-tool-uses-per-task. Carries (task_id, count, limit) for
    downstream reporting; otherwise indistinguishable from any other
    Exception caught by engine/loop.py::_solve_and_evaluate_one."""

    def __init__(self, task_id: str, count: int, limit: int):
        super().__init__(
            f"Task {task_id} hit max_tool_uses={limit} (count={count})"
        )
        self.task_id = task_id
        self.count = count
        self.limit = limit


# Module-level interrupt state. Was threading.local() in v1, but that failed
# silently in production: strands invokes AfterToolCallEvent callbacks from
# a different thread than the one calling agent(...), so the threading.local
# attribute was invisible to the callback (always read as None → counter
# never incremented → cap never fired). 4 cells accumulated 700-900+ tool
# uses on a single task without interrupting.
#
# Module-level dict is GIL-safe for dict ops in CPython and visible across
# threads. Safety constraint: parallel=1 per process (only one solve() runs
# concurrently in this process) — the runner uses --parallel 1 by default
# for MCP, so this holds. If a future change raises parallel>1, this design
# would race between concurrent solves and would need a per-Agent-instance
# counter via event.agent attribute instead.
_interrupt_state: dict = {
    "count": 0,
    "task_id": None,
    "limit": 0,        # 0 = disabled (hook silently returns)
    "interrupted": False,
}

# Shared collector for interrupted tasks. List append is atomic in CPython,
# no lock needed.
_interrupted_records: list[dict] = []


def _install_strands_interrupt_hook() -> None:
    """Monkey-patch strands.Agent.__init__ to register an AfterToolCallEvent
    hook on every Agent built in this process. The hook reads the module-level
    interrupt state and raises TaskInterruptedException when the count
    reaches the limit. Idempotent — guards against double-install if the
    runner is imported twice (e.g., via tests)."""
    from strands import Agent as _StrandsAgent
    from strands.hooks import AfterToolCallEvent

    if getattr(_StrandsAgent.__init__, "_evolverbench_patched", False):
        return

    _orig_init = _StrandsAgent.__init__

    def _after_tool_callback(event: AfterToolCallEvent) -> None:
        # Module-level dict read/write — GIL-protected in CPython, visible
        # across threads (unlike threading.local). Safe because parallel=1
        # in this runner; only one solve() runs at a time per process.
        limit = _interrupt_state["limit"]
        if limit <= 0 or _interrupt_state["interrupted"]:
            return
        _interrupt_state["count"] += 1
        if _interrupt_state["count"] >= limit:
            _interrupt_state["interrupted"] = True
            # Dual-mechanism stop:
            #   (1) request_state["stop_event_loop"] = True — checked by
            #       strands' event_loop.py:633 after each cycle. Even if
            #       tools/executors/_executor.py:271 swallows the
            #       TaskInterruptedException below and converts it to an
            #       error ToolResult, the OUTER event loop sees this flag
            #       and refuses to recurse → next model call never happens.
            #   (2) raise TaskInterruptedException — propagates straight
            #       out of the hook for the case where the executor doesn't
            #       swallow it. The `interrupted` guard above prevents
            #       double-firing if the executor's broad except catches it
            #       and re-fires the after-hook (the second invocation sees
            #       the flag and returns silently).
            try:
                request_state = event.invocation_state.setdefault("request_state", {})
                request_state["stop_event_loop"] = True
            except Exception:  # noqa: BLE001
                pass
            raise TaskInterruptedException(
                _interrupt_state["task_id"],
                _interrupt_state["count"],
                limit,
            )

    def _patched_init(self, *args, **kwargs):
        _orig_init(self, *args, **kwargs)
        # Register after the Agent finishes its own hook setup so our
        # callback is invoked after any retry-strategy hooks.
        self.hooks.add_callback(AfterToolCallEvent, _after_tool_callback)

    _patched_init._evolverbench_patched = True  # type: ignore[attr-defined]
    _StrandsAgent.__init__ = _patched_init


class InterruptibleCodeExecMcpAgent(CodeExecMcpAgent):
    """CodeExecMcpAgent subclass that enforces --max-tool-uses-per-task.

    Resets the module-level interrupt state before delegating to the parent
    solve(); the AfterToolCallEvent hook installed by
    _install_strands_interrupt_hook() reads that state. On
    TaskInterruptedException: records the interrupt to the module-level
    list, then re-raises so engine/loop.py drops the observation."""

    def __init__(self, *args, max_tool_uses_per_task: int = 0, **kwargs):
        super().__init__(*args, **kwargs)
        self._max_tool_uses_per_task = max_tool_uses_per_task

    def solve(self, task, shared_client=None):
        # Reset module-level state for this solve(). Safe because runner
        # uses parallel=1, so only one solve() per process at a time.
        _interrupt_state["count"] = 0
        _interrupt_state["task_id"] = task.id
        _interrupt_state["limit"] = self._max_tool_uses_per_task
        _interrupt_state["interrupted"] = False
        try:
            return super().solve(task, shared_client=shared_client)
        except TaskInterruptedException as exc:
            _interrupted_records.append({
                "task_id": exc.task_id,
                "tool_use_count": exc.count,
                "limit": exc.limit,
                "reason": "max_tool_uses",
                "timestamp": datetime.utcnow().isoformat() + "Z",
            })
            logger.warning(
                "Task %s interrupted: tool_use count %d reached limit %d",
                exc.task_id, exc.count, exc.limit,
            )
            raise
        finally:
            # Disable the hook between solves so any Agent built outside
            # our path (e.g., evolver-side LLM calls) doesn't accidentally
            # count toward the next task's quota.
            _interrupt_state["limit"] = 0


def main() -> int:
    p = argparse.ArgumentParser(
        description="MCP-Atlas evolution via UnifiedEngine + EvolutionLoop"
    )
    # Unified pass / cycle knobs (mirrors swe / sb unified runners).
    # When --passes or --cycle-per-batch is set, the script computes
    # max_cycles = passes × ⌈limit/batch_size⌉ × cycle_per_batch and
    # overrides --cycles. Otherwise --cycles is honoured (legacy default).
    p.add_argument("--passes", type=int, default=None,
                   help="Number of full sweeps of the dataset. If set "
                        "(or --cycle-per-batch is set), max_cycles is "
                        "computed as passes*⌈limit/batch⌉*cycle_per_batch.")
    p.add_argument("--cycle-per-batch", type=int, default=None, dest="cycle_per_batch",
                   help="In-batch retry multiplier (default: 1 when --passes is set).")
    p.add_argument("--cycles", type=int, default=None,
                   help="Direct EvolutionLoop max_cycles. "
                        "Overridden when --passes/--cycle-per-batch is set.")
    p.add_argument("--start-cycle", type=int, default=1,
                   help="Resume from this cycle (1=fresh run, default). "
                        "When N>1: skip workspace seeding, validate workspace "
                        "HEAD is at evo-{N-1} tag, load score_history from "
                        "existing results.metrics.json, advance benchmark "
                        "cursor by (N-1)*batch_size, and pass to "
                        "EvolutionLoop.run(start_cycle=N, existing_score_history=...).")
    p.add_argument("--batch-size", type=int, default=30,
                   help="Tasks per cycle (passed to bench.get_tasks limit)")
    p.add_argument("--parallel", type=int, default=1,
                   help="Parallel workers within each batch (default 1; legacy MCP is serial).")
    p.add_argument("--parallel-backend", default="thread",
                   choices=["thread", "process", "benchmark"],
                   help="In-batch parallel backend (default thread for MCP).")
    p.add_argument("--limit", type=int, default=500,
                   help="Cap on total tasks loaded")
    p.add_argument("--solver-model", default="us.anthropic.claude-opus-4-6-v1",
                   help="Model for the MCP agent (solve side)")
    p.add_argument("--evolver-model", default=None,
                   help="Model for the evolver operators (defaults to solver-model)")
    p.add_argument("--region", default="us-west-2")
    p.add_argument("--max-tokens", type=int, default=16384)
    p.add_argument("--judge-model", "--eval-model-id", dest="judge_model",
                   default="us.anthropic.claude-sonnet-4-6",
                   help="Model for the MCP-Atlas LLM-as-judge evaluator")
    p.add_argument("--docker-image", type=str, default=None,
                   help="MCP-Atlas docker image; when set, uses one shared container.")
    p.add_argument("--dataset", default="ScaleAI/MCP-Atlas")
    p.add_argument("--seed-workspace", default=str(REPO_ROOT / "seed_workspaces" / "mcp"))
    p.add_argument("--output-dir", default=None,
                   help="Defaults to logs/unified_mcp_<timestamp>")
    p.add_argument("--env-file", default=None,
                   help="Path to .env file with MCP API keys. When set, the "
                        "KeyRegistry is loaded and the bench filters out "
                        "tasks whose required MCP servers don't have keys "
                        "(matches legacy adaptive_evolve_all.py:240-247).")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument(
        "--max-tool-uses-per-task", type=int,
        default=int(os.environ.get("MAX_TOOL_USES_PER_TASK", "500")),
        help="Per-task ceiling on AfterToolCallEvent count. 500 (default) "
             "caps each task at 500 raw after-tool-call events; set 0 to "
             "disable. Exceeding the limit raises TaskInterruptedException; "
             "engine/loop.py drops the observation so interrupted tasks are "
             "excluded from final_score/pass_rate. Recorded to "
             "results.metrics.json + interrupted_tasks.jsonl. Also honours "
             "env var MAX_TOOL_USES_PER_TASK.",
    )
    args = p.parse_args()
    if args.max_tool_uses_per_task > 0:
        _install_strands_interrupt_hook()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    for noisy in ("botocore", "urllib3", "httpcore", "httpx",
                  "strands.models", "strands.tools", "strands.telemetry"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    out_dir = Path(args.output_dir) if args.output_dir else (
        REPO_ROOT / "logs" / f"unified_mcp_{datetime.utcnow():%Y%m%d_%H%M%S}"
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    # Benchmark
    bench = McpAtlasBenchmark(
        dataset_name=args.dataset,
        shuffle=False,
        eval_model_id=args.judge_model,
        eval_region=args.region,
        use_litellm=False,
    )
    logger.info("Capability: %s", bench.feedback_capability)

    # ── Load MCP API keys (legacy parity: adaptive_evolve_all.py:240-247) ──
    # KeyRegistry.load() populates self._keys; without it, McpAgent.solve()
    # gets empty env_vars and most tasks fail with missing_key. We also
    # ask the bench to filter out tasks whose required MCP servers lack
    # keys, then snapshot the filtered cache so EvolutionLoop's later
    # batched get_tasks() calls (which don't pass key_registry) still see
    # the filtered set.
    key_registry: KeyRegistry | None = None
    if args.env_file:
        key_registry = KeyRegistry(env_file_path=args.env_file)
        key_registry.load()
        logger.info(
            "Loaded %d MCP API key(s) from %s",
            len(key_registry.get_loaded_key_names()), args.env_file,
        )
    # Legacy adaptive_evolve_all.py runs over split="test" with --limit and
    # optional key filtering. EvolutionLoop asks for split="train", so redirect
    # train to that same filtered ordered list before the loop starts.
    filtered_tasks = bench.get_tasks(
        split="test", limit=args.limit, key_registry=key_registry,
    )
    keep_ids = {t.id for t in filtered_tasks}
    before = len(bench._cache.get("test", []))
    bench._cache["train"] = [
        r for r in bench._cache.get("test", [])
        if (r.get("TASK") or r.get("task_id", "")) in keep_ids
    ][:args.limit]
    bench._cursor = 0
    logger.info("Tasks after key_registry filter: %d (was %d)", len(bench._cache["train"]), before)

    # Shared workspace
    ws_dir = out_dir / "workspace"
    seed_dir = Path(args.seed_workspace)
    if args.start_cycle > 1:
        # Resume: workspace must already exist as a git repo, with HEAD at
        # the evo-{start_cycle-1} tag (caller's responsibility: they should
        # have done `git reset --hard evo-{N-1}` before launching).
        if not ws_dir.exists() or not (ws_dir / ".git").exists():
            raise SystemExit(
                f"--start-cycle={args.start_cycle} requires an existing git "
                f"workspace at {ws_dir}; got missing dir or missing .git/. "
                f"Run `git reset --hard evo-{args.start_cycle-1}` in the "
                f"workspace before launching with --start-cycle."
            )
        expected_tag = f"evo-{args.start_cycle - 1}"
        try:
            head_sha = subprocess.check_output(
                ["git", "-C", str(ws_dir), "rev-parse", "HEAD"],
                text=True,
            ).strip()
            tag_sha = subprocess.check_output(
                ["git", "-C", str(ws_dir), "rev-parse", f"refs/tags/{expected_tag}"],
                text=True,
            ).strip()
        except subprocess.CalledProcessError as e:
            raise SystemExit(
                f"--start-cycle={args.start_cycle} requires tag "
                f"{expected_tag} in {ws_dir}; git rev-parse failed: {e}"
            )
        if head_sha != tag_sha:
            raise SystemExit(
                f"--start-cycle={args.start_cycle} requires workspace HEAD "
                f"to be at tag {expected_tag} (sha={tag_sha[:12]}); "
                f"got HEAD={head_sha[:12]}. Run "
                f"`git -C {ws_dir} reset --hard {expected_tag}` first."
            )
        logger.info(
            "Resume mode: workspace at %s validated at tag %s (sha=%s)",
            ws_dir, expected_tag, head_sha[:12],
        )
    else:
        if ws_dir.exists():
            shutil.rmtree(ws_dir)
        shutil.copytree(seed_dir, ws_dir)
        logger.info("Workspace: %s (from seed %s)", ws_dir, seed_dir)

    # LLM provider for evolver operators. _resolve_llm understands local
    # OpenAI-compatible paths (e.g. /fsx/models/Qwen3.5-9B → OpenAIProvider
    # honouring EVOLVER_OPENAI_BASE_URL) in addition to the standard Bedrock
    # model_id route. The unconditional BedrockProvider construction this
    # replaces would have failed for local evolvers like qwen35_9b.
    evolver_model = args.evolver_model or args.solver_model
    llm, _llm_kind = _resolve_llm(evolver_model, args.region)

    # Single env-var ablation switch. When MCP_BLANK_SKILL_ONLY_EVOLVE=1:
    #   - Only skills evolve (evolve_prompts=False, evolve_memory=False)
    #   - AutoSeedSkills is dropped from per_claim recipe (in controller.py)
    #   - improvement_threshold / stagnation_window are irrelevant under
    #     NoVerify but left in extra for legacy parity.
    blank_skill_only = os.environ.get("MCP_BLANK_SKILL_ONLY_EVOLVE") == "1"
    if blank_skill_only:
        logger.info(
            "MCP_BLANK_SKILL_ONLY_EVOLVE=1 — evolve_prompts=False, "
            "evolve_memory=False, AutoSeedSkills dropped in controller."
        )

    # Resolve effective max_cycles. If --passes or --cycle-per-batch is
    # set explicitly, the unified formula wins; otherwise honour --cycles.
    if args.passes is not None or args.cycle_per_batch is not None:
        passes = args.passes if args.passes is not None else 1
        cpb = args.cycle_per_batch if args.cycle_per_batch is not None else 1
        batches_per_pass = max(1, math.ceil(args.limit / max(1, args.batch_size)))
        effective_cycles = passes * batches_per_pass * cpb
        cycle_source = (
            f"passes={passes} × ⌈{args.limit}/{args.batch_size}⌉={batches_per_pass} "
            f"× cycle_per_batch={cpb}"
        )
    elif args.cycles is not None:
        effective_cycles = args.cycles
        cycle_source = f"--cycles={args.cycles}"
    else:
        effective_cycles = max(1, math.ceil(len(bench._cache["train"]) / max(1, args.batch_size)))
        cycle_source = (
            f"legacy full sweep: ceil({len(bench._cache['train'])}/"
            f"{args.batch_size})"
        )

    config = EvolveConfig(
        batch_size=args.batch_size,
        max_cycles=effective_cycles,
        parallel_workers=max(1, args.parallel),
        parallel_backend=args.parallel_backend,
        evolver_model=evolver_model,
        evolver_max_tokens=8000,
        evolve_prompts=not blank_skill_only,
        evolve_skills=True,
        evolve_memory=not blank_skill_only,
        evolve_tools=False,
        extra={
            "region": args.region,
            "max_tokens": args.max_tokens,
            "legacy_profile": "mcp",
            "prompt_max_chars": 4000,
            "skill_max_chars": 2000,
            "max_skills": 15,
            "include_claim_details": True,
            "include_judge_patterns": True,
            "include_task_type_stats": True,
            "include_evolution_history": True,
            "improvement_threshold": 0.03,
            "stagnation_window": 5,
        },
    )

    all_env_vars = {}
    if args.docker_image and key_registry:
        all_env_vars = {
            name: entry.value
            for name, entry in key_registry._keys.items()
            if entry.value
        }

    container = None
    shared_client = None
    try:
        if args.docker_image:
            if not pull_image(args.docker_image):
                raise SystemExit(f"Failed to pull image {args.docker_image}")
            container = McpAtlasContainer(
                args.docker_image,
                container_name=os.environ.get("MCP_CONTAINER_NAME") or "mcp-atlas-unified-evolve",
                env_vars=all_env_vars,
            )
            container.start()
            shared_client = McpClientWrapper(base_url=container.base_url)
            logger.info("Shared MCP-Atlas container ready: %s", container.base_url)

        agent = InterruptibleCodeExecMcpAgent(
            workspace_dir=ws_dir,
            model_id=args.solver_model,
            region=args.region,
            max_tokens=args.max_tokens,
            docker_image=None,
            key_registry=key_registry,
            shared_client=shared_client,
            max_tool_uses_per_task=args.max_tool_uses_per_task,
        )
        engine = UnifiedEngine(config, bench)
        # LLMBashEvolve (the LLM-driven operator in the per_claim recipe)
        # reads state["llm_provider"] so operators don't implicitly construct
        # a new Bedrock client per step.
        engine._operator_state.setdefault("LLMBashEvolve", {})["llm_provider"] = llm

        # Per-task streaming → summary.csv so progress is visible long before
        # the first results.jsonl entry (which only lands after every cycle's
        # batch finishes; with default LIMIT=500/BATCH_SIZE=30 = 17 cycles ×
        # ~2h each ≈ 34h before any output). Columns mirror the legacy
        # adaptive_evolve_all.py + adaptive_evolve_baseline.py schema so the
        # downstream readers (scripts/mcp_blank_progress_table.py) work
        # unchanged. ThreadPoolExecutor parallel backend calls the observer
        # from worker threads, so the lock + line-buffered open are required.
        summary_path = out_dir / "summary.csv"
        write_header = (not summary_path.exists()) or summary_path.stat().st_size == 0
        summary_lock = threading.Lock()
        summary_file = open(summary_path, "a", newline="", buffering=1)
        summary_writer = csv.writer(summary_file)
        if write_header:
            summary_writer.writerow([
                "task_id", "result", "score", "elapsed_s",
                "output_len", "detail", "evo_cycle",
            ])
            summary_file.flush()

        def _per_task_summary_writer(obs, cycle_idx):
            with summary_lock:
                try:
                    success = bool(obs.feedback.success)
                    result_str = "PASS" if success else "FAIL"
                    score = float(obs.feedback.score)
                    out_len = (
                        len(obs.trajectory.output)
                        if obs.trajectory is not None and obs.trajectory.output is not None
                        else 0
                    )
                    detail = (obs.feedback.detail or "")[:300]
                    summary_writer.writerow([
                        obs.task.id, result_str, f"{score:.4f}", "",
                        out_len, detail, cycle_idx,
                    ])
                    summary_file.flush()
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "summary.csv write failed for %s (cycle %d): %s",
                        getattr(getattr(obs, "task", None), "id", "?"),
                        cycle_idx, exc,
                    )

        loop = EvolutionLoop(
            agent=agent, benchmark=bench, engine=engine, config=config,
            task_observer=_per_task_summary_writer,
        )

        logger.info(
            "Running %d cycles (%s) × batch_size=%d (limit=%d, solver=%s, evolver=%s)",
            effective_cycles, cycle_source, args.batch_size, args.limit,
            args.solver_model, evolver_model,
        )

        # ── Resume bookkeeping ────────────────────────────────────────
        # When resuming from cycle N>1: load score_history from the
        # cell-root results.metrics.json (written by the previous run),
        # validate its length, and advance the benchmark cursor by
        # (N-1)*batch_size so cycle N draws fresh tasks instead of
        # re-solving cycle 1's batch.
        existing_score_history: list[float] | None = None
        if args.start_cycle > 1:
            metrics_path = out_dir / "results.metrics.json"
            try:
                existing_metrics = json.loads(metrics_path.read_text())
            except FileNotFoundError:
                raise SystemExit(
                    f"--start-cycle={args.start_cycle} requires existing "
                    f"results.metrics.json at {metrics_path}; not found."
                )
            except json.JSONDecodeError as e:
                raise SystemExit(
                    f"--start-cycle={args.start_cycle} requires valid JSON "
                    f"at {metrics_path}; parse error: {e}"
                )
            if not isinstance(existing_metrics, dict):
                raise SystemExit(
                    f"--start-cycle={args.start_cycle}: "
                    f"results.metrics.json top-level must be a JSON object, "
                    f"got {type(existing_metrics).__name__}"
                )
            existing_score_history = existing_metrics.get("score_history")
            if existing_score_history is None:
                raise SystemExit(
                    f"--start-cycle={args.start_cycle}: "
                    f"results.metrics.json missing 'score_history' key"
                )
            if not isinstance(existing_score_history, list):
                raise SystemExit(
                    f"--start-cycle={args.start_cycle}: 'score_history' must "
                    f"be a list, got {type(existing_score_history).__name__}"
                )
            if len(existing_score_history) != args.start_cycle - 1:
                raise SystemExit(
                    f"--start-cycle={args.start_cycle}: expected score_history "
                    f"length {args.start_cycle - 1}, got {len(existing_score_history)}"
                )
            # Per-element type validation. Non-numeric entries would silently
            # corrupt the resumed history and crash convergence math
            # (`_is_score_converged` does float arithmetic).
            for idx, v in enumerate(existing_score_history):
                if not isinstance(v, (int, float)) or isinstance(v, bool):
                    raise SystemExit(
                        f"--start-cycle={args.start_cycle}: score_history[{idx}] "
                        f"must be a number, got {type(v).__name__}: {v!r}"
                    )
            # Advance the benchmark cursor by replaying (N-1) get_tasks calls.
            # This matches what the original run would have done: each cycle
            # calls get_tasks(split='train', limit=batch_size) exactly once;
            # the cursor advances per-call and RESETS (not modulo) when it
            # passes the dataset end (see mcp_atlas.py: cursor sets to 0
            # whenever it reaches len(rows)). Replaying N-1 calls reproduces
            # the original cursor trajectory bit-for-bit; do NOT compute
            # `(N-1)*batch_size % n` directly — that would diverge on small
            # dataset / end-of-sweep wrap.
            for _ in range(args.start_cycle - 1):
                bench.get_tasks(split="train", limit=args.batch_size)
            logger.info(
                "Resume: loaded %d prior cycle scores; bench cursor at %d "
                "after %d replay calls of batch=%d",
                len(existing_score_history), bench._cursor,
                args.start_cycle - 1, args.batch_size,
            )

        try:
            result = loop.run(
                cycles=effective_cycles,
                start_cycle=args.start_cycle,
                existing_score_history=existing_score_history,
            )
        finally:
            try:
                summary_file.flush()
                summary_file.close()
            except Exception:  # noqa: BLE001
                pass
    finally:
        if shared_client:
            shared_client.close()
        if container:
            container.stop()

    results_path = out_dir / "results.jsonl"
    with open(results_path, "w") as f:
        for cycle_idx, score in enumerate(result.score_history, 1):
            f.write(json.dumps({"cycle": cycle_idx, "score": score}) + "\n")

    # Interrupted-task ledger. _interrupted_records is populated by
    # InterruptibleCodeExecMcpAgent.solve() each time the cap fires.
    # These tasks were dropped from observations by engine/loop.py, so
    # they do NOT appear in score_history / final_score / summary.csv.
    interrupted_path = out_dir / "interrupted_tasks.jsonl"
    with open(interrupted_path, "w") as f:
        for rec in _interrupted_records:
            f.write(json.dumps(rec) + "\n")

    (out_dir / "results.metrics.json").write_text(json.dumps({
        "cycles_completed": result.cycles_completed,
        "final_score": result.final_score,
        "score_history": list(result.score_history),
        "converged": result.converged,
        "max_tool_uses_per_task": args.max_tool_uses_per_task,
        "interrupted_count": len(_interrupted_records),
        "interrupted_task_ids": sorted({r["task_id"] for r in _interrupted_records}),
        "engine": "UnifiedEngine",
        "legacy_settings": {
            "solver_model": args.solver_model,
            "evolver_model": evolver_model,
            "judge_model": args.judge_model,
            "use_litellm": False,
            "docker_image": args.docker_image,
            "limit": args.limit,
            "batch_size": args.batch_size,
            "parallel": args.parallel,
            "parallel_backend": args.parallel_backend,
            "evolver_max_tokens": 8000,
            "prompt_max_chars": 4000,
            "skill_max_chars": 2000,
            "max_skills": 15,
            "improvement_threshold": 0.03,
            "stagnation_window": 5,
        },
        "recipe": (
            "per_claim (PassFailReader+ClaimReader+PatternDetector+"
            "ClaimTypeAnalyzer+ScoreCurveReader | "
            "FixHallucinations+AutoSeedSkills+LLMBashEvolve+SanityCheck)"
        ),
        "workspace": str(ws_dir),
    }, indent=2))

    logger.info(
        "Done. cycles=%d final_score=%.4f interrupted=%d. Results: %s",
        result.cycles_completed, result.final_score,
        len(_interrupted_records), results_path,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
