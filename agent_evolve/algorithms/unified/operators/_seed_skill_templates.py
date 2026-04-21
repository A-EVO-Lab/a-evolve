"""Seed skill body templates used by :class:`AutoSeedSkills`.

Reference: ``agent_evolve/algorithms/adaptive_evolve/prompts.py`` lines
362-589 (``build_multi_req_skill`` / ``build_entity_verification_skill`` /
``build_claim_type_skill``). Content reproduced verbatim so
``UnifiedEngine`` output is byte-equivalent to the legacy engine under
mocked LLM tests (AC-8). No ``import`` from the legacy module — this is a
physical copy per DEC-2.
"""

from __future__ import annotations


MULTI_REQ_SKILL = """\
---
name: multi-requirement-handler
description: Handle tasks with multiple requirements systematically
triggers: ["and", "also", "additionally"]
---

# Multi-Requirement Task Handler

## Detection

Task has multiple requirements if it contains:
- " and " (e.g., "Get X and also Y")
- " also " (e.g., "Return A, also B")
- " additionally " (e.g., "Find X. Additionally, ...")
- Bullet points or numbered lists

## Protocol

### STEP 1: Extract ALL Requirements (BEFORE any tool calls)

Read task carefully. Write numbered list:

```
Requirements:
1. [First requirement]
2. [Second requirement]
3. [Third requirement if present]
...
```

### STEP 2: Plan Tool Calls Per Requirement

```
Requirement 1 needs: [list tools]
Requirement 2 needs: [list tools]
...
```

### STEP 3: Execute IN ORDER, Mark Completion

After completing requirement 1:
- ✓ Requirement 1: [brief result]
- Remaining: 2, 3, ...

After completing requirement 2:
- ✓ Requirement 1: [result]
- ✓ Requirement 2: [result]
- Remaining: 3, ...

### STEP 4: Final Verification (BEFORE answering)

For EACH requirement:
- "Do I have SPECIFIC data for this?" (not just "looked for it")
- If ANY = "No", make more tool calls NOW

### STEP 5: Structure Answer

Address each requirement explicitly:

```
1. [Requirement 1]: [specific answer with exact values]
2. [Requirement 2]: [specific answer with exact values]
...
```

## Common Pitfall

❌ Spending 80% effort on requirement 1, then rushing/skipping requirement 2
✓ Budget effort EVENLY: 1/N effort per requirement
"""


ENTITY_VERIFICATION_SKILL = """\
---
name: entity-verification
description: Verify working with correct entity before proceeding
triggers: ["quoted names", "specific IDs", "dates"]
---

# Entity Verification Protocol

## When Task Mentions Specific Entity

If task includes ANY of:
- Quoted names: "TensorFlow", "AssaultCube"
- Specific IDs: "Task ID: 12345", "Issue #42"
- Exact dates: "May 9, 2007"
- Unique identifiers: "repository tensorflow/tensorflow"

## Mandatory Checkpoint (After FIRST Tool Call)

**STOP and verify**:

1. Does the returned data include the EXACT identifier from the task?
   - Task said "TensorFlow" → response has "TensorFlow"? ✓/✗
   - Task said "May 9, 2007" → response has this date? ✓/✗
   - Task said "repository X/Y" → response has owner=X, name=Y? ✓/✗

2. **If identifier NOT found**:
   - Your tool call retrieved the WRONG entity
   - STOP immediately, do NOT proceed
   - Try more specific query with exact identifier
   - Use different tool/endpoint if needed

3. **Never proceed with wrong entity**
   - Wrong entity = 0.0 score (complete failure)
   - Better to try 3 different tools than proceed with wrong data

## Examples

### ❌ Wrong Approach
```
Task: "Get creation date of repository tensorflow/tensorflow"
Action: search_repos("tensorflow") → picks first result
Problem: Could be wrong repository (many contain "tensorflow")
```

### ✓ Correct Approach
```
Task: "Get creation date of repository tensorflow/tensorflow"
Action: get_repo(owner="tensorflow", name="tensorflow") → exact match
Verify: Response has owner="tensorflow" AND name="tensorflow"? ✓
Continue: Safe to extract creation date
```

## Critical Rule

**After first tool call, always ask**: "Does this data match the task's EXACT entity?"

If NO → PIVOT immediately, don't continue with wrong data.
"""


_CLAIM_DESCRIPTIONS = {
    "calculate": "Handle calculation and numerical difference requirements",
    "compare": "Handle comparison between two or more entities",
    "aggregate": "Handle requirements for totals, lists, and all items",
    "provide_fact": "Handle fact retrieval and information provision",
    "identify_entity": "Handle entity identification and finding",
    "entity_property": "Handle extraction of entity properties and attributes",
}

_CALCULATE_GUIDANCE = """
1. **Don't just mention the numbers** - perform the actual calculation
2. **State the operation explicitly**: "difference", "sum", "ratio"
3. **Show the result clearly**: "X - Y = Z" or "The difference is Z"
4. **Include units** if applicable (years, dollars, items, etc.)

Example:
- ❌ "The repo was created in 2013 and domain in 2007"
- ✓ "The repo was created in 2013, domain in 2007. Difference: 2013 - 2007 = 6 years"
"""

_COMPARE_GUIDANCE = """
1. **Fetch BOTH entities explicitly** - don't skip one
2. **Present side-by-side**: "Entity A: [properties] vs Entity B: [properties]"
3. **State the comparison result**: "A is larger/older/different than B"
4. **Include specific values**: Not just "different" but "A=10, B=20, difference=10"
"""

_AGGREGATE_GUIDANCE = """
1. **Don't stop at first page** - handle pagination to get ALL items
2. **State the total count**: "Found 47 items total"
3. **If listing all, actually list them** (not just "there are many")
4. **For totals/sums, show the calculation**: "10 + 20 + 30 = 60"
"""

_GENERIC_GUIDANCE = """
1. **Retrieve the specific information** required by the claim
2. **Present it explicitly** in your answer (don't assume it's implied)
3. **Use exact values** from tool responses (copy, don't paraphrase)
4. **Verify you answered the EXACT question** asked
"""

_VERIFICATION_CHECKLIST = """
## Verification Checklist

Before answering, verify:
- [ ] Did I retrieve the necessary data?
- [ ] Did I perform any required calculations/comparisons?
- [ ] Is the result EXPLICITLY stated in my answer?
- [ ] Are exact values included (not just descriptions)?
"""


def build_claim_type_skill(claim_type: str, examples: list[dict]) -> str:
    """Build a targeted skill body for the given claim type."""
    desc = _CLAIM_DESCRIPTIONS.get(
        claim_type, f"Handle {claim_type} type requirements"
    )
    content = (
        "---\n"
        f"name: {claim_type}-handler\n"
        f"description: {desc}\n"
        "---\n\n"
        f"# {claim_type.replace('_', ' ').title()} Handler\n\n"
        "## Common Failures for This Claim Type\n\n"
        "Based on recent failures, this claim type often fails when:\n"
    )
    for ex in (examples or [])[:2]:
        content += (
            f"- \"{ex.get('claim', '')}\" - {ex.get('justification', '')}\n"
        )
    content += (
        "\n"
        f"## Guidelines for {claim_type.replace('_', ' ').title()} Claims\n\n"
    )
    if claim_type == "calculate":
        content += _CALCULATE_GUIDANCE
    elif claim_type == "compare":
        content += _COMPARE_GUIDANCE
    elif claim_type == "aggregate":
        content += _AGGREGATE_GUIDANCE
    else:
        content += _GENERIC_GUIDANCE
    content += _VERIFICATION_CHECKLIST
    return content
