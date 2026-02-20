# clarify_ambiguous_requirements

**Scope:** 📚 Strategic Skill

**Description:** Identify and clarify ambiguous or underspecified task requirements before implementing solutions

**When to use:** task contains ambiguous quantifiers like 'enough', 'sufficient', 'adequate', task requirements could be satisfied by multiple different approaches, before implementing any music playlist or queue-based solution, when task involves duration-based requirements

## Instructions

Step 1: Parse the task goal and identify potentially ambiguous terms (e.g., 'enough', 'appropriate', 'suitable', 'adequate').

Step 2: For each ambiguous term, list possible interpretations:
- Example: 'enough songs for 85 minutes' could mean:
  a) A playlist that loops/repeats to cover 85 minutes
  b) Distinct songs totaling at least 85 minutes without repetition
  c) A playlist naturally covering 85 minutes

Step 3: Consider domain context and typical user expectations:
- For workout playlists: Users typically expect variety, not the same songs repeating
- For background music: Looping may be acceptable
- For study sessions: Distinct songs are usually preferred

Step 4: Choose the interpretation that best matches typical user intent:
- Default to the more comprehensive solution (distinct songs) unless context suggests otherwise
- If truly uncertain, implement the solution that provides more value

Step 5: Document your interpretation in your reasoning before proceeding with implementation.

Step 6: During solution planning, verify your approach aligns with the chosen interpretation.

---
*Created: 2026-02-19T02:48:38.316139*
*Version: 1*
*Scope: strategic_skill*
