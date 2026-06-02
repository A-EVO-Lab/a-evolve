"""Structured navigation template — multi-agent evolution + git branching.

Composes structured evolution (4-phase: analyze → research → build → verify)
with agentic navigation (git-backed strategy tree + per-task routing).

Key design:
  - One unified evolver workspace (task_board, research_log, strategy_tree)
  - Branching only in the solver workspace (git branches)
  - Analyst decides TARGET per regime (main vs branch/*)
  - Research is shared across all targets
  - Build+verify runs per-target: main first, then branches
  - Rebase branches onto main before building (if main mutated)

Activated via config: ``orchestrator: structured_navigation``
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .base import EvolutionTemplate
from ._guardrails import strip_search_caps, cap_prompt_size, verify_pipeline, MAX_PROMPT_CHARS
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
    load_strategy_tree,
    update_strategy_tree,
    parse_routing_stats,
    build_stratified_outcome_table,
    format_stratified_table,
    scan_regime_accretion,
    format_regime_accretion,
    build_capacity_signal,
    extract_targets_from_task_board,
    IGNORED_GAP_LABELS,
)
from .structured_evolution import (
    _strip_preamble,
    _extract_sample_query,
    _load_prompt,
    _parse_json_blocks,
    _bundle_infra,
)

if TYPE_CHECKING:
    from ....contract.workspace import AgentWorkspace
    from ....engine.versioning import VersionControl
    from ....types import BranchInfo, StrategyTree
    from ..engine import NavigationEngine

logger = logging.getLogger(__name__)


class StructuredNavigationTemplate(EvolutionTemplate):
    """4-phase structured evolution with git-branched solver workspace."""

    def __init__(self, engine: NavigationEngine):
        self.engine = engine

    @property
    def name(self) -> str:
        return "structured_navigation"

    def execute(
        self,
        vc: VersionControl,
        solver_workspace: AgentWorkspace,
        batch_results: list[dict[str, Any]],
        tree: StrategyTree,
        evo_number: int,
        routing_log_path: Path | None,
    ) -> dict[str, Any]:
        cfg = self.engine.config
        se_config = cfg.extra.get("structured_evolution", {})
        research_k = se_config.get("research_parallel", 3)
        max_retries = se_config.get("build_verify_retries", 3)
        prompts_dir_str = se_config.get("prompts_dir", "")
        prompts_dir = Path(prompts_dir_str) if prompts_dir_str else None

        ws_root = solver_workspace.root
        evo_ws = get_evolver_workspace_path(ws_root)
        init_evolution_workspace(evo_ws)

        trajectory: list[dict] = []

        # Update strategy_tree.md with routing stats from this batch
        routing_stats = parse_routing_stats(routing_log_path)
        update_strategy_tree(evo_ws, tree, routing_stats, evo_number)

        # ── Phase 1: ANALYZE (with branching decision) ──
        self._phase_analyze(
            vc, solver_workspace, batch_results,
            evo_number, prompts_dir, trajectory, evo_ws,
            routing_log_path,
        )

        # ── Phase 2: RESEARCH (shared) ──
        self._phase_research(
            vc, solver_workspace, batch_results,
            evo_number, research_k, prompts_dir, trajectory, evo_ws,
        )

        # ── Phase 3+4: BUILD + VERIFY (per-target) ──
        mutated = self._phase_build_verify_all_targets(
            vc, solver_workspace, batch_results,
            evo_number, max_retries, prompts_dir, trajectory, evo_ws, tree,
        )

        # ── Guardrails (per-target that mutated) ──
        if mutated:
            self._apply_guardrails_all(vc, solver_workspace, evo_number, trajectory)

        return {
            "evo_number": evo_number,
            "mutated": mutated,
            "plan": {},
            "branches": tree.branch_names(),
            "trajectory": trajectory,
        }

    # ────────────────────────────────────────────────────────────────
    # Phase 1: Analyze (extended with navigation context)
    # ────────────────────────────────────────────────────────────────

    def _phase_analyze(
        self, vc, solver_workspace, batch_results,
        evo_number, prompts_dir, trajectory, evo_ws,
        routing_log_path,
    ):
        from .._evolver_prompts import build_evolution_prompt
        cfg = self.engine.config

        task_board = load_task_board(evo_ws)
        research_log = load_research_log(evo_ws)
        strategy_tree_md = load_strategy_tree(evo_ws)

        # Build routing summary for this batch
        routing_summary = self._build_routing_summary(batch_results, routing_log_path)

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

        # ── Broad branching signal: full-history outcome×property tables ──
        # Stratify EVERY routed task across ALL cycles by each regime
        # property (not a sample of this batch). This is what lets the
        # analyst root-cause outcomes against data properties before
        # deciding to branch. Tables are sorted worst-relative-to-mean
        # first, so branch candidates surface at the top.
        category_summary = self._build_stratified_signal(
            batch_results, routing_log_path, solver_workspace.root,
        )

        template_vars = {
            "evo_number": str(evo_number),
            "batch_prompt": base_prompt,
            "task_board": task_board,
            "research_count": str(len(research_log)),
            "research_log": research_summary,
            "strategy_tree": strategy_tree_md,
            "routing_summary": routing_summary,
            "category_summary": category_summary,
        }
        # Nav-specific analyst prompt (root-cause-first branching protocol +
        # the {strategy_tree}/{routing_summary}/{category_summary} signal
        # slots). Distinct from the shared ``analyst.md`` used by the
        # non-branching structured_evolution template, which does not
        # provide those vars. Fall back to the shared prompt only if the
        # nav prompt is somehow absent.
        analyst_prompt = _load_prompt(prompts_dir, "analyst_nav.md", "")
        if not analyst_prompt:
            analyst_prompt = _load_prompt(
                prompts_dir, "analyst.md", "Analyze failures.",
            )
        # Use nav-specific system prompt (with branching instructions)
        system = _load_prompt(
            prompts_dir, "analyst_nav_system.md", "",
        )
        if not system:
            system = _load_prompt(
                prompts_dir, "analyst_system.md",
                "You are a failure analyst. Output ONLY the task board.",
            )

        # Inject navigation context into prompt
        analyst_prompt = analyst_prompt.format_map(template_vars)
        # Append category stats directly (branching signal)
        if category_summary:
            analyst_prompt += f"\n\n{category_summary}\n"

        logger.info("Phase 1: Analyzing batch failures (with branching decision)...")
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
                # File on disk is invalid — try repair
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
                        message=f"evo-{evo_number}-analyze: task board (repaired)",
                        tag=f"evo-{evo_number}-analyze",
                    )
                    trajectory.append({"step": "analyze", "success": True, "repaired": True})
                else:
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

    # Regime properties the analyst stratifies outcomes by, in priority
    # order. Each becomes a full-history contingency table in the prompt.
    _STRATIFY_PROPS = ("category", "domain", "year")

    def _build_stratified_signal(
        self, batch_results: list[dict], routing_log_path: Path | None,
        workspace_root: Path | None = None,
    ) -> str:
        """Build the broad branching signal handed to the analyst.

        Two complementary signals:
          1. REGIME-GATED ACCRETION in the shared harness — the PRIMARY
             branch trigger. Each "if regime X then Y" rule already living
             in main's prompt/skills is a non-transferable strategy riding
             in the shared harness (L_adapt made visible); extracting it
             into branch/<regime> is the point of branching.
          2. Full-history outcome tables stratified by regime property —
             SECONDARY context (confounded by composition/luck), used to
             size regimes and spot divergence, not as the trigger itself.

        Always returns a non-empty string so the instruction to look for
        strategy divergence is never silently dropped.
        """
        sections: list[str] = []

        # PRIMARY: harness CAPACITY pressure. main has a fixed budget; when
        # it is full/truncating, one shared harness can no longer be optimal
        # for all regimes — branching buys back budget. This is the active,
        # performance-linked branch trigger.
        sections.append("### Harness capacity (PRIMARY branch trigger)\n\n"
                        + build_capacity_signal(workspace_root, MAX_PROMPT_CHARS))

        # SUPPORTING: regime-gated rules already in the shared harness — the
        # self-contained clusters that a branch would extract to free budget.
        accretion = scan_regime_accretion(workspace_root)
        sections.append("### Regime-gated rule clusters in `main`\n\n"
                        + format_regime_accretion(accretion))

        # Full-history tables from the persisted routing log.
        any_signal = False
        if routing_log_path and routing_log_path.exists():
            for prop in self._STRATIFY_PROPS:
                table = build_stratified_outcome_table(routing_log_path, prop=prop)
                regimes = table.get("regimes", {})
                # Skip a property that carries no real partition (only
                # "unknown", or a single bucket).
                real = [k for k in regimes if k != "unknown"]
                if not real:
                    continue
                any_signal = True
                sections.append(format_stratified_table(table))

        # This-batch snapshot (fast-moving signal complementing history).
        batch_lines: dict[str, dict[str, int]] = {}
        for r in batch_results:
            cat = r.get("category") or r.get("domain") or "unknown"
            d = batch_lines.setdefault(cat, {"total": 0, "pass": 0, "revealed": 0})
            d["total"] += 1
            if "success" in r:
                d["revealed"] += 1
                if r.get("success"):
                    d["pass"] += 1
        if any(c != "unknown" for c in batch_lines):
            lines = ["This-batch performance by category:"]
            for cat, s in sorted(batch_lines.items(), key=lambda x: -x[1]["total"]):
                if cat == "unknown":
                    continue
                pct = (f"{100*s['pass']/s['revealed']:.0f}%"
                       if s["revealed"] else "pending")
                lines.append(f"  {cat}: {s['pass']}/{s['revealed']} ({pct}), "
                             f"{s['total']} routed")
            sections.append("\n".join(lines))

        if not any_signal:
            sections.append("(full-history stratification pending — first "
                            "cycle with revealed labels)")
        return "## Regime Signal\n\n" + "\n\n".join(sections)

    def _build_routing_summary(
        self, batch_results: list[dict], routing_log_path: Path | None,
    ) -> str:
        """Build a human-readable routing summary for this batch."""
        if not routing_log_path or not routing_log_path.exists():
            return "(no routing log available)"

        # Collect routing info for tasks in this batch
        batch_ids = {
            r.get("instance_id") or r.get("task_id", "") for r in batch_results
        }
        branch_results: dict[str, dict[str, int]] = {}
        for line in routing_log_path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            tid = rec.get("task_id", "")
            if tid not in batch_ids:
                continue
            branch = rec.get("branch", "main") or "main"
            if branch not in branch_results:
                branch_results[branch] = {"routed": 0, "revealed": 0, "passed": 0}
            branch_results[branch]["routed"] += 1
            if "success" in rec:
                branch_results[branch]["revealed"] += 1
                if rec["success"]:
                    branch_results[branch]["passed"] += 1

        if not branch_results:
            return "(no routing data for this batch)"

        lines = []
        for branch, stats in sorted(branch_results.items()):
            from ._evolution_workspace import _format_branch_stats
            lines.append(f"- {branch}: {_format_branch_stats(stats).lstrip('- ')}"
            )
        return "\n".join(lines)

    # ────────────────────────────────────────────────────────────────
    # Phase 2: Research (reuse from structured_evolution)
    # ────────────────────────────────────────────────────────────────

    def _phase_research(
        self, vc, solver_workspace, batch_results,
        evo_number, research_k, prompts_dir, trajectory, evo_ws,
    ):
        """Shared research phase — delegates to structured_evolution logic."""
        from concurrent.futures import ThreadPoolExecutor, as_completed

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
                file_path = evo_ws / "tests" / f"research_{gap}.jsonl"
                file_text = file_path.read_text() if file_path.exists() else ""
                all_text = file_text + "\n" + (result.get("content", "") or "")
                records = _parse_json_blocks(all_text)
                saved = 0
                for r in records:
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
                        continue
                    if validate_research_record(r):
                        append_research(evo_ws, r)
                        saved += 1
                return {"gap": gap, "records": saved}
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
        trajectory.append({"step": "research", "gaps": len(gaps), "results": results})

    def _extract_gaps(self, task_board: str, k: int) -> list[str]:
        """Extract top-K priority gaps from task board (same logic as structured_evolution)."""
        in_failure_section = False
        gaps = []
        for line in task_board.splitlines():
            stripped = line.strip().replace("`", "").replace("**", "")
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
    # Phase 3+4: Build + Verify (per-target)
    # ────────────────────────────────────────────────────────────────

    def _phase_build_verify_all_targets(
        self, vc, solver_workspace, batch_results,
        evo_number, max_retries, prompts_dir, trajectory, evo_ws, tree,
    ) -> bool:
        from ....types import BranchInfo

        task_board = load_task_board(evo_ws)
        targets = extract_targets_from_task_board(task_board)

        # Ensure main is always present
        if "main" not in targets:
            targets["main"] = []

        # Always build main first
        vc.checkout_branch("main")
        main_mutated = self._build_verify_single_target(
            vc, solver_workspace, batch_results, evo_number,
            max_retries, prompts_dir, trajectory, evo_ws,
            target="main", regimes=targets.get("main", []),
        )

        # Then build each branch (real specialization, not just header)
        branch_mutated: dict[str, bool] = {}
        for branch_name, regimes in targets.items():
            if branch_name == "main":
                continue

            # Create branch if new
            if not vc.branch_exists(branch_name):
                vc.create_branch(branch_name, "main")
                tree.branches.append(BranchInfo(
                    name=branch_name,
                    created_at_cycle=evo_number,
                    description=", ".join(regimes[:3]),
                ))
                logger.info("Created branch %s for regimes: %s", branch_name, regimes)

            # Rebase onto main before building (so builder sees main's latest)
            if main_mutated:
                try:
                    rebased = vc.rebase_branch(branch_name, "main")
                    if not rebased:
                        logger.info("Rebase %s failed, syncing tools/infra from main", branch_name)
                        vc.sync_paths_from("main", branch_name, [
                            "tools/", "infra/", "tools/registry.yaml",
                        ])
                except Exception as e:
                    logger.warning("Rebase %s onto main failed: %s", branch_name, e)

            vc.checkout_branch(branch_name)
            branch_mutated[branch_name] = self._build_verify_single_target(
                vc, solver_workspace, batch_results, evo_number,
                max_retries, prompts_dir, trajectory, evo_ws,
                target=branch_name, regimes=regimes,
            )

            # README for navigator
            readme_path = solver_workspace.root / "README.md"
            readme_path.write_text(
                f"# {branch_name}\nSpecialization: {', '.join(regimes[:5])}\n"
            )
            try:
                vc.commit(message=f"evo-{evo_number}-branch-readme-{branch_name}")
            except Exception:
                pass

        # Return to main
        vc.checkout_branch("main")

        return main_mutated or any(branch_mutated.values())

    def _build_verify_single_target(
        self, vc, solver_workspace, batch_results,
        evo_number, max_retries, prompts_dir, trajectory, evo_ws,
        target: str, regimes: list[str],
    ) -> bool:
        """Build+verify loop for a single target (main or branch)."""
        ws_root = solver_workspace.root
        target_slug = target.replace("/", "-")
        is_branch = target != "main"

        verified = get_verified_approaches(evo_ws)
        if is_branch:
            # Prioritize research results for this branch's regimes
            regime_verified = [r for r in verified if r.get("regime") in regimes]
            if regime_verified:
                verified = regime_verified

        if not verified and is_branch:
            logger.info("Phase 3 [%s]: No verified approaches for regimes %s, skipping.", target, regimes)
            trajectory.append({"step": "build", "target": target, "mutated": False, "reason": "no_verified"})
            return False

        if not verified:
            logger.info("Phase 3 [%s]: No verified approaches to build from.", target)
            trajectory.append({"step": "build", "target": target, "mutated": False, "reason": "no_verified"})
            return False

        architecture = load_architecture(evo_ws)
        task_board = load_task_board(evo_ws)
        verified_summary = "\n".join(json.dumps(r) for r in verified)

        cfg = self.engine.config
        workspace_extras = ""
        if cfg.evolve_skills:
            workspace_extras += "  /solver_workspace/skills/       — domain strategy files\n"
        if cfg.evolve_memory:
            workspace_extras += "  /solver_workspace/memory/       — batch learnings\n"

        # Choose prompt based on target
        if is_branch:
            builder_system = self._build_branch_system_prompt(
                prompts_dir, evo_number, workspace_extras, target, regimes, ws_root,
            )
            builder_prompt = self._build_branch_user_prompt(
                prompts_dir, verified_summary, task_board, architecture, target, regimes,
            )
        else:
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

        # Target-specific pre-build checkpoint
        pre_build_tag = f"evo-{evo_number}-pre-build-{target_slug}"
        vc.commit(message=f"pre-build checkpoint ({target})", tag=pre_build_tag)

        mutated = False
        for attempt in range(1, max_retries + 1):
            logger.info("Phase 3-4 [%s]: Build attempt %d/%d", target, attempt, max_retries)

            try:
                self.engine._run_llm(
                    builder_prompt, ws_root,
                    system_prompt=builder_system,
                    evolver_workspace=evo_ws,
                )
                committed = vc.commit(
                    message=f"evo-{evo_number}-build-{target_slug}-{attempt}",
                    tag=f"evo-{evo_number}-build-{target_slug}-{attempt}",
                )
                trajectory.append({
                    "step": "build", "target": target,
                    "attempt": attempt, "mutated": committed,
                })
                if not committed:
                    break
            except Exception as e:
                logger.warning("Build [%s] attempt %d failed: %s", target, attempt, e)
                trajectory.append({
                    "step": "build", "target": target,
                    "attempt": attempt, "mutated": False, "error": str(e),
                })
                break

            # Verify
            verify_result = self._verify(
                vc, solver_workspace, batch_results, evo_number, attempt,
                evo_ws=evo_ws, prompts_dir=prompts_dir, target=target, regimes=regimes,
            )
            trajectory.append({
                "step": "verify", "target": target,
                "attempt": attempt, **verify_result,
            })

            if verify_result.get("passed", False):
                append_research(evo_ws, {
                    "cycle": evo_number, "regime": target_slug,
                    "approach": "build_output", "tested": True,
                    "works": True, "type": "tool_test",
                })
                mutated = True
                break

            # Rollback before retry
            vc.rollback_to_tag(pre_build_tag)

            if attempt < max_retries:
                builder_prompt = _load_prompt(
                    prompts_dir, "builder_retry.md",
                    "PREVIOUS BUILD FAILED (attempt {attempt}). "
                    "Report: {verify_report}\nResearch: {verified_summary}\n",
                ).format(
                    attempt=attempt,
                    verify_report=verify_result.get("report", "unknown"),
                    verified_summary=verified_summary,
                )

        if not mutated:
            vc.rollback_to_tag(pre_build_tag)
            trajectory.append({
                "step": "build_verify_exhausted", "target": target,
                "attempts": max_retries,
            })

        return mutated

    def _build_branch_system_prompt(
        self, prompts_dir, evo_number, workspace_extras, target, regimes, ws_root,
    ) -> str:
        """Build system prompt for branch-specific builder."""
        # Try loading branch-specific prompt template
        branch_system = _load_prompt(prompts_dir, "builder_branch_system.md", "")
        if not branch_system:
            branch_system = _load_prompt(prompts_dir, "builder_system.md", "Builder.")

        # Read branch README if it exists
        readme_path = ws_root / "README.md"
        branch_readme = readme_path.read_text() if readme_path.exists() else "(no README yet)"

        return branch_system.format(
            evo_number=evo_number,
            workspace_extras=workspace_extras,
            target=target,
            regime_description=", ".join(regimes),
            branch_readme=branch_readme,
        )

    def _build_branch_user_prompt(
        self, prompts_dir, verified_summary, task_board, architecture, target, regimes,
    ) -> str:
        """Build user prompt for branch-specific builder."""
        branch_prompt = _load_prompt(prompts_dir, "builder_branch.md", "")
        if branch_prompt:
            return branch_prompt.format(
                target=target,
                regime_description=", ".join(regimes),
                verified_summary=verified_summary,
                task_board=task_board,
                architecture=architecture,
            )
        # Fallback: standard builder prompt with branch context prepended
        base = _load_prompt(prompts_dir, "builder.md", "Build from research.").format(
            verified_summary=verified_summary,
            task_board=task_board,
            architecture=architecture,
        )
        return (
            f"## Your Target: `{target}`\n\n"
            f"You are evolving a specialized branch for: **{', '.join(regimes)}**\n"
            f"Focus on regime-specific improvements. Update README.md.\n\n"
            + base
        )

    def _verify(
        self, vc, solver_workspace, batch_results,
        evo_number, attempt, evo_ws=None, prompts_dir=None,
        target="main", regimes=None,
    ) -> dict[str, Any]:
        """Run verifier agent (same as structured_evolution, with target context)."""
        ws_root = solver_workspace.root

        # Use regime-specific tasks for verification if available
        if regimes and target != "main":
            regime_tasks = [
                r for r in batch_results
                if any(reg in str(r.get("task_id", "")).lower() for reg in regimes)
            ]
            sample_query = _extract_sample_query(regime_tasks or batch_results)
        else:
            sample_query = _extract_sample_query(batch_results)

        verifier_system = _load_prompt(prompts_dir, "verifier_system.md", "Verification agent.")
        verifier_prompt = _load_prompt(prompts_dir, "verifier.md", "Test tools.").format(
            sample_query=sample_query, cutoff_date="2026-01-15",
        )

        try:
            result = self.engine._run_llm(
                verifier_prompt, ws_root,
                system_prompt=verifier_system,
                evolver_workspace=evo_ws,
            )
            content = result.get("content", "")
            up = content.upper() if content else ""
            partial = "VERDICT: PARTIAL" in up
            passed_strict = (
                "VERDICT: PASS" in up
                or (up.split("\n")[0].startswith("PASS") if up else False)
            ) if content else False
            # PARTIAL is treated as passed on the target's own scope but
            # the build is non-transferable — caller may isolate to branch.
            passed = passed_strict or partial
            vc.commit(
                message=f"evo-{evo_number}-verify-{target.replace('/', '-')}-{attempt}",
                tag=f"evo-{evo_number}-verify-{target.replace('/', '-')}-{attempt}",
            )
            # Extract recommended branch if PARTIAL (verifier writes
            # "isolate to branch/<name>" in the report).
            isolate_to = None
            if partial:
                import re as _re
                m = _re.search(r"branch/([\w\-]+)", content)
                if m:
                    isolate_to = f"branch/{m.group(1)}"
            return {
                "passed": passed,
                "partial": partial,
                "isolate_to": isolate_to,
                "report": content[:500],
            }
        except Exception as e:
            logger.warning("Verification [%s] failed: %s", target, e)
            return {"passed": False, "partial": False, "isolate_to": None, "report": str(e)}

    # ────────────────────────────────────────────────────────────────
    # Guardrails (per-target)
    # ────────────────────────────────────────────────────────────────

    def _apply_guardrails_all(self, vc, solver_workspace, evo_number, trajectory):
        """Apply guardrails on each mutated target."""
        ws_root = solver_workspace.root

        # Apply on current branch (main after returning from build loop)
        self._apply_guardrails(vc, ws_root, evo_number, trajectory, "main")

        # Apply on each branch that exists
        for branch_name in vc.list_branches():
            try:
                vc.checkout_branch(branch_name)
                self._apply_guardrails(vc, ws_root, evo_number, trajectory, branch_name)
            except Exception as e:
                logger.warning("Guardrails for %s failed: %s", branch_name, e)

        vc.checkout_branch("main")

    def _apply_guardrails(self, vc, ws_root, evo_number, trajectory, target):
        """Apply G2 + G5 + G6 guardrails on the current checked-out branch."""
        ws_root = Path(ws_root)
        guardrail_results: dict[str, Any] = {"target": target}

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

        # Bundle infra only on main
        if target == "main":
            _bundle_infra(ws_root)

        trajectory.append({"step": "guardrails", **guardrail_results})
        vc.commit(
            message=f"evo-{evo_number}-guardrails-{target.replace('/', '-')}: cleanup",
            tag=f"evo-{evo_number}-guardrails-{target.replace('/', '-')}",
        )


# Alias for dynamic import via orchestrator config (solve_all_with_evolution.py:292)
Template = StructuredNavigationTemplate
