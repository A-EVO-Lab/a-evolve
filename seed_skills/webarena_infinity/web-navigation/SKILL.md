---
name: web-navigation
description: Core patterns for navigating web apps — menus, tabs, search, breadcrumbs
---

## Key techniques
- Always start by identifying the main navigation menu or sidebar
- Use search bars when available — faster than clicking through menus
- Check for breadcrumbs to understand current location in the app
- Tab/panel switching: look for tab bars, sidebar items, or top navigation links
- URL patterns often reveal app structure (e.g. `/settings/account`, `/inbox/compose`)
- If a page loads dynamically, scroll down to trigger lazy-loaded content
- Use browser back button sparingly — prefer direct navigation links

## Gotchas
- Single-page apps (SPAs) may not update the URL on navigation — track state via page content
- Dropdown menus may require hover before click
- Some navigation items are hidden behind hamburger menus on narrow viewports
- Modal dialogs can block interaction with underlying page — close or dismiss them first
- Loading spinners indicate async content — wait for them to disappear before acting
