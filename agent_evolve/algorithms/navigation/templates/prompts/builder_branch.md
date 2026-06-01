## Your Target: `{target}`

You are evolving a **specialized branch** that handles: **{regime_description}**

This branch exists because these tasks need a different approach from main.
A task router reads your README.md to decide which tasks to send here, so
your README's quality directly determines whether the right tasks reach
this branch.

### Task Board (failure patterns assigned to this branch)
{task_board}

### Architecture
{architecture}

### Verified Research for This Regime
{verified_summary}

### Your Responsibilities

1. Specialize prompts/skills/memory/tools for THIS regime
2. Write or update README.md (see ROUTING-CRITICAL FORMAT below)
3. Do NOT generalize — changes here should help this regime specifically
4. Main's infrastructure (infra/) is inherited via rebase — don't duplicate it
5. Focus on what makes this regime DIFFERENT from main's approach

### ROUTING-CRITICAL: README.md format

The router routes tasks based on what it reads here. A vague README means
the router can't distinguish branches and will default to main, starving
this branch of training signal. Your README MUST contain these sections:

```markdown
# {target}

## When to route here
Concrete, observable signals from a task description that indicate this
branch is appropriate. Be specific:
- Task category contains: <list>
- Task title or input mentions: <list of keywords>
- Task metadata field <field> equals: <values>
- NOT applicable when: <list of negative signals>

## Strategy
The 2-4 sentence "what this branch does differently from main".
e.g., "Pwn challenges require interactive binary exploitation. This
branch's prompt forces use of pwntools.process(), allows extended
command budgets (50 cmds), and includes pre-built ROP gadget templates."

## Key tools / skills (added or specialized)
- `tools/<name>.py`: one-line purpose
- `skills/<name>.md`: one-line purpose
- `prompts/system.md` differences: <bullet list of unique rules>

## Known limitations
- What this branch does NOT handle (so router knows when to fall back to main)
```

The router's prompt is short — it only sees the README. If the "When to
route here" section is missing or vague, routing will be unreliable.
