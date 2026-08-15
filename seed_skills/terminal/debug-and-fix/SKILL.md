---
name: debug-and-fix
description: Strategies for debugging build failures, runtime errors, and fixing broken code in containerized environments.
---

# Debug and Fix Skill

For tasks that involve fixing broken code, builds, or configurations.

## 1. Read error messages carefully
```bash
# Capture both stdout and stderr
command_that_fails 2>&1 | tail -50
```

## 2. Common build fixes
```bash
# Missing dependencies
apt-get update && apt-get install -y build-essential pkg-config
pip install -r requirements.txt 2>/dev/null

# Missing headers
apt-get install -y lib*-dev

# Permission issues
chmod +x script.sh
```

## 3. Debugging strategies
```bash
# Check file encoding
file suspicious_file.txt

# Check for syntax errors
python3 -m py_compile file.py
node --check file.js

# Trace execution
bash -x script.sh 2>&1 | head -50
```

## 4. Git-related tasks
```bash
git status
git log --oneline -10
git branch -a
git reflog | head -10
git stash list
```
