# extract_implicit_requirements

**Scope:** 📚 Strategic Skill

**Description:** Extract both explicit and implicit requirements from task descriptions to ensure nothing is missed

**When to use:** at task start, before creating execution plan, when task description contains ordering words (first, last, oldest, newest), when task involves selection or filtering, when task has quantitative requirements, before marking task complete

## Instructions

Step 1: EXPLICIT REQUIREMENTS EXTRACTION
- Read task description carefully
- List all explicitly stated actions (e.g., 'add songs', 'create playlist', 'send payment')
- Note all quantitative requirements (counts, amounts, durations)
- Note all qualitative requirements (names, filters, criteria)

Step 2: IMPLICIT REQUIREMENTS DETECTION
Look for these common implicit requirements:
- ORDERING: Words like 'first', 'last', 'oldest', 'newest', 'sorted' imply specific ordering
- SELECTION: 'Choose', 'pick', 'select' may imply criteria not explicitly stated
- COMPLETENESS: 'All', 'every', 'each' require exhaustive operations
- PRECISION: Specific numbers (e.g., '3 songs') require exact matches, not approximations
- FORMATTING: Output format requirements may be implicit in context
- STATE PRESERVATION: Some tasks require not disturbing existing state
- TIMING: 'Recent', 'latest', 'current' imply time-based filtering

Step 3: REQUIREMENT AMBIGUITY RESOLUTION
- If a requirement is ambiguous, consider multiple interpretations
- Choose the interpretation that is most specific and testable
- Document your interpretation in your plan

Step 4: REQUIREMENT CHECKLIST CREATION
- Create a checklist of ALL requirements (explicit + implicit)
- For each requirement, define how you'll verify it's met
- Keep this checklist and verify each item before task completion

Step 5: EDGE CASE CONSIDERATION
- What if there are duplicates?
- What if there's insufficient data?
- What if ordering matters but isn't specified?
- Plan for these edge cases upfront

Example:
Task: 'Add the first 3 songs from album X to my library'
Explicit: Add songs, from album X, quantity = 3, destination = library
Implicit: 'first' means track order matters (tracks 1-3, not random 3), must verify exact tracks added

---
*Created: 2026-02-19T02:57:31.070053*
*Version: 1*
*Scope: strategic_skill*
