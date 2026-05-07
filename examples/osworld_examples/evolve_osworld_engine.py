#!/usr/bin/env python3
"""OSWorld evolution using the ProposeCurateEngine.

This is the engine-based version of evolve_osworld.py. The pipeline is:
  1. Parallel solve (ReAct agent on OSWorld VM)
  2. Parallel evaluate (env.evaluate() → 0.0 or 1.0)
  3. Analyze+propose skills (in solver conversation context)
  4. ProposeCurateEngine.step() — per-topic + general curation
  5. Reload workspace, next batch

Usage:
    python examples/osworld_examples/evolve_osworld_engine.py \
        --task-file evaluation_examples/test_all.json \
        --domain libreoffice_calc \
        --batch-size 5 --workers 2 \
        --output-dir outputs/osworld_evolve_engine_v1
"""
from __future__ import annotations

import argparse
import atexit
import json
import logging
import os
import re
import shutil
import signal
import sys
import threading
import time
import queue as queue_mod
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

OSWORLD_PATH = os.environ.get("OSWORLD_PATH")
if not OSWORLD_PATH:
    raise EnvironmentError("OSWORLD_PATH must be set to the OSWorld repo directory")
sys.path.insert(0, OSWORLD_PATH)
os.environ["BYPASS_TOOL_CONSENT"] = "true"

_RUN_ID = f"evolve-{os.getpid()}-{int(time.time())}"
os.environ["OSWORLD_RUN_ID"] = _RUN_ID

from agent_evolve.agents.osworld.react_solver import (
    react_solve, extract_conversation, SYSTEM_PROMPT as OSW_SYSTEM_PROMPT,
)
from agent_evolve.algorithms.propose_curate import ProposeCurateEngine
from agent_evolve.config import EvolveConfig
from agent_evolve.contract.workspace import AgentWorkspace
from agent_evolve.engine.observer import Observer
from agent_evolve.types import Feedback, Observation, Task, Trajectory

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# VM lifecycle management (reused from original)
# ---------------------------------------------------------------------------
_live_envs: list = []
_live_envs_lock = threading.Lock()
_cleanup_done = False


def _cleanup_all_envs():
    global _cleanup_done
    if _cleanup_done:
        return
    _cleanup_done = True
    with _live_envs_lock:
        envs = list(_live_envs)
        _live_envs.clear()
    for env in envs:
        try:
            env.close()
        except Exception:
            pass


def _signal_handler(sig, frame):
    _cleanup_all_envs()
    sys.exit(1)


atexit.register(_cleanup_all_envs)
signal.signal(signal.SIGTERM, _signal_handler)
try:
    signal.signal(signal.SIGINT, _signal_handler)
except ValueError:
    pass

# ---------------------------------------------------------------------------
# Model map
# ---------------------------------------------------------------------------
MODEL_MAP = {
    "1": "us.anthropic.claude-opus-4-6-v1",
    "2": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    "3": "us.anthropic.claude-opus-4-5-20251101-v1:0",
}

# ---------------------------------------------------------------------------
# Task loading
# ---------------------------------------------------------------------------

def load_osworld_tasks(task_file: str, domain: str = None) -> list[dict]:
    """Load OSWorld task configs from a JSON file."""
    task_file = Path(task_file)
    base_dir = task_file.parent

    with open(task_file) as f:
        meta = json.load(f)

    if not isinstance(meta, dict):
        return [t for t in meta if not domain or t.get("domain") == domain]

    tasks = []
    for dom, task_ids in meta.items():
        if domain and dom != domain:
            continue
        for tid in task_ids:
            config_path = base_dir / "examples" / dom / f"{tid}.json"
            if not config_path.exists():
                continue
            with open(config_path) as f:
                config = json.load(f)
            config.setdefault("id", tid)
            config.setdefault("domain", dom)
            tasks.append(config)
    return tasks


def _task_id(task_config: dict) -> str:
    return task_config.get("id", task_config.get("task_id", "unknown"))


# ---------------------------------------------------------------------------
# Trajectory helpers (compressed from original)
# ---------------------------------------------------------------------------

def _extract_trajectory_signals(conversation: list[dict]) -> dict:
    n_turns = n_actions = n_errors = 0
    for entry in conversation:
        if entry.get("role") == "assistant":
            n_turns += 1
            for part in entry.get("parts", []):
                if part.get("type") == "tool_use" and part.get("name") == "computer":
                    n_actions += 1
        elif entry.get("role") == "user":
            for part in entry.get("parts", []):
                if part.get("type") == "tool_result":
                    content = part.get("text", "")
                    if "Error:" in content or "Traceback" in content[:200]:
                        n_errors += 1
    return {"n_turns": n_turns, "n_actions": n_actions, "n_errors": n_errors}


def _compress_trajectory(conversation: list[dict]) -> str:
    actions = []
    for entry in conversation:
        if entry.get("role") == "assistant":
            for part in entry.get("parts", []):
                if part.get("type") == "tool_use" and part.get("name") == "computer":
                    inp = part.get("input", {})
                    desc = inp.get("action", "")
                    coord = inp.get("coordinate")
                    if coord:
                        desc += f"({coord[0]},{coord[1]})"
                    actions.append(desc[:120])
    if len(actions) <= 6:
        return f"Actions ({len(actions)}): " + " → ".join(actions)
    return (
        f"Actions ({len(actions)}): "
        + " → ".join(actions[:3])
        + " ... "
        + " → ".join(actions[-3:])
    )


# ---------------------------------------------------------------------------
# Propose prompt (in solver conversation context)
# ---------------------------------------------------------------------------

PROPOSE_SYSTEM_PROMPT = """\
You are a skill extraction agent for a GUI desktop agent. \
You analyze task attempts and distill reusable skills.

A good skill is:
- Specific: actual menu paths, keyboard shortcuts, element names
- Structured: bullet points under "Key techniques" and "Gotchas", under 200 words
- Actionable: another agent can immediately apply the technique
- Transferable: useful beyond this single task

If nothing useful was learned, output ACTION: NONE."""

PROPOSE_PROMPT = """\
The evaluation result: {eval_result}

Trajectory: {n_actions} actions, {n_errors} errors.

{existing_skills_section}

Based on this attempt, propose a reusable skill:

TOPIC: <broad topic, e.g. "libreoffice-calc", "chrome", "gimp">
ACTION: NEW / ENHANCE / NONE
TARGET: existing_skill_name (only for ENHANCE)
NAME: short-kebab-name (only for NEW)
DESCRIPTION: one sentence, under 100 chars
CONTENT:
## Key techniques
- (specific actions, menu paths, shortcuts)
## Gotchas
- (pitfalls to avoid)

Rules:
- Bullet points only, under 200 words
- Be SPECIFIC: include actual menu paths, shortcuts, element names
- Prefer ENHANCE over NEW if an existing skill is related
- If nothing useful, output ACTION: NONE"""


# ---------------------------------------------------------------------------
# Skill loading & system prompt building
# ---------------------------------------------------------------------------

def load_skills(workspace_dir: Path) -> list[dict]:
    skills = []
    skills_dir = workspace_dir / "skills"
    if not skills_dir.exists():
        return skills
    for sf in sorted(skills_dir.rglob("SKILL.md")):
        content = sf.read_text().strip()
        name = sf.parent.name
        desc, body = "", content
        for line in content.split("\n"):
            if line.strip().startswith("description:"):
                desc = line.split(":", 1)[1].strip()
                break
        if content.startswith("---"):
            end = content.find("---", 3)
            if end != -1:
                body = content[end + 3:].strip()
        rel_path = str(sf.parent.relative_to(workspace_dir))
        skills.append({"name": name, "description": desc, "body": body, "path": rel_path})
    return skills


def build_system_prompt(skills: list[dict]) -> str:
    parts = [OSW_SYSTEM_PROMPT]
    if skills:
        parts.append("\n\n## Skills")
        for s in skills:
            parts.append(f"\n### {s['name']}\n{s['body']}" if s['body'] else f"\n### {s['name']}\n{s['description']}")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Per-task solve+evaluate+propose
# ---------------------------------------------------------------------------

def solve_one_task(
    task_config: dict,
    env,
    model_id: str,
    region: str,
    max_tokens: int,
    system_prompt: str,
    workspace_dir: Path,
    max_steps: int = 30,
    curator_model: str = "",
) -> dict:
    """Full pipeline for one task: solve → evaluate → propose."""
    task_name = _task_id(task_config)
    domain = task_config.get("domain", "unknown")
    task_instruction = task_config.get("instruction", task_config.get("task", ""))
    t0 = time.time()

    try:
        # Reset VM
        for attempt in range(3):
            try:
                env.reset(task_config=task_config)
                break
            except Exception:
                if attempt < 2:
                    time.sleep(10)
                else:
                    raise RuntimeError(f"Setup failed for {task_name}")
        time.sleep(60)
        obs = env._get_obs()

        # Solve
        react_result = react_solve(
            task_prompt=task_instruction,
            env=env,
            model_id=model_id,
            region=region,
            max_tokens=max_tokens,
            timeout_sec=task_config.get("agent_timeout_sec", 900),
            max_turns=max_steps,
            system_prompt=system_prompt,
            initial_obs=obs,
        )
        conversation = extract_conversation(react_result.messages)
        solve_time = time.time() - t0

        # Evaluate
        time.sleep(20)
        try:
            eval_detail = env.evaluate_detailed()
            score = float(eval_detail.get("score", 0.0))
        except Exception as e:
            score = 0.0
            eval_detail = {"score": 0.0, "failure_reason": str(e)[:200]}

        passed = score >= 1.0
        traj_signals = _extract_trajectory_signals(conversation)
        compressed = _compress_trajectory(conversation)

        # Propose (in solver conversation context)
        proposal = None
        if not passed and react_result.messages:
            proposal = _propose_skill(
                react_result.messages, score, eval_detail, traj_signals,
                workspace_dir, region, curator_model, task_name,
            )

        return {
            "task_name": task_name,
            "domain": domain,
            "passed": passed,
            "score": score,
            "eval_detail": eval_detail,
            "solve_time": solve_time,
            "proposal": proposal,
            "trajectory_signals": traj_signals,
            "compressed_trajectory": compressed,
            "feedback_detail": eval_detail.get("failure_reason", ""),
        }
    except Exception as e:
        return {
            "task_name": task_name,
            "domain": domain,
            "passed": False,
            "score": 0.0,
            "error": str(e)[:500],
            "proposal": None,
        }


def _propose_skill(
    messages, score, eval_detail, traj_signals, workspace_dir, region, curator_model, task_name,
) -> dict | None:
    """Propose a skill in solver conversation context."""
    from anthropic import AnthropicBedrock

    skills = load_skills(workspace_dir)
    if skills:
        existing_section = "Current skills:\n" + "\n".join(
            f"- **{s['name']}**: {s['description']}" for s in skills
        )
    else:
        existing_section = "No existing skills yet."

    eval_text = f"FAILED (score={score:.1f})"
    if eval_detail.get("failure_reason"):
        eval_text += f" — {eval_detail['failure_reason']}"

    prompt_text = PROPOSE_PROMPT.format(
        eval_result=eval_text,
        n_actions=traj_signals.get("n_actions", 0),
        n_errors=traj_signals.get("n_errors", 0),
        existing_skills_section=existing_section,
    )

    # Build messages for propose (strip images except last 3)
    propose_messages = list(messages)
    propose_messages.append({
        "role": "user",
        "content": [{"type": "text", "text": prompt_text}],
    })

    try:
        client = AnthropicBedrock(
            aws_access_key=os.getenv("AWS_ACCESS_KEY_ID"),
            aws_secret_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
            aws_region=region,
        )
        resp = client.messages.create(
            model=curator_model,
            max_tokens=1536,
            messages=propose_messages,
            system=PROPOSE_SYSTEM_PROMPT,
            temperature=0.3,
        )
        resp_text = "".join(b.text for b in resp.content if hasattr(b, "text"))
    except Exception as e:
        logger.warning("Propose failed for %s: %s", task_name, str(e)[:200])
        return None

    if not resp_text or "ACTION: NONE" in resp_text.upper():
        return None

    return _parse_proposal(resp_text, task_name)


def _parse_proposal(resp: str, task_name: str) -> dict | None:
    proposal = {
        "source_task": task_name,
        "topic": "general",
        "action": "NEW",
        "target": "",
        "name": "",
        "description": "",
        "content": "",
    }
    for line in resp.split("\n"):
        s = line.strip()
        u = s.upper()
        if u.startswith("TOPIC:"):
            raw = s.split(":", 1)[1].strip()
            proposal["topic"] = re.sub(r"[^a-z0-9-]", "-", raw.lower()).strip("-") or "general"
        elif u.startswith("ACTION:"):
            proposal["action"] = s.split(":", 1)[1].strip().upper()
        elif u.startswith("TARGET:"):
            proposal["target"] = s.split(":", 1)[1].strip()
        elif u.startswith("NAME:"):
            raw = s.split(":", 1)[1].strip()
            proposal["name"] = re.sub(r"[^a-z0-9-]", "-", raw.lower()).strip("-")
        elif u.startswith("DESCRIPTION:"):
            proposal["description"] = s.split(":", 1)[1].strip()[:150]

    idx = resp.upper().find("CONTENT:")
    if idx >= 0:
        proposal["content"] = resp[idx + len("CONTENT:"):].strip()

    if proposal["action"] == "ENHANCE" and proposal["target"] and not proposal["name"]:
        proposal["name"] = proposal["target"]
    if not proposal["name"] and proposal["action"] != "NONE":
        proposal["name"] = f"skill-{task_name[:20]}"
    if not proposal["content"]:
        return None
    return proposal


# ---------------------------------------------------------------------------
# Main batch loop with ProposeCurateEngine
# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(description="OSWorld + ProposeCurateEngine")
    p.add_argument("--task-file", type=str, required=True)
    p.add_argument("--provider", type=str, default="aws", choices=["aws", "vmware", "docker"])
    p.add_argument("--domain", type=str, default=None)
    p.add_argument("--tasks", type=str, default=None)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--shuffle", action="store_true")
    p.add_argument("--solver-model", type=str, default="1")
    p.add_argument("--curator-model", type=str, default="2")
    p.add_argument("--region", type=str, default="us-west-2")
    p.add_argument("--max-tokens", type=int, default=16384)
    p.add_argument("--batch-size", type=int, default=5)
    p.add_argument("--workers", type=int, default=2)
    p.add_argument("--max-steps", type=int, default=30)
    p.add_argument("--max-skills-per-topic", type=int, default=5)
    p.add_argument("--max-general-skills", type=int, default=10)
    p.add_argument("--no-evolve", action="store_true")
    p.add_argument("--output-dir", type=str, default="outputs/osworld_evolve_engine")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    for n in ("botocore", "urllib3", "httpcore", "httpx"):
        logging.getLogger(n).setLevel(logging.WARNING)

    model_id = MODEL_MAP.get(args.solver_model, args.solver_model)
    curator_model_id = MODEL_MAP.get(args.curator_model, args.curator_model)

    # Load tasks
    all_tasks = load_osworld_tasks(args.task_file, domain=args.domain)
    if args.tasks:
        ids = set(n.strip() for n in args.tasks.split(","))
        all_tasks = [t for t in all_tasks if _task_id(t) in ids]
    if args.shuffle:
        import random
        random.seed(42)
        random.shuffle(all_tasks)
    if args.limit:
        all_tasks = all_tasks[:args.limit]

    if not all_tasks:
        print("No tasks to run.")
        return

    # Setup workspace + engine
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    workspace_dir = output_dir / "workspace"

    if not workspace_dir.exists():
        workspace_dir.mkdir(parents=True, exist_ok=True)
        (workspace_dir / "skills" / "topic").mkdir(parents=True, exist_ok=True)
        (workspace_dir / "skills" / "general").mkdir(parents=True, exist_ok=True)
        # Copy seed skills
        seed_src = Path(__file__).resolve().parent.parent.parent / "seed_workspaces" / "osworld" / "skills"
        if seed_src.exists():
            seed_dst = workspace_dir / "skills" / "seed"
            shutil.copytree(seed_src, seed_dst)

    config = EvolveConfig(
        evolver_model=curator_model_id,
        extra={"region": args.region},
    )
    engine = ProposeCurateEngine(
        config=config,
        max_skills_per_topic=args.max_skills_per_topic,
        max_general_skills=args.max_general_skills,
        skill_layout="topic",
        curator_model=curator_model_id,
    )
    workspace = AgentWorkspace(workspace_dir)

    # Batch loop
    all_results = []
    batches = [all_tasks[i:i + args.batch_size] for i in range(0, len(all_tasks), args.batch_size)]

    logger.info("Running %d tasks in %d batches | solver=%s curator=%s",
                len(all_tasks), len(batches), model_id, curator_model_id)

    for bi, batch in enumerate(batches):
        logger.info("=== Batch %d/%d (%d tasks) ===", bi + 1, len(batches), len(batch))

        # Build system prompt with current skills
        skills = load_skills(workspace_dir)
        system_prompt = build_system_prompt(skills)

        # Parallel solve
        task_queue: queue_mod.Queue = queue_mod.Queue()
        for t in batch:
            task_queue.put(t)

        task_outputs: dict[str, dict] = {}
        outputs_lock = threading.Lock()

        def _worker(worker_idx: int):
            from desktop_env.desktop_env import DesktopEnv
            env = None
            try:
                env_kwargs = dict(
                    provider_name=args.provider,
                    region=args.region,
                    os_type="Ubuntu",
                    action_space="claude_computer_use",
                    screen_size=(1920, 1080),
                    require_a11y_tree=False,
                )
                if args.provider == "aws":
                    from desktop_env.providers.aws.manager import IMAGE_ID_MAP
                    ami = IMAGE_ID_MAP[args.region].get((1920, 1080))
                    env_kwargs["snapshot_name"] = ami
                env = DesktopEnv(**env_kwargs)
                with _live_envs_lock:
                    _live_envs.append(env)

                while True:
                    try:
                        t = task_queue.get_nowait()
                    except queue_mod.Empty:
                        break
                    tid = _task_id(t)
                    out = solve_one_task(
                        task_config=t, env=env,
                        model_id=model_id, region=args.region,
                        max_tokens=args.max_tokens,
                        system_prompt=system_prompt,
                        workspace_dir=workspace_dir,
                        max_steps=args.max_steps,
                        curator_model=curator_model_id,
                    )
                    with outputs_lock:
                        task_outputs[tid] = out
            finally:
                if env:
                    try:
                        env.close()
                    except Exception:
                        pass
                    with _live_envs_lock:
                        try:
                            _live_envs.remove(env)
                        except ValueError:
                            pass

        threads = []
        for i in range(min(args.workers, len(batch))):
            t = threading.Thread(target=_worker, args=(i,))
            t.start()
            threads.append(t)
        for t in threads:
            t.join()

        # Build observations for engine
        observations = []
        for tc in batch:
            tid = _task_id(tc)
            out = task_outputs.get(tid, {})
            passed = out.get("passed", False)
            score = out.get("score", 0.0)

            raw = {"domain": out.get("domain", "unknown")}
            if out.get("proposal"):
                raw["proposal"] = out["proposal"]
            if out.get("trajectory_signals"):
                raw["trajectory_signals"] = out["trajectory_signals"]
            if out.get("compressed_trajectory"):
                raw["compressed_trajectory"] = out["compressed_trajectory"]
            if out.get("feedback_detail"):
                raw["feedback_analysis"] = out["feedback_detail"]
            ed = out.get("eval_detail", {})
            if ed.get("failure_reason"):
                raw["failure_reason"] = ed["failure_reason"]

            task_obj = Task(id=tid, input=tc.get("instruction", ""), metadata={"domain": out.get("domain")})
            traj = Trajectory(task_id=tid, output="")
            fb = Feedback(success=passed, score=score, detail=out.get("feedback_detail", ""), raw=raw)
            observations.append(Observation(task=task_obj, trajectory=traj, feedback=fb))

            all_results.append(out)

        # Run engine
        if not args.no_evolve and observations:
            result = engine.step(workspace, observations, history=None, trial=None)
            logger.info("Engine: %s", result.summary)

        passed_so_far = sum(1 for r in all_results if r.get("passed"))
        logger.info("Cumulative: %d/%d (%.1f%%)", passed_so_far, len(all_results),
                    100 * passed_so_far / max(len(all_results), 1))

    # Final summary
    total_passed = sum(1 for r in all_results if r.get("passed"))
    total = len(all_results)
    logger.info("=" * 60)
    logger.info("FINAL: %d/%d (%.1f%%)", total_passed, total, 100 * total_passed / max(total, 1))

    summary = {
        "timestamp": datetime.now().isoformat(),
        "solver_model": model_id,
        "curator_model": curator_model_id,
        "total": total,
        "passed": total_passed,
        "rate": total_passed / max(total, 1),
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    with open(output_dir / "all_results.jsonl", "w") as f:
        for r in all_results:
            f.write(json.dumps(r, default=str) + "\n")

    _cleanup_all_envs()


if __name__ == "__main__":
    main()
