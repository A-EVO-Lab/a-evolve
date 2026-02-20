# verify_complete_data_collection

**Scope:** 📚 Strategic Skill

**Description:** Systematic verification that all required data has been collected from all sources before marking a task complete

**When to use:** before marking task complete, before submitting final answer, after collecting data from multiple sources, when task involves pagination, when output format is specified (CSV, JSON, etc.)

## Instructions

BEFORE marking any task complete, perform this verification checklist:

1. **Identify ALL data sources**: List every API, database, or source mentioned in the task
2. **Verify pagination completeness**: For each paginated source, confirm you collected ALL pages until empty results
3. **Check data counts**: Compare collected item counts against expected totals or until API returns empty
4. **Validate deduplication**: If combining multiple sources, verify no duplicate entries by unique ID
5. **Verify all required fields**: Check that every field requested in the task is present in your output
6. **Format validation**: If CSV/specific format required, verify structure matches exactly (headers, separators, escaping)
7. **Cross-reference requirements**: Re-read the task and check off each requirement one by one
8. **Sample validation**: Spot-check 3-5 random items to ensure data quality and completeness

If ANY check fails, DO NOT mark complete. Instead:
- Identify what's missing
- Collect the missing data
- Re-run verification

Only mark complete when ALL checks pass.

---
*Created: 2026-02-19T01:36:18.558311*
*Version: 1*
*Scope: strategic_skill*
