# HippocampalEvolveEngine

A neuroscience-inspired evolution algorithm that uses associative memory and spreading activation instead of direct LLM-guided mutation.

## Biological inspiration

This engine maps hippocampal memory structures onto the evolution loop:

| Brain Region | Function | Implementation |
|---|---|---|
| **Dentate Gyrus** | Pattern separation | Concept extraction from observation trajectories |
| **CA3** | Associative recall | Spreading activation through co-occurrence graph |
| **CA1** | Output filtering | Multi-signal ranking with recency and relevance |
| **Basal Ganglia** | Procedural memory | Skill crystallization from recurring patterns |

## How it works

Each evolution cycle runs five phases:

1. **Encoding** -- Extract technical concepts from task trajectories using regex-based keyword extraction. No LLM call needed.

2. **Consolidation** -- Build co-activation edges between concepts that appear together in observations (Hebbian learning: "fire together, wire together").

3. **Recall** -- Spread activation from failure-pattern concepts through the graph to find associated successful patterns (CA3 pattern completion).

4. **Crystallization** -- Detect concept clusters with high success rates and crystallize them into reusable skills.

5. **Expression** -- Write new skills and episodic memory to the agent workspace.

## Key features

- **No LLM calls during evolution** -- Uses graph algorithms instead, making each cycle very fast
- **Temporal adaptation** -- Exponential decay (23-day half-life) keeps the graph current
- **Automatic skill detection** -- Skills emerge from observed patterns, not manual engineering
- **Serializable state** -- Graph can be persisted and resumed across runs

## Usage

```python
import agent_evolve as ae
from agent_evolve.algorithms.hippocampal_evolve import HippocampalEvolveEngine

engine = HippocampalEvolveEngine(
    recency_lambda=0.03,       # ~23-day half-life
    min_skill_occurrences=2,   # observations before skill crystallization
    max_skills=10,             # cap on skills in workspace
)

evolver = ae.Evolver(
    agent="./my_agent",
    benchmark="my_benchmark",
    engine=engine,
)
results = evolver.run(cycles=20)
```

## Measured impact (from Claude Hippocampus deployment)

When deployed as a persistent memory system for Claude Code:

- 29% faster task completion
- 13% token consumption reduction
- 36% fewer tool calls
- Preemptive bug prevention through cross-session knowledge recall

## Reference

Based on [claude_hippocampus](https://github.com/allthingssecurity/claude_hippocampus) -- a neuroscience-inspired persistent memory system for Claude Code.
