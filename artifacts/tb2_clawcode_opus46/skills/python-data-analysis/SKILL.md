---
name: python-data-analysis
description: Best practices for multi-step Python tasks including data analysis, HuggingFace datasets, token counting, and any task requiring state across multiple python() calls.
---

# Python Multi-Step Tasks

## RULE #1: Each python() call starts fresh — NO state carries over
Variables, imports, data from previous calls DO NOT EXIST. This is the #1 source of NameError.

**DEFAULT STRATEGY: Write a complete .py script to a file, then run it.**

```python
with open('/app/solve.py', 'w') as f:
    f.write('''#!/usr/bin/env python3
import numpy as np
# ALL logic in one file
data = np.load("/app/data.npy")
result = process(data)
with open("/app/answer.txt","w") as out:
    out.write(str(result))
''')
```
Then: `bash("python3 /app/solve.py && cat /app/answer.txt")`

## If you must use multiple python() calls:
1. **Save state to files between calls:**
```python
import json, numpy as np
# Save: json.dump(obj, open('/tmp/state.json','w'))
# Save: np.save('/tmp/arr.npy', array)
# Load: obj = json.load(open('/tmp/state.json'))
```
2. **Every call MUST re-import and re-load** — never reference prior variables
3. **NameError = forgot to re-define** — add missing definitions, don't just re-run

## HuggingFace Datasets + Token Counting (ONE call)
```python
from datasets import load_dataset
from transformers import AutoTokenizer
ds = load_dataset("org/name", split="train")
tok = AutoTokenizer.from_pretrained("model-name")
total = sum(len(tok.encode(r["text"])) for r in ds if r["domain"] == "science")
with open("/app/answer.txt","w") as f: f.write(str(total))
```

## For iterative exploration, keep blocks self-contained
```python
# Each block: imports + load + process + print
import pandas as pd
df = pd.read_csv('f.csv')
print(df.describe())
```

## Verification
- Print/cat output files BEFORE submitting
- Confirm formats match expected schema exactly
