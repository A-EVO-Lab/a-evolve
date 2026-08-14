---
name: verification-strategy
description: Self-check patterns to verify task completion before submitting
---

## Key techniques
- After performing an action, verify it took effect by re-reading the page
- For creation tasks: navigate to the list/index page and confirm the new item appears
- For deletion tasks: verify the item is gone from the list
- For editing tasks: reload the detail page and check the updated values
- For email/messaging tasks: check Sent folder or confirmation toast/banner
- For settings changes: navigate away then back to verify persistence
- Read confirmation messages carefully — "are you sure?" dialogs need a second click

## Gotchas
- Success toasts/notifications may disappear quickly — read them immediately
- Some actions are queued/async — the result may not be visible immediately
- "Save" doesn't always mean "saved" — look for confirmation feedback
- Undo actions may be available briefly after completion — don't accidentally trigger them
- If the task asks you to verify something, navigate to where the verification is visible
