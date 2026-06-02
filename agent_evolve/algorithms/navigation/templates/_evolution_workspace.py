"""Helpers for the structured-evolution evolver workspace.

The evolver workspace persists across evolution cycles and contains:
  task_board.md       — failure patterns + prioritized gaps
  research_log.jsonl  — one JSON record per tested approach
  architecture.md     — what was built and why
  insights.jsonl      — cross-cycle lessons
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

WORKSPACE_DIR = "evolver_workspace"  # sibling directory name
TASK_BOARD = "task_board.md"
RESEARCH_LOG = "research_log.jsonl"
ARCHITECTURE = "architecture.md"
INSIGHTS = "insights.jsonl"
STRATEGY_TREE = "strategy_tree.md"

REQUIRED_RESEARCH_FIELDS = {
    "cycle", "regime", "approach", "endpoint", "tested", "works",
    "latency_ms", "coverage", "does_not_cover", "complementary_to",
    "sample_output", "credential_needed", "credential_env", "error", "notes",
}
REQUIRED_TOOL_TEST_FIELDS = {
    "cycle", "regime", "approach", "tested", "works", "type",
}
MINIMAL_RESEARCH_FIELDS = {"cycle", "regime", "approach", "tested", "works"}


def get_evolver_workspace_path(solver_root: Path) -> Path:
    """Return the evolver workspace path as a sibling of the solver workspace."""
    return solver_root.parent / WORKSPACE_DIR


def _ws_path(evo_root: Path) -> Path:
    """Identity — evo_root IS the evolver workspace directory."""
    return evo_root


TESTS_DIR = "tests"


def init_evolution_workspace(evo_root: Path) -> None:
    """Create evolver workspace structure if missing.

    Layout:
      evolver_workspace/
        task_board.md          — failure patterns + prioritized gaps
        research_log.jsonl     — structured research records
        architecture.md        — what was built and why
        insights.jsonl         — cross-cycle lessons
        tests/                 — verification test scripts + logs
        evolution/             — observer data (created by harness)
    """
    ws = _ws_path(evo_root)
    ws.mkdir(parents=True, exist_ok=True)
    (ws / TESTS_DIR).mkdir(exist_ok=True)
    tb = ws / TASK_BOARD
    if not tb.exists():
        tb.write_text(
            "## Failure Patterns\n\n## Verified Capabilities\n\n"
            "## Unresolved\n\n## Human Requests\n"
        )
    arch = ws / ARCHITECTURE
    if not arch.exists():
        arch.write_text("# Architecture\n\nNo pipelines built yet.\n")
    for jsonl in (RESEARCH_LOG, INSIGHTS):
        p = ws / jsonl
        if not p.exists():
            p.touch()


TASK_BOARD_REQUIRED_SECTIONS = {
    "## Failure Patterns",
    "## Verified Capabilities",
    "## Unresolved",
    "## Human Requests",
}

IGNORED_GAP_LABELS = {
    "successes", "failures", "diagnosis", "summary", "analysis",
    "verified", "unresolved", "human", "failure", "none", "no",
    "the", "and", "for", "from", "patterns", "capabilities",
    "requests", "cycle",
}


_PRIORITY_BULLET_RE = re.compile(
    r"[-*]\s*\w[\w_]*:\s*\d+.*PRIORITY:\s*(HIGH|MEDIUM|LOW)"
    r"(\s*→\s*TARGET:\s*(main|branch/[\w-]+))?",
    re.IGNORECASE,
)
_MALFORMED_PRIORITY_RE = re.compile(
    r"[-*]\s*\w[\w_]*:.*PRIORITY:\s*(HIGH|MEDIUM|LOW)"
    r"(\s*→\s*TARGET:\s*(main|branch/[\w-]+))?",
    re.IGNORECASE,
)


def validate_task_board(content: str) -> bool:
    """Check that task board has required sections, no preamble before
    ## Failure Patterns, and ALL priority bullets use numeric format.

    The first non-empty line must be the ## Failure Patterns heading.
    """
    content_lower = content.lower()
    for section in TASK_BOARD_REQUIRED_SECTIONS:
        if section.lower() not in content_lower:
            return False
    # First non-empty line must be ## Failure Patterns heading
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if not re.match(r"^##\s+Failure Patterns(?:\s*\(.*\))?\s*$", stripped, re.IGNORECASE):
            return False
        break
    in_failure = False
    valid_count = 0
    for line in content.splitlines():
        stripped = line.strip().replace("`", "").replace("**", "")
        if re.match(r"^##\s+Failure Patterns(?:\s*\(.*\))?\s*$", stripped, re.IGNORECASE):
            in_failure = True
            continue
        if stripped.startswith("## "):
            in_failure = False
            continue
        if not in_failure:
            continue
        if _MALFORMED_PRIORITY_RE.match(stripped):
            if not _PRIORITY_BULLET_RE.match(stripped):
                return False
            valid_count += 1
    return valid_count > 0


def load_task_board(ws_root: Path) -> str:
    """Read task_board.md content."""
    p = _ws_path(ws_root) / TASK_BOARD
    if not p.exists():
        return ""
    return p.read_text()


def update_task_board(ws_root: Path, content: str) -> None:
    """Write updated task_board.md."""
    p = _ws_path(ws_root) / TASK_BOARD
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


def load_research_log(ws_root: Path) -> list[dict[str, Any]]:
    """Load all research records."""
    p = _ws_path(ws_root) / RESEARCH_LOG
    if not p.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            logger.warning("Skipping malformed research record: %s", line[:80])
    return records


def append_research(ws_root: Path, record: dict[str, Any]) -> None:
    """Validate and append a research record."""
    if not validate_research_record(record):
        raise ValueError(
            f"Research record missing required fields "
            f"{REQUIRED_RESEARCH_FIELDS - set(record.keys())}"
        )
    p = _ws_path(ws_root) / RESEARCH_LOG
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def validate_research_record(record: dict[str, Any]) -> bool:
    """Validate a research or tool_test record.

    tool_test records (type="tool_test") require: cycle, regime, approach,
    tested, works, type.
    Source-test records require all 15 planned fields.
    """
    if record.get("type") == "tool_test":
        return REQUIRED_TOOL_TEST_FIELDS.issubset(record.keys())
    return REQUIRED_RESEARCH_FIELDS.issubset(record.keys())


def is_legacy_record(record: dict[str, Any]) -> bool:
    """Check if a record has only the minimal 5 fields (legacy format)."""
    return (
        MINIMAL_RESEARCH_FIELDS.issubset(record.keys())
        and not REQUIRED_RESEARCH_FIELDS.issubset(record.keys())
        and record.get("type") != "tool_test"
    )


def get_verified_approaches(ws_root: Path) -> list[dict[str, Any]]:
    """Return source records with works=True and the full 15-field schema.

    Excludes tool_test records and legacy/minimal records that lack the
    fields the builder needs for fallback-chain decisions.
    """
    results = []
    for r in load_research_log(ws_root):
        if r.get("works") is not True:
            continue
        if r.get("type") == "tool_test":
            continue
        if not REQUIRED_RESEARCH_FIELDS.issubset(r.keys()):
            logger.debug("Skipping record missing full schema: %s", r.get("approach"))
            continue
        results.append(r)
    return results


def get_failed_approaches(ws_root: Path) -> list[dict[str, Any]]:
    """Return records with works=False (avoid retesting)."""
    return [r for r in load_research_log(ws_root) if r.get("works") is False]


def load_architecture(ws_root: Path) -> str:
    """Read architecture.md."""
    p = _ws_path(ws_root) / ARCHITECTURE
    if not p.exists():
        return ""
    return p.read_text()


def update_architecture(ws_root: Path, content: str) -> None:
    """Write updated architecture.md."""
    p = _ws_path(ws_root) / ARCHITECTURE
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


def append_insight(ws_root: Path, record: dict[str, Any]) -> None:
    """Append cross-cycle insight."""
    p = _ws_path(ws_root) / INSIGHTS
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def load_insights(ws_root: Path) -> list[dict[str, Any]]:
    """Load all insight records."""
    p = _ws_path(ws_root) / INSIGHTS
    if not p.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            logger.warning("Skipping malformed insight record: %s", line[:80])
    return records


# ── Strategy Tree (for structured_navigation) ──────────────────────


def load_strategy_tree(ws_root: Path) -> str:
    """Read strategy_tree.md content."""
    p = _ws_path(ws_root) / STRATEGY_TREE
    if not p.exists():
        return ""
    return p.read_text()


def _format_branch_stats(stats: dict[str, int]) -> str:
    """Format per-branch stats truthfully (pending vs revealed)."""
    routed = stats.get("routed", 0)
    revealed = stats.get("revealed", 0)
    passed = stats.get("passed", 0)
    if routed == 0:
        return "- Routed: 0 tasks"
    if revealed == 0:
        return f"- Routed: {routed} tasks (labels pending reveal)"
    pending = routed - revealed
    pct = f"{100 * passed / revealed:.0f}%"
    if pending:
        return f"- Routed: {routed} tasks, {passed}/{revealed} passed ({pct}, {pending} pending)"
    return f"- Routed: {routed} tasks, {passed}/{revealed} passed ({pct})"


def update_strategy_tree(
    ws_root: Path,
    tree: Any,
    routing_stats: dict[str, dict[str, int]],
    evo_number: int,
) -> None:
    """Write strategy_tree.md from StrategyTree + routing performance data.

    Args:
        tree: StrategyTree with .branches list
        routing_stats: {"main": {"routed": N, "revealed": M, "passed": K}, ...}
        evo_number: current cycle number
    """
    lines = [f"## Strategy Tree (Cycle {evo_number})", ""]

    # Main
    lines.append("### main")
    lines.append("General-purpose root strategy. Handles all tasks by default.")
    lines.append(_format_branch_stats(routing_stats.get("main", {})))
    lines.append("")

    # Branches
    for b in getattr(tree, "branches", []):
        if b.name == "main":
            continue
        lines.append(f"### {b.name}")
        lines.append(b.description or "(no description)")
        lines.append(f"- Created: cycle {b.created_at_cycle}")
        lines.append(_format_branch_stats(routing_stats.get(b.name, {})))
        lines.append("")

    p = _ws_path(ws_root) / STRATEGY_TREE
    p.write_text("\n".join(lines))


def parse_routing_stats(routing_log_path: Path | None) -> dict[str, dict[str, int]]:
    """Parse routing_log.jsonl into per-branch stats.

    Returns: {"main": {"routed": N, "revealed": M, "passed": K}, ...}
    Entries without 'success' field are pending (label not yet revealed).
    """
    stats: dict[str, dict[str, int]] = {}
    if not routing_log_path or not routing_log_path.exists():
        return stats
    for line in routing_log_path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        branch = rec.get("branch", "main") or "main"
        if branch not in stats:
            stats[branch] = {"routed": 0, "revealed": 0, "passed": 0}
        stats[branch]["routed"] += 1
        if "success" in rec:
            stats[branch]["revealed"] += 1
            if rec["success"]:
                stats[branch]["passed"] += 1
    return stats


# Regime/domain vocabulary the accretion scanner looks for. These are the
# words that, when they gate a rule in the SHARED harness, signal a latent
# branch ("if the task is regime X, do Y" === one harness can't be optimal
# for all tasks === L_adapt made visible).
_REGIME_TERMS = [
    "sport", "soccer", "football", "basketball", "tennis", "baseball",
    "hockey", "nfl", "nba", "mlb", "nhl", "ufc", "mma", "boxing",
    "esport", "esports", "lol", "dota", "cs2", "csgo", "valorant",
    "crypto", "bitcoin", "btc", "ethereum", "eth", "finance", "stock",
    "earnings", "fed", "macro", "politic", "election", "geopolit",
    "tweet", "mention", "culture", "entertainment", "music", "award",
    "climate", "weather", "fotmob", "espn", "moneyline", "handicap",
]


def scan_regime_accretion(
    workspace_root: Path | None,
    files: list[str] | None = None,
) -> dict[str, list[str]]:
    """Scan the SHARED (main) harness for regime-gated rules.

    A line in main's prompt/skills that conditions behaviour on a specific
    regime ("for ESPORTS …", "CRYPTO/FINANCE THRESHOLDS …", "Sports data
    sites like FotMob …") is a latent branch: a non-transferable rule that
    is dead weight or a hazard for other regimes. Counting these is the
    most direct branching signal — each clause is a candidate to extract
    into ``branch/<regime>``.

    Returns ``{regime_term: [matching lines]}`` aggregated across the
    scanned files. Only regime terms that actually appear are returned.
    """
    if not workspace_root:
        return {}
    root = Path(workspace_root)
    if files is None:
        files = ["prompts/system.md"]
        skills_dir = root / "skills"
        if skills_dir.is_dir():
            files += [f"skills/{p.name}" for p in sorted(skills_dir.glob("*.md"))]

    import re as _re
    hits: dict[str, list[str]] = {}
    term_re = _re.compile(
        r"\b(" + "|".join(_re.escape(t) for t in _REGIME_TERMS) + r")\b",
        _re.IGNORECASE,
    )
    for rel in files:
        fp = root / rel
        if not fp.is_file():
            continue
        try:
            text = fp.read_text()
        except Exception:
            continue
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            # Skip illustrative examples and table rows — these MENTION a
            # regime term but do not GATE behaviour on it (e.g. a
            # confidence-calibration example "I'm 97% sure BTC is below
            # $76K"). They inflate the count without signalling divergence.
            stripped = line.lstrip("-*> ").strip()
            if stripped[:1] in ("✅", "❌", "|", '"', "'"):
                continue
            m = term_re.search(line)
            if not m:
                continue
            term = m.group(1).lower()
            entry = f"{rel}: {line[:160]}"
            hits.setdefault(term, [])
            if entry not in hits[term]:
                hits[term].append(entry)
    return hits


def format_regime_accretion(hits: dict[str, list[str]]) -> str:
    """Render scan_regime_accretion output for the analyst prompt.

    Surfaces, in priority order: (1) whole regime-DEDICATED skill files
    (a file like ``skills/crypto_markets.md`` is the strongest latent-branch
    signal — an entire strategy that only applies to one regime), then
    (2) individual regime-gated rule lines grouped by term.
    """
    if not hits:
        return ("(no regime-gated rules detected in the shared harness — "
                "main is currently regime-agnostic)")

    # A skill file is "regime-dedicated" when most of its matching lines
    # come from one file whose name carries a regime term.
    file_terms: dict[str, set] = {}
    for term, entries in hits.items():
        for e in entries:
            fname = e.split(":", 1)[0].strip()
            file_terms.setdefault(fname, set()).add(term)
    dedicated = sorted(
        f for f in file_terms
        if f.startswith("skills/") and any(t in f.lower() for t in _REGIME_TERMS)
    )

    total = sum(len(v) for v in hits.values())
    lines = [
        f"The shared `main` harness already contains {total} regime-gated "
        f"rule(s) across {len(hits)} regime term(s). Each is a latent "
        f"branch — non-transferable content riding in the shared harness "
        f"(burden/contamination risk for other regimes):",
        "",
    ]
    if dedicated:
        lines.append("Regime-DEDICATED skill files (strongest branch candidates "
                     "— an entire strategy scoped to one regime):")
        for f in dedicated:
            lines.append(f"- `{f}`  → candidate `branch/{f.split('/')[-1].replace('_markets.md','').replace('.md','')}`")
        lines.append("")
    lines.append("Regime-gated rule lines:")
    for term in sorted(hits, key=lambda t: -len(hits[t])):
        lines.append(f"- **{term}** ({len(hits[term])}):")
        for ln in hits[term][:4]:
            lines.append(f"    {ln}")
    return "\n".join(lines)


def build_capacity_signal(
    workspace_root: Path | None,
    max_prompt_chars: int = 10_000,
) -> str:
    """Report `main`'s harness CAPACITY pressure — the active branch trigger.

    The shared harness has a FIXED budget (``max_prompt_chars`` for
    prompts/system.md, plus a soft skills budget). Every regime competes
    for it. When the strategy the evolver wants to write EXCEEDS the
    budget, ``cap_prompt_size`` truncates it — main literally drops
    strategy it can no longer hold. That truncation IS the adaptation gap
    (L_adapt) made concrete: one harness can no longer be optimal for all
    regimes at once. Branching buys back capacity — each branch spends the
    FULL budget on ONE regime.

    Surfaces three numbers the analyst cannot rationalise away:
      - system.md size vs budget (and whether it is being truncated)
      - per-skill-file sizes (the biggest are the most reclaimable)
      - the largest self-contained regime cluster = how much budget a
        branch extraction would free for the rest of main.
    """
    if not workspace_root:
        return "(no workspace to measure capacity)"
    root = Path(workspace_root)
    prompt = root / "prompts" / "system.md"
    if not prompt.exists():
        return "(no prompts/system.md to measure)"

    try:
        text = prompt.read_text()
    except Exception:
        return "(prompts/system.md unreadable)"
    n = len(text)
    pct = 100 * n / max_prompt_chars if max_prompt_chars else 0
    truncated = n >= max_prompt_chars

    lines = [
        f"system.md: {n}/{max_prompt_chars} chars ({pct:.0f}% of budget)"
        + ("  ⚠️ AT/OVER BUDGET — content is being TRUNCATED (strategy is "
           "being dropped from main every cycle). This is hard evidence that "
           "one shared harness can no longer hold all regimes' strategies: "
           "BRANCH to reclaim budget." if truncated
           else "  (budget headroom remaining)."),
    ]

    # Skill-file sizes — biggest are the most reclaimable by branching.
    skills_dir = root / "skills"
    if skills_dir.is_dir():
        sizes = []
        for p in sorted(skills_dir.glob("*.md")):
            try:
                sizes.append((p.name, len(p.read_text())))
            except Exception:
                continue
        if sizes:
            lines.append("")
            lines.append("Skill files (a regime-scoped file is fully "
                         "reclaimable by branching that regime):")
            for name, sz in sorted(sizes, key=lambda x: -x[1]):
                lines.append(f"- skills/{name}: {sz} chars")

    return "\n".join(lines)


def build_stratified_outcome_table(
    routing_log_path: Path | None,
    prop: str = "category",
    recent_cycles: int = 3,
) -> dict[str, Any]:
    """Stratify ALL routed outcomes by a task property, over FULL history.

    This is the broad branching signal: rather than sampling a few
    trajectories, it scans the entire routing log (every task, every
    cycle) and computes per-regime pass rates plus a recent-vs-prior
    drift, so the analyst can root-cause outcomes against data
    properties before deciding to branch.

    Reads the ``properties`` dict persisted on each routing entry
    (e.g. ``{"category": "Sports", "domain": "Sports,Culture",
    "year": "2026"}``).

    Returns a dict:
      {
        "prop": "category",
        "population": {"routed": N, "revealed": M, "passed": K},
        "regimes": {
            "Sports": {"routed", "revealed", "passed", "rate",
                       "recent_rate", "prior_rate", "drift", "branch"},
            ...
        },
        "n_cycles": <max cycle seen>,
      }
    ``rate``/``recent_rate``/``prior_rate`` are floats in [0,1] or None
    when no labels are revealed yet. ``drift`` = recent_rate - prior_rate
    (None if either is undefined). ``branch`` records which branch the
    regime's tasks were routed to (for spotting regime↔branch alignment).
    """
    empty = {"prop": prop, "population": {"routed": 0, "revealed": 0, "passed": 0},
             "regimes": {}, "n_cycles": 0}
    if not routing_log_path or not routing_log_path.exists():
        return empty

    records = []
    max_cycle = 0
    for line in routing_log_path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        records.append(rec)
        c = rec.get("cycle")
        if isinstance(c, int):
            max_cycle = max(max_cycle, c)

    if not records:
        return empty

    cutoff = max_cycle - recent_cycles + 1  # cycles >= cutoff are "recent"

    def _blank():
        return {"routed": 0, "revealed": 0, "passed": 0,
                "recent_revealed": 0, "recent_passed": 0,
                "prior_revealed": 0, "prior_passed": 0,
                "branches": {}}

    pop = {"routed": 0, "revealed": 0, "passed": 0}
    regimes: dict[str, dict[str, Any]] = {}
    for rec in records:
        props = rec.get("properties") or {}
        key = props.get(prop) or "unknown"
        agg = regimes.setdefault(key, _blank())
        revealed = "success" in rec
        passed = bool(rec.get("success"))
        cyc = rec.get("cycle")
        is_recent = isinstance(cyc, int) and cyc >= cutoff

        pop["routed"] += 1
        agg["routed"] += 1
        br = rec.get("branch", "main") or "main"
        agg["branches"][br] = agg["branches"].get(br, 0) + 1
        if revealed:
            pop["revealed"] += 1
            agg["revealed"] += 1
            if passed:
                pop["passed"] += 1
                agg["passed"] += 1
            if is_recent:
                agg["recent_revealed"] += 1
                agg["recent_passed"] += int(passed)
            else:
                agg["prior_revealed"] += 1
                agg["prior_passed"] += int(passed)

    def _rate(p, n):
        return (p / n) if n else None

    out_regimes: dict[str, Any] = {}
    for key, agg in regimes.items():
        recent_rate = _rate(agg["recent_passed"], agg["recent_revealed"])
        prior_rate = _rate(agg["prior_passed"], agg["prior_revealed"])
        drift = (recent_rate - prior_rate
                 if recent_rate is not None and prior_rate is not None else None)
        dominant_branch = (max(agg["branches"].items(), key=lambda kv: kv[1])[0]
                           if agg["branches"] else "main")
        out_regimes[key] = {
            "routed": agg["routed"],
            "revealed": agg["revealed"],
            "passed": agg["passed"],
            "rate": _rate(agg["passed"], agg["revealed"]),
            "recent_rate": recent_rate,
            "prior_rate": prior_rate,
            "drift": drift,
            "branch": dominant_branch,
        }
    return {"prop": prop, "population": pop, "regimes": out_regimes,
            "n_cycles": max_cycle}


def format_stratified_table(table: dict[str, Any]) -> str:
    """Render build_stratified_outcome_table output as a compact markdown
    table for the analyst prompt. Sorted by pass-rate gap vs population
    (worst-relative-to-mean regimes first — the branch candidates)."""
    pop = table.get("population", {})
    regimes = table.get("regimes", {})
    prop = table.get("prop", "property")
    if not regimes or pop.get("revealed", 0) == 0:
        return f"(no revealed outcomes to stratify by {prop} yet)"

    pop_rate = pop["passed"] / pop["revealed"]
    rows = []
    for name, s in regimes.items():
        if s["revealed"] == 0:
            continue
        gap = s["rate"] - pop_rate if s["rate"] is not None else 0.0
        rows.append((gap, name, s))
    rows.sort(key=lambda x: x[0])  # most-below-mean first

    def _pct(x):
        return f"{100*x:.0f}%" if x is not None else "—"

    def _signed(x):
        return f"{100*x:+.0f}pp" if x is not None else "—"

    lines = [
        f"Full-history outcomes stratified by `{prop}` "
        f"({table.get('n_cycles', 0)} cycles, population pass-rate "
        f"{_pct(pop_rate)} over {pop['revealed']} revealed):",
        "",
        f"| {prop} | routed | pass-rate | vs-mean | recent | prior | drift | routed-to |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for gap, name, s in rows:
        lines.append(
            f"| {name} | {s['routed']} | {_pct(s['rate'])} | {_signed(gap)} "
            f"| {_pct(s['recent_rate'])} | {_pct(s['prior_rate'])} "
            f"| {_signed(s['drift'])} | {s['branch']} |"
        )
    return "\n".join(lines)


# ── TARGET parsing for structured_navigation ────────────────────────

_TARGET_RE = re.compile(r"→\s*TARGET:\s*(main|branch/[\w-]+)", re.IGNORECASE)


def extract_targets_from_task_board(content: str) -> dict[str, list[str]]:
    """Parse task board bullets for TARGET annotations.

    Returns: {"main": ["regime_a", ...], "branch/X": ["regime_b", ...]}
    Regimes without explicit TARGET default to "main".
    """
    targets: dict[str, list[str]] = {}
    in_failure = False
    for line in content.splitlines():
        stripped = line.strip()
        if re.match(r"^##\s+Failure Patterns", stripped, re.IGNORECASE):
            in_failure = True
            continue
        if stripped.startswith("## "):
            in_failure = False
            continue
        if not in_failure:
            continue
        if not _PRIORITY_BULLET_RE.match(stripped.replace("`", "")):
            continue
        # Extract regime name
        regime_match = re.match(r"[-*]\s*(\w[\w_]*):", stripped)
        if not regime_match:
            continue
        regime = regime_match.group(1)
        # Extract target
        target_match = _TARGET_RE.search(stripped)
        target = target_match.group(1) if target_match else "main"
        targets.setdefault(target, []).append(regime)
    return targets
