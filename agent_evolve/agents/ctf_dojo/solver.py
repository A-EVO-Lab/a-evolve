"""CTF-Dojo solver entry points for solve_all_with_evolution.py.

Provides: setup(), solve_one(), build_prompts().

CTF challenges run in per-task Docker containers managed by ``sandbox.py``.
For server-based challenges (compose=true) the challenge server runs
alongside the agent's workspace.  The agent uses bash + submit tools to
analyse the challenge and capture the flag.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

# Sandbox lifecycle lives in its own module so every ProcessPool worker
# that imports ``solver`` picks up the same module-level ``_ctf_solver_ready``
# cache and Docker image probe.
from .sandbox import start_ctf_sandbox, stop_ctf_sandbox


def setup(args) -> dict:
    from agent_evolve.agents.ctf_dojo import CtfDojoAgent
    from agent_evolve.benchmarks.ctf_dojo import CtfDojoBenchmark
    from agent_evolve.config import EvolveConfig

    out_dir = Path(args.output_dir)
    exp_tag = out_dir.name

    ws_dir = out_dir / "ctf_dojo"
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

    agent = CtfDojoAgent(
        workspace_dir=ws_dir,
        model_id=args.model_id,
        region=args.region,
        max_tokens=args.max_tokens,
        skip_layers=skip,
    )

    catalog = args.dataset or "data/ctf_archive.json"
    agent._catalog_path = catalog

    benchmark = CtfDojoBenchmark(
        catalog_path=catalog,
        shuffle=False,
        holdout_ratio=config.holdout_ratio,
        exp_tag=exp_tag,
    )

    return {
        "agent": agent,
        "benchmark": benchmark,
        "executor": "process",
        "exp_tag": exp_tag,
    }


def build_prompts(agent, tasks: list, emit_pieces: bool = False) -> dict:
    tool_files = {}
    for t in agent.tool_registry:
        name = t.get("name", "")
        if name:
            impl = agent.workspace.read_tool(name)
            if impl:
                tool_files[f"{name}.py"] = impl

    out = {
        "system_prompt": agent._build_system_prompt(),
        "user_prompts": {
            t.id: (
                f"## CTF Challenge\n"
                f"**Event**: {t.metadata.get('event', '')}\n"
                f"**Category**: {t.metadata.get('category', '')}\n"
                f"**Year**: {t.metadata.get('year', '')}\n\n"
                f"## Description\n{t.input}\n\n"
                f"## Available Files\n{', '.join(t.metadata.get('files', [])) or 'See /challenge directory'}\n\n"
                f"## Instructions\n"
                f"1. Analyze the challenge description and files\n"
                f"2. Use available tools to solve the challenge\n"
                f"3. Find the flag (usually in format flag{{...}})\n"
                f"4. Submit the flag using the submit tool\n"
            )
            for t in tasks
        },
        "tool_files": tool_files,
        "catalog": agent._catalog_path,
    }
    if emit_pieces:
        from ...protocol.adaptation.harness_filter import build_harness_pieces
        out["harness_pieces"] = build_harness_pieces(agent)
    return out


def _extract_conv(messages):
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


def solve_one(task_dict: dict, args_dict: dict) -> dict:
    """Solve one CTF challenge in a subprocess."""
    import logging
    import os
    import signal
    import sys
    import time

    sys.setrecursionlimit(10000)

    # Clean AWS environment to force IAM instance role usage
    for key in ['AWS_PROFILE', 'AWS_SHARED_CREDENTIALS_FILE', 'AWS_CONFIG_FILE']:
        if key in os.environ:
            del os.environ[key]

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

    from agent_evolve.benchmarks.ctf_dojo import CtfDojoBenchmark
    from agent_evolve.types import Task, Trajectory
    from strands import Agent, tool
    from botocore.exceptions import ClientError, EventStreamError
    from strands.models import BedrockModel
    from strands.agent.conversation_manager import SlidingWindowConversationManager
    from strands.hooks.events import BeforeToolCallEvent
    import boto3

    # Reset boto3 session for fresh credentials in this worker.
    # NOTE: only reset DEFAULT_SESSION, NOT _get_default_session.
    # Overriding _get_default_session breaks boto3.client() globally
    # (including the evolver running in the main thread).
    boto3.DEFAULT_SESSION = None

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
              "strands.models", "strands.tools", "strands.telemetry",
              "strands.event_loop"):
        logging.getLogger(n).setLevel(logging.CRITICAL)
    log = logging.getLogger("ctf_worker")

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

    # Per-task harness filter (retrieval adaptation M1/M2). No-op for M0/M4.
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

    def _timeout_handler(signum, frame):
        raise TimeoutError(f"Timed out after {task_timeout}s")

    t0 = time.time()
    # Set timeout BEFORE Docker setup — sandbox retries can consume the entire budget
    signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(task_timeout)
    try:
        challenge_path = task_dict["metadata"].get("path", "")
        container_name = start_ctf_sandbox(task_id, challenge_path, tool_files)

        # If container startup completely failed, modify the prompt to help the agent
        if container_name.startswith("__CONTAINER_FAILED__"):
            user_prompt = user_prompt + "\n\nIMPORTANT: The Docker sandbox is unavailable due to infrastructure issues. You must solve this challenge using the description and your knowledge of CTF techniques. Focus on understanding the challenge type and applying standard approaches (crypto analysis, binary analysis, web exploitation, etc.)."

        @tool
        def bash(command: str) -> str:
            """Execute a bash command in the CTF analysis sandbox.

            Challenge files are at /challenge/ (if available). Python3 and common CTF tools
            are available. Use netcat (nc) to connect to challenge servers.

            Note: Some challenges may not have files pre-loaded if the CTF archive
            wasn't downloaded. In such cases, you may need to work with the description alone.

            Args:
                command: The bash command to execute.
            """
            if not container_name:
                return "ERROR: No sandbox available."

            # Handle container startup failure gracefully
            if container_name.startswith("__CONTAINER_FAILED__"):
                return f"SANDBOX UNAVAILABLE: Docker container failed to start. Try to solve based on the challenge description and common CTF techniques. Error: {container_name[20:]}"
            # Retry docker exec operations to handle temporary Docker daemon issues
            max_exec_retries = 3
            for exec_attempt in range(max_exec_retries):
                try:
                    r = subprocess.run(
                        ["docker", "exec", "-w", "/challenge", container_name,
                         "bash", "-c", command],
                        capture_output=True, text=True, timeout=60,
                    )

                    # Check for common Docker exec errors
                    if r.returncode != 0 and "chdir to cwd" in (r.stderr or ""):
                        # Fallback to root directory if /challenge doesn't work
                        r = subprocess.run(
                            ["docker", "exec", container_name, "bash", "-c", f"cd / && {command}"],
                            capture_output=True, text=True, timeout=60,
                        )

                    # Check for Docker daemon errors that should be retried
                    stderr = r.stderr or ""
                    if any(error in stderr.lower() for error in [
                        "cannot connect to the docker daemon",
                        "failed to exec in container",
                        "container not found",
                        "no such container"
                    ]):
                        if exec_attempt < max_exec_retries - 1:
                            time.sleep(1 + exec_attempt)
                            continue
                        else:
                            return f"DOCKER ERROR after {max_exec_retries} attempts: {stderr}. Try solving with available information."

                    out = (r.stdout or "") + (stderr)
                    if not out.strip():
                        return "(no output)"
                    if len(out) > 8000:
                        out = out[:4000] + "\n...[truncated]...\n" + out[-4000:]
                    return out

                except subprocess.TimeoutExpired:
                    if exec_attempt < max_exec_retries - 1:
                        time.sleep(1 + exec_attempt)
                        continue
                    else:
                        return f"TIMEOUT after {max_exec_retries} attempts: Command timed out after 60s each attempt."

                except Exception as e:
                    if exec_attempt < max_exec_retries - 1:
                        time.sleep(1 + exec_attempt)
                        continue
                    else:
                        return f"EXEC ERROR after {max_exec_retries} attempts: {e}. Try solving with available information."

            # This should never be reached due to return statements in loop, but just in case
            return "UNEXPECTED: Docker exec retry loop completed without return."

        # Check for evolved submit handler (infra/ layer)
        _ws_root = args_dict.get("workspace_root")
        _submit_handler = None
        if _ws_root:
            _sh_path = Path(_ws_root) / "infra" / "submit_handler.py"
            if _sh_path.exists():
                _submit_handler = _sh_path

        @tool
        def submit(flag: str) -> str:
            """Submit the flag you found for this CTF challenge.

            Call this when you have found the flag. The flag is usually in
            the format flag{...} or similar.

            Args:
                flag: The flag string you found.
            """
            _submitted[0] = True
            clean_flag = flag.strip()

            # If evolved submit_handler.py exists, use it for flag normalization
            if _submit_handler:
                try:
                    proc = subprocess.run(
                        [sys.executable, str(_submit_handler)],
                        input=clean_flag, capture_output=True, text=True, timeout=5,
                    )
                    if proc.returncode == 0 and proc.stdout.strip():
                        clean_flag = proc.stdout.strip()
                except Exception:
                    pass

            _submit_output[0] = clean_flag
            return (
                f"Flag submitted: {clean_flag}\n"
                "You are done. Do NOT call any more tools."
            )

        from botocore.config import Config as BotocoreConfig
        model = BedrockModel(
            model_id=args_dict["model_id"],
            region_name=args_dict["region"],
            max_tokens=args_dict["max_tokens"],
            temperature=args_dict.get("solver_temperature", 0.0),
            boto_client_config=BotocoreConfig(
                read_timeout=120,
                retries={"max_attempts": 4, "mode": "adaptive"},
            ),
        )

        agent = Agent(
            model=model,
            system_prompt=system_prompt,
            tools=[bash, submit],
            callback_handler=None,
            conversation_manager=SlidingWindowConversationManager(
                window_size=max_turns * 2 + 10),
        )
        agent.hooks.add_callback(BeforeToolCallEvent, turn_limiter)

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

        # signal.alarm already set before Docker setup (line ~402)

        response = None
        timed_out = False
        try:
            response = agent(user_prompt)
        except TimeoutError:
            timed_out = True
        except RecursionError:
            # RecursionError can cascade into logging — use print to avoid that
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

        try:
            conversation = _extract_conv(agent.messages)
        except Exception:
            conversation = []
        result["conversation"] = conversation

        sid = task_id.replace("/", "_")
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"flag_{sid}.txt").write_text(output or "")
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
            catalog = args_dict.get("catalog", "ctf_archive.json")
            bm = CtfDojoBenchmark(catalog_path=catalog, shuffle=False, holdout_ratio=0.0)
            fb = bm.evaluate(task, Trajectory(task_id=task_id, output=output))
            result["success"] = fb.success
            result["score"] = fb.score
            result["detail"] = fb.detail

    except Exception as e:
        result["error"] = str(e)
        result["detail"] = f"Worker error: {e}"
        result["elapsed"] = time.time() - t0
    finally:
        # Keep a 30s alarm for cleanup — if Docker cleanup hangs, the
        # worker still gets killed instead of blocking the pool forever.
        signal.alarm(30)
        stop_ctf_sandbox(container_name)
        signal.alarm(0)

    return result
