# validate_csv_before_submission

**Scope:** 🎯 Tactical Patch

**Description:** Immediate validation check for CSV outputs to catch formatting errors before task submission

**When to use:** immediately after generating CSV output, before submitting answer for CSV tasks, when task requires CSV format, after using format_csv_output tool

## Instructions

EVERY TIME you create CSV output, perform these immediate checks:

1. **Parse your own output**: Try to parse the CSV you just created
2. **Count rows**: Verify row count matches your data count + 1 (for header)
3. **Count columns**: Verify every row has the same number of columns
4. **Check header**: Verify first row contains expected column names
5. **Spot check**: Pick 2-3 rows and verify values match your source data
6. **List separator check**: If task mentions multiple values (e.g., 'artists'), verify separator is correct (usually ', ')
7. **Special characters**: If data contains commas/quotes, verify they're properly escaped

If ANY validation fails:
- DO NOT submit the CSV
- Fix the issue
- Re-validate
- Only submit after validation passes

This is a MANDATORY step before submission for any CSV task.

---
*Created: 2026-02-19T01:36:18.652352*
*Version: 1*
*Scope: tactical_patch*
*TTL: 50 episodes*
