---
name: skill-usage
description: How to discover, load, and effectively use skills to solve SkillBench tasks.
---

# Skill Usage Guide

Skills are modular instruction packages placed in the container to help you solve tasks. Using them effectively is the single most important factor for success.

## Skill locations

Skills are copied to multiple well-known paths inside the container:

- `/root/.agents/skills/`
- `/root/.claude/skills/`
- `/root/.codex/skills/`

All paths contain the same skills. Use whichever is available.

## Skill structure

Each skill is a folder:

```
skill-name/
├── SKILL.md        # Main instructions (YAML frontmatter + markdown body)
├── scripts/        # Optional helper scripts (Python, Bash)
└── references/     # Optional reference documentation
```

## How to use skills

1. **Always list skills first** — call `list_skills` at the start of every task
2. **Load relevant skills** — if a skill name matches the task domain, call `load_skill` with the skill name
3. **Follow skill instructions** — skills contain domain-specific procedures, parameter values, or code patterns that are essential
4. **Run helper scripts** — if the skill has `scripts/`, execute them (they often automate tedious steps)
5. **Consult references** — check `references/` for API docs or formula sheets

## Common skill patterns

- **Data format skills** (e.g., xlsx, csv): Teach how to read/write specific formats
- **Domain skills** (e.g., pid-control, seismic): Contain domain formulas and constants
- **Tool skills** (e.g., libreoffice, ffmpeg): Show correct CLI flags and workflows
- **Framework skills** (e.g., react, spring-boot): Guide migration or debugging patterns

## Anti-patterns

- Do NOT ignore skills — they exist specifically for the task at hand
- Do NOT reinvent what a skill script already automates
- Do NOT skip reading the full SKILL.md — summaries miss critical details
