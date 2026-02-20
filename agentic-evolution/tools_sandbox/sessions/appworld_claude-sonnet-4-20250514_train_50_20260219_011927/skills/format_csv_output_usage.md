---
name: format_csv_output_usage
description: How to use the format_csv_output tool. Use when you need to updated tool.
---

# format_csv_output Tool Usage

**Purpose:** Updated tool

**When to use:** When your task requires updated tool.

## Example Usage

```python
# Example 1: With dictionary data (auto-infer columns)
data = [{'name': 'Alice', 'age': 30}, {'name': 'Bob', 'age': 25}]
result = format_csv_output(data)
print(result['csv_string'])
# Output: name,age\nAlice,30\nBob,25

# Example 2: With explicit columns
result = format_csv_output(data, columns=['name', 'age'])

# Example 3: With list data (auto-generate column names)
data = [['Alice', 30], ['Bob', 25]]
result = format_csv_output(data)
print(result['csv_string'])
# Output: col_0,col_1\nAlice,30\nBob,25
```

## Best Practices

1. **Verify input format** - Ensure your input matches the expected parameter types
2. **Check return values** - Handle the tool's output appropriately
3. **Error handling** - Catch any errors from the tool execution

---
*Auto-generated skill for tool: format_csv_output*
*Created: 2026-02-19T01:37:05.620968*
