# handle_authentication_flow

**Description:** 

**When to use:** N/A

## Instructions

AUTHENTICATION FLOW (SPEC-FIRST APPROACH):

**STEP 0 - MANDATORY SPECIFICATION CHECK:**
- ALWAYS start by calling get_all_apis_in_app(app_name) to see available APIs
- ALWAYS call get_api_description_for_app(app_name, api_name) for the login/auth API
- Identify the EXACT parameter names from the spec (usually 'username' and 'password')
- Note: Do NOT assume 'email' or 'phone_number' - verify the actual parameter name

**STEP 1 - Gather credentials:**
- Use get_user_info() to retrieve user details
- Match the user data fields to the API's required parameter names
- Common mapping: user's email/phone → API's 'username' parameter

**STEP 2 - Attempt authentication:**
- Call the login/authenticate API with EXACT parameter names from spec
- Store any returned tokens/session data
- Verify authentication succeeded (check response for success indicators)

**STEP 3 - Handle failures:**
- If 401/403: Verify credentials are correct, check if account exists
- If 400: Double-check parameter names match the spec exactly
- If other errors: Read error message and adjust accordingly

**STEP 4 - Maintain session:**
- Use returned auth tokens in subsequent API calls
- Re-authenticate if you receive 401 errors later

KEY PRINCIPLE: Specification checking is NOT optional - it's the mandatory first step that prevents all parameter-related errors.

---
*Updated: 2026-02-19T01:50:24.360657*
*Version: 4*
