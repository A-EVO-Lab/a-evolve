# investigate_state_discrepancies

**Scope:** 📚 Strategic Skill

**Description:** When API operations succeed but state verification shows discrepancies (missing items, unexpected items, count mismatches), investigate the root cause before proceeding

**When to use:** verification shows fewer items than expected, API returns success but state doesn't match, count mismatch between operation and verification, items missing from library after addition, unexpected items appear in results

## Instructions

Step 1: When you observe a discrepancy between expected and actual state (e.g., you added 5 items but only 3 appear), DO NOT immediately accept this as final.

Step 2: Investigate potential causes:
- Check if the API has eventual consistency (items may appear after delay)
- Look for API response messages indicating partial success or warnings
- Check if there are implicit constraints (duplicates rejected, limits exceeded)
- Verify your query/filter parameters are correct
- Check for pagination issues in verification

Step 3: Take corrective action:
- If eventual consistency: Wait 2-3 seconds and re-verify
- If duplicates/constraints: Document which items were rejected and why
- If pagination: Ensure you're checking ALL pages, not just first page
- If API error: Retry the failed operations

Step 4: Only proceed to completion when:
- The discrepancy is fully explained AND documented, OR
- You've taken all reasonable corrective actions

Step 5: If discrepancy remains unexplained after investigation, mark the task as INCOMPLETE with detailed explanation of what failed.

---
*Created: 2026-02-19T02:57:30.940086*
*Version: 1*
*Scope: strategic_skill*
