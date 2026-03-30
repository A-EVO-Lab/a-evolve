"""Adaptive evolution engine with per-claim feedback and meta-learning.

Key improvements over code_evolve:
1. Uses per-claim feedback from MCP-Atlas to identify specific weaknesses
2. Tracks performance by task type and claim type
3. Learns from evolution history (meta-evolution)
4. Makes surgical, targeted changes based on analysis
5. Includes failure-pattern detection and auto-correction
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Any

from ...config import EvolveConfig
from ...contract.workspace import AgentWorkspace
from ...engine.base import EvolutionEngine
from ...engine.versioning import VersionControl
from ...llm.base import LLMProvider
from ...types import Observation, StepResult

# Shared utilities
from .base_analysis import analyze_observations, McpAutoCorrector, McpErrorPatternExtractor
from .base_analysis import AutoCorrector, ErrorPatternExtractor
from .code_analysis import CodeExecAnalyzer

# Adaptive components
from .analyzer import AdaptiveAnalyzer, AdaptiveAnalysisResult
from .prompts import (
    AdaptivePromptConfig,
    build_adaptive_evolution_prompt,
    build_adaptive_system_prompt,
    build_claim_type_skill,
    build_entity_verification_skill,
    build_multi_req_skill,
)

logger = logging.getLogger(__name__)


BASH_TOOL_SPEC = {
    "name": "workspace_bash",
    "description": (
        "Execute a bash command in the agent workspace directory. "
        "Use this to read/write skills, prompts, memory files, and inspect git history."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The bash command to execute in the workspace directory.",
            },
        },
        "required": ["command"],
    },
}


def _make_workspace_bash(workspace_root: str | Path):
    """Create workspace bash executor."""
    def bash(command: str) -> str:
        try:
            result = subprocess.run(
                ["bash", "-c", command],
                capture_output=True, text=True, timeout=60,
                cwd=str(workspace_root),
            )
            output = (result.stdout + result.stderr).strip()
            return output if output else "(no output)"
        except subprocess.TimeoutExpired:
            return "ERROR: Command timed out."
        except Exception as e:
            return f"ERROR: {e}"
    return bash


def _create_default_llm(config: EvolveConfig) -> LLMProvider:
    """Create default LLM provider based on config."""
    model = config.evolver_model

    if config.evolver_base_url:
        from ...llm.openai import OpenAIProvider
        return OpenAIProvider(
            model=model,
            base_url=config.evolver_base_url,
            api_key=config.evolver_api_key,
        )

    if "." in model and ("anthropic" in model or "amazon" in model or "meta" in model):
        from ...llm.bedrock import BedrockProvider
        return BedrockProvider(model_id=model, region=config.extra.get("region", "us-west-2"))
    if model.startswith("claude"):
        from ...llm.anthropic import AnthropicProvider
        return AnthropicProvider(model=model)
    if model.startswith(("gpt-", "o1", "o3")):
        from ...llm.openai import OpenAIProvider
        return OpenAIProvider(model=model)
    from ...llm.bedrock import BedrockProvider
    return BedrockProvider(model_id=model)


class AdaptiveEvolveEngine(EvolutionEngine):
    """Adaptive evolution engine with per-claim feedback and meta-learning.

    Improvements over code_evolve:
    - Analyzes per-claim failures to identify specific weaknesses
    - Tracks task-type and claim-type performance
    - Learns from evolution history (what worked/didn't work)
    - Makes surgical changes based on failure patterns
    - Includes auto-seeding of targeted skills
    """

    def __init__(
        self,
        config: EvolveConfig,
        llm: LLMProvider | None = None,
        *,
        prompt_config: AdaptivePromptConfig | None = None,
        error_extractor: ErrorPatternExtractor | None = None,
        auto_corrector: AutoCorrector | None = None,
        seed_prompt: str = "",
        memory_cap: int = 15,
        improvement_threshold: float = 0.02,
        stagnation_window: int = 5,
    ):
        """Initialize adaptive evolution engine.

        Args:
            config: Evolution configuration
            llm: Optional LLM provider (created if None)
            prompt_config: Optional prompt configuration
            error_extractor: Optional error pattern extractor
            auto_corrector: Optional auto-corrector
            seed_prompt: Original system prompt (preserved)
            memory_cap: Maximum memory entries
            improvement_threshold: Minimum improvement to reset stagnation (default: 2%)
            stagnation_window: Cycles without improvement before forcing mutation
        """
        self.config = config
        self._llm = llm
        self._prompt_config = prompt_config or AdaptivePromptConfig()
        self._error_extractor = error_extractor
        self._auto_corrector = auto_corrector
        self._memory_cap = memory_cap

        # Analyzers
        self._adaptive_analyzer = AdaptiveAnalyzer()
        self._code_analyzer = CodeExecAnalyzer()

        self._seed_prompt = seed_prompt
        self._accumulated_state: dict[str, Any] = {"name_corrections": {}}

        # Meta-evolution tracking
        self._evolution_history: list[dict[str, Any]] = []
        self._best_pass_rate: float = 0.0
        self._best_evo_tag: str = "evo-0"
        self._cycles_without_improvement: int = 0
        self._improvement_threshold = improvement_threshold
        self._stagnation_window = stagnation_window

        # System prompt for evolver LLM
        self._system_prompt = build_adaptive_system_prompt(self._prompt_config)

    @property
    def llm(self) -> LLMProvider:
        if self._llm is None:
            self._llm = _create_default_llm(self.config)
        return self._llm

    # ── EvolutionEngine Interface ────────────────────────────────

    def step(
        self,
        workspace: AgentWorkspace,
        observations: list[Observation],
        history: Any,
        trial: Any,
    ) -> StepResult:
        """Run one evolution step with adaptive analysis."""
        recent_logs = history.get_observations(last_n_cycles=2)
        cycle_num = history.latest_cycle + 1

        if not self._seed_prompt:
            self._seed_prompt = workspace.read_prompt()

        # Phase 1: Base analysis
        base_analysis = analyze_observations(
            recent_logs, error_extractor=self._error_extractor
        )

        # Phase 2: Code execution analysis
        code_stats = self._code_analyzer.analyze(recent_logs, base_analysis)

        # Phase 3: Adaptive analysis (NEW)
        adaptive_result = self._adaptive_analyzer.analyze(
            recent_logs, base_analysis, code_stats
        )

        logger.info("Base: %s", base_analysis.summary_text())
        logger.info("Code exec: %s", code_stats.summary_text())
        logger.info(
            "Adaptive: %d claim types, %d task types, %d failure patterns",
            len(adaptive_result.claim_stats),
            len(adaptive_result.task_type_stats),
            len(adaptive_result.failure_patterns),
        )

        # Phase 4: Auto-corrections
        self._accumulated_state["name_corrections"].update(base_analysis.hallucination_map)
        auto_fixes = self._apply_auto_corrections(workspace, base_analysis)

        # Phase 5: Auto-seed targeted skills based on failure patterns
        auto_fixes += self._auto_seed_skills(workspace, adaptive_result)

        # Phase 6: LLM-driven evolution (with adaptive analysis)
        skills_before = [s.name for s in workspace.list_skills()]

        prompt = build_adaptive_evolution_prompt(
            workspace=workspace,
            observations=recent_logs,
            analysis=adaptive_result,
            evo_number=cycle_num,
            evolve_prompts=self.config.evolve_prompts,
            evolve_skills=self.config.evolve_skills,
            evolve_memory=self.config.evolve_memory,
            prompt_config=self._prompt_config,
            evolution_history=self._evolution_history,
        )

        response = self._run_llm(prompt, workspace.root)

        # Phase 7: Workspace sanity check
        sanity_fixes = self._workspace_sanity_check(workspace)
        auto_fixes += len(sanity_fixes)

        skills_after = [s.name for s in workspace.list_skills()]
        new_skills = len(set(skills_after) - set(skills_before))
        workspace.clear_drafts()

        mutated = set(skills_after) != set(skills_before) or new_skills > 0 or auto_fixes > 0

        return StepResult(
            mutated=mutated,
            summary=(
                f"AdaptiveEvolve: {auto_fixes} auto-fixes, {new_skills} new skills, "
                f"{len(adaptive_result.failure_patterns)} patterns detected"
            ),
            metadata={
                "evo_number": cycle_num,
                "tasks_analyzed": len(recent_logs),
                "pass_rate": base_analysis.pass_rate,
                "auto_fixes": auto_fixes,
                "sanity_fixes": sanity_fixes,
                "skills_before": len(skills_before),
                "skills_after": len(skills_after),
                "new_skills": new_skills,
                "claim_types_analyzed": len(adaptive_result.claim_stats),
                "task_types_analyzed": len(adaptive_result.task_type_stats),
                "failure_patterns": len(adaptive_result.failure_patterns),
                "weakest_claim_types": [
                    {"type": ct, "pass_rate": pr}
                    for ct, pr in adaptive_result.weakest_claim_types[:3]
                ],
                "weakest_task_types": [
                    {"type": tt, "pass_rate": pr}
                    for tt, pr in adaptive_result.weakest_task_types[:3]
                ],
                "usage": response.get("usage", {}),
            },
        )

    def on_cycle_end(self, accepted: bool, score: float) -> None:
        """Called after cycle completes. Track meta-evolution history."""
        pass  # Tracking happens in evolve() method

    # ── Standalone Evolution API ─────────────────────────────────

    def evolve(
        self,
        workspace: AgentWorkspace,
        observation_logs: list[dict[str, Any]],
        evo_number: int = 0,
    ) -> dict[str, Any]:
        """Run one evolution pass with adaptive analysis and meta-learning.

        Args:
            workspace: Agent workspace
            observation_logs: Observations from solve cycles
            evo_number: Current evolution cycle number

        Returns:
            Evolution result metadata
        """
        vc = VersionControl(workspace.root)
        vc.init()

        if not self._seed_prompt:
            self._seed_prompt = workspace.read_prompt()

        # Phase 1: Analysis
        base_analysis = analyze_observations(
            observation_logs, error_extractor=self._error_extractor
        )
        code_stats = self._code_analyzer.analyze(observation_logs, base_analysis)
        adaptive_result = self._adaptive_analyzer.analyze(
            observation_logs, base_analysis, code_stats
        )

        logger.info("Base: %s", base_analysis.summary_text())
        logger.info("Code exec: %s", code_stats.summary_text())
        logger.info(
            "Adaptive: %d claim types, %d task types, %d failure patterns",
            len(adaptive_result.claim_stats),
            len(adaptive_result.task_type_stats),
            len(adaptive_result.failure_patterns),
        )

        self._accumulated_state["name_corrections"].update(base_analysis.hallucination_map)

        # Snapshot before evolution
        skills_before = [s.name for s in workspace.list_skills()]
        vc.commit(message=f"pre-evo-{evo_number}: snapshot", tag=f"pre-evo-{evo_number}")

        # Phase 2: Auto-corrections
        auto_fixes = self._apply_auto_corrections(workspace, base_analysis)

        # Phase 3: Auto-seed skills (NEW)
        auto_fixes += self._auto_seed_skills(workspace, adaptive_result)

        # Phase 4: Determine evolution scope (NEW - graduated scope)
        scope = self._determine_evolution_scope(adaptive_result)
        logger.info("Evolution scope: %s", scope)

        # Phase 5: LLM-driven evolution
        if scope["should_evolve"]:
            prompt = build_adaptive_evolution_prompt(
                workspace=workspace,
                observations=observation_logs,
                analysis=adaptive_result,
                evo_number=evo_number,
                evolve_prompts=self.config.evolve_prompts and scope.get("modify_prompt", True),
                evolve_skills=self.config.evolve_skills and scope.get("modify_skills", True),
                evolve_memory=self.config.evolve_memory,
                prompt_config=self._prompt_config,
                evolution_history=self._evolution_history,
            )
            response = self._run_llm(prompt, workspace.root)
        else:
            logger.info("Skipping LLM evolution (pass rate high, minimal changes needed)")
            response = {"content": "Skipped (high performance)", "usage": {}}

        # Phase 6: Workspace sanity check
        sanity_fixes = self._workspace_sanity_check(workspace)
        auto_fixes += len(sanity_fixes)

        skills_after = [s.name for s in workspace.list_skills()]
        new_skills = len(set(skills_after) - set(skills_before))
        workspace.clear_drafts()

        # Phase 7: Track meta-evolution (NEW)
        current_pass_rate = base_analysis.pass_rate
        improvement = current_pass_rate - self._best_pass_rate

        change_description = self._describe_changes(skills_before, skills_after, auto_fixes)
        self._record_evolution(evo_number, change_description, improvement, current_pass_rate)

        # Phase 8: Check for stagnation (NEW - enhanced gating)
        rejected = self._check_stagnation_gate(current_pass_rate, evo_number, vc)

        mutated = (
            not rejected
            and (set(skills_after) != set(skills_before) or new_skills > 0 or auto_fixes > 0)
        )

        if rejected:
            msg = f"evo-{evo_number}: ROLLED BACK (stagnation detected)"
        elif mutated:
            msg = f"evo-{evo_number}: {change_description}"
        else:
            msg = f"evo-{evo_number}: no mutation"

        vc.commit(message=msg, tag=f"evo-{evo_number}")

        return {
            "evo_number": evo_number,
            "tasks_analyzed": len(observation_logs),
            "pass_rate": current_pass_rate,
            "improvement": improvement,
            "auto_fixes": auto_fixes,
            "sanity_fixes": sanity_fixes,
            "skills_before": len(skills_before),
            "skills_after": len(skills_after),
            "new_skills": new_skills,
            "rejected": rejected,
            "claim_types_analyzed": len(adaptive_result.claim_stats),
            "task_types_analyzed": len(adaptive_result.task_type_stats),
            "failure_patterns": [p.pattern_name for p in adaptive_result.failure_patterns],
            "weakest_claim_types": dict(adaptive_result.weakest_claim_types[:3]),
            "weakest_task_types": dict(adaptive_result.weakest_task_types[:3]),
            "evolution_scope": scope,
            "usage": response.get("usage", {}),
        }

    # ── Private Helpers ──────────────────────────────────────────

    def _apply_auto_corrections(
        self, workspace: AgentWorkspace, analysis
    ) -> int:
        """Apply auto-corrections (tool name fixes, memory pruning)."""
        fixes = 0
        if self._auto_corrector is not None:
            fixes += self._auto_corrector.apply(
                workspace, analysis, self._accumulated_state
            )
        else:
            fixes += McpAutoCorrector().apply(
                workspace, analysis, self._accumulated_state
            )
        fixes += self._prune_memory(workspace)
        return fixes

    def _auto_seed_skills(
        self, workspace: AgentWorkspace, analysis: AdaptiveAnalysisResult
    ) -> int:
        """Auto-seed skills based on detected failure patterns.

        This is a key adaptive feature: immediately inject targeted skills
        when specific failure patterns are detected.
        """
        seeded = 0
        existing_skills = {s.name for s in workspace.list_skills()}

        # 1. Multi-requirement failures → seed multi-req handler
        multi_req_pattern = next(
            (p for p in analysis.failure_patterns if p.pattern_name == "multi_requirement_miss"),
            None
        )
        if multi_req_pattern and multi_req_pattern.count >= 3:
            if "multi-requirement-handler" not in existing_skills:
                self._write_skill(workspace, "multi-requirement-handler", build_multi_req_skill())
                logger.info("Auto-seeded multi-requirement-handler skill")
                seeded += 1

        # 2. Wrong entity failures → seed entity verification
        wrong_entity_pattern = next(
            (p for p in analysis.failure_patterns if p.pattern_name == "wrong_entity_targeting"),
            None
        )
        if wrong_entity_pattern and wrong_entity_pattern.count >= 2:
            if "entity-verification" not in existing_skills:
                self._write_skill(workspace, "entity-verification", build_entity_verification_skill())
                logger.info("Auto-seeded entity-verification skill")
                seeded += 1

        # 3. Weak claim types → seed claim-type-specific skills
        for claim_type, pass_rate in analysis.weakest_claim_types[:2]:
            if pass_rate < 0.5 and len(existing_skills) < self._prompt_config.max_skills - 1:
                skill_name = f"{claim_type}-handler"
                if skill_name not in existing_skills:
                    claim_stats = analysis.claim_stats.get(claim_type)
                    if claim_stats and claim_stats.examples:
                        skill_content = build_claim_type_skill(claim_type, claim_stats.examples)
                        self._write_skill(workspace, skill_name, skill_content)
                        logger.info(f"Auto-seeded {skill_name} skill (pass rate: {pass_rate:.0%})")
                        seeded += 1

        return seeded

    def _write_skill(self, workspace: AgentWorkspace, skill_name: str, content: str):
        """Write a skill to workspace."""
        skill_dir = workspace.root / "skills" / skill_name
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(content)

    def _determine_evolution_scope(self, analysis: AdaptiveAnalysisResult) -> dict[str, Any]:
        """Determine what should be evolved based on performance.

        This implements graduated scope evolution: make minimal changes when
        performance is high, targeted changes when specific issues detected.
        """
        pass_rate = analysis.base_analysis.pass_rate
        scope = {
            "should_evolve": True,
            "modify_prompt": False,
            "modify_skills": False,
            "intensity": "minimal",
        }

        # High performance → minimal changes
        # Only skip evolution if BOTH high pass rate AND recent stability
        if pass_rate >= 0.90 and self._cycles_without_improvement >= 2:
            # Consistently high (2+ cycles at/near best) → skip
            scope["should_evolve"] = False
            scope["intensity"] = "none"
            return scope
        elif pass_rate >= 0.90:
            # High but potentially unstable → very light touch
            scope["intensity"] = "minimal"
            scope["modify_skills"] = True  # Keep skills fresh
            return scope

        if pass_rate >= 0.85:
            scope["intensity"] = "minimal"
            # Only modify skills for specific weak claim/task types
            if analysis.weakest_claim_types and analysis.weakest_claim_types[0][1] < 0.6:
                scope["modify_skills"] = True
            return scope

        # Medium performance → targeted changes
        if pass_rate >= 0.70:
            scope["intensity"] = "targeted"
            scope["modify_skills"] = True

            # Modify prompt only if failure patterns require it
            if any(
                p.pattern_name in ["multi_requirement_miss", "wrong_entity_targeting"]
                for p in analysis.failure_patterns
            ):
                scope["modify_prompt"] = True

            return scope

        # Low performance → comprehensive evolution
        scope["intensity"] = "comprehensive"
        scope["modify_prompt"] = True
        scope["modify_skills"] = True

        return scope

    def _check_stagnation_gate(
        self, current_pass_rate: float, evo_number: int, vc: VersionControl
    ) -> bool:
        """Check for stagnation and rollback if needed.

        Enhanced gating: rollback if no improvement for N cycles AND
        pass rate is below 90% (i.e., still room for improvement).
        """
        improvement = current_pass_rate - self._best_pass_rate

        if improvement >= self._improvement_threshold:
            # Significant improvement
            self._best_pass_rate = current_pass_rate
            self._best_evo_tag = f"pre-evo-{evo_number}"
            self._cycles_without_improvement = 0
            return False

        # No improvement
        self._cycles_without_improvement += 1

        # Check stagnation condition
        if self._cycles_without_improvement >= self._stagnation_window:
            # Calculate degradation from best
            degradation = self._best_pass_rate - current_pass_rate

            # Rollback if: (1) stagnant AND (2) significantly below best (>5% degradation)
            # OR if best was below 90% (original condition for low performers)
            if degradation > 0.05 or self._best_pass_rate < 0.90:
                logger.warning(
                    "Stagnation detected: %d cycles without improvement. "
                    "Best: %.1f%%, Current: %.1f%% (%.1f%% degradation). Rolling back to %s.",
                    self._cycles_without_improvement,
                    self._best_pass_rate * 100,
                    current_pass_rate * 100,
                    degradation * 100,
                    self._best_evo_tag,
                )
                vc.rollback_to_tag(self._best_evo_tag)
                self._cycles_without_improvement = 0
                return True

        return False

    def _record_evolution(
        self, cycle: int, description: str, improvement: float, pass_rate: float
    ):
        """Record evolution change for meta-learning."""
        self._evolution_history.append({
            "cycle": cycle,
            "description": description,
            "improvement": improvement,
            "impact": improvement,  # Alias for consistency
            "pass_rate": pass_rate,
        })

        # Keep only recent history
        self._evolution_history = self._evolution_history[-10:]

    def _describe_changes(
        self, skills_before: list[str], skills_after: list[str], auto_fixes: int
    ) -> str:
        """Generate human-readable description of changes."""
        added = set(skills_after) - set(skills_before)
        removed = set(skills_before) - set(skills_after)

        parts = []
        if auto_fixes:
            parts.append(f"{auto_fixes} auto-fixes")
        if added:
            parts.append(f"added {', '.join(added)}")
        if removed:
            parts.append(f"removed {', '.join(removed)}")

        return ", ".join(parts) if parts else "no changes"

    def _prune_memory(self, workspace: AgentWorkspace) -> int:
        """Prune memory files to cap."""
        if not workspace.memory_dir.exists():
            return 0
        pruned = 0
        for mem_file in workspace.memory_dir.glob("*.jsonl"):
            lines = [l for l in mem_file.read_text().splitlines() if l.strip()]
            if len(lines) > self._memory_cap:
                kept = lines[-self._memory_cap:]
                mem_file.write_text("\n".join(kept) + "\n")
                pruned += len(lines) - self._memory_cap
        if pruned:
            logger.info("Pruned %d memory entries (cap=%d)", pruned, self._memory_cap)
        return min(pruned, 1)

    def _workspace_sanity_check(self, workspace: AgentWorkspace) -> list[str]:
        """Deterministic post-mutation fixes.

        Runs after every LLM mutation to prevent workspace corruption.
        No scoring, no rejection — just fixes problems silently.

        Checks:
        1. Prompt over limit → truncate preserving seed content
        2. Empty skills (body < 20 chars) → remove
        3. Duplicate skills (>60% word overlap) → remove shorter one
        4. Overfitting in prompt (batch-specific data) → strip those lines
        5. Skill count over 15 → remove oldest excess
        6. Identity paragraph removed → restore it
        """
        import re

        fixes: list[str] = []
        cfg = self._prompt_config

        # 1. Truncate bloated prompt
        prompt = workspace.read_prompt()
        if len(prompt) > cfg.prompt_max_chars:
            truncated = self._truncate_prompt(prompt, self._seed_prompt, cfg.prompt_max_chars)
            workspace.write_prompt(truncated)
            fixes.append(f"Truncated prompt: {len(prompt)} → {len(truncated)} chars")

        # 2. Remove empty skills
        for skill in workspace.list_skills():
            content = workspace.read_skill(skill.name)
            body = self._strip_frontmatter(content)
            if len(body.strip()) < 20:
                workspace.delete_skill(skill.name)
                fixes.append(f"Removed empty skill: {skill.name}")

        # 3. Deduplicate skills by word overlap
        skills = workspace.list_skills()
        if len(skills) >= 2:
            skill_words: dict[str, set[str]] = {}
            skill_sizes: dict[str, int] = {}
            for s in skills:
                content = workspace.read_skill(s.name)
                body = self._strip_frontmatter(content).lower()
                words = set(re.findall(r'\b[a-z]{3,}\b', body))
                skill_words[s.name] = words
                skill_sizes[s.name] = len(body)

            names = list(skill_words.keys())
            removed: set[str] = set()
            for i in range(len(names)):
                if names[i] in removed:
                    continue
                for j in range(i + 1, len(names)):
                    if names[j] in removed:
                        continue
                    w1, w2 = skill_words[names[i]], skill_words[names[j]]
                    if not w1 or not w2:
                        continue
                    jaccard = len(w1 & w2) / len(w1 | w2)
                    if jaccard > 0.6:
                        victim = names[i] if skill_sizes[names[i]] < skill_sizes[names[j]] else names[j]
                        workspace.delete_skill(victim)
                        removed.add(victim)
                        fixes.append(f"Removed duplicate skill: {victim} (jaccard={jaccard:.2f})")
                        break

        # 4. Strip overfitting patterns from prompt
        prompt = workspace.read_prompt()
        overfitting_patterns = [
            r'^.*B\d+:.*$', r'^.*batch \d+.*$',
            r'^.*\d+/\d+ at.*$', r'^.*\d+ claims? lost.*$',
        ]
        lines = prompt.splitlines()
        cleaned = []
        stripped_count = 0
        for line in lines:
            if any(re.search(p, line, re.IGNORECASE) for p in overfitting_patterns):
                stripped_count += 1
            else:
                cleaned.append(line)
        if stripped_count > 0:
            workspace.write_prompt("\n".join(cleaned))
            fixes.append(f"Stripped {stripped_count} overfitting line(s) from prompt")

        # 5. Enforce skill count limit
        skills = workspace.list_skills()
        max_skills = cfg.max_skills
        if len(skills) > max_skills:
            excess = skills[:-max_skills]
            for s in excess:
                workspace.delete_skill(s.name)
                fixes.append(f"Removed excess skill: {s.name}")

        # 6. Restore identity paragraph if removed
        if self._seed_prompt:
            seed_identity = self._seed_prompt.split("\n\n")[0].strip()
            current = workspace.read_prompt()
            if seed_identity and seed_identity not in current:
                workspace.write_prompt(seed_identity + "\n\n" + current)
                fixes.append("Restored seed identity paragraph")

        if fixes:
            logger.info("Sanity fixes: %s", fixes)
        return fixes

    @staticmethod
    def _strip_frontmatter(content: str) -> str:
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                return parts[2]
        return content

    @staticmethod
    def _truncate_prompt(prompt: str, seed: str, limit: int) -> str:
        """Truncate prompt preserving seed content."""
        import re

        if len(prompt) <= limit:
            return prompt
        if seed and len(seed) <= limit:
            sections = re.split(r"(?=^## )", prompt, flags=re.MULTILINE)
            seed_sections = re.split(r"(?=^## )", seed, flags=re.MULTILINE)
            seed_headers = {s.split("\n")[0].strip() for s in seed_sections if s.strip()}
            result = seed.rstrip()
            remaining = limit - len(result)
            for section in sections:
                header = section.split("\n")[0].strip()
                if header in seed_headers or not header:
                    continue
                if len(section) + 2 <= remaining:
                    result += "\n\n" + section.rstrip()
                    remaining -= len(section) + 2
            return result.rstrip() + "\n"
        sections = re.split(r"(?=^## )", prompt, flags=re.MULTILINE)
        parts = [sections[0]] if sections else []
        remaining = limit - len(parts[0]) if parts else limit
        for section in sections[1:]:
            if len(section) <= remaining:
                parts.append(section)
                remaining -= len(section)
        return "".join(parts).rstrip() + "\n"

    def _run_llm(self, prompt: str, workspace_root: Path) -> dict[str, Any]:
        """Run LLM with bash tool access."""
        bash_fn = _make_workspace_bash(workspace_root)
        try:
            from ...llm.bedrock import BedrockProvider
            if isinstance(self.llm, BedrockProvider):
                response = self.llm.converse_loop(
                    system_prompt=self._system_prompt,
                    user_message=prompt,
                    tools=[BASH_TOOL_SPEC],
                    tool_executor={"workspace_bash": lambda command: bash_fn(command)},
                    max_tokens=self.config.evolver_max_tokens,
                )
                return {"content": response.content, "usage": response.usage}
        except ImportError:
            pass
        from ...llm.base import LLMMessage
        messages = [
            LLMMessage(role="system", content=self._system_prompt),
            LLMMessage(role="user", content=prompt),
        ]
        response = self.llm.complete(messages, max_tokens=self.config.evolver_max_tokens)
        return {"content": response.content, "usage": response.usage}

    @staticmethod
    def prepare_workspace(workspace_root: Path) -> None:
        """Patch an MCP seed workspace before first solve. Idempotent."""
        # Patch system prompt with code execution guidance
        prompt_path = workspace_root / "prompts" / "system.md"
        if prompt_path.exists():
            current = prompt_path.read_text()
            if "execute_code" not in current:
                prompt_path.write_text(current.rstrip() + "\n" + _SYSTEM_PROMPT_PATCH)
                logger.info("Patched system prompt with code execution guidance")

        # Seed code-execution-patterns skill
        skill_dir = workspace_root / "skills" / "code-execution-patterns"
        skill_file = skill_dir / "SKILL.md"
        if not skill_file.exists():
            skill_dir.mkdir(parents=True, exist_ok=True)
            skill_file.write_text(_CODE_EXEC_SEED_SKILL)
            logger.info("Seeded code-execution-patterns skill")


# ── Constants ────────────────────────────────────────────────

_SYSTEM_PROMPT_PATCH = """
## Code Execution

You have an `execute_code` tool that runs Python code with access to all MCP tools via `call_tool(name, args)`.

**Use `execute_code` when:**
- A task requires searching, iterating, or trying multiple values
- You need to chain 3+ tool calls where output feeds into the next
- You need to filter or aggregate large result sets
- A tool call fails and you want to retry with variations

**Use direct tool calls when:**
- The task needs only 1-2 simple tool calls with known parameters
- You need to reason carefully about each intermediate result

Inside `execute_code`, use `print()` to return results. Available: `json`, `re`, `math`, `datetime`.
"""

_CODE_EXEC_SEED_SKILL = """\
---
name: code-execution-patterns
description: When and how to use execute_code for efficient MCP tool orchestration
---

# Code Execution Patterns

Use `execute_code` to write Python when a task involves:

## When to use code execution
- **Search/iteration**: Trying multiple IDs, queries, or parameter values
- **Chaining 3+ tools**: Output of one feeds into the next
- **Filtering large results**: Process data before returning to context
- **Retries with variations**: Same tool with different parameters
- **Aggregation**: Combining results from multiple tool calls

## When to use direct tool calls
- Simple 1-2 tool tasks with known parameters
- Tasks where you need to reason about each result before the next call

## Pattern: Search loop
```python
for candidate in candidates:
    result = call_tool("search_tool", {"query": candidate})
    data = json.loads(result)
    if data.get("found"):
        print(json.dumps(data))
        break
```

## Pattern: Tool chaining
```python
# Get data -> transform -> store result
result1 = call_tool("get_data", {"id": "123"})
data = json.loads(result1)
processed = [x["name"] for x in data if x["active"]]
print(json.dumps(processed))
```

## Pattern: Retry with fallbacks
```python
queries = ["exact match", "fuzzy match", "broad match"]
for q in queries:
    result = call_tool("search", {"query": q})
    if "found" in result:
        print(result)
        break
```
"""
