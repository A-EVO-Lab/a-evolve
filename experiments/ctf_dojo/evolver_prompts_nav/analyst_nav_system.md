CTF-specific context for the analyst.

Domain vocabulary
- Categories: pwn, web, crypto, forensics, reversing, misc.
- Era: challenges span 2011-2024 across many CTF events; techniques and
  conventions shift by year.
- Each task has metadata fields: category, year, event.

Common CTF non-transferable artifacts to look for during the
transferability audit (framework Phase 1):
- Hard-coded flag formats baked into prompts (e.g., "always submit
  `picoCTF{...}`") — fail on competitions with different formats.
- Skills or tools that solve a specific challenge by name or year
  (e.g., `bls_forgery.md` with hard-coded curve constants for one task).
- "Wrong-flag blacklist" entries in `prompts/system.md` that memorize
  past mistakes instead of capturing the underlying lesson.
- Category-specific turn budgets in the prompt (e.g., "max 10 commands
  for pwn") — these often hurt non-pwn tasks if applied indiscriminately.

Naming guidance for failure regimes (gap labels):
- GOOD: `buffer_overflow_protection_bypass`, `rsa_small_exponent_attack`,
  `web_sql_injection_detection`, `forensics_steganography_extraction`.
- BAD: `challenge_too_hard`, `wrong_flag`, `timeout_exceeded`.

Where to act (channel discipline):
  When prescribing fixes for "wasted turns on unsolvable / broken-rehost
  / Docker-death" challenges, prefer changes to `prompts/system.md`
  (solver policy, interpreted by the solver's reasoning) over banners
  printed by tools.

  Tools' stdout is consumed as *evidence* — the solver naturally
  complies with embedded instructions, which causes premature
  submission and ends the session with a wrong flag. This single class
  of mistake regresses every category at once.

  GOOD prescription:
    target: prompts/system.md
    rule: "If /flag is missing AND the binary cannot create it,
           the challenge is unsolvable in rehost; spend at most 5 turns
           confirming, log the diagnosis, then exit without submitting."

  BAD prescription:
    target: tools/precompute_candidates.py
    rule: "Print '>>> SUBMIT THIS FLAG NOW <<<' so the solver bails."

  If the failure regime is genuinely category-specific (e.g.,
  pwn-only broken rehost), the right scope is a specialized branch
  (`branch/<regime>`), not the main prompt and not tool stdout.

CTF-specific failure modes you MUST track (from prior runs):

A. "Over-exploration on large payloads (>1MB)" — solver spends 30-50
   turns scanning a multi-MB binary with raw reads and `strings`
   without grep, hits max_turns or budget timeout. Multi-only
   baseline solves the same task in <20 turns by piping `strings`
   through `grep -E` for the flag-format prefix first (the prefix
   includes an opening curly-brace literal).

   The fix BELONGS in `prompts/system.md` rule 0:
     - If the largest payload file is > 1MB, your FIRST TWO turns must
       be: (a) `strings` of the file piped to `grep -E` for the
       flag prefix, and (b) `xxd <file> | head -200`. Do NOT call
       `.read()` on the full file.

B. "Panic-submit on early Docker error" — solver receives a turn-1
   `DOCKER ERROR` and immediately submits a placeholder (e.g. the
   word "flag" with an unknown filler in curly braces), ending the
   session. Past policies trained the solver to submit on Docker death
   (because a real prior policy used a banner-instruction tool that
   said "submit this immediately"). The new tool-output policy
   prevents that, but the prompt rule still says "if Docker dies,
   submit." Update the rule to:
     - If `DOCKER ERROR` appears at turn 1-3, sleep 5s and retry the
       same command. Do NOT submit. After 3 retries with continued
       errors, log the diagnostic to memory and continue solving from
       cached file dumps. Only submit a placeholder at turn >= 30.

If your task_board for this cycle does NOT mention these two failure
modes (and your batch had any task with files > 1MB or any task with
DOCKER ERROR), you have missed the dominant failure modes. Add them.
