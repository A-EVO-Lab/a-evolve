---
name: form-interaction
description: Patterns for filling forms, selecting options, handling inputs in web apps
---

## Key techniques
- Identify all required fields before starting to fill — look for asterisks or "required" labels
- For text inputs: click the field first, then type the value
- For dropdowns/select elements: use select_option or click to open, then click the option
- For checkboxes/radio buttons: click the label text (often more reliable than the tiny input)
- For date pickers: try typing the date in the input field directly (YYYY-MM-DD or MM/DD/YYYY)
- For file uploads: look for file input elements or drag-and-drop zones
- After filling all fields, look for Submit/Save/Confirm buttons — they may be at the bottom

## Gotchas
- Auto-complete dropdowns require typing first, then selecting from suggestions
- Some forms have multi-step wizards — look for Next/Continue buttons
- Validation errors appear after submission — re-read the page to see error messages
- Rich text editors (WYSIWYG) need different interaction than plain text inputs
- Hidden fields may auto-populate — don't try to fill fields you can't see
- Form submission may redirect to a different page — verify the action completed
