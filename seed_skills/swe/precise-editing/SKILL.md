---
name: precise-editing
description: Make minimal, targeted code edits that fix the issue without introducing regressions.
---

# Precise Editing

## When to Use

When applying code changes to fix a bug or implement a small feature.

## Instructions

1. Read the full function/method before editing -- understand the context
2. Make the smallest change that fixes the issue
3. Preserve existing code style (indentation, naming conventions, etc.)
4. If adding a new code path, check how similar paths are handled nearby
5. Update related comments/docstrings if the behavior changes
6. Never remove or modify code unrelated to the fix
