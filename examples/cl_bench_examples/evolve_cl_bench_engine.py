#!/usr/bin/env python3
"""CL-bench evolution using the ProposeCurateEngine.

This is the engine-based version of evolve_cl_bench.py. The pipeline is:
  1. Parallel solve (Bedrock converse with context document)
  2. Parallel judge (rubric-based LLM evaluation)
  3. In-context skill proposal (solver continues conversation with feedback)
  4. ProposeCurateEngine.step() — per-context + general curation
  5. Reload workspace, next batch

Usage:
    python examples/cl_bench_examples/evolve_cl_bench_engine.py \
        --max-samples 100 --batch-size 10 \
        --output-dir outputs/cl_bench_engine_v1
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
os.environ["BYPASS_TOOL_CONSENT"] = "true"

from agent_evolve.algorithms.propose_curate import ProposeCurateEngine
from agent_evolve.benchmarks.cl_bench import (
    CLBenchBenchmark,
    _call_bedrock,
    _call_bedrock_converse,
    _convert_openai_messages_to_bedrock,
    _get_client,
    _init_worker,
    _truncate,
    MODEL_MAP,
)
from agent_evolve.config import EvolveConfig
from agent_evolve.contract.workspace import AgentWorkspace
from agent_evolve.engine.observer import Observer
from agent_evolve.types import Feedback, Observation, Task, Trajectory

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

PROPOSE_SKILL_PROMPT = """\
Your answer had issues. Here is the user's feedback:

{feedback}

{existing_skills_section}

Based on this feedback, write a SHORT, actionable tip for future questions about this same document.

ACTION: NEW / ENHANCE / NONE
TARGET: existing_skill_name (only for ENHANCE)
NAME: short-kebab-name (only for NEW)
DESCRIPTION: one sentence, under 100 chars
CONTENT:
## Key points
- (specific bullet points referencing exact terms/rules/numbers from the document)
## Gotchas
- (specific pitfalls to avoid, based on what the feedback flagged)

Rules:
- Bullet points, not paragraphs. CONTENT must be under 200 words.
- Reference exact terms, numbers, sections from the document
- Focus ONLY on what the feedback flagged
- Prefer ENHANCE over NEW if an existing skill is related
- If nothing useful, output ACTION: NONE"""

REPHRASE_FEEDBACK_PROMPT = """\
You are simulating a real user giving feedback on an AI assistant's response. \
The user asked a question and the AI's answer had some problems.

Given the list of issues below, write a natural user feedback message as if you \
are the user who is unsatisfied. Address EVERY issue listed. Be specific about \
what was wrong. Sound like a real person, not a rubric checklist.

Output ONLY the feedback text, nothing else."""

# ---------------------------------------------------------------------------
# Agent (simplified from original CLBenchAgent)
# ---------------------------------------------------------------------------


class CLBenchSolver:
    """Lightweight solver for CL-bench (no BaseAgent inheritance needed)."""

    def __init__(self, bench: CLBenchBenchmark, workspace_dir: Path, system_prompt: str = ""):
        self.bench = bench
        self.workspace_dir = workspace_dir
        self.system_prompt = system_prompt

    def solve_raw(self, task: Task) -> tuple[Trajectory, dict]:
        """Solve and return (Trajectory, conversation_state)."""
        client = _get_client(self.bench.region)
        meta = task.metadata
        task_id = task.id

        raw_messages = self.bench._get_raw_messages(task_id)
        if raw_messages:
            system_prompts, bedrock_messages = _convert_openai_messages_to_bedrock(
                raw_messages,
                extra_system_text=self.system_prompt if self.system_prompt else None,
            )
        else:
            context = meta.get("context", "")
            task_text = meta.get("task_text", "")
            user_text = f"Context:\n{context}\n\nTask:\n{task_text}"
            system_prompts = [{"text": self.system_prompt}] if self.system_prompt else []
            bedrock_messages = [{"role": "user", "content": [{"text": user_text}]}]

        answer, err = _call_bedrock_converse(
            client, self.bench.model_id, system_prompts, bedrock_messages,
            max_tokens=self.bench.max_tokens, temperature=self.bench.temperature,
        )

        if err:
            traj = Trajectory(task_id=task_id, output=f"[ERROR] {err}")
            return traj, {}

        answer_text = (answer or "").strip()
        conv_messages = list(bedrock_messages)
        conv_messages.append({"role": "assistant", "content": [{"text": answer_text}]})

        state = {
            "system_prompts": system_prompts,
            "messages": conv_messages,
            "client": client,
            "model_id": self.bench.model_id,
        }
        return Trajectory(task_id=task_id, output=answer_text), state

    def build_system_prompt(self, task: Task) -> str:
        """Build system prompt with context-specific skills injected."""
        parts = [self.system_prompt] if self.system_prompt else []
        context_id = task.metadata.get("context_id", "")

        # Inject context-specific skills
        ctx_dir = self.workspace_dir / "skills" / "context" / context_id
        if ctx_dir.exists():
            ctx_skills = []
            for sf in sorted(ctx_dir.rglob("SKILL.md")):
                content = sf.read_text().strip()
                name = sf.parent.name
                body = content
                if content.startswith("---"):
                    end = content.find("---", 3)
                    if end != -1:
                        body = content[end + 3:].strip()
                ctx_skills.append((name, body))
            if ctx_skills:
                parts.append("\n\n## Lessons learned for this context")
                for name, body in ctx_skills:
                    parts.append(f"\n### {name}\n{body}")

        # Inject general skills (brief)
        gen_dir = self.workspace_dir / "skills" / "general"
        if gen_dir.exists():
            gen_skills = []
            for sf in sorted(gen_dir.rglob("SKILL.md")):
                name = sf.parent.name
                desc = ""
                for line in sf.read_text().split("\n"):
                    if line.strip().startswith("description:"):
                        desc = line.split(":", 1)[1].strip()
                        break
                gen_skills.append((name, desc))
            if gen_skills:
                parts.append("\n\n## General strategies")
                for name, desc in gen_skills:
                    parts.append(f"\n- **{name}**: {desc}")

        return "\n".join(parts)


# ---------------------------------------------------------------------------
# Per-task pipeline
# ---------------------------------------------------------------------------

def _process_one_task(
    solver: CLBenchSolver,
    bench: CLBenchBenchmark,
    task: Task,
    region: str,
) -> dict:
    """solve → judge → rephrase feedback → propose skill."""
    _init_worker(region)

    # 1. Solve
    try:
        solver.system_prompt = solver.build_system_prompt(task)
        traj, conv_state = solver.solve_raw(task)
    except Exception as e:
        logger.error("Solve failed for %s: %s", task.id, e)
        traj = Trajectory(task_id=task.id, output=f"[ERROR] {e}")
        conv_state = {}

    # 2. Judge
    try:
        fb = bench.evaluate(task, traj)
    except Exception as e:
        logger.error("Judge failed for %s: %s", task.id, e)
        fb = Feedback(success=False, score=0.0, detail=str(e), raw={})

    # 3. Rephrase feedback
    detail = "Result: PASS" if fb.success else _rephrase_feedback(task, fb, region)

    # 4. Propose (in-context, failures only)
    proposal = None
    if not fb.success and conv_state and "FAIL" in detail:
        proposal = _propose_in_context(task, conv_state, detail, solver.workspace_dir, region)

    return {
        "task": task,
        "trajectory": traj,
        "feedback": fb,
        "detail": detail,
        "proposal": proposal,
    }


def _rephrase_feedback(task: Task, fb: Feedback, region: str) -> str:
    """Convert rubric failures into natural user feedback."""
    rubrics = task.metadata.get("rubrics", [])
    req_status = fb.raw.get("requirement_status", [])

    failed_rubrics = []
    for i, rubric in enumerate(rubrics):
        status = str(req_status[i]).strip().lower() if i < len(req_status) else "unknown"
        if status != "yes":
            text = rubric.get("rubric_criteria", "") if isinstance(rubric, dict) else str(rubric)
            failed_rubrics.append(text)

    if not failed_rubrics:
        return "Result: FAIL"

    issues_text = "\n".join(f"- {r}" for r in failed_rubrics)
    task_question = task.metadata.get("task_text", "")[:300]
    user_msg = f"Task:\n{task_question}\n\nIssues:\n{issues_text}"

    try:
        client = _get_client(region)
        rephrased, err = _call_bedrock(
            client, "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
            REPHRASE_FEEDBACK_PROMPT, user_msg,
            max_tokens=1024, temperature=0.3,
        )
        if not err and rephrased:
            return f"Result: FAIL\nUser feedback: {rephrased.strip()}"
    except Exception:
        pass
    return "Result: FAIL"


def _propose_in_context(
    task: Task, conv_state: dict, feedback_detail: str, workspace_dir: Path, region: str,
) -> dict | None:
    """Propose a skill continuing the solver conversation."""
    feedback_text = feedback_detail
    if "User feedback:" in feedback_detail:
        feedback_text = feedback_detail.split("User feedback:", 1)[1].strip()
    if not feedback_text or len(feedback_text) < 20:
        return None

    context_id = task.metadata.get("context_id", "")

    # Existing context skills
    ctx_dir = workspace_dir / "skills" / "context" / context_id
    existing = []
    if ctx_dir.exists():
        for sf in sorted(ctx_dir.rglob("SKILL.md")):
            name = sf.parent.name
            desc = ""
            for line in sf.read_text().split("\n"):
                if line.strip().startswith("description:"):
                    desc = line.split(":", 1)[1].strip()
                    break
            existing.append((name, desc))

    existing_section = (
        "Current skills for this context:\n" + "\n".join(f"- **{n}**: {d}" for n, d in existing)
        if existing else "No existing skills for this context yet."
    )

    prompt = PROPOSE_SKILL_PROMPT.format(
        feedback=feedback_text,
        existing_skills_section=existing_section,
    )

    client = conv_state["client"]
    model_id = conv_state["model_id"]
    system_prompts = conv_state["system_prompts"]
    messages = list(conv_state["messages"])
    messages.append({"role": "user", "content": [{"text": prompt}]})

    resp, err = _call_bedrock_converse(
        client, model_id, system_prompts, messages,
        max_tokens=1024, temperature=0.3,
    )
    if err or not resp:
        return None

    return _parse_proposal(resp, task.id, context_id)


def _parse_proposal(resp: str, task_id: str, context_id: str) -> dict | None:
    if "ACTION: NONE" in resp.upper():
        return None

    proposal = {
        "source_task": task_id,
        "topic": context_id,
        "action": "NEW",
        "target": "",
        "name": "",
        "description": "",
        "content": "",
    }

    for line in resp.split("\n"):
        s = line.strip()
        u = s.upper()
        if u.startswith("ACTION:"):
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
        proposal["name"] = f"skill-{task_id[:8]}"
    if not proposal["content"]:
        return None
    return proposal


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(description="CL-bench + ProposeCurateEngine")
    p.add_argument("--grouped-path", type=str, default="/fsx/tianxin/CL-bench/CL-bench-grouped.jsonl")
    p.add_argument("--raw-path", type=str, default="/fsx/tianxin/CL-bench/CL-bench.jsonl")
    p.add_argument("--max-samples", type=int, default=100)
    p.add_argument("--solver-model", type=str, default="1")
    p.add_argument("--judge-model", type=str, default="3")
    p.add_argument("--curator-model", type=str, default="2")
    p.add_argument("--region", type=str, default="us-west-2")
    p.add_argument("--batch-size", type=int, default=10)
    p.add_argument("--batch-workers", type=int, default=4)
    p.add_argument("--max-skills-per-context", type=int, default=5)
    p.add_argument("--max-general-skills", type=int, default=10)
    p.add_argument("--no-evolve", action="store_true")
    p.add_argument("--output-dir", type=str, default="outputs/cl_bench_engine")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    for n in ("botocore", "urllib3", "httpcore", "httpx"):
        logging.getLogger(n).setLevel(logging.WARNING)

    curator_model_id = MODEL_MAP.get(args.curator_model, args.curator_model)

    # Setup workspace
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    workspace_dir = output_dir / "workspace"

    if not workspace_dir.exists():
        workspace_dir.mkdir(parents=True, exist_ok=True)
        (workspace_dir / "prompts").mkdir(parents=True, exist_ok=True)
        (workspace_dir / "prompts" / "system.md").write_text("")
        (workspace_dir / "skills" / "context").mkdir(parents=True, exist_ok=True)
        (workspace_dir / "skills" / "general").mkdir(parents=True, exist_ok=True)
        (workspace_dir / "evolution").mkdir(parents=True, exist_ok=True)

    # Benchmark
    bench = CLBenchBenchmark(
        grouped_path=args.grouped_path,
        raw_path=args.raw_path,
        k_dev_contexts=0,
        max_samples=args.max_samples,
        model_id=args.solver_model,
        judge_model_id=args.judge_model,
        region=args.region,
    )
    bench._ensure_loaded()
    all_tasks = bench.get_tasks(split="test", limit=999999)

    # Build context pools for sampling
    import random
    random.seed(42)
    ctx_pools = defaultdict(list)
    for t in all_tasks:
        ctx_pools[t.metadata.get("context_id", "")].append(t)
    for cid in ctx_pools:
        random.shuffle(ctx_pools[cid])
    total_tasks = sum(len(v) for v in ctx_pools.values())

    # Engine
    config = EvolveConfig(
        evolver_model=curator_model_id,
        extra={"region": args.region},
    )
    engine = ProposeCurateEngine(
        config=config,
        max_skills_per_topic=args.max_skills_per_context,
        max_general_skills=args.max_general_skills,
        skill_layout="context",
        curator_model=curator_model_id,
    )
    workspace = AgentWorkspace(workspace_dir)

    # Solver
    solver = CLBenchSolver(bench=bench, workspace_dir=workspace_dir)

    logger.info("Loaded %d tasks across %d contexts | solver=%s curator=%s",
                total_tasks, len(ctx_pools), bench.model_id, curator_model_id)

    # Batch loop
    all_results = []
    t0 = time.time()
    batch_idx = 0

    while any(ctx_pools.values()):
        available_cids = [cid for cid, pool in ctx_pools.items() if pool]
        chosen_cids = random.sample(available_cids, min(args.batch_size, len(available_cids)))
        batch_tasks = [ctx_pools[cid].pop() for cid in chosen_cids]
        ctx_pools = {k: v for k, v in ctx_pools.items() if v}

        logger.info("=== Batch %d (%d tasks, %d remaining) ===",
                    batch_idx + 1, len(batch_tasks), sum(len(v) for v in ctx_pools.values()))

        # Parallel solve+judge+propose
        task_outputs: dict[str, dict] = {}
        with ThreadPoolExecutor(
            max_workers=args.batch_workers, initializer=_init_worker, initargs=(args.region,)
        ) as pool:
            futures = {
                pool.submit(_process_one_task, solver, bench, t, args.region): t
                for t in batch_tasks
            }
            for fut in as_completed(futures):
                t = futures[fut]
                try:
                    task_outputs[t.id] = fut.result()
                except Exception as e:
                    logger.error("Task %s failed: %s", t.id, e)
                    task_outputs[t.id] = {
                        "task": t,
                        "trajectory": Trajectory(task_id=t.id, output=f"[ERROR] {e}"),
                        "feedback": Feedback(success=False, score=0.0, detail=str(e), raw={}),
                        "detail": "Result: FAIL",
                        "proposal": None,
                    }

        # Build observations for engine
        observations = []
        for t in batch_tasks:
            out = task_outputs[t.id]
            fb = out["feedback"]
            raw = dict(fb.raw)
            if out["proposal"]:
                raw["proposal"] = out["proposal"]
            raw["category"] = t.metadata.get("context_category", "")

            observations.append(Observation(
                task=t,
                trajectory=out["trajectory"],
                feedback=Feedback(
                    success=fb.success,
                    score=fb.score,
                    detail=out["detail"],
                    raw=raw,
                ),
            ))

            all_results.append({
                "task_id": t.id,
                "category": t.metadata.get("context_category", ""),
                "passed": fb.success,
                "score": fb.score,
            })

        # Run engine
        if not args.no_evolve and observations:
            result = engine.step(workspace, observations, history=None, trial=None)
            logger.info("Engine: %s", result.summary)

        passed_so_far = sum(1 for r in all_results if r["passed"])
        logger.info("Cumulative: %d/%d (%.1f%%)", passed_so_far, len(all_results),
                    100 * passed_so_far / max(len(all_results), 1))
        batch_idx += 1

    # Final summary
    elapsed = time.time() - t0
    total_passed = sum(1 for r in all_results if r["passed"])
    total = len(all_results)

    logger.info("=" * 60)
    logger.info("FINAL: %d/%d (%.1f%%) in %.0fs", total_passed, total,
                100 * total_passed / max(total, 1), elapsed)

    cat_stats = defaultdict(lambda: {"passed": 0, "total": 0})
    for r in all_results:
        cat = r.get("category", "unknown")
        cat_stats[cat]["total"] += 1
        if r["passed"]:
            cat_stats[cat]["passed"] += 1
    for cat, s in sorted(cat_stats.items()):
        logger.info("  %s: %d/%d (%.1f%%)", cat, s["passed"], s["total"],
                    100 * s["passed"] / max(s["total"], 1))

    summary = {
        "timestamp": datetime.now().isoformat(),
        "solver_model": bench.model_id,
        "curator_model": curator_model_id,
        "total": total,
        "passed": total_passed,
        "rate": total_passed / max(total, 1),
        "elapsed": elapsed,
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    with open(output_dir / "all_results.jsonl", "w") as f:
        for r in all_results:
            f.write(json.dumps(r, default=str) + "\n")

    logger.info("Done! Output: %s", output_dir)


if __name__ == "__main__":
    main()
