CTF-specific context for the builder.

Solver runtime
- Bash in a Docker sandbox with the challenge files mounted under
  `/challenge/`. Wall-clock budget per task is ~600s.
- Tools run inside that same sandbox WITH the challenge files
  available. They get the challenge filename(s) on argv.

Tool naming and shape (idiomatic for this benchmark):
- `tools/<verb>_<noun>.py` (e.g., `string_extract.py`, `xor_decrypt.py`,
  `format_string.py`, `rsa_factor.py`).
- Each tool registered in `tools/registry.yaml` with `name` and one-line
  `description`.
- Tools should fail fast (timeouts) and emit usable output to stdout —
  not just an error trace — so the solver can pipe-and-grep on them.

Skill files (`skills/<name>.md`):
- YAML frontmatter with `name` and `description`.
- Body: actionable techniques, not generic advice.
  GOOD: "Run `checksec` first; if NX is disabled, shellcode injection
  is viable — see template in `tools/shellcode_x86.py`."
  BAD: "Try harder on pwn challenges."

Memory entries should capture transferable lessons (e.g., "When
flagCheck says `Correct flag!`, the SHA256 verifier may still mismatch
due to rehosting — submit anyway"). Avoid memorizing specific challenge
names without the underlying pattern.

Tool output discipline (HARD CONSTRAINT — verifier will FAIL the build):
  Tools print *information* for the solver to interpret. They MUST NOT
  print imperatives that direct the solver toward a terminal action
  (submit ends the CTF session — there is no second chance).

  Forbidden in tool stdout:
    - "submit this", "submit FIRST", "Call submit"
    - "YOUR NEXT ACTION", "DO NOT call bash"
    - Banners with "CHECKPOINT", "LAST_RESORT_FLAG"
    - ">>> "-style banner lines that prescribe solver behavior

  Why: solvers consume tool stdout as evidence and naturally comply
  with embedded instructions. A tool that nudges submission causes the
  solver to skip solving and submit a fallback string, which is wrong
  in nearly every case and ends the session. The cost shows up across
  all categories at once, not just the regime the tool was meant for.

  GOOD pattern: tool prints "decoded flag candidate: <X>" → solver
  reasons about whether to submit.
  BAD pattern: tool prints ">>> SUBMIT THIS NOW <<<" → solver complies.

  If the analyst diagnosed "wasted turns on unsolvable challenges",
  the right place to act is `prompts/system.md` (solver policy), not
  tool stdout banners.

Two evidence-based prompt rules every CTF `prompts/system.md` should
contain (re-affirm them every cycle that mutates that file):

1. Large-payload guidance (challenge files > 1MB):
   - DO NOT scan the full file with regex / Python `.read()` over the
     whole bytes. Use `strings <file>` piped through `grep -E` for the
     flag-format prefix (the prefix has an opening curly brace), or
     `binwalk -e`, `xxd <file> | head -200`, or
     `objdump -d | head -300`.
   - Budget: at most 6 bash commands of binary inspection before pivoting
     to a different approach. If 6 commands didn't find a candidate,
     the flag is not in raw byte content — try semantic analysis
     (decompiler, dynamic execution, network/protocol analysis).
   - Reason: a single `cat` of a 25 MB binary fills the bash output
     cap (100 KB), wasting the turn on garbage.

2. Docker error / broken rehost: investigate, do NOT panic-submit.
   - If a Docker error appears WITHIN THE FIRST 3 TURNS, retry the
     command after 5 s sleep — transient errors recover. Do NOT
     submit a guessed flag at turn 1 / 2 / 3.
   - If Docker errors persist after 3 retries, dump everything you've
     learned (file listing, sha256, descriptions) to memory and
     submit only if the verify_flag.py tool has CONFIRMED a match.
     Otherwise the task is unsolvable in this environment — submit
     a placeholder ONLY at turn ≥ 30 or when max_turns is approached.
   - Reason: many tasks recover from transient sandbox issues. A
     turn-1 panic-submit (e.g. submitting "flag" then a literal
     curly-brace placeholder) ends the session and forfeits a winnable
     task. We have measured this directly: past evolved policies that
     submitted on first Docker error forfeited solvable tasks the
     multi-only baseline solved in 3-15 turns.
