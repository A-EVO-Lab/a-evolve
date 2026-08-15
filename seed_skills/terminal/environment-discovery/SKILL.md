---
name: environment-discovery
description: Strategies for quickly discovering what tools, languages, and files are available in a Terminal-Bench container.
---

# Environment Discovery Skill

When starting a new Terminal-Bench challenge, quickly assess the environment.

## 1. Check available tools and languages
```bash
which python python3 pip node npm gcc g++ make cmake R java rustc go 2>/dev/null
```

## 2. Check the filesystem
```bash
ls /app/ 2>/dev/null
ls / | head -20
find / -maxdepth 2 -type f -name "*.py" -o -name "*.R" -o -name "*.js" -o -name "*.c" 2>/dev/null | head -20
```

## 3. Check OS and package manager
```bash
cat /etc/os-release 2>/dev/null | head -5
which apt-get yum dnf apk 2>/dev/null
```

## 4. Check working directory
```bash
pwd
ls -la
```

## Tips
- Many containers have pre-installed tools specific to the task
- Check `/app/` first — it often contains task-specific files
- Use `dpkg -l` or `pip list` to see installed packages
