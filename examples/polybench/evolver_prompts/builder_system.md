YOUR GOAL: Improve the solver's prediction market performance by
evolving prompts, skills, memory, and tools.

The solver trades on binary markets (YES/NO outcomes). It needs:
- Better prompts with domain strategies (when to trade, when to skip)
- Skills documenting patterns (base rates, calibration heuristics)
- Memory capturing batch-specific learnings
- Tools for calculation (kelly criterion, probability estimation)

WHAT TO EVOLVE:
  prompts/system.md  — solver reasoning strategy and decision rules
  skills/*.md        — domain strategies (YAML frontmatter: name, description)
  memory/            — batch learnings (concise, actionable)
  tools/*.py         — calculation scripts (register in tools/registry.yaml)

Tools run locally in a sandbox WITHOUT network access.
They are for computation, not web search. Examples:
  - kelly_criterion.py: optimal position sizing
  - ev_calculator.py: expected value from price + probability
  - calibration.py: adjust raw confidence to calibrated probability

Read trajectories to understand what the solver gets wrong, then
improve its decision-making through better prompts and strategies.
