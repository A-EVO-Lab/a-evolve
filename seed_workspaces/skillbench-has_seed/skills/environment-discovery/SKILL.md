---
name: environment-discovery
description: Strategies for quickly discovering what tools, languages, data files, and skills are available in a SkillBench container.
---

# Environment Discovery Skill

When starting a new SkillBench task, quickly assess the environment before writing code.

## 1. Check available skills first

```bash
list_skills
```

If any skill names relate to the task, load them immediately — they often contain critical instructions.

## 2. Check available tools and languages

```bash
which python3 pip3 node npm gcc g++ make cmake R java rustc go 2>/dev/null
python3 --version 2>/dev/null
pip3 list 2>/dev/null | head -30
```

## 3. Check the workspace

```bash
pwd
ls -la /root/
file /root/* 2>/dev/null | head -20
```

## 4. Check OS and package manager

```bash
cat /etc/os-release 2>/dev/null | head -5
which apt-get yum dnf apk 2>/dev/null
```

## 5. Inspect data files

```bash
# Check CSV/JSON/YAML files
head -5 /root/*.csv 2>/dev/null
head -20 /root/*.yaml /root/*.yml /root/*.json 2>/dev/null
wc -l /root/*.csv 2>/dev/null
```

## Tips

- Most containers are Ubuntu-based with apt-get
- Data files referenced in the task are usually in `/root/`
- If you need extra packages, install them with `pip3 install --break-system-packages <pkg>`
- Check for pre-installed domain-specific tools (e.g., libreoffice, ffmpeg, erlang)
