# verify_authentication_immediately

**Scope:** 🎯 Tactical Patch

**Description:** Immediately verify authentication works after login by making a test API call with explicit token passing

**When to use:** after login API call succeeds, after receiving access_token, before making first authenticated API call

## Instructions

WHEN: Immediately after ANY successful login API call

WHAT: Verify the access_token works before proceeding with main task

HOW:
1. Extract access_token from login response
2. Identify a simple GET endpoint for the app (e.g., /api/user, /api/profile, /api/status)
3. Make test call with EXPLICIT token parameter:
   - GET: call_api('GET', f'/api/endpoint?access_token={access_token}')
   - Verify response is NOT 401 Unauthorized
4. If test fails (401 error):
   - DO NOT proceed with main task
   - Diagnose: Did you pass access_token as a parameter?
   - Retry with explicit token in URL or body
5. Only after successful verification, proceed with main task operations
6. Remember: EVERY subsequent API call needs access_token parameter

WHY: This catches authentication issues immediately rather than discovering them deep into task execution

---
*Created: 2026-02-19T01:45:06.854506*
*Version: 1*
*Scope: tactical_patch*
*TTL: 150 episodes*
