# pre_completion_audit

**Description:** 

**When to use:** N/A

## Instructions

Before marking ANY task complete, perform this mandatory audit:

Step 1: STATE VERIFICATION (MANDATORY)
- Re-verify the final state matches ALL task requirements
- Check counts/quantities match exactly what was requested
- Verify all items/entities mentioned in task are present
- Confirm no unexpected items or side effects occurred

Step 2: HARD STOP RULES - DO NOT complete task if:
- ❌ Verification shows missing items/data that should be present
- ❌ Counts don't match (requested 5, got 3)
- ❌ Any operation reported an error or warning
- ❌ You have unexplained discrepancies in state
- ❌ You skipped any part of the task requirements
- ❌ External factors (API errors, auth failures) prevented completion

Step 3: IMPLICIT REQUIREMENT CHECK
- Review task description for implicit requirements (ordering, selection criteria, formatting)
- Check if task mentions 'first', 'oldest', 'newest', 'sorted', 'filtered' - these are explicit requirements
- Verify any time-based, order-based, or selection-based requirements are met

Step 4: EVIDENCE COLLECTION
- Document specific evidence that EACH requirement was met
- For quantitative requirements: Show exact counts/numbers
- For qualitative requirements: Show examples proving compliance

Step 5: COMPLETION DECISION
- ✅ Complete ONLY if: All requirements met + All verifications passed + No hard stop conditions
- ❌ Mark incomplete if: ANY hard stop condition exists + Provide detailed explanation of what failed

Remember: It is BETTER to mark a task incomplete with clear explanation than to mark it complete when requirements aren't fully met.

---
*Updated: 2026-02-19T02:57:30.991548*
*Version: 2*
