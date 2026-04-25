---
name: data-formats
description: Reading, writing, and converting common data formats (CSV, Excel, JSON, YAML) with correct handling of encoding, types, and edge cases.
---

# Data Formats Skill

## Excel (.xlsx) Files

### Reading Excel
```python
import pandas as pd
df = pd.read_excel('input.xlsx', engine='openpyxl')
# Check what you got
print(df.columns.tolist())
print(df.dtypes)
print(df.head())
```

### Writing Excel
```python
df.to_excel('output.xlsx', index=False, engine='openpyxl')
```

### CRITICAL: Excel Formula Evaluation
openpyxl writes formula strings but does NOT compute them. Verifiers read VALUES, not formulas.

**Problem**: Cell with `=SUM(A1:A3)` shows as 0 or `#N/A` when read back.

**Solutions** (in order of preference):
1. Compute values in Python and write computed values directly
2. Use gnumeric to recalculate: `ssconvert --recalc file.xlsx file.xlsx`
3. Use LibreOffice: `libreoffice --headless --calc --convert-to xlsx file.xlsx`

### Common Excel Pitfalls
- **Type mismatch**: Number `2025` vs string `"2025"` breaks MATCH/VLOOKUP
- **Missing packages**: `pip3 install --break-system-packages openpyxl xlsxwriter`
- **Sheet names**: Check with `pd.ExcelFile('input.xlsx').sheet_names`
- **Multiple sheets**: `pd.read_excel('input.xlsx', sheet_name='Sheet2')`

## CSV Files

```python
import pandas as pd
# Always specify encoding
df = pd.read_csv('input.csv', encoding='utf-8')
# Check for issues
print(f"Shape: {df.shape}")
print(f"Columns: {df.columns.tolist()}")
print(df.head())

# Write
df.to_csv('output.csv', index=False, encoding='utf-8')
```

### CSV Pitfalls
- **Delimiter**: Some files use `;` or `\t` — check with `head -2 file.csv`
- **Encoding**: Try `encoding='latin-1'` if utf-8 fails
- **Header**: Some files have no header — use `header=None`
- **Mixed types**: Use `dtype=str` to read everything as strings first

## JSON Files

```python
import json
with open('input.json', encoding='utf-8') as f:
    data = json.load(f)

# Write with proper formatting
with open('output.json', 'w', encoding='utf-8') as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
```

## YAML Files

```python
# Install: pip3 install --break-system-packages pyyaml
import yaml
with open('input.yaml') as f:
    data = yaml.safe_load(f)
```

## General Tips
- Always check output file exists and has content before finishing
- Verify column names match exactly what the task expects
- Watch for NaN values: `df.fillna(0)` or `df.dropna()`
- Numeric precision: use `round(value, N)` for expected decimal places
