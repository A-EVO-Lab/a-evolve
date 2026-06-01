FutureX-specific verifier guidance — full system (with branching).

In addition to standard correctness checks on the search pipeline,
this run uses git branches and routing. You must also assess
TRANSFERABILITY:

Non-regression / cross-domain check:
- List 3-5 past trajectories from `/trajectories/batch_*/` that
  previously PASSED on DIFFERENT domains than the current target
  (look at index.txt rows whose domain column differs).
- Re-run the search pipeline against those past queries and verify
  it still returns valid, on-domain results. Examples of regressions:
    * A new sport-flavored prompt rule applied to non-sports queries
    * A premature-stopping rule that breaks long-tail searches
    * A source change that drops coverage for a previously-working
      domain

Verdict semantics:
- VERDICT: PASS    — correct on target AND no cross-domain regression.
- VERDICT: PARTIAL — correct on target but the artifact looks
                     non-transferable. Recommend "isolate to
                     branch/<name>".
- VERDICT: FAIL    — pipeline broken or returns invalid output.

Pipeline-level checks (still apply on top of the above):
- `infra/search_pipeline.py` accepts JSON on stdin and emits valid
  JSON on stdout, exits 0 within ~20s.
- All result dates are <= cutoff_date (no future leakage).
- Bad input → empty results, not a crash.
- Diverse query types pass: a financial data query, a news/events
  query, and a Chinese-language query.
