---
name: patch-hygiene
description: Keep patches minimal and clean for successful git apply
---

## Keep patches minimal
- Only change lines needed to fix the bug
- Don't reformat surrounding code, add docstrings, or fix unrelated issues
- Don't add new imports unless strictly necessary for the fix
- Use `git diff` to review before submitting — remove any accidental changes

## Editing files safely
- Use `sed -i` for single-line changes: `sed -i 's/old_pattern/new_pattern/' file.py`
- For multi-line edits, use Python:
  ```bash
  python3 -c "
  p = '/testbed/path/to/file.py'
  t = open(p).read()
  t = t.replace('old_code', 'new_code')
  open(p, 'w').write(t)
  "
  ```
- Verify the edit: `git diff /testbed/path/to/file.py`

## Before submitting
- `git diff` — review all changes, remove anything unrelated
- `git checkout -- file.py` to revert accidental edits
- `git diff --stat` — should show only 1-3 files changed for most fixes
