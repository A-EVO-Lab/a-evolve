# Vendored Source Pin

This directory vendors selected files from SkillsBench for native Terminus2 parity.

- Upstream repository: `https://github.com/benchflow-ai/skillsbench`
- Upstream commit: `828bb921fb94dc065bfefd6bac4e8938be3f71e0`

## Vendored files and hashes

- `terminus_json_plain_parser.py`
  - upstream: `libs/terminus_agent/agents/terminus_2/terminus_json_plain_parser.py`
  - sha256: `6409957029a42bdf860922198b8e599aa3a0091cc12f660839da0d00ca0c2f6b`
- `skill_docs.py` (container-adapted from upstream logic)
  - upstream reference: `libs/terminus_agent/agents/terminus_2/skill_docs.py`
  - upstream sha256: `6b656caa45a6a991cf1e65e2624e88f82c8ab33f2610864787eb48ccb8655700`
  - vendored-adapted sha256: `ac5cb7a61bc3ec329338ec2df77e53388211dd129a56e472666baa209cf9a75f`
- `prompt-templates/terminus-json-plain.txt`
  - upstream: `libs/terminus_agent/agents/prompt-templates/terminus-json-plain.txt`
  - sha256: `aafe42f85be500f945bdf2bef3ec4d8b71ec8173db76f3ae8d62d5772aa6d25f`

## Notes

- `skill_docs.py` is intentionally adapted to use `SkillBenchContainer` (sync API)
  while preserving upstream constants and parsing behavior.
