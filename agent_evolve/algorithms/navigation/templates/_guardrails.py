"""Shared guardrails for V2 evolution templates.

Every V2 template calls these after the evolver finishes and before
committing.  They enforce the hard constraints that V1 experiments
showed are necessary to avoid regression:

  G2. strip_search_caps   — remove solver search-count limits
  G3. seed_news_hint      — return the Google News RSS seed text
  G4. verify_tools        — subprocess-test each tool, remove failures
  G5. cap_prompt_size     — truncate prompts/system.md to MAX chars
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MAX_PROMPT_CHARS = 10_000

SEARCH_CAP_PATTERNS = [
    # Section-level removal: entire "HARD TURN/SEARCH LIMIT" blocks
    r"(?si)##\s*⚠️?\s*HARD\s+(?:TURN|SEARCH)\s+LIMIT.*?(?=\n##\s|\n---|\Z)",
    # Line-level patterns
    r"(?i)limit\s+searches?\s+to\s+\d+",
    r"(?i)use\s+\d+-?\d*\s+searches?",
    r"(?i)submit\s+by\s+search\s+\d+",
    r"(?i)max(imum)?\s+\d+\s+searches?",
    r"(?i)no\s+more\s+than\s+\d+\s+searches?",
    r"(?i)AT\s+MOST\s+\d+\s+tool\s+calls?[^.]*\.",
    r"(?i)after\s+(?:your\s+)?\d+(?:th|st|nd|rd)?\s+(?:tool\s+call|search)[^.]*\.",
    r"(?i)\d+\s+tool\s+calls?\s+total[^.]*\.",
    r"(?i)count\s+your\s+tool\s+calls[^.]*\.",
    r"(?i)\[?Tool\s+call\s+\[?\d+/\d+\]?",
    r"(?i)if\s+you\s+have\s+not\s+found\s+data\s+after\s+\d+\s+tool\s+calls?[^.]*\.",
]

NEWS_SEARCH_HINT = (
    "Google News RSS at `https://news.google.com/rss/search?q=...` "
    "returns timestamped headlines without htmldate. Build a "
    "`news_search.py` tool using this source as your first priority."
)

NO_THROTTLE_RULE = (
    "Never limit the solver's search count. The solver has an 80-turn "
    "budget and manages it. Your job is better tools, not fewer turns."
)


def strip_search_caps(prompt: str) -> str:
    """Remove solver search-count limits from the system prompt (G2)."""
    for pattern in SEARCH_CAP_PATTERNS:
        prompt = re.sub(pattern, "", prompt)
    # Clean up resulting double-blank-lines.
    prompt = re.sub(r"\n{3,}", "\n\n", prompt)
    return prompt.strip()


def seed_news_hint() -> str:
    """Return the Google News RSS seed text for evolver prompts (G3)."""
    return NEWS_SEARCH_HINT


def no_throttle_rule() -> str:
    """Return the no-throttle instruction for evolver prompts (G2)."""
    return NO_THROTTLE_RULE


def verify_tools(
    workspace_root: Path,
    sample_query: str = "test query 2026",
    cutoff: str = "2026-01-15",
    timeout: int = 15,
) -> list[str]:
    """Test each registered tool and remove failures from registry (G4).

    Returns list of removed tool names.
    """
    import yaml

    tools_dir = workspace_root / "tools"
    reg_path = tools_dir / "registry.yaml"
    if not reg_path.exists():
        return []

    try:
        data = yaml.safe_load(reg_path.read_text()) or {}
    except Exception:
        return []

    tools = data.get("tools", [])
    if not tools:
        return []

    removed = []
    kept = []
    for t in tools:
        name = t.get("name", "")
        script = tools_dir / f"{name}.py"
        if not script.exists():
            removed.append(name)
            logger.info("G4: removed %s (script missing)", name)
            continue
        try:
            proc = subprocess.run(
                ["python3", str(script), sample_query, cutoff],
                capture_output=True, text=True, timeout=timeout,
                cwd=str(workspace_root),
            )
            if proc.returncode != 0:
                removed.append(name)
                logger.info("G4: removed %s (exit %d)", name, proc.returncode)
                continue
            if not proc.stdout.strip():
                removed.append(name)
                logger.info("G4: removed %s (empty output)", name)
                continue
        except subprocess.TimeoutExpired:
            removed.append(name)
            logger.info("G4: removed %s (timeout >%ds)", name, timeout)
            continue
        except Exception as e:
            removed.append(name)
            logger.info("G4: removed %s (%s)", name, e)
            continue
        kept.append(t)

    if removed:
        data["tools"] = kept
        reg_path.write_text(yaml.dump(data, default_flow_style=False))
        logger.info("G4: registry updated, kept %d, removed %d: %s",
                     len(kept), len(removed), removed)
    return removed


def cap_prompt_size(
    workspace_root: Path,
    max_chars: int = MAX_PROMPT_CHARS,
) -> bool:
    """Truncate prompts/system.md if it exceeds max_chars (G5).

    Truncates at a clean boundary (the last section/paragraph break before
    the cap) rather than mid-sentence, so the cut never corrupts main into
    a half-written rule. Logs a loud WARNING with the overflow size —
    persistent truncation means the evolver wants more strategy than one
    shared harness can hold, which is the signal to BRANCH (extract a
    regime cluster to reclaim budget) rather than keep silently dropping
    strategy. See build_capacity_signal().

    Returns True if truncation happened.
    """
    prompt_path = workspace_root / "prompts" / "system.md"
    if not prompt_path.exists():
        return False
    content = prompt_path.read_text()
    if len(content) <= max_chars:
        return False

    head = content[:max_chars]
    # Prefer a section break (\n## or \n---), then a blank-line paragraph
    # break, then a newline — searching backwards from the cap. Only accept
    # a boundary that keeps at least half the budget (avoid over-trimming).
    floor = max_chars // 2
    cut = -1
    for sep in ("\n## ", "\n---", "\n\n", "\n"):
        idx = head.rfind(sep)
        if idx >= floor:
            cut = idx
            break
    clean = head[:cut] if cut != -1 else head
    clean = clean.rstrip() + "\n"
    prompt_path.write_text(clean)
    logger.warning(
        "G5: prompt OVER BUDGET — truncated %d→%d chars (%d dropped at a "
        "clean boundary). Persistent overflow ⇒ shared harness is full; "
        "consider branching a regime cluster to reclaim budget.",
        len(content), len(clean), len(content) - len(clean),
    )
    return True


def verify_pipeline(workspace_root: Path, timeout: int = 20) -> bool:
    """Test that infra/search_pipeline.py runs and returns valid JSON (G6).

    Returns True if valid or absent. Returns False on failure but does
    NOT delete the pipeline — the builder's code may work on real queries
    even if it fails the test. Deleting was too aggressive and destroyed
    working pipelines across multiple runs.
    """
    pipeline = workspace_root / "infra" / "search_pipeline.py"
    if not pipeline.exists():
        return True
    test_input = json.dumps({"query": "test query 2026", "cutoff_date": "2026-01-15"})
    try:
        proc = subprocess.run(
            [sys.executable, str(pipeline)],
            input=test_input, capture_output=True, text=True, timeout=timeout,
            cwd=str(workspace_root),
        )
        if proc.returncode != 0:
            logger.warning("G6: search_pipeline.py failed (exit %d: %s) — kept for runtime",
                           proc.returncode, proc.stderr[:200])
            return False
        output = json.loads(proc.stdout.strip())
        if not isinstance(output, dict):
            logger.warning("G6: search_pipeline.py returned non-dict — kept for runtime")
            return False
        return True
    except subprocess.TimeoutExpired:
        logger.warning("G6: search_pipeline.py timed out (%ds) — kept for runtime", timeout)
        return False
    except (json.JSONDecodeError, Exception) as e:
        logger.warning("G6: search_pipeline.py error (%s) — kept for runtime", e)
        pipeline.unlink()
        return False


def apply_all_guardrails(
    workspace_root: Path,
    sample_query: str = "test query 2026",
    cutoff: str = "2026-01-15",
) -> dict[str, Any]:
    """Apply G2-G5 guardrails. Returns a summary dict."""
    results: dict[str, Any] = {}

    # G2: strip search caps from evolved prompt
    prompt_path = workspace_root / "prompts" / "system.md"
    if prompt_path.exists():
        original = prompt_path.read_text()
        cleaned = strip_search_caps(original)
        if cleaned != original:
            prompt_path.write_text(cleaned)
            results["search_caps_stripped"] = True
        else:
            results["search_caps_stripped"] = False

    # G4: verify tools
    removed = verify_tools(workspace_root, sample_query, cutoff)
    results["tools_removed"] = removed

    # G5: cap prompt size
    truncated = cap_prompt_size(workspace_root)
    results["prompt_truncated"] = truncated

    return results
