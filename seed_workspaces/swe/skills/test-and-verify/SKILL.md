---
name: test-and-verify
description: Run tests to verify fixes and catch regressions before submitting a patch.
---

# Test and Verify

## When to Use

After making code changes, before finalizing the patch.

## Instructions

1. Identify the relevant test file(s) for the modified code
2. Run the specific failing test first: `python -m pytest tests/test_specific.py -x -v`
3. If the test passes, run the broader test suite for the module
4. If tests fail on unrelated code, note it but don't try to fix unrelated issues
5. If no existing test covers the fix, consider whether the patch is correct by re-reading the issue
6. Check for import errors or syntax errors with `python -c "import module"`
