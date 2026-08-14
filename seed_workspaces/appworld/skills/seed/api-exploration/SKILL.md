---
name: api-exploration
description: How to discover and read API docs in AppWorld
---

## Key techniques
- `apis.api_docs.show_api_descriptions(app_name="venmo")` — list all endpoints
- `apis.api_docs.show_api_doc(app_name="venmo", api_name="login")` — detailed docs
- Always read docs before calling any endpoint
- Check parameter names, types, and required fields carefully

## Gotchas
- API names are snake_case (e.g. `get_balance`, `send_money`)
- Some endpoints require specific parameter formats (dates, IDs)
- Read the full doc — optional params often needed for complete solutions
