"""PolyBench solver entry points for solve_all_with_evolution.py.

Provides: setup(), solve_one(), build_prompts().

Reasoning + optional sandbox tools.  When evolved tools exist in the
workspace, each task gets a lightweight Docker container (same image as
the evolver sandbox) so the solver can call them via a ``bash`` tool.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from .sandbox import start_sandbox, stop_sandbox


def setup(args) -> dict:
    from agent_evolve.agents.polybench.agent import PolyBenchAgent
    from agent_evolve.benchmarks.polybench import PolyBenchBenchmark
    from agent_evolve.config import EvolveConfig

    out_dir = Path(args.output_dir)
    exp_tag = out_dir.name

    ws_dir = out_dir / "polybench"
    if not ws_dir.exists():
        seed = Path(args.seed_workspace)
        ws_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(seed, ws_dir)

    config = EvolveConfig.from_yaml(args.config) if args.config else EvolveConfig()
    skip = frozenset(
        name for name, enabled in [
            ("skills", config.evolve_skills),
            ("memory", config.evolve_memory),
            ("tools", config.evolve_tools),
        ] if not enabled
    )
    db_path = args.dataset
    if not db_path:
        raise ValueError("--dataset must point to the PolyBench SQLite database path")

    agent = PolyBenchAgent(
        workspace_dir=ws_dir,
        model_id=args.model_id,
        region=args.region,
        max_tokens=args.max_tokens,
        skip_layers=skip,
    )
    agent._db_path = db_path  # pass through for build_prompts

    benchmark = PolyBenchBenchmark(db_path=db_path, shuffle=False, holdout_ratio=config.holdout_ratio)

    return {
        "agent": agent,
        "benchmark": benchmark,
        "executor": "process",
        "exp_tag": exp_tag,
    }


def build_prompts(agent, tasks: list, emit_pieces: bool = False) -> dict:
    # Serialize evolved tools so solve_one can copy them into the sandbox
    tool_files = {}
    for t in agent.tool_registry:
        name = t.get("name", "")
        if name:
            impl = agent.workspace.read_tool(name)
            if impl:
                tool_files[f"{name}.py"] = impl

    out = {
        "system_prompt": agent._build_system_prompt(),
        "user_prompts": {t.id: t.input for t in tasks},
        "dataset": agent._db_path,
        "tool_files": tool_files,
    }
    if emit_pieces:
        from ...protocol.adaptation.harness_filter import build_harness_pieces
        out["harness_pieces"] = build_harness_pieces(agent)
    return out


def _extract_conv(messages):
    """Extract readable conversation from strands agent messages."""
    conv = []
    for msg in messages:
        role = msg.get("role", "unknown")
        parts = []
        for b in msg.get("content", []):
            if isinstance(b, str):
                parts.append(b)
            elif isinstance(b, dict):
                if "text" in b:
                    parts.append(b["text"])
                elif "toolUse" in b:
                    tu = b["toolUse"]
                    inp = json.dumps(tu.get("input", {}))
                    parts.append(f"[tool_use: {tu['name']}]\n{inp}")
                elif "toolResult" in b:
                    tr = b["toolResult"]
                    txt = "".join(
                        c.get("text", "") for c in tr.get("content", [])
                        if isinstance(c, dict)
                    )
                    parts.append(f"[tool_result]\n{txt}")
        if parts:
            conv.append({"role": role, "content": "\n".join(parts)})
    return conv


# ── Solver ───────────────────────────────────────────────────────────

def solve_one(task_dict: dict, args_dict: dict) -> dict:
    """Solve one PolyBench prediction task in a subprocess."""
    import logging
    import os
    import signal
    import subprocess
    import sys
    import time

    sys.setrecursionlimit(10000)

    # Clean AWS environment to force IAM instance role usage
    for key in ['AWS_PROFILE', 'AWS_SHARED_CREDENTIALS_FILE', 'AWS_CONFIG_FILE']:
        if key in os.environ:
            del os.environ[key]

    # Patch strands telemetry (same as SWE backend)
    import strands.telemetry.tracer as _strands_tracer
    _orig_add_msgs = _strands_tracer.Tracer._add_event_messages

    def _safe_add_event_messages(self, span, messages):
        if not span or not getattr(span, "is_recording", lambda: False)():
            return
        try:
            _orig_add_msgs(self, span, messages)
        except RecursionError:
            pass

    _strands_tracer.Tracer._add_event_messages = _safe_add_event_messages

    from strands import Agent, tool
    from botocore.exceptions import ClientError, EventStreamError
    from strands.models import BedrockModel
    from strands.agent.conversation_manager import SlidingWindowConversationManager
    from strands.hooks.events import AfterModelCallEvent, BeforeToolCallEvent
    from strands.types.exceptions import ModelThrottledException
    from agent_evolve.benchmarks.polybench import PolyBenchBenchmark
    from agent_evolve.types import Task, Trajectory
    import boto3

    # Clear boto3 session cache to ensure fresh credentials
    # boto3._get_default_session removed — breaks evolver in main thread
    boto3.DEFAULT_SESSION = None

    # Patch BedrockModel for assistant-message-prefill errors + transient retries
    _orig_stream = BedrockModel._stream

    _RETRYABLE_ERRORS = ("internalServerException", "modelStreamErrorException",
                         "ThrottlingException", "serviceUnavailableException")

    def _safe_stream(self, callback, messages, *args, **kwargs):
        import time as _time

        last_exc = None
        for attempt in range(4):
            try:
                return _orig_stream(self, callback, messages, *args, **kwargs)
            except ClientError as exc:
                if "must end with a user message" not in str(exc):
                    raise
                messages.append({"role": "user", "content": [{"text": "continue"}]})
                return _orig_stream(self, callback, messages, *args, **kwargs)
            except (EventStreamError, ConnectionError, TimeoutError) as exc:
                exc_str = str(exc)
                if not any(e in exc_str for e in _RETRYABLE_ERRORS) and \
                        not isinstance(exc, (ConnectionError, TimeoutError)):
                    raise
                last_exc = exc
                wait = 2 ** attempt
                logging.getLogger(__name__).warning(
                    "Bedrock transient error (attempt %d/4, retry in %ds): %s",
                    attempt + 1, wait, exc_str[:120],
                )
                _time.sleep(wait)
        raise last_exc

    BedrockModel._stream = _safe_stream

    logging.basicConfig(level=logging.INFO,
                        format=f"%(asctime)s [{task_dict['id']}] %(message)s")
    for n in ("botocore", "urllib3", "httpcore", "httpx",
              "strands.models", "strands.tools", "strands.telemetry"):
        logging.getLogger(n).setLevel(logging.WARNING)
    log = logging.getLogger("polybench_worker")

    task_id = task_dict["id"]
    max_turns = args_dict.get("max_turns", 80)
    task_timeout = args_dict.get("task_timeout", 600)
    out_dir = Path(args_dict["output_dir"])

    result = {
        "instance_id": task_id, "success": False, "score": 0.0, "detail": "",
        "elapsed": 0.0, "turns": 0,
        "input_tokens": 0, "output_tokens": 0,
        "max_turns_hit": False, "timed_out": False, "error": None,
        "batch_num": args_dict.get("batch_num", 0),
        "evo_cycle": args_dict.get("evo_cycle", 0),
    }

    system_prompt = args_dict["system_prompt"]
    user_prompt = args_dict["user_prompts"][task_id]
    tool_files = args_dict.get("tool_files", {})

    # Per-task harness filter (retrieval adaptation M1/M2). No-op for M0/M4:
    # task_filters is absent so `_tf` is None and the harness is unchanged.
    _tf = (args_dict.get("task_filters") or {}).get(task_id)
    if _tf is not None:
        from ...protocol.adaptation.harness_filter import apply_task_filter
        system_prompt, tool_files = apply_task_filter(
            system_prompt, tool_files, _tf, args_dict.get("harness_pieces"))

    _submitted = [False]
    _submit_output = [None]
    tool_call_count = [0]
    container_name = None

    def turn_limiter(event: BeforeToolCallEvent):
        if _submitted[0]:
            event.cancel_tool = "Already submitted. No more tool calls."
            return
        if event.tool_use.get("name") == "submit":
            return
        tool_call_count[0] += 1
        if tool_call_count[0] > max_turns:
            event.cancel_tool = f"Turn limit reached ({max_turns}). Call submit now."

    @tool
    def submit(
        decision: str,
        side: str = "",
        confidence: float = 0.5,
        reasoning: str = "",
    ) -> str:
        """Submit your prediction for this market.

        Call this when you have finished analyzing the market.
        After calling submit, do NOT call any more tools.

        Args:
            decision: BUY or SKIP. BUY means you want to take a position.
            side: Which outcome to buy — must match one of the listed outcomes (e.g. YES, NO, or a specific outcome name).
            confidence: Your confidence in this prediction, between 0 and 1.
            reasoning: Brief summary of your analysis.
        """
        _submitted[0] = True
        output = {
            "decision": decision.upper(),
            "side": side.upper() if side else "",
            "confidence": max(0.0, min(1.0, confidence)),
            "reasoning": reasoning,
        }
        _submit_output[0] = json.dumps(output)
        return (
            f"Prediction submitted: {decision} {side} (conf={confidence:.2f})\n"
            "You are done. Do NOT call any more tools."
        )

    @tool
    def bash(command: str) -> str:
        """Execute a bash command in the analysis sandbox.

        Python3 is available. Evolved tools are in /tools/.
        Example: python3 /tools/ev_calculator.py 0.85 0.796 0.017

        Args:
            command: The bash command to execute.
        """
        if not container_name:
            return "ERROR: No sandbox available (no evolved tools loaded)."
        try:
            r = subprocess.run(
                ["docker", "exec", "-w", "/tools", container_name,
                 "bash", "-c", command],
                capture_output=True, text=True, timeout=30,
            )
            out = (r.stdout or "") + (r.stderr or "")
            if not out.strip():
                return "(no output)"
            if len(out) > 8000:
                out = out[:4000] + "\n...[truncated]...\n" + out[-4000:]
            return out
        except subprocess.TimeoutExpired:
            return "ERROR: Command timed out after 30s."
        except Exception as e:
            return f"ERROR: {e}"

    _thought_history = []
    _branches = {}

    @tool
    def sequentialthinking(
        thought: str,
        next_thought_needed: bool,
        thought_number: int,
        total_thoughts: int,
        is_revision: bool = False,
        revises_thought: int = 0,
        branch_from_thought: int = 0,
        branch_id: str = "",
        needs_more_thoughts: bool = False,
    ) -> str:
        """Break down complex problems through step-by-step reasoning.

        Use this to plan, analyze, revise, and verify before making a prediction.
        Each thought builds on previous ones. You can revise, branch, or extend.

        Args:
            thought: Your current thinking step.
            next_thought_needed: True if more thinking is needed.
            thought_number: Current step number (starts at 1).
            total_thoughts: Estimated total steps (adjust as needed).
            is_revision: True if revising a previous thought.
            revises_thought: Which thought number is being revised.
            branch_from_thought: Branching point thought number.
            branch_id: Branch identifier.
            needs_more_thoughts: True if more steps needed beyond total.
        """
        entry = {
            "thought": thought,
            "thought_number": thought_number,
            "total_thoughts": total_thoughts,
            "next_thought_needed": next_thought_needed,
            "is_revision": is_revision,
            "revises_thought": revises_thought or None,
            "branch_from_thought": branch_from_thought or None,
            "branch_id": branch_id or None,
            "needs_more_thoughts": needs_more_thoughts,
        }
        if thought_number > total_thoughts:
            entry["total_thoughts"] = thought_number
        _thought_history.append(entry)
        if branch_from_thought and branch_id:
            _branches.setdefault(branch_id, []).append(entry)
        status = {
            "thought_number": entry["thought_number"],
            "total_thoughts": entry["total_thoughts"],
            "next_thought_needed": next_thought_needed,
            "branches": list(_branches.keys()),
            "history_length": len(_thought_history),
        }
        return f"Thinking step recorded.\n{json.dumps(status, indent=2)}"

    def _timeout_handler(signum, frame):
        raise TimeoutError(f"Timed out after {task_timeout}s")

    try:
        # Start sandbox if evolved tools exist
        if tool_files:
            try:
                container_name = start_sandbox(task_id, tool_files)
            except Exception as e:
                log.warning("Sandbox start failed (tools unavailable): %s", e)

        from botocore.config import Config as BotocoreConfig
        model = BedrockModel(
            model_id=args_dict["model_id"],
            region_name=args_dict["region"],
            max_tokens=args_dict["max_tokens"],
            temperature=args_dict.get("solver_temperature", 0.0),
            boto_client_config=BotocoreConfig(
                read_timeout=600,
                retries={"max_attempts": 8, "mode": "adaptive"},
            ),
        )

        # bash tool available only when sandbox is running
        tools = [sequentialthinking, submit]
        if container_name:
            tools.insert(0, bash)

        agent = Agent(
            model=model,
            system_prompt=system_prompt,
            tools=tools,
            callback_handler=None,
            conversation_manager=SlidingWindowConversationManager(
                window_size=max_turns * 2 + 10),
        )
        agent.hooks.add_callback(BeforeToolCallEvent, turn_limiter)

        _throttle_count = [0]

        def throttle_logger(event: AfterModelCallEvent):
            if event.exception and isinstance(event.exception, ModelThrottledException):
                _throttle_count[0] += 1
                log.warning("API throttled (attempt %d) for %s — retrying with backoff",
                            _throttle_count[0], task_id)

        agent.hooks.add_callback(AfterModelCallEvent, throttle_logger)

        t0 = time.time()
        # Persist a partial trajectory after every tool call so the harness
        # can recover real state for Futures cancelled by the batch deadline.
        from agent_evolve.agents._partial_trajectory import install_partial_writer
        install_partial_writer(
            agent,
            task_id=task_id,
            out_dir=out_dir,
            turn_counter=tool_call_count,
            start_time=t0,
            extract_conversation=lambda a: _extract_conv(a.messages),
        )

        signal.signal(signal.SIGALRM, _timeout_handler)
        signal.alarm(task_timeout)

        response = None
        timed_out = False
        try:
            response = agent(user_prompt)
        except TimeoutError:
            timed_out = True
        except RecursionError:
            print(f"[{task_id}] Agent hit RecursionError", flush=True)
        except Exception as e:
            try:
                log.warning("Agent error: %s", e)
            except RecursionError:
                print(f"[{task_id}] Agent error (logging failed): {type(e).__name__}", flush=True)
        finally:
            signal.alarm(0)

        elapsed = time.time() - t0
        try:
            u = response.metrics.accumulated_usage
            result["input_tokens"] = u.get("inputTokens", 0)
            result["output_tokens"] = u.get("outputTokens", 0)
        except Exception:
            pass

        output = _submit_output[0] or ""
        result["elapsed"] = elapsed
        result["turns"] = tool_call_count[0]
        result["max_turns_hit"] = tool_call_count[0] >= max_turns
        result["timed_out"] = timed_out
        result["submitted"] = _submitted[0]
        result["output"] = output

        # Extract conversation
        try:
            conversation = _extract_conv(agent.messages)
        except Exception:
            conversation = []
        result["conversation"] = conversation

        # Save files
        sid = task_id.replace("/", "_")
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"prediction_{sid}.json").write_text(output or "{}")
        if conversation:
            try:
                (out_dir / f"trajectory_{sid}.json").write_text(
                    json.dumps(conversation, indent=2, ensure_ascii=False))
            except Exception:
                pass
        try:
            from agent_evolve.agents._partial_trajectory import clear_partial_trajectory
            final_partial = clear_partial_trajectory(out_dir, task_id)
            if final_partial and final_partial.get("tool_timings"):
                result["tool_timings"] = final_partial["tool_timings"]
        except Exception:
            pass

        # Evaluate
        if args_dict.get("run_eval", True):
            task = Task(id=task_id, input=task_dict["input"], metadata=task_dict["metadata"])
            bm = PolyBenchBenchmark(db_path=args_dict["dataset"], shuffle=False, holdout_ratio=0.0)
            fb = bm.evaluate(task, Trajectory(task_id=task_id, output=output))
            result["success"] = fb.success
            result["score"] = fb.score
            result["detail"] = fb.detail
            # Merge evaluation metrics (benchmark-specific) into result
            for k, v in (fb.raw or {}).items():
                if k not in result:
                    result[k] = v

    except Exception as e:
        result["error"] = str(e)
        result["detail"] = f"Worker error: {e}"
    finally:
        signal.alarm(0)
        stop_sandbox(container_name)

    return result
