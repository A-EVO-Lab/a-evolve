# Terminal-Bench 2 (TB2) — Experiment Report

Terminal-Bench 2 is an 89-task benchmark where an LLM agent solves command-line challenges inside Docker containers. Each task provides a prompt, a container image, and a `test.sh` evaluation script. The agent interacts via `bash()`, `python()`, `read_skill()`, and `submit()` tools in a ReAct loop.

This document describes how we run TB2, the task-guidance prompt and evolved skills used, and the results we obtained.

---

## Task-Guidance Prompt

Before each task, the agent receives the following system prompt (see [`prompt.md`](prompt.md)):

```text
You are an AI assistant tasked with solving command-line tasks in a Linux environment.
You will be given a task description and the output from previously executed commands.
You have several tools available to help with finding the solution. You are running as
root inside a Docker container. Do not use sudo — it is not installed and not needed
since you already have root privileges.

Your goal is to solve the task by providing batches of shell commands. If you need to
perform multiple actions, you can always send more messages with additional tool calls.

Before taking action, you should:
  1. ANALYZE the current state based on previous tool outputs
  2. PLAN your next steps — what commands will you run and why

Format your reasoning as follows before calling tools:

  **Analysis:** [Analyze the current state.]
  **Plan:** [Describe your plan for the next steps.]

Then call the appropriate tools to execute your plan.

Important: Each bash() call is independent — no state is preserved between calls.
If you need to run commands in sequence, chain them with &&

After you think you have completed the task, read the self-verification skill to
verify your solution.

When you have completed and verified the task, call the submit() tool with "DONE"
as argument to report that the task is complete.
```

Key design choices in this prompt:

- Explicit Analysis → Plan reasoning structure before every tool call.
- Reminder that `bash()` calls are stateless — commands must be chained with `&&`.
- A self-verification nudge: the agent is told to `read_skill("self-verification")` before submitting, which loads a checklist that catches common mistakes.

---

## Evolved Skills (7 Total)

Skills are Markdown documents loaded on-demand via the `read_skill(name)` tool. Seven skills were produced by the A-EVOLVE adaptive_skill pipeline.

### Skill Overview

| # | Skill | Note | Purpose |
|---|-------|--------|---------|
| 1 | `self-verification` | Seed | Checklist to verify every requirement before calling `submit()` |
| 2 | `environment-discovery` | Seed | Quickly discover tools, languages, and files in the container |
| 3 | `debug-and-fix` | Seed | Fix build failures, write structured output, solve constraint tasks |
| 4 | `scientific-computing` | Seed | Numerical methods, bioinformatics, ML training, logic circuits |
| 5 | `build-compiled-extensions` | Seed | Build C/C++/Cython/Fortran extensions and frameworks from source |
| 6 | `python-data-analysis` | Seed | Multi-step Python, HuggingFace datasets, stateless `python()` patterns |
| 7 | `systematic-exploration` | Seed | Avoid dead ends, backtrack when stuck, verify interpretations |

Skills are stored in `skills/<name>/SKILL.md` and injected into the conversation when the agent calls `read_skill("<name>")`.

### Skills 

- `self-verification` — A four-step checklist (re-read requirements → verify each one → check assumptions → review common pitfalls). Invoked automatically before submission thanks to the prompt nudge.
- `environment-discovery` — One-liner commands to probe the container (`which`, `ls /app/`, `pip list`, etc.) so the agent doesn't waste turns guessing what's installed.
- `debug-and-fix` — Recipes for C/C++ build fixes, binary inspection without `xxd`, writing complete structured files (ICS/JSON/XML) via Python to avoid heredoc truncation, and systematic constraint-satisfaction solvers.
- `scientific-computing` — Patterns for bioinformatics (primer design, FASTA parsing), logic-circuit simulation, numerical optimization with SciPy, and ML training on CPU-only containers.
- `build-compiled-extensions` — Step-by-step workflows for Cython, GCC manual builds, large framework compilation (Caffe, OpenCV), and protobuf version mismatch fixes.
- `python-data-analysis` — Encodes the critical rule that each `python()` call starts with a blank slate (no state carries over). Default strategy: write a complete `.py` script to a file, then execute it via `bash()`. Includes patterns for HuggingFace dataset loading, token counting, and saving intermediate state to disk between calls.
- `systematic-exploration` — Meta-strategies for when the agent gets stuck: don't reject an approach after a single failure (vary parameters widely first), backtrack after 5+ unproductive turns, verify task interpretation before committing, and treat independent requirements separately.

---

## Results on TB2

We compare two configurations:

- **Baseline** — Vanilla harness, no skills, default system prompt.
- **Opus 4.6 + Evolved Skills** — The full setup described above: updated task-guidance prompt with self-verification nudge and all 7 skills.

### Per-Run Results

| Run | Configuration | Pass Rate |
|-----|---------------|-----------|
| 1 | Baseline | 67.1% |
| 2 | Baseline | 67.0% |
| 3 | Baseline | 69.4% |
| 4 | Opus 4.6 + Evolved Skills | 74.1% |
| 5 | Opus 4.6 + Evolved Skills | 69.4% |
| 6 | Opus 4.6 + Evolved Skills | 75.3% |

### Summary

| Configuration | Avg Pass Rate | Range |
|---------------|---------------|-------|
| Baseline | 67.8% | 67.0–69.4% |
| Opus 4.6 + Evolved Skills | 72.9% | 69.4–75.3% |
| **Improvement** | **+5.1 pp** | |

---

## What Drove the Improvement

The gain comes from four changes layered on top of the baseline:

1. **The evolved skills** — Skills address the common failure modes (e.g., stateless `python()` confusion and premature approach abandonment.) 
2. **Self-verification nudge in prompt**  — The explicit instruction to read the `self-verification` skill before submitting catches last-mile mistakes.

---

