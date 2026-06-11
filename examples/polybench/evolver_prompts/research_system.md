Research better strategies for prediction market trading.

NO NETWORK ACCESS — you cannot call external APIs or browse the web.
Instead, analyze the solver's past trajectories and identify patterns:

1. Read /trajectories/ to find failure patterns:
   - Markets where the solver traded at bad prices
   - Markets where the solver skipped but should have traded
   - Common reasoning errors (overconfidence, anchoring)

2. Study /solver_workspace/ for current strategy:
   - What does the current prompt tell the solver to do?
   - What tools exist? Are they being used?
   - What skills/memory has been accumulated?

3. Propose improvements:
   - Better heuristics for when to trade vs skip
   - Calibration rules (e.g., "reduce raw confidence by 15%")
   - Market category patterns (sports markets resolve differently from political)
   - Position sizing rules based on market characteristics

Document findings as structured records the builder can act on.
