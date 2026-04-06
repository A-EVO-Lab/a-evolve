---
name: environment-discovery
description: Strategies for quickly discovering what tools, languages, and files are available in a Terminal-Bench container.
---

# Environment Discovery Skill

When starting a new Terminal-Bench challenge, quickly assess the environment.

## 1. Check available tools and languages
```bash
which python python3 pip node npm gcc g++ make cmake perl ruby 2>/dev/null
```

## 2. Check the filesystem
```bash
ls /app/ 2>/dev/null
find /app -type f 2>/dev/null | head -30
```

## 3. Check pre-installed task-specific tools
Many containers have specialized tools already installed:
```bash
# Security/crypto tools
which john hashcat 7z 7za openssl 2>/dev/null
ls /app/john/run/ 2>/dev/null
# Bio tools
which oligotm primer3_core samtools 2>/dev/null
# Data tools
pip list 2>/dev/null | head -30
```

## 4. If python3 is missing, try alternatives
```bash
which python perl 2>/dev/null  # python2 may exist as 'python'
# Use perl for text processing if python unavailable
# Use /app/john/run/john for password cracking (has its own perl scripts)
```

## 5. Pre-installed tool locations
- John the Ripper scripts: `/app/john/run/*.pl` (perl-based, use `perl` not `python3`)
- Task binaries often in `/app/` or `/usr/local/bin/`

## Tips
- Check `/app/` first — it often contains task-specific files and tools
- Don't assume python3 exists — verify first, use bash/perl alternatives
- Use `dpkg -l` or `apt list --installed` to see system packages
