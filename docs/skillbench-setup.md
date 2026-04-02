# SkillBench Setup

This guide covers the public SkillBench setup flow for A-Evolve, including auto-bootstrap, manual overrides, and common troubleshooting steps.

## Requirements

- `git` in `PATH` for auto-bootstrap
- Docker for SkillBench task execution
- Python 3.11+ for A-Evolve
- `uv` if you want to use SkillBench's `harbor` mode

Install the A-Evolve SkillBench extras first:

```bash
pip install -e ".[skillbench,dev]"
```

## Auto-Bootstrap

If you do not provide any SkillBench paths, A-Evolve bootstraps the public [`benchflow-ai/skillsbench`](https://github.com/benchflow-ai/skillsbench) repo automatically.

- Default repo URL: `https://github.com/benchflow-ai/skillsbench.git`
- Default pinned ref: `828bb921fb94dc065bfefd6bac4e8938be3f71e0`
- Default cache root: `~/.cache/agent-evolve/skillbench/<ref>/repo`

The bootstrap downloads the subset needed for both `native` and `harbor`:

- `tasks/`
- `tasks-no-skills/`
- `libs/`
- `pyproject.toml`
- `uv.lock`
- `.python-version`

## Path Precedence

A-Evolve resolves SkillBench paths in this order:

1. Explicit CLI arguments such as `--tasks-dir-with-skills` or `--harbor-repo`
2. Low-level env vars:
   `SKILLBENCH_TASKS_DIR`
   `SKILLBENCH_TASKS_NO_SKILLS_DIR`
   `SKILLBENCH_HARBOR_REPO`
3. Repo-level env vars:
   `SKILLBENCH_REPO_DIR`
   `SKILLBENCH_REPO_REF`
4. Auto-bootstrap into the cache directory

Low-level path overrides always win over repo-level auto-derivation.

## Environment Variables

| Variable | Purpose |
|---|---|
| `SKILLBENCH_REPO_DIR` | Path to a local public SkillsBench clone; A-Evolve derives `tasks/`, `tasks-no-skills/`, and the Harbor repo root from it |
| `SKILLBENCH_REPO_REF` | Ref used for auto-bootstrap when `SKILLBENCH_REPO_DIR` is not set |
| `SKILLBENCH_TASKS_DIR` | Direct override for `tasks/` |
| `SKILLBENCH_TASKS_NO_SKILLS_DIR` | Direct override for `tasks-no-skills/` |
| `SKILLBENCH_HARBOR_REPO` | Direct override for the Harbor-capable SkillsBench repo root |

## Common Flows

### Native

```bash
python examples/skillbench_examples/skillbench_solve_one.py \
  --mode native \
  --use-skills true
```

### Harbor

```bash
python examples/skillbench_examples/skillbench_solve_one.py \
  --mode harbor \
  --use-skills false
```

### Use a Local SkillsBench Clone

```bash
export SKILLBENCH_REPO_DIR=/path/to/skillsbench
python examples/skillbench_examples/skillbench_solve_one.py --mode native
```

### Override Tasks Only

```bash
export SKILLBENCH_TASKS_DIR=/path/to/tasks
export SKILLBENCH_TASKS_NO_SKILLS_DIR=/path/to/tasks-no-skills
python examples/skillbench_examples/skillbench_evolve_in_situ_cycle.py --use-skills false
```

## Troubleshooting

### `git` missing

Auto-bootstrap requires `git`. Install it or set `SKILLBENCH_REPO_DIR` / `SKILLBENCH_TASKS_DIR` manually.

### Download or checkout failure

If bootstrap fails, verify:

- the machine has internet access to GitHub
- the pinned ref still exists
- `git` can clone `https://github.com/benchflow-ai/skillsbench.git`

You can bypass bootstrap by cloning the repo yourself and setting `SKILLBENCH_REPO_DIR`.

### Tasks directory missing

For `native` mode, A-Evolve only requires the selected tasks directory:

- `use_skills=true` needs `tasks/`
- `use_skills=false` needs `tasks-no-skills/`

Check your direct overrides first, then repo-level overrides.

### Harbor repo missing or incomplete

`harbor` mode needs a full public SkillsBench repo root containing at least:

- `libs/`
- `pyproject.toml`

If you only set task directories, `harbor` can still fail. Point `SKILLBENCH_HARBOR_REPO` or `SKILLBENCH_REPO_DIR` at a full SkillsBench repo.

### Installed wheel cannot find the seed workspace

The built package now bundles `seed_workspaces/skillbench`. If a packaged install still cannot find the seed workspace, rebuild and reinstall the wheel, then verify that `seed_workspaces/skillbench/manifest.yaml` is present in the wheel contents.
