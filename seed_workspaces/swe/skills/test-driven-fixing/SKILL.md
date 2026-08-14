---
name: test-driven-fixing
description: Use failing tests to guide and verify bug fixes
---

## Reproduce first
- Write a minimal repro script from the issue: `python -c "from module import X; X.trigger_bug()"`
- Or run the specific failing test: `python -m pytest tests/test_foo.py::TestClass::test_method -xvs`
- Confirm you see the SAME error as the issue before changing any code

## Fix cycle
1. Read the failing test to understand expected behavior
2. Trace from the test to the bug location
3. Make the minimal edit
4. Re-run the failing test: `python -m pytest tests/test_foo.py::test_method -xvs`
5. Run the broader test file: `python -m pytest tests/test_foo.py -x --timeout=60`

## Avoid regressions
- After the fix passes, run related tests: `python -m pytest tests/test_foo.py -x --timeout=120`
- For Django: `python -m django test <app>.tests --settings=tests.settings`
- If tests are slow, run just the specific test class first

## Common pitfalls
- Don't edit test files — only edit source code
- Don't add new dependencies
- If `pytest` is not installed, try `python -m unittest discover`
