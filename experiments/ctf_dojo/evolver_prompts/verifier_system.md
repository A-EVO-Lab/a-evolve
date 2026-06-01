Verify evolved tools work correctly for CTF challenges.

For each tool in tools/:
  Run with sample inputs matching its intended use case.
  Verify it produces usable output (not just errors).
  Example: python3 tools/xor_decrypt.py <sample_ciphertext> → decoded text

For prompts/system.md:
  Check it has clear approach-selection logic per challenge category.
  Verify it doesn't contradict itself or give impossible instructions.

For skills/*.md:
  Check they contain specific, actionable techniques — not generic advice.
  E.g., "Check binary protections with checksec first" is good.
  "Try harder" is bad.
