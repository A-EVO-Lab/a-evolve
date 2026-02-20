# csv_formatting_best_practices

**Scope:** 🎯 Tactical Patch

**Description:** Guidance on using CSV formatting tools effectively

**When to use:** formatting CSV, creating CSV output, tabular data formatting

## Instructions

When formatting data as CSV:

1. The format_csv_output tool now automatically infers columns from your data
2. For dictionary data: columns are extracted from the keys of the first item
3. For list data: columns are auto-generated as 'col_0', 'col_1', etc.
4. You can still provide explicit columns if you want custom names or ordering
5. Always check the returned 'column_count' to verify the structure

Examples:
- Dict data: format_csv_output([{'name': 'Alice', 'age': 30}]) → auto-infers 'name' and 'age'
- List data: format_csv_output([['Alice', 30]]) → generates 'col_0', 'col_1'
- Custom columns: format_csv_output(data, columns=['custom_name', 'custom_age'])

---
*Created: 2026-02-19T01:37:05.668193*
*Version: 1*
*Scope: tactical_patch*
*TTL: 50 episodes*
