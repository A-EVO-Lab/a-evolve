# strict_completion_gate

**Scope:** 🎯 Tactical Patch

**Description:** Emergency behavioral patch: Never complete a task when verification shows failures

**When to use:** immediately before calling task_complete(), when verification shows any discrepancy, when counts don't match, when you observe missing items

## Instructions

CRITICAL RULE: Before you call task_complete(), ask yourself:

'Did my verification show that EVERYTHING the task asked for was successfully accomplished?'

If the answer is anything other than 'YES, with evidence':
- DO NOT call task_complete()
- Call task_incomplete() instead
- Provide specific details about what failed or is missing

RED FLAGS that mean you MUST mark incomplete:
🚩 'Some songs are missing from the library'
🚩 'Only X out of Y items were processed'
🚩 'The count doesn't match what was requested'
🚩 'There was an error but I continued anyway'
🚩 'I'm not sure why this discrepancy exists'
🚩 'The API returned success but verification failed'

GREEN LIGHTS for completion:
✅ Every requested item is present and verified
✅ All counts match exactly
✅ No errors or warnings occurred
✅ All implicit requirements are met
✅ You have concrete evidence for each requirement

When in doubt: Mark incomplete. It's better to be conservative than to claim success incorrectly.

---
*Created: 2026-02-19T02:57:31.022132*
*Version: 1*
*Scope: tactical_patch*
*TTL: 150 episodes*
