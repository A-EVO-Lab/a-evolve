---
name: auth-and-login
description: Authentication patterns across AppWorld apps
---

## Key techniques
- Get supervisor profile first: `apis.supervisor.show_profile()`
- Email = username for all apps
- Login pattern: `apis.<app>.login(username=email, password=password)`
- Password is typically the part before @ in email
- Access tokens are managed automatically after login

## Gotchas
- Must login to each app separately before using protected endpoints
- Login before any data access — unauthenticated calls will fail
- Some tasks need login to multiple apps (cross-app tasks)
