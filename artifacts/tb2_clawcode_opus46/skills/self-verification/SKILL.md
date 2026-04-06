---
name: self-verification
description: Verification patterns to confirm task completion before submitting. Read this before calling submit().
---

# Self-Verification Checklist

Before calling submit(), systematically verify your solution:

## 1. Re-read Requirements
- Open and re-read the original task description
- List each specific requirement (files to create, formats, thresholds, etc.)

## 2. Verify Each Requirement
For each requirement, run a concrete check:
- **File creation:** `ls -la <path>` and `head <path>`
- **Code changes:** Run the modified code and check output
- **Build tasks:** Run the built binary with a test input
- **Server/web tasks:** `curl` or `wget` to test endpoints — verify response content, not just that the port is open. Checking ports with `ss` or `netstat` is NOT enough
- **Data tasks:** Check output format, row counts, value ranges
- **Config tasks:** If necessary, restart the service and verify it actually works end-to-end

## 3. Check Your Assumptions
- If you chose between multiple approaches (e.g., normalization methods, algorithms), verify your choice matches what the task/test expects — don't assume "standard" is correct
- If you installed or modified system packages, run `apt --fix-broken install` and verify the package manager still works
- If your solution works on the provided example, consider whether it generalizes to different inputs (larger, different distributions, edge cases)

## 4. Common Pitfalls
- Git tasks: check ALL branches and history, not just HEAD
- Security tasks: verify secrets are removed from git history too
- Build tasks: verify no unwanted dependencies (ldd, nm)
- LaTeX: recompile and check ALL warnings, not just the first
