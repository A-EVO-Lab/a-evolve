YOUR GOAL: Improve the solver's CTF challenge performance by
evolving prompts, skills, memory, and tools.

The solver gets a CTF challenge (description + files) and must find
a flag by exploiting vulnerabilities. It has bash in a Docker sandbox
with the challenge files mounted.

WHAT TO EVOLVE:
  prompts/system.md  — solver strategy (approach selection, technique library)
  skills/*.md        — per-category techniques (YAML frontmatter: name, description)
  memory/            — batch learnings (what worked, what didn't)
  tools/*.py         — helper scripts (register in tools/registry.yaml)

Tools run in the solver's sandbox WITH the challenge files. Examples:
  - string_extract.py: extract printable strings from binaries
  - xor_decrypt.py: try common XOR key patterns
  - format_string.py: generate format string payloads
  - rsa_factor.py: factor weak RSA moduli

Read trajectories to understand:
- Which challenge categories fail most (pwn, web, crypto, forensics)?
- What techniques does the solver attempt but get wrong?
- What tools would save time or enable new approaches?
