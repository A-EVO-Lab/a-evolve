"""SFR pipeline: end-to-end driver.

Stages:
  1. Locked rubric extraction (Phase A): per (skill, evolver), call Sonnet to
     extract a structured rubric from SKILL.md. Cached on disk.
  2. Per-cell judging (Phase B): blinded trajectory + locked rubric → 6-label
     verdict + violation timing + phase adherence. Cached per cell.
  3. Mechanical proxies (4a uptake + 4b constraint-violation detector).
  4. Aggregate statistics (paired delta, regression, reliability checks).

Resume capability: every Bedrock call's output is cached as JSON on disk; rerun
skips already-computed work.
"""
from __future__ import annotations
import argparse
import concurrent.futures
import json
import os
import re
import sys
import threading
from collections import defaultdict, Counter
from typing import Any

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ARTEFACTS = os.path.join(SCRIPT_DIR, "_artefacts")


def _find_evolverbench_root(start: str) -> str:
    """Walk up from `start` until `_region_picker.py` (EvolverBench root marker) is found."""
    d = start
    for _ in range(6):
        if os.path.exists(os.path.join(d, "_region_picker.py")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    raise RuntimeError(
        f"Could not locate EvolverBench root from {start} (no _region_picker.py within 6 parent levels)"
    )


EVOLVERBENCH_ROOT = _find_evolverbench_root(SCRIPT_DIR)

sys.path.insert(0, SCRIPT_DIR)
from bedrock_client import SonnetJudge  # noqa: E402
from matched_set import (  # noqa: E402
    build_matched_set,
    unique_cells_from_pairs,
    STRONG,
    MIDWEAK,
)


# -----------------------------------------------------------------------------
# Data loading
# -----------------------------------------------------------------------------

def sb_skill_body(solver: str, evolver: str, skill: str) -> str | None:
    """Return the SKILL.md content for a skill in a specific cell's workspace.

    Try task_skills first then skills."""
    ws = os.path.join(
        EVOLVERBENCH_ROOT, "results", "exp1_final_sb",
        f"{solver}_x_{evolver}_sb_s42", "workspace"
    )
    for sk_dir in ("task_skills", "skills"):
        p = os.path.join(ws, sk_dir, skill, "SKILL.md")
        if os.path.exists(p):
            with open(p) as f:
                return f.read()
    return None


def sb_trajectory(solver: str, evolver: str, task: str, cycle: int = 1) -> list[dict] | None:
    """Return the trajectory.json for a (solver, evolver, task, cycle) tuple."""
    cell = f"{solver}_x_{evolver}_sb_s42"
    p = os.path.join(
        EVOLVERBENCH_ROOT, "results", "exp1_final_sb", cell,
        "outputs", "official_like",
        f"{task}__{cell}-cycle-{cycle}", "agent", "trajectory.json"
    )
    if not os.path.exists(p):
        return None
    with open(p) as f:
        return json.load(f)


def cycle_with_skill(solver: str, evolver: str, task: str, skill: str) -> int | None:
    """Find a cycle for this task where the given skill was actually loaded."""
    cell = f"{solver}_x_{evolver}_sb_s42"
    results_path = os.path.join(
        EVOLVERBENCH_ROOT, "results", "exp1_final_sb", cell, "results.jsonl"
    )
    if not os.path.exists(results_path):
        return None

    def parse(line: str) -> dict | None:
        s = re.sub(r"\bFalse\b", "false", line.strip())
        s = re.sub(r"\bTrue\b", "true", s)
        s = re.sub(r"\bNone\b", "null", s)
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            return None

    with open(results_path) as f:
        for line in f:
            r = parse(line)
            if not r:
                continue
            if r.get("task_id") != task:
                continue
            sl = r.get("skills_loaded") or []
            if skill in sl and r.get("cycle") is not None:
                return int(r["cycle"])
    return None


# -----------------------------------------------------------------------------
# Blinding
# -----------------------------------------------------------------------------

SOLVER_ALIASES = {
    "opus46": "Claude Opus 4.6",
    "sonnet46": "Claude Sonnet 4.6",
    "haiku45": "Claude Haiku 4.5",
    "qwen235b": "Qwen3-235B",
    "qwen32b": "Qwen3-32B",
    "gptoss120b": "gpt-oss-120b",
    "minimax": "MiniMax",
    "kimi": "Kimi",
}


def blind_text(text: str) -> str:
    """Replace model names and obvious identity tokens with <MODEL>."""
    if not text:
        return text
    out = text
    # Replace model names (case-insensitive, longest first to avoid partial)
    aliases = sorted(SOLVER_ALIASES.values(), key=len, reverse=True)
    for alias in aliases:
        out = re.sub(re.escape(alias), "<MODEL>", out, flags=re.IGNORECASE)
    # Replace short names too
    for short in sorted(SOLVER_ALIASES.keys(), key=len, reverse=True):
        out = re.sub(rf"\b{re.escape(short)}\b", "<MODEL>", out, flags=re.IGNORECASE)
    # Generic Claude/Anthropic markers
    out = re.sub(r"\bClaude\b", "<MODEL>", out, flags=re.IGNORECASE)
    return out


def format_trajectory_for_judge(traj: list[dict], blind: bool = True) -> str:
    """Format SB trajectory.json into turn-by-turn text for the judge.

    Each turn becomes "Turn N: INPUT ... OUTPUT ...". Truncates each
    prompt/response to a soft cap to keep prompt size manageable while
    preserving load-bearing content.
    """
    lines = []
    PER_FIELD_CAP = 4000  # chars per turn-field
    for i, turn in enumerate(traj):
        prompt = turn.get("prompt", "")
        response = turn.get("response", "")
        if blind:
            prompt = blind_text(prompt)
            response = blind_text(response)
        if len(prompt) > PER_FIELD_CAP:
            prompt = prompt[:PER_FIELD_CAP] + "\n[... truncated ...]"
        if len(response) > PER_FIELD_CAP:
            response = response[:PER_FIELD_CAP] + "\n[... truncated ...]"
        lines.append(f"--- Turn {i} ---")
        lines.append(f"INPUT (from runner):\n{prompt}")
        lines.append(f"OUTPUT (agent response):\n{response}")
    return "\n\n".join(lines)


# -----------------------------------------------------------------------------
# Stage 1: rubric extraction (Phase A)
# -----------------------------------------------------------------------------

RUBRIC_SYSTEM = """You are auditing a procedural skill document used by an LLM agent. You will output a strict JSON rubric that captures the imperative procedural instructions of the skill, suitable for downstream automated adherence judging.

Output JSON only, no prose."""

RUBRIC_USER_TEMPLATE = """<SKILL_BODY>
{skill_body}
</SKILL_BODY>

Task:
1. Identify procedural instructions directly entailed by imperative or normative language in SKILL_BODY. Do NOT extract advice, rationale, examples, or motivational text as instructions.
2. For each instruction, provide:
   - id: stable identifier (e.g., "step_1")
   - source_span: EXACT quoted text from SKILL_BODY that grounds this instruction (must be a substring of SKILL_BODY, max 250 chars)
   - text: paraphrased instruction (1 sentence, imperative)
   - type: "required" (must execute) | "conditional" (must execute IF trigger occurs) | "optional"
   - trigger: for conditional only; describe the condition (e.g., "if pip install fails"). null otherwise.
   - success_criteria: 1-sentence test for FOLLOWED verdict
   - violation_criteria: 1-sentence test for VIOLATED verdict (commission or omission)

Aim for 3-8 instructions. Do not pad with low-salience items. Reject SKILL_BODY content that is purely descriptive or motivational.

Output JSON only:
{{
  "skill_id": "<skill folder name>",
  "instructions": [
    {{
      "id": "step_1",
      "source_span": "...",
      "text": "...",
      "type": "required|conditional|optional",
      "trigger": null,
      "success_criteria": "...",
      "violation_criteria": "..."
    }}
  ]
}}"""


def rubric_path(skill: str, evolver: str) -> str:
    return os.path.join(ARTEFACTS, "rubrics", f"{skill}__{evolver}.json")


def extract_rubric(skill: str, evolver: str, source_solver: str, judge: SonnetJudge, force: bool = False) -> dict | None:
    """Extract rubric for (skill, evolver) by reading skill body from
    source_solver's workspace. Cached."""
    out_path = rubric_path(skill, evolver)
    if os.path.exists(out_path) and not force:
        with open(out_path) as f:
            return json.load(f)
    body = sb_skill_body(source_solver, evolver, skill)
    if body is None:
        return None
    user = RUBRIC_USER_TEMPLATE.format(skill_body=body)
    try:
        resp = judge.judge(RUBRIC_SYSTEM, user, temperature=0.0, max_tokens=3500)
    except Exception as e:
        print(f"[rubric] FAIL {skill}/{evolver}: {e}", flush=True)
        return None
    # Inject skill_id
    resp.setdefault("skill_id", skill)
    # Validate source_spans by normalised substring match (tolerate
    # whitespace / markdown formatting differences). Per Codex r3 fix #2:
    # require either a 60-char prefix match for long spans, or a full
    # exact-substring match for short spans. Drop the lax 30-char fallback.
    def _norm(s: str) -> str:
        s = re.sub(r"[*_`]", "", s)
        s = re.sub(r"\s+", " ", s)
        return s.strip().lower()
    body_norm = _norm(body)
    valid_insts = []
    invalid = 0
    for inst in resp.get("instructions", []):
        span = inst.get("source_span", "")
        span_norm = _norm(span)
        if not span:
            invalid += 1
            continue
        ok = False
        if len(span_norm) >= 60:
            ok = span_norm[:60] in body_norm
        else:
            # Short spans must match the entire normalised span; 30-char fallback removed
            ok = span_norm in body_norm
        if ok:
            valid_insts.append(inst)
        else:
            invalid += 1
    resp["instructions"] = valid_insts
    resp["_meta"] = {
        "skill": skill,
        "evolver": evolver,
        "source_solver": source_solver,
        "invalid_spans_dropped": invalid,
        "body_chars": len(body),
    }
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(resp, f, indent=2)
    return resp


# -----------------------------------------------------------------------------
# Stage 2: per-cell judging (Phase B)
# -----------------------------------------------------------------------------

JUDGE_SYSTEM = """You are evaluating an LLM agent trajectory against a fixed procedural rubric. Apply the rubric exactly as given; do not add or remove instructions.

The trajectory is BLINDED: model identity has been replaced with <MODEL>. Score adherence based on observable actions only.

Output JSON only, no prose."""

JUDGE_USER_TEMPLATE = """<RUBRIC>
{rubric_json}
</RUBRIC>

<TRAJECTORY>
{trajectory_text}
</TRAJECTORY>

For each instruction in RUBRIC, classify the trajectory as one of:
- FOLLOWED: the trajectory explicitly satisfies success_criteria. Cite turn_idx and quote the action.
- VIOLATED_COMMISSION: the trajectory took an action that directly contradicts the instruction. Cite turn_idx and quote the action.
- VIOLATED_OMISSION: the instruction is required (or its conditional trigger occurred), the trajectory ran long enough to act on it, but did not. Cite the latest turn_idx by which the omission was clear.
- REQUIRED_BUT_UNOBSERVED: the instruction is required but the trajectory terminated too early to observe whether it would have been followed.
- NOT_APPLICABLE: conditional instruction whose trigger did not occur, OR optional instruction the agent chose not to take.
- INSUFFICIENT_EVIDENCE: trajectory is ambiguous; cannot determine.

Also identify violation timing (for any VIOLATED_COMMISSION or VIOLATED_OMISSION):
- violation_earliest_possible_turn: smallest turn_idx where the trajectory could have first violated this instruction
- violation_confirmed_turn: turn_idx where the violation became unambiguous
- violation_type: "commission" | "omission" | "premature_stop" | "wrong_strategy"

Phase classification: assign each turn to one phase ("skill_loaded" = turn 1; "first_action" = first action turn after; "midpoint" = middle 50%; "pre_final" = last 25% but not final; "final_validation" = final turn). Give per-phase adherence in [0,1].

Output JSON only:
{{
  "verdicts": [
    {{"instruction_id": "step_1", "verdict": "FOLLOWED|VIOLATED_COMMISSION|VIOLATED_OMISSION|REQUIRED_BUT_UNOBSERVED|NOT_APPLICABLE|INSUFFICIENT_EVIDENCE",
      "turn_idx": <int or null>,
      "evidence": "quoted action or omission description"}}
  ],
  "violations": [
    {{"instruction_id": "step_1", "violation_type": "commission|omission|premature_stop|wrong_strategy",
      "earliest_possible_turn": <int>,
      "confirmed_turn": <int>}}
  ],
  "phase_adherence": {{
    "skill_loaded": 0.0,
    "first_action": 0.0,
    "midpoint": 0.0,
    "pre_final": 0.0,
    "final_validation": 0.0
  }},
  "summary": "1-sentence neutral description"
}}"""


def judge_cell(cell: dict, rubric: dict, judge: SonnetJudge, force: bool = False) -> dict | None:
    out_dir = os.path.join(ARTEFACTS, "judgments")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{cell['cell_id']}.json")
    if os.path.exists(out_path) and not force:
        with open(out_path) as f:
            return json.load(f)
    # Resolve cycle
    cyc = cycle_with_skill(cell["solver"], cell["evolver"], cell["task"], cell["skill"])
    if cyc is None:
        # Skill loaded but we couldn't find which cycle — fallback to cycle 1
        cyc = 1
    traj = sb_trajectory(cell["solver"], cell["evolver"], cell["task"], cyc)
    if traj is None:
        print(f"[judge] no trajectory for {cell['cell_id']}", flush=True)
        return None
    traj_text = format_trajectory_for_judge(traj, blind=True)
    rubric_payload = {"skill_id": rubric.get("skill_id"), "instructions": rubric.get("instructions", [])}
    user = JUDGE_USER_TEMPLATE.format(
        rubric_json=json.dumps(rubric_payload, indent=2),
        trajectory_text=traj_text,
    )
    try:
        resp = judge.judge(JUDGE_SYSTEM, user, temperature=0.0, max_tokens=4096)
    except Exception as e:
        print(f"[judge] FAIL {cell['cell_id']}: {e}", flush=True)
        return None
    resp["_meta"] = {
        "cell_id": cell["cell_id"],
        "solver": cell["solver"],
        "evolver": cell["evolver"],
        "task": cell["task"],
        "skill": cell["skill"],
        "tier": cell["tier"],
        "passed": cell.get("passed"),
        "score": cell.get("score"),
        "cycle_used": cyc,
        "trajectory_turns": len(traj),
    }
    with open(out_path, "w") as f:
        json.dump(resp, f, indent=2)
    return resp


# -----------------------------------------------------------------------------
# Stage 4: mechanical proxies
# -----------------------------------------------------------------------------

FORBIDDEN_PATTERNS = [
    r"\bdo\s+not\b", r"\bdon't\b", r"\bnever\b", r"\bmust\s+not\b",
    r"\bavoid\b", r"\binstead\s+of\b",
]
ORDERING_PATTERNS = [
    r"\bbefore\b", r"\bafter\b", r"\bonly\s+after\b",
    r"\bprior\s+to\b", r"\bfirst\b", r"\bthen\b",
]


def _extract_identifiers(text: str) -> set[str]:
    """Extract tool / file / command identifiers from a text body."""
    out: set[str] = set()
    # Backticked code: `foo`, `foo.py`, `/path/to/file`
    out |= set(re.findall(r"`([\w./\-]+(?:\.[a-zA-Z]+)?)`", text))
    # Quoted paths
    out |= set(re.findall(r"\"(/[\w./\-]+)\"", text))
    return {o for o in out if len(o) > 2}


def compute_mechanical_proxies(cell: dict, rubric: dict) -> dict:
    """Compute mechanical proxies for a cell.

    Returns a dict per Stage 4 of methodology.
    """
    cyc = cycle_with_skill(cell["solver"], cell["evolver"], cell["task"], cell["skill"]) or 1
    traj = sb_trajectory(cell["solver"], cell["evolver"], cell["task"], cyc)
    body = sb_skill_body(cell["solver"], cell["evolver"], cell["skill"]) or ""
    if traj is None:
        return {"_meta": {"cell_id": cell["cell_id"]}, "error": "no_trajectory"}

    # Extract skill identifiers from source_spans of rubric + skill body
    skill_idents: set[str] = set()
    for inst in rubric.get("instructions", []):
        skill_idents |= _extract_identifiers(inst.get("source_span", ""))
    skill_idents |= _extract_identifiers(body[:3000])  # also from skill body

    # 4a positive uptake
    full_traj_text = "\n".join(t.get("prompt", "") + "\n" + t.get("response", "") for t in traj)
    full_traj_responses = "\n".join(t.get("response", "") for t in traj)
    n_idents_in_traj = sum(1 for ident in skill_idents if ident in full_traj_responses)
    overlap_rate = n_idents_in_traj / max(1, len(skill_idents))

    # retrieval_to_use_gap: tokens from turn 1 to first turn whose response contains any skill_ident
    cumulative_tokens = 0
    gap_tokens = -1
    gap_turns = -1
    for i, t in enumerate(traj):
        resp = t.get("response", "")
        prompt = t.get("prompt", "")
        if i >= 1:  # skill body delivered at turn 1
            if any(ident in resp for ident in skill_idents):
                gap_tokens = cumulative_tokens
                gap_turns = i - 1
                break
        cumulative_tokens += _approx_tokens(prompt) + _approx_tokens(resp)

    # ordered_milestone_completion: fraction of required ordered instructions touched
    required_insts = [i for i in rubric.get("instructions", []) if i.get("type") == "required"]
    touched = 0
    for inst in required_insts:
        inst_idents = _extract_identifiers(inst.get("source_span", ""))
        if any(ident in full_traj_responses for ident in inst_idents):
            touched += 1
    milestone_rate = touched / max(1, len(required_insts))

    # command_edit_distance — skipped (would need explicit prescribed sequence per skill); placeholder
    edit_distance = None

    # 4b constraint-violation detector.
    # Per Codex r3 fix #3: forbidden_identifier_mention is INFORMATIONAL ONLY
    # because mentioning a path/command in a prohibition rubric does not prove
    # the agent took the forbidden action. Verbose strong-tier responses
    # mention identifiers more often, which would bias the violation count.
    # The mechanical_violation_count below uses only the two unambiguous
    # signals (early termination + answer before validation).
    forbidden_identifier_mention = 0
    for inst in rubric.get("instructions", []):
        vc = inst.get("violation_criteria", "").lower()
        if any(re.search(p, vc) for p in FORBIDDEN_PATTERNS):
            inst_idents = _extract_identifiers(inst.get("source_span", ""))
            if any(ident in full_traj_responses for ident in inst_idents):
                forbidden_identifier_mention += 1

    # early_termination_with_required_unfinished
    last_turn_resp = traj[-1].get("response", "")
    is_task_complete = '"task_complete": true' in last_turn_resp.lower() or '"task_complete":true' in last_turn_resp.lower()
    untouched_required = len(required_insts) - touched
    early_termination = is_task_complete and untouched_required > 0

    # answer_before_validation: heuristic — final turn marks task_complete but rubric has a 'verify/validate' instruction not in trajectory
    validate_pending = False
    for inst in rubric.get("instructions", []):
        text = (inst.get("text", "") + " " + inst.get("success_criteria", "")).lower()
        if any(kw in text for kw in ("verify", "validate", "check the output")):
            inst_idents = _extract_identifiers(inst.get("source_span", ""))
            if not any(ident in full_traj_responses for ident in inst_idents):
                validate_pending = True
                break
    answer_before_validation = is_task_complete and validate_pending

    # Violation count uses only the two unambiguous mechanical signals.
    mechanical_violation_count = (1 if early_termination else 0) + (1 if answer_before_validation else 0)

    # Trajectory token length
    total_tokens = sum(_approx_tokens(t.get("prompt", "")) + _approx_tokens(t.get("response", "")) for t in traj)

    out = {
        "_meta": {
            "cell_id": cell["cell_id"],
            "solver": cell["solver"],
            "evolver": cell["evolver"],
            "task": cell["task"],
            "skill": cell["skill"],
            "tier": cell["tier"],
            "passed": cell.get("passed"),
            "trajectory_turns": len(traj),
        },
        "trajectory_total_tokens": total_tokens,
        "skill_idents_count": len(skill_idents),
        # 4a
        "tool_filename_overlap_rate": overlap_rate,
        "retrieval_to_use_gap_tokens": gap_tokens,
        "retrieval_to_use_gap_turns": gap_turns,
        "ordered_milestone_completion": milestone_rate,
        "command_edit_distance": edit_distance,
        # 4b
        "forbidden_identifier_mention": forbidden_identifier_mention,
        "early_termination_with_required_unfinished": int(early_termination),
        "answer_before_validation": int(answer_before_validation),
        "mechanical_violation_count": mechanical_violation_count,
        "mechanical_violation_flag": int(mechanical_violation_count > 0),
    }

    out_dir = os.path.join(ARTEFACTS, "mechanical")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, f"{cell['cell_id']}.json"), "w") as f:
        json.dump(out, f, indent=2)
    return out


def _approx_tokens(s: str) -> int:
    """Approximate token count (≈4 chars per token for English text)."""
    return max(1, len(s) // 4)


# -----------------------------------------------------------------------------
# Driver
# -----------------------------------------------------------------------------

def stage1_extract_rubrics(cells: list[dict], judge: SonnetJudge, max_workers: int = 4) -> dict[tuple[str, str], dict]:
    """For each unique (skill, evolver), extract rubric using a source solver."""
    # Group by (skill, evolver) → pick a source solver
    seen: dict[tuple[str, str], dict] = {}
    for c in cells:
        key = (c["skill"], c["evolver"])
        if key not in seen:
            seen[key] = c  # use first cell's solver as source

    print(f"[stage1] {len(seen)} unique (skill, evolver) rubrics to extract")
    rubrics: dict[tuple[str, str], dict] = {}

    def _one(item):
        key, src = item
        skill, evolver = key
        rub = extract_rubric(skill, evolver, src["solver"], judge)
        return key, rub

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = [ex.submit(_one, kv) for kv in seen.items()]
        done = 0
        for fut in concurrent.futures.as_completed(futures):
            try:
                key, rub = fut.result()
                if rub is not None:
                    rubrics[key] = rub
            except Exception as e:
                print(f"[stage1] worker error: {e}", flush=True)
            done += 1
            if done % 20 == 0:
                print(f"[stage1] {done}/{len(seen)} done", flush=True)
    print(f"[stage1] complete: {len(rubrics)} rubrics extracted")
    return rubrics


def stage2_judge_cells(cells: list[dict], rubrics: dict[tuple[str, str], dict], judge: SonnetJudge, max_workers: int = 4) -> dict[str, dict]:
    print(f"[stage2] judging {len(cells)} cells")
    judgments: dict[str, dict] = {}

    def _one(c):
        key = (c["skill"], c["evolver"])
        rub = rubrics.get(key)
        if rub is None:
            return c["cell_id"], None
        j = judge_cell(c, rub, judge)
        return c["cell_id"], j

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = [ex.submit(_one, c) for c in cells]
        done = 0
        for fut in concurrent.futures.as_completed(futures):
            try:
                cid, j = fut.result()
                if j is not None:
                    judgments[cid] = j
            except Exception as e:
                print(f"[stage2] worker error: {e}", flush=True)
            done += 1
            if done % 25 == 0:
                print(f"[stage2] {done}/{len(cells)} done", flush=True)
    print(f"[stage2] complete: {len(judgments)} judgments")
    return judgments


def stage4_mechanical(cells: list[dict], rubrics: dict[tuple[str, str], dict]) -> dict[str, dict]:
    print(f"[stage4] mechanical proxies on {len(cells)} cells")
    out: dict[str, dict] = {}
    for c in cells:
        key = (c["skill"], c["evolver"])
        rub = rubrics.get(key, {"instructions": []})
        m = compute_mechanical_proxies(c, rub)
        out[c["cell_id"]] = m
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=0, help="If >0, run pilot on first N cells.")
    p.add_argument("--max-workers", type=int, default=4)
    p.add_argument("--stages", default="1,2,4", help="Comma-separated stage ids to run: 1=rubric, 2=judge, 4=mechanical")
    args = p.parse_args()

    pairs = build_matched_set()
    cells = unique_cells_from_pairs(pairs)
    if args.limit > 0:
        cells = cells[: args.limit]
        print(f"[pilot] limiting to {len(cells)} cells")

    judge = SonnetJudge()
    print(f"[init] judge model_id={judge.model_id} region={judge.region}")

    stages = {int(s) for s in args.stages.split(",") if s.strip()}

    if 1 in stages:
        rubrics = stage1_extract_rubrics(cells, judge, max_workers=args.max_workers)
    else:
        # Load from cache
        rubrics = {}
        rdir = os.path.join(ARTEFACTS, "rubrics")
        if os.path.exists(rdir):
            for fname in os.listdir(rdir):
                if not fname.endswith(".json"): continue
                with open(os.path.join(rdir, fname)) as f:
                    r = json.load(f)
                meta = r.get("_meta", {})
                rubrics[(meta.get("skill"), meta.get("evolver"))] = r
        print(f"[stage1-cached] loaded {len(rubrics)} rubrics from disk")

    if 2 in stages:
        stage2_judge_cells(cells, rubrics, judge, max_workers=args.max_workers)

    if 4 in stages:
        stage4_mechanical(cells, rubrics)

    print("[done]")


if __name__ == "__main__":
    main()
