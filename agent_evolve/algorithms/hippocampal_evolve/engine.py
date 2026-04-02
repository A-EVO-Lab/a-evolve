"""HippocampalEvolveEngine -- neuroscience-inspired associative memory evolution.

Implements a biologically-grounded evolution strategy based on hippocampal
memory consolidation. Rather than using an LLM to propose mutations directly,
this engine builds an associative knowledge graph from observation traces and
uses spreading activation (CA3 pattern completion) to retrieve relevant
prior experience, then applies Hebbian learning to strengthen successful
patterns and weaken unsuccessful ones.

Three integrated feedback loops drive evolution:

1. **Graph Learning Loop** -- Each observation enriches a co-occurrence graph.
   Concepts extracted from task trajectories become nodes; pairs that co-occur
   in successful observations get COACTIVATED edges strengthened.

2. **Skill Crystallization Loop** -- Recurring procedural patterns across
   multiple observations are detected and crystallized into reusable skills,
   similar to how the basal ganglia consolidates motor routines.

3. **Temporal Adaptation** -- Exponential recency decay (23-day half-life)
   ensures the graph naturally shifts toward current problem patterns while
   preserving long-term structure.

Reference: https://github.com/allthingssecurity/claude_hippocampus
"""

from __future__ import annotations

import logging
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ...engine.base import EvolutionEngine
from ...types import Observation, StepResult

logger = logging.getLogger(__name__)

# ── Hippocampal model constants ─────────────────────────────────────

SPREAD_DECAY = 0.6          # activation drop per hop
MAX_SPREAD_HOPS = 2         # depth of associative spread
ACTIVATION_THRESHOLD = 0.15 # minimum score to keep a node
RECENCY_LAMBDA = 0.03       # exp decay rate (~23-day half-life)
MAX_SEEDS = 10              # max seed nodes per query
MAX_NEIGHBORS_PER_HOP = 15  # cap neighbors explored per hop
MIN_SKILL_OCCURRENCES = 2   # observations before crystallizing a skill

STOPWORDS = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "need", "must", "that",
    "this", "these", "those", "with", "from", "into", "for", "and",
    "but", "not", "file", "line", "code", "test", "error", "none",
    "true", "false", "null", "task", "step", "tool", "output", "input",
})


# ── Data structures ─────────────────────────────────────────────────

@dataclass
class MemoryNode:
    """A concept node in the associative graph."""
    name: str
    activation: float = 0.0
    occurrences: int = 0
    last_seen: datetime | None = None
    source: str = "extraction"  # extraction, skill, seed


@dataclass
class CoactivationEdge:
    """A weighted edge between two co-occurring concepts."""
    count: int = 0
    weight: float = 0.0
    last_updated: datetime | None = None
    contexts: list[str] = field(default_factory=list)


@dataclass
class CrystallizedSkill:
    """A procedural skill detected from recurring patterns."""
    name: str
    description: str
    trigger_concepts: list[str]
    success_rate: float
    observation_count: int


# ── Core engine ─────────────────────────────────────────────────────

class HippocampalEvolveEngine(EvolutionEngine):
    """Evolution engine using hippocampal associative memory.

    Instead of asking an LLM to propose mutations, this engine:
    1. Extracts concepts from observation trajectories (Dentate Gyrus)
    2. Builds co-occurrence edges between concepts (Hebbian learning)
    3. Uses spreading activation to find relevant prior knowledge (CA3)
    4. Crystallizes recurring success patterns into skills (Basal Ganglia)
    5. Writes the best skills and memory into the agent workspace

    The workspace evolves through accumulated experience rather than
    explicit LLM-guided mutation.
    """

    def __init__(
        self,
        recency_lambda: float = RECENCY_LAMBDA,
        min_skill_occurrences: int = MIN_SKILL_OCCURRENCES,
        max_skills: int = 10,
    ) -> None:
        self._nodes: dict[str, MemoryNode] = {}
        self._edges: dict[tuple[str, str], CoactivationEdge] = {}
        self._skills: dict[str, CrystallizedSkill] = {}
        self._cycle_count = 0
        self._recency_lambda = recency_lambda
        self._min_skill_occurrences = min_skill_occurrences
        self._max_skills = max_skills
        # Track concept→outcome associations for skill detection
        self._concept_success: Counter = Counter()
        self._concept_failure: Counter = Counter()
        self._concept_cooccurrence: Counter = Counter()

    # ── EvolutionEngine interface ───────────────────────────────

    def step(
        self,
        workspace: Any,
        observations: list[Observation],
        history: Any,
        trial: Any,
    ) -> StepResult:
        """Run one hippocampal evolution cycle.

        Phase 1 (Encoding): Extract concepts from observations
        Phase 2 (Consolidation): Build co-activation edges (Hebbian)
        Phase 3 (Recall): Spread activation to find relevant patterns
        Phase 4 (Crystallization): Detect and write skills
        Phase 5 (Expression): Update workspace with new knowledge
        """
        self._cycle_count += 1
        now = datetime.now(timezone.utc)
        logger.info("=== Hippocampal evolution cycle %d ===", self._cycle_count)

        # Phase 1: Encoding (Dentate Gyrus -- pattern separation)
        all_concepts: list[list[str]] = []
        for obs in observations:
            concepts = self._extract_concepts(obs)
            all_concepts.append(concepts)
            score = obs.feedback.score if obs.feedback else 0.0
            for c in concepts:
                self._ensure_node(c, now)
                if score > 0:
                    self._concept_success[c] += 1
                else:
                    self._concept_failure[c] += 1
        logger.info("Phase 1: Extracted concepts from %d observations", len(observations))

        # Phase 2: Consolidation (Hebbian learning -- fire together, wire together)
        edges_updated = 0
        for concepts in all_concepts:
            edges_updated += self._build_coactivations(concepts, now)
        logger.info("Phase 2: Updated %d co-activation edges", edges_updated)

        # Phase 3: Recall (CA3 -- spreading activation)
        # Build a query from the most frequent failure concepts
        failure_concepts = [c for c, _ in self._concept_failure.most_common(5)]
        if failure_concepts:
            activated = self._spreading_activation(failure_concepts)
            logger.info(
                "Phase 3: Activated %d nodes from failure patterns",
                len(activated),
            )
        else:
            activated = []

        # Phase 4: Crystallization (Basal Ganglia -- skill detection)
        new_skills = self._detect_skills()
        logger.info("Phase 4: Detected %d new skills", len(new_skills))

        # Phase 5: Expression -- write to workspace
        skills_written = self._write_to_workspace(workspace, new_skills, activated)
        memory_written = self._write_memory(workspace, observations)

        mutated = skills_written > 0 or memory_written > 0
        summary = (
            f"hippocampal cycle {self._cycle_count}: "
            f"{len(self._nodes)} concepts, {len(self._edges)} edges, "
            f"{skills_written} skills written, {memory_written} memories"
        )

        return StepResult(
            mutated=mutated,
            summary=summary,
            metadata={
                "cycle": self._cycle_count,
                "concepts_total": len(self._nodes),
                "edges_total": len(self._edges),
                "skills_detected": len(new_skills),
                "skills_written": skills_written,
                "memory_written": memory_written,
                "activated_nodes": len(activated),
            },
        )

    def on_cycle_end(self, accepted: bool, score: float) -> None:
        """Apply temporal decay to the graph after each cycle."""
        self._apply_temporal_decay()

    # ── Phase 1: Concept extraction (Dentate Gyrus) ─────────────

    def _extract_concepts(self, obs: Observation) -> list[str]:
        """Extract meaningful concepts from an observation's trajectory."""
        texts = []

        # From task input
        if obs.task.input:
            texts.append(obs.task.input)

        # From trajectory output (patch/diff)
        if obs.trajectory.output:
            texts.append(obs.trajectory.output)

        # From trajectory steps
        for step in obs.trajectory.steps:
            if isinstance(step, dict):
                for key in ("input_summary", "output_summary", "action", "tool"):
                    val = step.get(key, "")
                    if val:
                        texts.append(str(val))

        # From feedback
        if obs.feedback and obs.feedback.detail:
            texts.append(obs.feedback.detail)

        combined = " ".join(texts)
        return self._extract_keywords(combined)

    @staticmethod
    def _extract_keywords(text: str) -> list[str]:
        """Extract meaningful keywords from text.

        Uses regex-based extraction for technical terms, error patterns,
        and multi-word phrases. No LLM needed.
        """
        concepts = set()
        lower = text.lower()

        # Single meaningful tokens
        tokens = re.findall(r"[a-zA-Z0-9_\-]+", lower)
        for t in tokens:
            if len(t) > 3 and t not in STOPWORDS:
                concepts.add(t)

        # Hyphenated technical terms (e.g., wasm-pack, docker-compose)
        for match in re.findall(r"[a-zA-Z]+-[a-zA-Z]+(?:-[a-zA-Z]+)*", text):
            if 4 < len(match) <= 40:
                concepts.add(match.lower())

        # CamelCase identifiers
        for match in re.findall(r"[A-Z][a-z]+[A-Z][a-zA-Z]+", text):
            if 4 < len(match) <= 40:
                concepts.add(match)

        # Error patterns
        for match in re.findall(r"(\w+Error|\w+Exception)", text):
            concepts.add(match)

        # File extensions as technology signals
        for match in re.findall(r"\.(py|rs|ts|js|go|java|rb|cpp|c|h|jsx|tsx)\b", lower):
            lang_map = {
                "py": "python", "rs": "rust", "ts": "typescript",
                "js": "javascript", "go": "golang", "java": "java",
                "rb": "ruby", "cpp": "cpp", "jsx": "react", "tsx": "react",
            }
            if match in lang_map:
                concepts.add(lang_map[match])

        # Filter noise
        filtered = set()
        noise_prefixes = ("/Users/", "/tmp/", "http://", "https://", "/var/")
        for c in concepts:
            if any(c.startswith(p) for p in noise_prefixes):
                continue
            if len(c) > 60:
                continue
            filtered.add(c)

        return sorted(filtered)[:30]  # cap to prevent explosion

    # ── Phase 2: Hebbian consolidation ──────────────────────────

    def _ensure_node(self, name: str, now: datetime) -> None:
        if name in self._nodes:
            self._nodes[name].occurrences += 1
            self._nodes[name].last_seen = now
        else:
            self._nodes[name] = MemoryNode(
                name=name, occurrences=1, last_seen=now
            )

    def _build_coactivations(self, concepts: list[str], now: datetime) -> int:
        """Create/strengthen edges between co-occurring concepts."""
        updated = 0
        # Only connect the top concepts to avoid quadratic explosion
        capped = concepts[:20]
        for i in range(len(capped)):
            for j in range(i + 1, len(capped)):
                a, b = capped[i], capped[j]
                key = (min(a, b), max(a, b))
                self._concept_cooccurrence[key] += 1

                if key in self._edges:
                    edge = self._edges[key]
                    edge.count += 1
                    edge.last_updated = now
                else:
                    self._edges[key] = CoactivationEdge(
                        count=1, last_updated=now
                    )
                # Recompute weight with recency
                self._edges[key].weight = self._compute_weight(
                    self._edges[key].count, self._edges[key].last_updated
                )
                updated += 1
        return updated

    def _compute_weight(self, count: int, last_updated: datetime | None) -> float:
        """Weight = count * recency_decay (exponential, 23-day half-life)."""
        if not last_updated:
            return float(count)
        days = (datetime.now(timezone.utc) - last_updated).total_seconds() / 86400
        recency = math.exp(-self._recency_lambda * max(0, days))
        return count * recency

    # ── Phase 3: Spreading activation (CA3) ─────────────────────

    def _spreading_activation(
        self,
        seed_names: list[str],
        max_hops: int = MAX_SPREAD_HOPS,
    ) -> list[tuple[str, float]]:
        """Spread activation from seed concepts through the co-occurrence graph.

        Returns [(concept_name, activation_score)] sorted by score descending.
        """
        activation_map: dict[str, float] = {}

        # Seed phase
        for name in seed_names:
            if name in self._nodes:
                activation_map[name] = 1.0

        if not activation_map:
            return []

        # Spread phase
        visited = set(activation_map.keys())
        for hop in range(1, max_hops + 1):
            frontier = {
                n: s for n, s in activation_map.items()
                if s >= ACTIVATION_THRESHOLD
            }
            if not frontier:
                break

            for node_name, node_score in frontier.items():
                neighbors = self._get_neighbors(node_name, visited)
                if not neighbors:
                    continue

                max_weight = max(w for _, w in neighbors)
                for neighbor_name, weight in neighbors[:MAX_NEIGHBORS_PER_HOP]:
                    norm_weight = weight / max_weight if max_weight > 0 else 0.5
                    spread_score = node_score * SPREAD_DECAY * norm_weight

                    # Workspace-aware boosting: boost nodes related to success
                    success = self._concept_success.get(neighbor_name, 0)
                    failure = self._concept_failure.get(neighbor_name, 0)
                    total = success + failure
                    if total > 0:
                        success_ratio = success / total
                        spread_score *= (0.5 + success_ratio)  # 0.5x to 1.5x

                    if neighbor_name in activation_map:
                        activation_map[neighbor_name] = max(
                            activation_map[neighbor_name], spread_score
                        )
                    else:
                        activation_map[neighbor_name] = spread_score
                    visited.add(neighbor_name)

        # Collect and sort
        results = [
            (name, score) for name, score in activation_map.items()
            if score >= ACTIVATION_THRESHOLD
        ]
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:30]

    def _get_neighbors(
        self, node_name: str, exclude: set[str]
    ) -> list[tuple[str, float]]:
        """Get neighbors of a node sorted by edge weight."""
        neighbors = []
        for (a, b), edge in self._edges.items():
            if a == node_name and b not in exclude:
                neighbors.append((b, edge.weight))
            elif b == node_name and a not in exclude:
                neighbors.append((a, edge.weight))
        neighbors.sort(key=lambda x: x[1], reverse=True)
        return neighbors

    # ── Phase 4: Skill crystallization (Basal Ganglia) ──────────

    def _detect_skills(self) -> list[CrystallizedSkill]:
        """Detect recurring success patterns and crystallize into skills."""
        new_skills = []

        # Find concept clusters that co-occur frequently with success
        success_concepts = {
            c for c, count in self._concept_success.items()
            if count >= self._min_skill_occurrences
        }

        if not success_concepts:
            return new_skills

        # Group by strongly connected subgraphs
        clusters = self._find_concept_clusters(success_concepts)

        for cluster in clusters:
            # Compute cluster success rate
            total_success = sum(self._concept_success.get(c, 0) for c in cluster)
            total_failure = sum(self._concept_failure.get(c, 0) for c in cluster)
            total = total_success + total_failure
            if total < self._min_skill_occurrences:
                continue
            success_rate = total_success / total if total > 0 else 0.0

            # Only crystallize high-success clusters
            if success_rate < 0.5:
                continue

            # Generate skill name from top concepts
            sorted_concepts = sorted(
                cluster,
                key=lambda c: self._concept_success.get(c, 0),
                reverse=True,
            )
            skill_name = "_".join(sorted_concepts[:3])

            if skill_name in self._skills:
                continue

            skill = CrystallizedSkill(
                name=skill_name,
                description=(
                    f"Pattern detected from {total} observations "
                    f"({success_rate:.0%} success rate). "
                    f"Key concepts: {', '.join(sorted_concepts[:5])}."
                ),
                trigger_concepts=sorted_concepts[:5],
                success_rate=success_rate,
                observation_count=total,
            )
            self._skills[skill_name] = skill
            new_skills.append(skill)
            logger.info(
                "Crystallized skill: %s (%.0f%% success, %d obs)",
                skill_name, success_rate * 100, total,
            )

        return new_skills

    def _find_concept_clusters(
        self, concepts: set[str], min_edge_weight: float = 1.0
    ) -> list[list[str]]:
        """Find connected components among high-success concepts."""
        # Build adjacency list restricted to the concept set
        adj: dict[str, set[str]] = {c: set() for c in concepts}
        for (a, b), edge in self._edges.items():
            if a in concepts and b in concepts and edge.weight >= min_edge_weight:
                adj[a].add(b)
                adj[b].add(a)

        # BFS to find connected components
        visited: set[str] = set()
        clusters = []
        for start in concepts:
            if start in visited:
                continue
            cluster = []
            queue = [start]
            while queue:
                node = queue.pop(0)
                if node in visited:
                    continue
                visited.add(node)
                cluster.append(node)
                for neighbor in adj.get(node, set()):
                    if neighbor not in visited:
                        queue.append(neighbor)
            if len(cluster) >= 2:  # Only meaningful clusters
                clusters.append(cluster)

        return clusters

    # ── Phase 5: Workspace expression ───────────────────────────

    def _write_to_workspace(
        self,
        workspace: Any,
        new_skills: list[CrystallizedSkill],
        activated: list[tuple[str, float]],
    ) -> int:
        """Write crystallized skills to the agent workspace."""
        written = 0
        existing = {s.name for s in workspace.list_skills()}

        for skill in new_skills[:self._max_skills]:
            if skill.name in existing:
                continue
            if len(existing) >= self._max_skills:
                break

            content = self._format_skill_content(skill, activated)
            workspace.write_skill(
                skill.name,
                f"---\nname: {skill.name}\n"
                f"description: {skill.description[:150]}\n"
                f"---\n\n{content}",
            )
            existing.add(skill.name)
            written += 1
            logger.info("Wrote skill to workspace: %s", skill.name)

        return written

    def _format_skill_content(
        self,
        skill: CrystallizedSkill,
        activated: list[tuple[str, float]],
    ) -> str:
        """Format a crystallized skill as markdown content."""
        lines = [
            f"# {skill.name.replace('_', ' ').title()}",
            "",
            f"**Success rate:** {skill.success_rate:.0%} "
            f"across {skill.observation_count} observations",
            "",
            "## Trigger Concepts",
            "",
        ]
        for concept in skill.trigger_concepts:
            success = self._concept_success.get(concept, 0)
            failure = self._concept_failure.get(concept, 0)
            lines.append(f"- `{concept}` ({success} successes, {failure} failures)")
        lines.append("")

        # Add associated high-activation concepts from CA3 recall
        relevant = [
            (name, score) for name, score in activated
            if name in {c for c in skill.trigger_concepts}
            or any(
                (min(name, tc), max(name, tc)) in self._edges
                for tc in skill.trigger_concepts
            )
        ]
        if relevant:
            lines.append("## Associated Patterns")
            lines.append("")
            for name, score in relevant[:5]:
                lines.append(f"- `{name}` (activation: {score:.2f})")
            lines.append("")

        return "\n".join(lines)

    def _write_memory(self, workspace: Any, observations: list[Observation]) -> int:
        """Write episodic memory entries from observations."""
        written = 0
        for obs in observations:
            score = obs.feedback.score if obs.feedback else 0.0
            concepts = self._extract_concepts(obs)

            # Extract files from patch
            files = []
            if obs.trajectory.output:
                for m in re.finditer(
                    r"^(?:\+\+\+)\s+[ab]/(.+)$",
                    obs.trajectory.output,
                    re.MULTILINE,
                ):
                    if m.group(1) != "/dev/null":
                        files.append(m.group(1))

            entry = {
                "task_id": obs.task.id,
                "cycle": self._cycle_count,
                "score": score,
                "files_edited": files[:10],
                "concepts": concepts[:10],
                "approach_summary": (
                    f"Cycle {self._cycle_count}: "
                    f"{'PASS' if score > 0 else 'FAIL'} on {obs.task.id}. "
                    f"Edited {len(files)} file(s). "
                    f"Key concepts: {', '.join(concepts[:5])}."
                ),
            }
            workspace.add_memory(entry, category="episodic")
            written += 1

        return written

    # ── Temporal decay ──────────────────────────────────────────

    def _apply_temporal_decay(self) -> None:
        """Recompute all edge weights with temporal decay."""
        to_remove = []
        for key, edge in self._edges.items():
            edge.weight = self._compute_weight(edge.count, edge.last_updated)
            if edge.weight < 0.01:
                to_remove.append(key)

        for key in to_remove:
            del self._edges[key]

        if to_remove:
            logger.debug("Pruned %d decayed edges", len(to_remove))

    # ── Serialization (for persistence across runs) ─────────────

    def get_state(self) -> dict[str, Any]:
        """Serialize the graph state for persistence."""
        return {
            "nodes": {
                name: {
                    "occurrences": node.occurrences,
                    "last_seen": node.last_seen.isoformat() if node.last_seen else None,
                }
                for name, node in self._nodes.items()
            },
            "edges": {
                f"{a}|{b}": {
                    "count": edge.count,
                    "weight": edge.weight,
                    "last_updated": edge.last_updated.isoformat() if edge.last_updated else None,
                }
                for (a, b), edge in self._edges.items()
            },
            "concept_success": dict(self._concept_success),
            "concept_failure": dict(self._concept_failure),
            "cycle_count": self._cycle_count,
        }

    def load_state(self, state: dict[str, Any]) -> None:
        """Load graph state from a persisted dict."""
        self._cycle_count = state.get("cycle_count", 0)
        self._concept_success = Counter(state.get("concept_success", {}))
        self._concept_failure = Counter(state.get("concept_failure", {}))

        for name, data in state.get("nodes", {}).items():
            last_seen = None
            if data.get("last_seen"):
                last_seen = datetime.fromisoformat(data["last_seen"])
            self._nodes[name] = MemoryNode(
                name=name,
                occurrences=data.get("occurrences", 1),
                last_seen=last_seen,
            )

        for key_str, data in state.get("edges", {}).items():
            a, b = key_str.split("|", 1)
            last_updated = None
            if data.get("last_updated"):
                last_updated = datetime.fromisoformat(data["last_updated"])
            self._edges[(a, b)] = CoactivationEdge(
                count=data.get("count", 1),
                weight=data.get("weight", 1.0),
                last_updated=last_updated,
            )
