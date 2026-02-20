# pass_access_token_to_api_calls

**Scope:** 📚 Strategic Skill

**Description:** Ensure access_token is explicitly passed to every authenticated API call in AppWorld

**When to use:** before making any authenticated API call, after login when calling other endpoints, when receiving 401 Unauthorized error

## Instructions

For EVERY API call after login (except the login call itself):

1. Confirm you have the access_token variable from login response

2. Determine the HTTP method:
   - GET requests: Append token to URL query string
   - POST/PUT/PATCH requests: Include token in request body
   - DELETE requests: Append token to URL query string

3. Format the token parameter:
   - GET/DELETE: Add ?access_token={token} or &access_token={token} to URL
   - POST/PUT: Add 'access_token': token to the JSON body

4. Examples:
   - GET: call_api('GET', f'/api/payments?user_id=123&access_token={access_token}')
   - POST: call_api('POST', '/api/transfer', {'from': 'A', 'to': 'B', 'access_token': access_token})
   - DELETE: call_api('DELETE', f'/api/item/456?access_token={access_token}')

5. Common mistakes to AVOID:
   - Assuming automatic session handling (AppWorld doesn't do this)
   - Using Authorization header (AppWorld uses parameters)
   - Forgetting token on subsequent calls after login
   - Using wrong parameter name (must be 'access_token')

6. If you get 401 Unauthorized:
   - First check: Did I pass access_token parameter?
   - Second check: Is the parameter name exactly 'access_token'?
   - Third check: Is the token value correct from login response?

---
*Created: 2026-02-19T01:45:06.877910*
*Version: 1*
*Scope: strategic_skill*
