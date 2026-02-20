# verify_api_spec_before_use

**Scope:** 🎯 Tactical Patch

**Description:** CRITICAL: Always check API specifications before making the first call to any API endpoint, especially authentication endpoints. This prevents parameter name mismatches.

**When to use:** about to call any API for the first time, need to authenticate, need to login, starting a new task, before using any app API

## Instructions

MANDATORY PRE-CALL CHECKLIST:

1. BEFORE calling ANY API for the first time in a task:
   - Use get_all_apis_in_app() to list available APIs
   - Use get_api_description_for_app() to get the EXACT parameter names
   - Read the 'Parameters' section carefully

2. For authentication/login APIs specifically:
   - NEVER assume parameter names (don't guess 'email', 'phone_number', etc.)
   - The parameter is often 'username' but MUST be verified from spec
   - Check both required and optional parameters

3. Verification process:
   - Write down the exact parameter names from the API spec
   - Match your available data to these exact parameter names
   - Only then construct the API call

4. If you catch yourself about to call an API:
   - STOP and ask: 'Have I checked the spec for this specific API?'
   - If NO, check it first
   - If YES, proceed with confidence

This applies to ALL APIs, not just authentication. Checking specs takes 1 step but saves 3+ steps of error recovery.

---
*Created: 2026-02-19T01:50:24.309311*
*Version: 1*
*Scope: tactical_patch*
*TTL: 150 episodes*
