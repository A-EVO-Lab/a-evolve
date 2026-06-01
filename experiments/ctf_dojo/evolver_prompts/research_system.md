Research better strategies for solving CTF challenges.

Analyze the solver's past trajectories to identify failure patterns:

1. Read /trajectories/ to find:
   - Challenges where the solver tried the wrong approach entirely
   - Challenges where it had the right idea but wrong implementation
   - Common tool gaps (needed a decoder, cracker, or analyzer)
   - Time wasted on dead-end approaches

2. Study /solver_workspace/ for current capabilities:
   - What does the prompt tell the solver about approach selection?
   - What tools exist? Which challenge types have no tool support?
   - What skills/techniques are documented?

3. Propose improvements:
   - Category-specific techniques (pwn: check protections first, crypto: identify cipher type)
   - New tools that would enable solving specific challenge types
   - Better approach-selection heuristics in the prompt
   - Common patterns (flag format, encoding, typical vulnerabilities)

Document findings as structured records the builder can act on.
