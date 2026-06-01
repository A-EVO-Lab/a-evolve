PolyBench-specific verifier guidance — full system (with branching).

In addition to standard correctness checks on evolved decision logic,
this run uses git branches and routing. You must also assess
TRANSFERABILITY:

Non-regression / cross-event-type check:
- List 3-5 past trajectories from `/trajectories/batch_*/` that
  previously PASSED on event types DIFFERENT from the current target
  (binary vs multi-outcome vs over/under) or different domains.
- Re-run the evolved decision logic on those past inputs and confirm
  it still trades correctly. Examples of regressions to catch:
    * A new confidence threshold that flips correct trades to SKIP
    * A domain-specific heuristic now applied indiscriminately
    * Updated decision tools that drop coverage on a previous market type

Verdict semantics:
- VERDICT: PASS    — correct on target AND no cross-domain regression.
- VERDICT: PARTIAL — correct on target but the artifact looks
                     non-transferable. Recommend "isolate to
                     branch/<name>".
- VERDICT: FAIL    — broken decision logic; builder should retry.
