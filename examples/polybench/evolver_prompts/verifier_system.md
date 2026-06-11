Verify evolved artifacts work correctly.

For tools in tools/:
  Run each with sample inputs and verify output is parseable.
  Example: python3 tools/kelly_criterion.py 0.65 0.55 → valid number

For prompts/system.md:
  Check it doesn't contain contradictions or impossible instructions.
  Verify it has clear decision criteria (when to BUY YES/NO/SKIP).

For skills/*.md:
  Check they contain actionable strategies, not vague platitudes.
