---
name: repo-navigation
description: Efficiently navigate large Python repos to locate relevant code for a bug fix
---

## Locate the relevant code
- Start with `find /testbed -type f -name "*.py" | head -30` to understand repo layout
- Use `grep -rn "ErrorMessage\|function_name" /testbed/src/ --include="*.py" -l` to find files
- Check `git log --oneline -20` for recent changes related to the issue
- Read the traceback in the issue to identify exact file + line

## Understand the codebase
- Read the test that's failing: `cat /testbed/tests/test_xxx.py`
- Check imports and class hierarchy: `grep -n "class.*:" /testbed/src/module.py`
- Use `git log --all --oneline -- path/to/file.py` to see change history

## Common repo layouts
- Django: `django/<app>/` with tests in `tests/<app>/`
- Scikit-learn: `sklearn/<module>/` with tests in `sklearn/<module>/tests/`
- Matplotlib: `lib/matplotlib/` with tests in `lib/matplotlib/tests/`
- Sympy: `sympy/<module>/` with tests in `sympy/<module>/tests/`
