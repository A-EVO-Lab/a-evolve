"""Structured evolution template — 4-phase process-driven multi-agent.

Addresses all V1/V2 root causes (R1-R6) through a structured cycle:
  Phase 1: ANALYZE — read trajectories, update task board with failure regimes
  Phase 2: RESEARCH — parallel agents explore data source regimes
  Phase 3: BUILD — construct infra pipelines from verified research
  Phase 4: VERIFY — test pipelines, retry on failure (max 3)

Persists structured state across cycles via the evolution workspace
(task_board.md, research_log.jsonl, architecture.md).

Activated via config: ``orchestrator: structured_evolution``
"""

from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from .base import EvolutionTemplate
from ._guardrails import strip_search_caps, cap_prompt_size, verify_pipeline
from ....engine.human_interface import create_interface
from ._evolution_workspace import (
    get_evolver_workspace_path,
    init_evolution_workspace,
    load_task_board,
    update_task_board,
    validate_task_board,
    load_research_log,
    append_research,
    validate_research_record,
    get_verified_approaches,
    load_architecture,
    update_architecture,
    append_insight,
    IGNORED_GAP_LABELS,
)

logger = logging.getLogger(__name__)


def _strip_preamble(content: str) -> str:
    """Strip conversational text before the Failure Patterns heading.

    LLMs often emit reasoning text before the structured output.
    We keep only the content starting from the heading.
    Tolerates #, ##, ###, or **bold** heading variants and normalizes
    to ## for downstream validation.
    """
    lines = content.splitlines(keepends=True)
    for i, line in enumerate(lines):
        stripped = line.strip()
        if re.match(r"^#{1,3}\s+Failure Patterns", stripped, re.IGNORECASE):
            # Normalize to ## heading
            normalized = re.sub(r"^#{1,3}\s+", "## ", stripped)
            return normalized + "\n" + "".join(lines[i + 1:])
        if re.match(r"^\*{1,2}Failure Patterns", stripped, re.IGNORECASE):
            normalized = re.sub(r"^\*{1,2}(.*)\*{1,2}$", r"## \1", stripped)
            return normalized + "\n" + "".join(lines[i + 1:])
    return content


def _extract_sample_query(batch_results: list[dict]) -> str:
    for r in batch_results[:3]:
        inp = r.get("task_input") or r.get("input") or ""
        if isinstance(inp, dict):
            inp = inp.get("input", "")
        if inp:
            return str(inp)[:100]
    return "latest news headlines 2026"


_GENERAL_PROMPTS_DIR = Path(__file__).parent / "prompts"


def _load_prompt(prompts_dir: Path | None, name: str, fallback: str = "") -> str:
    """Load a general prompt and append benchmark-specific context.

    1. Load the general prompt from templates/prompts/<name>
    2. Load the benchmark context from <prompts_dir>/<name> if it exists.
       If <prompts_dir> is named ``evolver_prompts_nav`` (the
       full-system-specific override) and <name> is not present there,
       fall back to the sibling ``evolver_prompts`` directory so shared
       per-benchmark facts are still loaded.
    3. Replace {benchmark_context} in the general prompt with the
       benchmark content (or empty string if no benchmark file).
    """
    general = _GENERAL_PROMPTS_DIR / name
    if general.exists():
        text = general.read_text()
    elif fallback:
        text = fallback
    else:
        return ""

    benchmark_context = ""
    if prompts_dir:
        pd = Path(prompts_dir)
        bp = pd / name
        if bp.exists():
            benchmark_context = bp.read_text().strip()
        elif pd.name == "evolver_prompts_nav":
            # Inherit shared per-benchmark prompt from the sibling
            # ``evolver_prompts`` directory.
            shared = pd.parent / "evolver_prompts" / name
            if shared.exists():
                benchmark_context = shared.read_text().strip()

    return text.replace("{benchmark_context}", benchmark_context)


def _parse_json_blocks(text: str) -> list[dict]:
    """Extract JSON objects from LLM output (one per line or in code blocks)."""
    records = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("```"):
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                records.append(obj)
        except json.JSONDecodeError:
            pass
    return records


def _bundle_infra(ws_root: Path) -> None:
    """Bundle all .py files under infra/ into search_pipeline.py.

    The builder writes multi-file code for readability. The solver runs
    search_pipeline.py as a subprocess. This bundler concatenates
    everything into one self-contained file.

    Handles any file organization the builder chooses:
    - Flat files in infra/
    - Nested directories (infra/sources/, infra/whatever/)
    - Single search_pipeline.py (skip bundling)

    The file with `if __name__ == "__main__"` becomes the entry point.
    If none exists, a minimal fallback entry is generated.
    """
    infra_dir = ws_root / "infra"
    if not infra_dir.exists():
        return

    # Collect all .py files recursively, excluding search_pipeline.py itself
    all_pys = []
    for p in sorted(infra_dir.rglob("*.py")):
        if p.name == "__init__.py" or p.name == "search_pipeline.py":
            continue
        if "__pycache__" in str(p):
            continue
        all_pys.append(p)

    # If no source files besides search_pipeline.py, skip bundling
    if not all_pys:
        return

    parts = [
        "#!/usr/bin/env python3",
        '"""Bundled search pipeline — auto-generated from infra/."""',
        "import json, re, sys, os, time, urllib.error, urllib.parse, urllib.request",
        "import xml.etree.ElementTree as ET",
        "from datetime import datetime, timedelta, timezone",
        "",
    ]

    _SKIP_IMPORTS = {
        "import json", "import re", "import sys", "import os", "import time",
        "import urllib.request", "import urllib.parse", "import urllib.error",
        "import xml.etree.ElementTree as ET",
        "from datetime import datetime",
        "from datetime import datetime, timedelta",
        "from datetime import datetime, timedelta, timezone",
    }

    # Find which file has the entry point (if __name__ == "__main__")
    entry_file = None
    for p in all_pys:
        if 'if __name__' in p.read_text() and '__main__' in p.read_text():
            entry_file = p

    # Bundle: non-entry files first, entry file last
    non_entry = [p for p in all_pys if p != entry_file]
    ordered = non_entry + ([entry_file] if entry_file else [])

    for src in ordered:
        code = src.read_text()
        is_entry = (src == entry_file)
        lines = []
        in_name_main = False
        for line in code.splitlines():
            # Strip if __name__ from non-entry files
            if not is_entry and line.strip().startswith("if __name__") and "__main__" in line:
                in_name_main = True
                continue
            if in_name_main:
                if line and not line[0].isspace():
                    in_name_main = False
                else:
                    continue
            if line.strip().startswith("from infra") or line.strip().startswith("import infra"):
                continue
            if line.strip() in _SKIP_IMPORTS:
                continue
            if line.strip().startswith("#!/usr/bin/env"):
                continue
            lines.append(line)
        rel = src.relative_to(infra_dir)
        parts.append(f"\n# ── {rel} {'─' * (50 - len(str(rel)))}")
        parts.append("\n".join(lines))

    # If no entry point found, generate a minimal one
    combined = "\n".join(parts)
    if 'if __name__' not in combined or '__main__' not in combined:
        parts.append("""
# ── auto-generated entry point ───────────────────────────
if __name__ == "__main__":
    raw = sys.stdin.read().strip()
    try:
        ctx = json.loads(raw)
    except json.JSONDecodeError:
        ctx = {"query": raw, "cutoff_date": "2099-01-01"}
    query = ctx.get("query", raw)
    cutoff = ctx.get("cutoff_date", "2099-01-01")
    # Try calling main() if the builder defined one
    if "main" in dir() and callable(main):
        main()
    else:
        json.dump({"direct_results": [], "queries": [query], "classification": "general"}, sys.stdout)
""")

    bundled = "\n".join(parts)
    (infra_dir / "search_pipeline.py").write_text(bundled)
    logger.info("Bundled %d files into search_pipeline.py (%d lines)",
                len(all_pys), bundled.count("\n"))


class Template(EvolutionTemplate):
    """4-phase structured evolution: analyze → research → build → verify."""

    def __init__(self, engine):
        self.engine = engine

    @property
    def name(self) -> str:
        return "structured_evolution"

    def execute(
        self,
        vc, solver_workspace, batch_results,
        tree, evo_number, routing_log_path,
    ) -> dict[str, Any]:
        cfg = self.engine.config
        se_config = cfg.extra.get("structured_evolution", {})
        research_k = se_config.get("research_parallel", 3)
        max_retries = se_config.get("build_verify_retries", 3)
        hitl_enabled = se_config.get("hitl_enabled", False)
        prompts_dir_str = se_config.get("prompts_dir", "")
        prompts_dir = Path(prompts_dir_str) if prompts_dir_str else None

        # Create human interface when HITL is enabled
        hi = None
        if hitl_enabled:
            hi = create_interface(cfg.extra)
            cfg.extra["human_in_the_loop"] = True

        ws_root = solver_workspace.root
        evo_ws = get_evolver_workspace_path(ws_root)
        init_evolution_workspace(evo_ws)

        trajectory: list[dict] = []

        # ── Phase 1: ANALYZE ──
        self._phase_analyze(
            vc, solver_workspace, batch_results,
            evo_number, prompts_dir, trajectory, evo_ws,
        )

        # ── Phase 2: RESEARCH ──
        self._phase_research(
            vc, solver_workspace, batch_results,
            evo_number, research_k, prompts_dir, trajectory, evo_ws,
        )

        # ── HITL: handle credential-gated research records ──
        if hi:
            self._hitl_credentials(evo_ws, ws_root, evo_number, hi, prompts_dir, trajectory)

        # ── HITL: show task board to human before build ──
        if hi:
            self._hitl_task_board(evo_ws, hi, trajectory)

        # ── Phase 3+4: BUILD + VERIFY loop ──
        mutated = self._phase_build_verify(
            vc, solver_workspace, batch_results,
            evo_number, max_retries, prompts_dir, trajectory, evo_ws,
        )

        # ── Guardrails G2 + G5 + G6 + infra cleanup ──
        if mutated:
            guardrail_results: dict[str, Any] = {}
            prompt_path = ws_root / "prompts" / "system.md"
            if prompt_path.exists():
                original = prompt_path.read_text()
                cleaned = strip_search_caps(original)
                if cleaned != original:
                    prompt_path.write_text(cleaned)
                    guardrail_results["search_caps_stripped"] = True
                else:
                    guardrail_results["search_caps_stripped"] = False
            guardrail_results["prompt_truncated"] = cap_prompt_size(ws_root)
            guardrail_results["pipeline_valid"] = verify_pipeline(ws_root)
            trajectory.append({"step": "guardrails", **guardrail_results})
            vc.commit(
                message=f"evo-{evo_number}-guardrails: cleanup",
                tag=f"evo-{evo_number}-guardrails",
            )

        return {
            "evo_number": evo_number,
            "mutated": mutated,
            "plan": {},
            "branches": tree.branch_names(),
            "trajectory": trajectory,
        }

    # ────────────────────────────────────────────────────────────────
    # Phase 1: Analyze
    # ────────────────────────────────────────────────────────────────

    def _phase_analyze(
        self, vc, solver_workspace, batch_results,
        evo_number, prompts_dir, trajectory, evo_ws,
    ):
        from ....algorithms.aevolve.prompts import build_evolution_prompt
        cfg = self.engine.config

        task_board = load_task_board(evo_ws)
        research_log = load_research_log(evo_ws)

        base_prompt = build_evolution_prompt(
            solver_workspace, batch_results, drafts=[],
            evo_number=evo_number,
            evolve_prompts=cfg.evolve_prompts,
            evolve_skills=cfg.evolve_skills,
            evolve_memory=cfg.evolve_memory,
            evolve_tools=cfg.evolve_tools,
            evolve_infra=cfg.evolve_infra,
        )

        research_summary = "\n".join(json.dumps(r) for r in research_log[-20:])
        template_vars = {
            "evo_number": str(evo_number),
            "batch_prompt": base_prompt,
            "task_board": task_board,
            "research_count": str(len(research_log)),
            "research_log": research_summary,
        }
        analyst_prompt = _load_prompt(
            prompts_dir, "analyst.md", "Analyze failures.",
        ).format_map(template_vars)
        system = _load_prompt(
            prompts_dir, "analyst_system.md",
            "You are a failure analyst. Output ONLY the task board in the "
            "exact markdown format specified. No conversational text.",
        )

        logger.info("Phase 1: Analyzing batch failures...")
        try:
            self.engine._run_llm(
                analyst_prompt, solver_workspace.root,
                system_prompt=system,
                evolver_workspace=evo_ws,
            )
            # The analyst writes task_board.md via bash. Read from disk.
            content = _strip_preamble(load_task_board(evo_ws))
            if content.strip() and validate_task_board(content):
                update_task_board(evo_ws, content)
                vc.commit(
                    message=f"evo-{evo_number}-analyze: update task board",
                    tag=f"evo-{evo_number}-analyze",
                )
                trajectory.append({"step": "analyze", "success": True})
            elif content.strip():
                repair_prompt = _load_prompt(
                    prompts_dir, "analyst_repair.md",
                    "Your previous output was REJECTED. Rewrite using the "
                    "exact markdown structure. Cycle {evo_number}.\n\n"
                    "Here is what you wrote:\n{previous_output}\n",
                ).format(evo_number=evo_number, previous_output=content[:2000])
                self.engine._run_llm(
                    repair_prompt, solver_workspace.root,
                    system_prompt=system,
                    evolver_workspace=evo_ws,
                )
                repaired = _strip_preamble(load_task_board(evo_ws))
                if repaired.strip() and validate_task_board(repaired):
                    update_task_board(evo_ws, repaired)
                    vc.commit(
                        message=f"evo-{evo_number}-analyze: update task board (repaired)",
                        tag=f"evo-{evo_number}-analyze",
                    )
                    trajectory.append({"step": "analyze", "success": True, "repaired": True})
                else:
                    logger.warning("Phase 1: analyst output failed validation after repair")
                    trajectory.append({"step": "analyze", "success": False,
                                       "reason": "invalid_format", "best_effort": True})
            else:
                trajectory.append({"step": "analyze", "success": False, "reason": "empty_output"})
        except Exception as e:
            logger.warning("Phase 1 (analyze) with bash failed: %s", e)
            # Fallback: no-bash call with index embedded in prompt
            try:
                index_lines = []
                for r in batch_results:
                    cat = r.get("category", "")
                    yr = r.get("year", "")
                    turns = r.get("turns", 0)
                    tid = r.get("instance_id") or r.get("task_id", "")
                    outcome = ("PASS" if r.get("success") else "FAIL") if "success" in r else "pending"
                    if cat or yr:
                        index_lines.append(f"{tid} | {cat} | {yr} | {turns}t | {outcome}")
                if index_lines:
                    analyst_prompt += "\n\n## Batch Index\n```\n" + "\n".join(index_lines) + "\n```\n"
                logger.info("Phase 1: retrying without bash (index embedded)...")
                from ....llm.bedrock import BedrockProvider
                llm = self.engine.llm
                if isinstance(llm, BedrockProvider):
                    response = llm.converse_loop(
                        system_prompt=system,
                        user_message=analyst_prompt,
                        tools=[],
                        tool_executor={},
                        max_tokens=16384,
                        temperature=0.0,
                    )
                    content = _strip_preamble(response.content or "")
                    if content.strip() and validate_task_board(content):
                        update_task_board(evo_ws, content)
                        vc.commit(
                            message=f"evo-{evo_number}-analyze: update task board (no-bash fallback)",
                            tag=f"evo-{evo_number}-analyze",
                        )
                        trajectory.append({"step": "analyze", "success": True, "fallback": True})
                    else:
                        trajectory.append({"step": "analyze", "success": False,
                                           "reason": "fallback_invalid", "error": str(e)})
                else:
                    trajectory.append({"step": "analyze", "success": False, "error": str(e)})
            except Exception as e2:
                logger.warning("Phase 1 fallback also failed: %s", e2)
                trajectory.append({"step": "analyze", "success": False, "error": str(e)})

    # ────────────────────────────────────────────────────────────────
    # Phase 2: Research
    # ────────────────────────────────────────────────────────────────

    def _phase_research(
        self, vc, solver_workspace, batch_results,
        evo_number, research_k, prompts_dir, trajectory, evo_ws,
    ):
        ws_root = solver_workspace.root
        task_board = load_task_board(evo_ws)

        gaps = self._extract_gaps(task_board, research_k)
        if not gaps:
            logger.info("Phase 2: No gaps to research.")
            trajectory.append({"step": "research", "gaps": 0})
            return

        logger.info("Phase 2: Researching %d gaps...", len(gaps))

        known = load_research_log(evo_ws)
        known_summary = "\n".join(
            f"- {r['approach']}: works={r['works']}" for r in known[-30:]
        )

        def _research_one(gap: str) -> dict:
            template_vars = {
                "regime": gap,
                "evo_number": str(evo_number),
                "known_results": known_summary,
            }
            prompt = _load_prompt(
                prompts_dir, "research.md", "Investigate regime.",
            ).format_map(template_vars)
            system = _load_prompt(
                prompts_dir, "research_system.md", "Research agent.",
            ).format(regime=gap)
            try:
                result = self.engine._run_llm(
                    prompt, ws_root,
                    system_prompt=system,
                    evolver_workspace=evo_ws,
                )
                # Read records from file the agent wrote, plus any in response text
                file_path = evo_ws / "tests" / f"research_{gap}.jsonl"
                file_text = file_path.read_text() if file_path.exists() else ""
                all_text = file_text + "\n" + (result.get("content", "") or "")
                records = _parse_json_blocks(all_text)
                saved = 0
                mismatched = 0
                for r in records:
                    # Fill defaults for fields the LLM may omit
                    r.setdefault("cycle", evo_number)
                    r.setdefault("tested", True)
                    r.setdefault("endpoint", "")
                    r.setdefault("latency_ms", 0)
                    r.setdefault("coverage", [])
                    r.setdefault("does_not_cover", [])
                    r.setdefault("complementary_to", [])
                    r.setdefault("sample_output", "")
                    r.setdefault("credential_needed", False)
                    r.setdefault("credential_env", "")
                    r.setdefault("error", "")
                    r.setdefault("notes", "")
                    if "regime" not in r:
                        r["regime"] = gap
                    elif r["regime"] != gap:
                        mismatched += 1
                        continue
                    if validate_research_record(r):
                        append_research(evo_ws, r)
                        saved += 1
                    else:
                        logger.warning(
                            "Research record for %s failed validation: %s",
                            gap, {k for k in r if r[k] is not None}
                        )
                return {"gap": gap, "records": saved, "mismatched": mismatched}
            except Exception as e:
                logger.warning("Research for %s failed: %s", gap, e)
                return {"gap": gap, "records": 0, "error": str(e)}

        results = []
        with ThreadPoolExecutor(max_workers=min(research_k, 3)) as pool:
            futures = {pool.submit(_research_one, g): g for g in gaps}
            for fut in as_completed(futures):
                try:
                    results.append(fut.result())
                except Exception as e:
                    results.append({"gap": futures[fut], "error": str(e)})

        vc.commit(
            message=f"evo-{evo_number}-research: {len(gaps)} regimes",
            tag=f"evo-{evo_number}-research",
        )
        trajectory.append({
            "step": "research",
            "gaps": len(gaps),
            "results": results,
        })

    def _extract_gaps(self, task_board: str, k: int) -> list[str]:
        """Extract top-K priority gaps from validated failure-pattern bullets.

        Only parses lines matching the planned format:
          - <regime_tag>: ... PRIORITY: HIGH|MEDIUM|LOW
        Ignores table rows, generic labels, and conversational text.
        """
        in_failure_section = False
        gaps = []
        for line in task_board.splitlines():
            stripped = line.strip().replace("`", "").replace("**", "")
            # Accept #, ##, ### variants of Failure Patterns heading
            if re.match(r"^#{1,3}\s+Failure Patterns", stripped, re.IGNORECASE):
                in_failure_section = True
                continue
            if re.match(r"^#{1,3}\s+", stripped):
                in_failure_section = False
                continue
            if not in_failure_section:
                continue
            if "|" in stripped:
                continue
            match = re.match(
                r"[-*]\s*(\w[\w_]*):\s*\d+.*PRIORITY:\s*(HIGH|MEDIUM|LOW)",
                stripped, re.IGNORECASE,
            )
            if match:
                tag = match.group(1).lower()
                if tag not in IGNORED_GAP_LABELS:
                    gaps.append(tag)
        # Fallback: if no gaps found in section, scan all PRIORITY bullets
        if not gaps:
            for line in task_board.splitlines():
                stripped = line.strip().replace("`", "").replace("**", "")
                if "|" in stripped:
                    continue
                match = re.match(
                    r"[-*]\s*(\w[\w_]*):\s*\d+.*PRIORITY:\s*(HIGH|MEDIUM|LOW)",
                    stripped, re.IGNORECASE,
                )
                if match:
                    tag = match.group(1).lower()
                    if tag not in IGNORED_GAP_LABELS:
                        gaps.append(tag)
        return gaps[:k]

    # ────────────────────────────────────────────────────────────────
    # HITL integration
    # ────────────────────────────────────────────────────────────────

    def _hitl_credentials(self, evo_ws, solver_root, evo_number, hi, prompts_dir, trajectory):
        """Handle credential-gated research records via human interface.

        After the human supplies a credential, reruns research for that
        regime once and appends a real source-test record.
        """
        records = load_research_log(evo_ws)
        pending = [
            r for r in records
            if r.get("credential_needed") and r.get("works") in ("unknown", "blocked")
        ]
        if not pending:
            return

        retested = 0
        for rec in pending:
            env_var = rec.get("credential_env", "API_KEY")
            approach = rec.get("approach", "unknown")
            regime = rec.get("regime", "unknown")
            endpoint = rec.get("endpoint", "")
            response = hi.handle(
                f"Research found '{approach}' for regime '{regime}' but needs "
                f"credentials. Provide {env_var} or type 'skip':",
                action_type="credential_request",
            )
            if response.strip().lower() in ("skip", "(no response)", ""):
                append_research(evo_ws, {
                    "cycle": evo_number, "regime": regime,
                    "approach": approach, "endpoint": endpoint,
                    "tested": False, "works": False,
                    "latency_ms": 0, "coverage": [], "does_not_cover": [],
                    "complementary_to": [], "sample_output": "",
                    "credential_needed": True, "credential_env": env_var,
                    "error": "Human skipped credential", "notes": "",
                })
            else:
                import os
                os.environ[env_var] = response.strip()
                # Rerun research for this specific regime+approach
                retest_result = self._retest_credential_approach(
                    evo_ws, evo_number, regime, approach, endpoint,
                    env_var, prompts_dir, solver_root=solver_root,
                )
                retested += 1
                trajectory_note = retest_result

        trajectory.append({
            "step": "hitl_credentials",
            "pending": len(pending),
            "retested": retested,
        })

    def _retest_credential_approach(
        self, evo_ws, evo_number, regime, approach, endpoint, env_var, prompts_dir,
        solver_root=None,
    ) -> dict:
        """Rerun research for a credential-gated approach after credential supplied."""
        ws_root = solver_root
        prompt = _load_prompt(
            prompts_dir, "retest_credential.md",
            "RETEST: The credential {env_var} has been supplied.\n"
            "Test the approach '{approach}' for regime '{regime}'.\n"
            "Endpoint: {endpoint}\n",
        ).format(
            env_var=env_var, approach=approach, regime=regime,
            endpoint=endpoint, evo_number=evo_number,
        )

        system = _load_prompt(
            prompts_dir, "retest_credential_system.md",
            "You are a research agent retesting '{approach}' for regime "
            "'{regime}' after credentials were supplied. Test with real "
            "HTTP calls and report the result as a JSON record.",
        ).format(approach=approach, regime=regime)

        try:
            result = self.engine._run_llm(
                prompt, ws_root, system_prompt=system, evolver_workspace=evo_ws,
            )
            parsed = _parse_json_blocks(result.get("content", ""))
            if parsed:
                rec = parsed[0]
                rec.setdefault("cycle", evo_number)
                rec.setdefault("regime", regime)
                rec.setdefault("approach", approach)
                rec.setdefault("tested", True)
                rec.setdefault("credential_needed", True)
                rec.setdefault("credential_env", env_var)
                if validate_research_record(rec):
                    append_research(evo_ws, rec)
                    return {"retested": True, "works": rec.get("works")}
            # Fallback: no parseable record
            append_research(evo_ws, {
                "cycle": evo_number, "regime": regime,
                "approach": approach, "endpoint": endpoint,
                "tested": True, "works": False,
                "latency_ms": 0, "coverage": [], "does_not_cover": [],
                "complementary_to": [], "sample_output": "",
                "credential_needed": True, "credential_env": env_var,
                "error": "Retest produced no parseable record", "notes": "",
            })
            return {"retested": True, "works": False}
        except Exception as e:
            append_research(evo_ws, {
                "cycle": evo_number, "regime": regime,
                "approach": approach, "endpoint": endpoint,
                "tested": True, "works": False,
                "latency_ms": 0, "coverage": [], "does_not_cover": [],
                "complementary_to": [], "sample_output": "",
                "credential_needed": True, "credential_env": env_var,
                "error": str(e), "notes": "",
            })
            return {"retested": True, "works": False, "error": str(e)}

    def _hitl_task_board(self, evo_ws, hi, trajectory):
        """Show task board to human and accept edits before build."""
        board = load_task_board(evo_ws)
        response = hi.handle(
            f"Current task board before build phase:\n\n{board}\n\n"
            "Add requests or adjustments (or press Enter to skip):",
            action_type="task_board_review",
        )
        if response.strip() and response.strip() not in ("(no response)", "skip"):
            board += f"\n\n## Human Requests\n- {response.strip()}\n"
            update_task_board(evo_ws, board)
            trajectory.append({"step": "hitl_task_board", "updated": True})
        else:
            trajectory.append({"step": "hitl_task_board", "updated": False})

    # ────────────────────────────────────────────────────────────────
    # Phase 3+4: Build + Verify loop
    # ────────────────────────────────────────────────────────────────

    def _phase_build_verify(
        self, vc, solver_workspace, batch_results,
        evo_number, max_retries, prompts_dir, trajectory, evo_ws,
    ) -> bool:
        ws_root = solver_workspace.root
        verified = get_verified_approaches(evo_ws)
        if not verified:
            logger.info("Phase 3: No verified approaches to build from.")
            trajectory.append({"step": "build", "mutated": False, "reason": "no_verified"})
            return False

        architecture = load_architecture(evo_ws)
        task_board = load_task_board(evo_ws)

        verified_summary = "\n".join(
            json.dumps(r) for r in verified
        )

        cfg = self.engine.config
        workspace_extras = ""
        if cfg.evolve_skills:
            workspace_extras += "  /solver_workspace/skills/       — domain strategy files (YAML frontmatter: name, description)\n"
        if cfg.evolve_memory:
            workspace_extras += "  /solver_workspace/memory/       — concise, actionable batch learnings\n"
        builder_system = _load_prompt(
            prompts_dir, "builder_system.md", "Builder.",
        ).format(evo_number=evo_number, workspace_extras=workspace_extras)
        builder_prompt = _load_prompt(
            prompts_dir, "builder.md", "Build from research.",
        ).format(
            verified_summary=verified_summary,
            task_board=task_board,
            architecture=architecture,
        )

        # Tag the pre-build state so we can roll back on failure
        pre_build_tag = f"evo-{evo_number}-pre-build"
        vc.commit(message="pre-build checkpoint", tag=pre_build_tag)

        mutated = False
        for attempt in range(1, max_retries + 1):
            logger.info("Phase 3-4: Build attempt %d/%d", attempt, max_retries)

            # BUILD
            try:
                self.engine._run_llm(
                    builder_prompt, ws_root,
                    system_prompt=builder_system,
                    evolver_workspace=evo_ws,
                )
                committed = vc.commit(
                    message=f"evo-{evo_number}-build-attempt-{attempt}",
                    tag=f"evo-{evo_number}-build-{attempt}",
                )
                trajectory.append({
                    "step": "build", "attempt": attempt,
                    "mutated": committed,
                })
                if not committed:
                    break
            except Exception as e:
                logger.warning("Build attempt %d failed: %s", attempt, e)
                trajectory.append({
                    "step": "build", "attempt": attempt,
                    "mutated": False, "error": str(e),
                })
                break

            # VERIFY
            verify_result = self._verify(
                vc, solver_workspace, batch_results, evo_number, attempt,
                evo_ws=evo_ws, prompts_dir=prompts_dir,
            )
            trajectory.append({
                "step": "verify", "attempt": attempt, **verify_result,
            })

            if verify_result.get("passed", False):
                append_research(evo_ws, {
                    "cycle": evo_number, "regime": "all",
                    "approach": "build_output", "tested": True,
                    "works": True, "type": "tool_test",
                    "evidence": verify_result.get("report", "")[:300],
                })
                mutated = True
                break

            # Roll back to pre-build state before retry
            vc.rollback_to_tag(pre_build_tag)

            if attempt < max_retries:
                builder_prompt = _load_prompt(
                    prompts_dir, "builder_retry.md",
                    "PREVIOUS BUILD FAILED (attempt {attempt}). "
                    "Report: {verify_report}\nResearch: {verified_summary}\n"
                    "Fix the issues and rebuild.",
                ).format(
                    attempt=attempt,
                    verify_report=verify_result.get("report", "unknown"),
                    verified_summary=verified_summary,
                )

        if not mutated:
            # Ensure workspace is clean — roll back to pre-build
            vc.rollback_to_tag(pre_build_tag)
            append_research(evo_ws, {
                "cycle": evo_number, "regime": "all",
                "approach": "build_output", "tested": True,
                "works": False, "type": "tool_test",
                "error": "All build-verify attempts exhausted",
            })
            trajectory.append({
                "step": "build_verify_exhausted",
                "attempts": max_retries,
            })

        return mutated

    def _verify(
        self, vc, solver_workspace, batch_results,
        evo_number, attempt, evo_ws=None, prompts_dir=None,
    ) -> dict[str, Any]:
        """Run verifier agent on the current workspace."""
        ws_root = solver_workspace.root
        sample_query = _extract_sample_query(batch_results)

        verifier_system = _load_prompt(
            prompts_dir, "verifier_system.md", "Verification agent.",
        )
        verifier_prompt = _load_prompt(
            prompts_dir, "verifier.md", "Test tools.",
        ).format(sample_query=sample_query, cutoff_date="2026-01-15")

        try:
            result = self.engine._run_llm(
                verifier_prompt, ws_root,
                system_prompt=verifier_system,
                evolver_workspace=evo_ws,
            )
            content = result.get("content", "")
            passed = "VERDICT: PASS" in content.upper() or "PASS" in content.upper().split("\n")[0] if content else False
            vc.commit(
                message=f"evo-{evo_number}-verify-{attempt}",
                tag=f"evo-{evo_number}-verify-{attempt}",
            )
            return {"passed": passed, "report": content[:500]}
        except Exception as e:
            logger.warning("Verification failed: %s", e)
            return {"passed": False, "report": str(e)}
