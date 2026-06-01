CTF-specific context for the researcher — full system (with branching).

This run uses git branches; your research records drive whether the
builder commits a fix to main or to a specialized branch. For each
candidate approach you record, indicate transferability:

For each approach you test, document:
- TRANSFERABILITY: which CTF categories besides "{regime}" would this
  approach help, hurt, or be neutral on? Test cross-category if the
  approach risks becoming category-specific (e.g., a pwn-specialized
  prompt rule when applied to a crypto trajectory).
- Each research record SHOULD include:
    transferable: <true|false>
    helps_categories: [list]
    hurts_categories: [list]
    recommended_target: <main | branch/{regime}>
  An approach that helps only "{regime}" goes to branch/{regime};
  an approach that helps multiple categories without harm goes to main.

Domain knowledge to draw on (factual, not strategy):
- pwn: standard pre-exploit triage is `checksec` → identify protection
  (NX, PIE, canary, RELRO) → match to exploit class (ret2libc, ROP,
  format string, heap UAF).
- crypto: cipher identification first (block cipher vs stream cipher,
  RSA, ECC); known weaknesses (small e, shared modulus, weak primes,
  reused IV/nonce) before attempting brute force.
- web: standard injection types (SQL, command, XSS, SSTI); auth bypass
  patterns; deserialization vulnerabilities.
- forensics: file carving (`binwalk`, `foremost`), steganography
  (`steghide`, `zsteg`, LSB), memory analysis (`volatility`).
- reversing: prefer disassemblers + dynamic analysis (`gdb`, `ltrace`)
  over decompilers for stripped binaries.

Open-source references worth considering when researching new tools:
- Public CTF writeups (ctftime.org), pwn.college, picoCTF, CryptoHack.
- Common toolkits: pwntools, Crypto.PublicKey (pycryptodome), z3-solver.

Sandbox constraints to respect:
- Docker container has a wall-clock budget; tools should fail fast
  rather than hang on long-running sweeps.
- The evolver sandbox runs with `network=none` by default — research
  HTTP calls only work in the dedicated research phase, not in the
  resulting tool at solve time.
