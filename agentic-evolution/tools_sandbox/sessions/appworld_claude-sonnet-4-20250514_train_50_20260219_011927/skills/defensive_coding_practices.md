# defensive_coding_practices

**Scope:** 🎯 Tactical Patch

**Description:** Validates variables, responses, and data structures before use to prevent NameError, KeyError, and AttributeError exceptions

**When to use:** before using any variable, after receiving API response, before accessing nested data, when passing authentication parameters, to prevent NameError, to prevent KeyError, to prevent AttributeError

## Instructions

When writing code or accessing data:

1. **Variable Validation**: Before using a variable, verify it exists in your scope:
   - Check if you've defined the variable in previous steps
   - If unsure, use conditional checks: `if 'var_name' in locals()` or define it first
   - Example: Don't use `user_id` unless you've explicitly set it earlier

2. **Response Validation**: After API calls, validate the response structure:
   - Check if response is not None: `if response is None: handle_error()`
   - Verify expected keys exist: `if 'data' in response: ...`
   - Use `.get()` for dictionaries: `response.get('key', default_value)`
   - Example: `user_data = response.get('data', {})` instead of `user_data = response['data']`

3. **Nested Data Access**: When accessing nested structures:
   - Check each level exists before going deeper
   - Use chained `.get()`: `response.get('data', {}).get('user', {})`
   - Or validate: `if response and 'data' in response and 'user' in response['data']:`

4. **Type Assumptions**: Don't assume data types:
   - Check if list before iterating: `if isinstance(items, list):`
   - Check if dict before accessing keys: `if isinstance(obj, dict):`
   - Handle None values: `items = items or []`

5. **Authentication Parameters**: When passing credentials:
   - Verify you have the access token before using it
   - Check parameter types match API spec (string vs dict)
   - Example: If API expects string, pass `access_token` not `{'access_token': token}`

---
*Created: 2026-02-19T01:54:58.175003*
*Version: 1*
*Scope: tactical_patch*
*TTL: 150 episodes*
