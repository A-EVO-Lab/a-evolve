# error_recovery_workflow

**Scope:** 🎯 Tactical Patch

**Description:** Systematic approach to recover from runtime errors by analyzing the error, checking specifications, and adjusting the approach

**When to use:** after NameError, after KeyError, after TypeError, after AttributeError, when code execution fails

## Instructions

When you encounter a runtime error (NameError, KeyError, TypeError, etc.):

1. **Analyze the Error Message**:
   - Read the exact error type and message
   - Identify which variable/key/operation caused the error
   - Note the line/context where it occurred

2. **Check Your Previous Steps**:
   - Review your recent actions to see if you defined the variable
   - Verify you stored API responses correctly
   - Confirm you're using the right variable names

3. **Verify API Specifications** (if API-related):
   - Use verify_api_spec_before_use to check correct parameters
   - Confirm response structure matches your assumptions
   - Check authentication parameter format (string vs dict)

4. **Apply the Fix**:
   - Define missing variables before use
   - Use defensive access patterns (.get() for dicts)
   - Correct parameter types to match API spec
   - Add validation checks before accessing data

5. **Retry with Correction**:
   - Make the minimal change needed to fix the error
   - Don't change working parts of your approach
   - Continue with the corrected code

This recovery process should be fast (1-2 steps) since you're already on the right track.

---
*Created: 2026-02-19T01:54:58.213906*
*Version: 1*
*Scope: tactical_patch*
*TTL: 150 episodes*
