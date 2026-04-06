---
name: debug-and-fix
description: Strategies for debugging build failures, runtime errors, writing complete output files (ICS/JSON/config), and constraint satisfaction tasks.
---

# Debug and Fix Skill

## 1. C/C++ build fixes
```bash
apt-get update && apt-get install -y build-essential pkg-config
```
### Protobuf API fix
```bash
find . -name "*.cc" -o -name "*.cpp" | xargs sed -i \
  's/SetTotalBytesLimit(\([^,]*\),[^)]*)/SetTotalBytesLimit(\1)/g'
```

## 2. Binary inspection without xxd
```bash
od -A x -t x1z -v file.dat | head -100
```

## 3. Writing complete structured output files
Use Python to write ICS/JSON/XML — avoids heredoc truncation:
```python
from datetime import datetime
lines = ['BEGIN:VCALENDAR','VERSION:2.0','BEGIN:VEVENT',
    f'DTSTART:{start:%Y%m%dT%H%M%S}',
    f'DTEND:{end:%Y%m%dT%H%M%S}',
    f'SUMMARY:{title}',
    'END:VEVENT','END:VCALENDAR']
with open('/app/out.ics','w') as f:
    f.write('\r\n'.join(lines)+'\r\n')
```

## 4. Scheduling / constraint satisfaction
- Parse ALL constraints before attempting solutions
- Write a systematic solver to a .py file (enumerate candidates, check all constraints)
- Verify solution satisfies EVERY constraint before writing output
```python
# Write complete solver to file to avoid truncation:
with open('/app/solver.py','w') as f:
    f.write('''...all imports, logic, output...''')
# Then run: python3 /app/solver.py
```

## 5. Implementation code
- Write complete files — don't build up fragments
- For complex logic, use Python to write files programmatically
- Test immediately after writing
- If tests fail, rewrite the COMPLETE file
