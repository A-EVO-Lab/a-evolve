---
name: code-search
description: Efficiently search and navigate large codebases to find relevant files, functions, and patterns.
---

# Code Search

## When to Use

When you need to find relevant code in a repository -- specific functions, class definitions, error messages, or usage patterns.

## Instructions

1. Start with `grep -rn` for exact string matches (error messages, function names)
2. Use `find` for file discovery by name or extension
3. For Python: check `__init__.py` files to understand module structure
4. For test files: look in `tests/`, `test_*.py`, `*_test.py` patterns
5. Read imports in the failing file to trace dependencies
6. Check git blame / git log for recent changes to suspicious files
