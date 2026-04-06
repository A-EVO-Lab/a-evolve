---
name: systematic-exploration
description: Strategies for avoiding dead ends and premature conclusions. Read this when stuck or when an approach seems to not work.
---

# Systematic Exploration

## Don't reject approaches based on a single test
When a promising approach (solver, algorithm, transformation) gives bad results:
- Vary the key parameter across a wide range before rejecting (e.g., iterations: 1, 5, 20, 50, 100)
- Combine with other settings (disable warmstart, change precision, different flags)
- A method that fails with default parameters may succeed with tuned parameters

## Stuck for 5+ turns? Backtrack.
If you've been optimizing the same approach without crossing the threshold:
1. List all approaches you've tried AND rejected
2. For each rejected approach, ask: "Did I test it thoroughly, or did I dismiss it after one attempt?"
3. Re-test the most promising rejected approach with different configurations

## Verify your interpretation before committing
When data looks unusual or results seem off:
- Try multiple interpretations of the data (different units, coordinate transforms, encodings)
- Don't lock in on the first plausible explanation — test at least 2 alternatives
- Check if numerical coincidences are real or spurious (e.g., ratio match vs exact value match)

## Independent requirements
When a task lists multiple criteria (A, B, C):
- Each criterion may be satisfied by DIFFERENT entities unless explicitly stated otherwise
- Don't combine independent filters into a single query/check
