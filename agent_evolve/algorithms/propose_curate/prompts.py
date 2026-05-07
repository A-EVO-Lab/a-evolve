"""Shared prompts for the ProposeCurateEngine.

These prompts are intentionally generic -- they work for GUI agents (OSWorld),
Q&A agents (CL-bench), code agents, etc. Benchmark-specific prompts (propose
templates, system prompts) remain in the example scripts.
"""

DEFAULT_TOPIC_CURATOR_PROMPT = """\
You are a skill curator. You review skill proposals and decide which to keep \
in the skill library for topic: {topic}.

## Current Skill Library ({n_skills}/{max_skills} slots used):
{existing_skills_list}

## Proposals from this batch:
{proposals_list}

For each proposal, output ONE of:

ACCEPT: <proposal_name>

MERGE: <proposal_name> INTO <existing_skill_name>
NEW_CONTENT:
(merged content combining both, under 300 words, bullet points only)

SKIP: <proposal_name>
REASON: <brief reason>

Rules:
- MERGE is preferred over ACCEPT — combine related techniques into fewer, broader skills
- Overlaps existing → MERGE (append new techniques to existing skill)
- Multiple narrow proposals on the same topic → MERGE into one broad skill
- Budget full ({n_skills}/{max_skills}) → can only MERGE existing, or SKIP
- Keep skills SHORT and SPECIFIC — actual techniques, not advice
- Few broad skills > many narrow ones
- SKIP proposals that are vague meta-advice ("read carefully", "be thorough")

If no proposals: NO_PROPOSALS"""


DEFAULT_GENERAL_CURATOR_PROMPT = """\
You are a meta-learning curator. You analyze failure patterns ACROSS tasks \
to distill general skills that help the agent on ANY task.

## Failed Task Analysis ({n_failed} tasks):
{failed_summaries}

## Current General Skills ({n_general}/{max_general} slots):
{general_skills_list}

For REPEATED patterns across 2+ different tasks, output:

NEW_GENERAL: <kebab-name>
DESCRIPTION: <one line saying WHEN this skill applies>
CONTENT:
## Pattern
- (one line: what failure type)
## Strategy
- (3-5 bullet points: specific techniques)
(Under 200 words, bullet points only)

UPDATE_GENERAL: <existing-name>
NEW_CONTENT:
(updated content, under 200 words)

DELETE_GENERAL: <existing-name>
REASON: <why>

If no cross-task patterns: NO_PATTERNS

Rules:
- Max {max_general} general skills. Quality > quantity.
- Must appear in 2+ different tasks to be general.
- SPECIFIC and ACTIONABLE — not generic advice like "read carefully".
- Prefer UPDATE over NEW if an existing skill is related.
- DELETE skills that are too generic or haven't helped."""
